"""`#301-S1-A` -- physical Git snapshot: countermodels, positive controls,
Git parity and mutation discrimination.

Contract: `docs/engineering/agent-review-v2-301-s1a/ARCHITECTURE_FREEZE.md`.
Countermodel ids (A1-A13, R1-R14, P1-P19, L1-L10) and claim ids (C1-C12)
are the freeze's; each test names the ones it exercises.

Discriminators are functions that return the observed facts; the real code
is asserted GREEN on them and each mutant is asserted to flip the SPECIFIC
fact the discriminator exists for (`Killed(M) BY IntendedDiscriminator`), not
merely to fail somewhere.

Git is used ONLY as the upstream semantic oracle (PARITY_LAYOUT,
PARITY_OBJECT_SET), with `GIT_*` cleared and the version recorded; the
producer itself never runs git.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import signal
import stat
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import app.agent_review.physical_snapshot_v2 as psv
import app.agent_review.trusted_object_authority_v2 as toa
from app.agent_review.physical_snapshot_v2 import (
    CommittedSnapshotResidualV2,
    CompletePublicationV2,
    DeclaredGitObjectFormatV2,
    IndeterminatePublicationV2,
    KernelObjectIdentityV2,
    NotPublishedV2,
    PhysicalSnapshotErrorV2,
    PhysicalWorkBudgetV2,
    PhysicalWorkTrackerV2,
    PublishedSnapshotV2,
    SnapshotPublicationRootV2,
    SourceRepositoryLocatorV2,
    UnconfirmedPublicationV2,
    publish_physical_snapshot_v2,
)
from app.agent_review.trusted_object_authority_v2 import AuthorizedGitStorageSetV2

SHA1 = DeclaredGitObjectFormatV2.SHA1
SHA256 = DeclaredGitObjectFormatV2.SHA256

# -- environment ---------------------------------------------------------------------


def _git_env(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


def _git(cwd: Path, *args: str, env_extra: dict[str, str] | None = None) -> str:
    env = _git_env(cwd.parent if cwd.is_dir() else cwd)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout


GIT_VERSION = subprocess.run(["git", "--version"], capture_output=True, text=True, check=True).stdout.strip()


def _make_repo(path: Path, *, files: int = 3, packed: bool = True, object_format: str = "sha1") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", f"--object-format={object_format}", "-b", "main")
    _git(path, "config", "gc.auto", "0")
    for index in range(files):
        (path / f"f{index}.txt").write_text(f"content {index}\n" * (index + 1))
        _git(path, "add", f"f{index}.txt")
        _git(path, "commit", "-q", "-m", f"c{index}")
    if packed:
        _git(path, "repack", "-q", "-d")
    (path / "loose.txt").write_text("loose object\n")
    _git(path, "add", "loose.txt")
    _git(path, "commit", "-q", "-m", "loose")
    return path


def _budget(**overrides: int) -> PhysicalWorkBudgetV2:
    values = dict(
        max_descriptor_opens=100_000,
        max_path_components=100_000,
        max_entries_scanned=100_000,
        max_pointers_followed=1_000,
        max_pointer_bytes=1_000_000,
        max_pointer_lines=10_000,
        max_alternate_depth=16,
        max_source_bytes=1_000_000_000,
        max_files_copied=100_000,
    )
    values.update(overrides)
    return PhysicalWorkBudgetV2(**values)


def _publication_root(base: Path) -> SnapshotPublicationRootV2:
    (base / "staging").mkdir(parents=True)
    (base / "committed").mkdir()
    fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
    try:
        return SnapshotPublicationRootV2.from_directory_fd(fd)
    finally:
        os.close(fd)


def _publish(
    roots: list[Path],
    locator: str | int,
    pub: Path,
    *,
    budget: PhysicalWorkBudgetV2 | None = None,
    object_format: DeclaredGitObjectFormatV2 = SHA1,
    snapshot_id: str | None = None,
    authority: AuthorizedGitStorageSetV2 | None = None,
    publication_root: SnapshotPublicationRootV2 | None = None,
):
    capability = authority or AuthorizedGitStorageSetV2.from_roots([str(r) for r in roots])
    root = publication_root or _publication_root(pub)
    try:
        source_locator = (
            SourceRepositoryLocatorV2.root(locator)
            if isinstance(locator, int)
            else SourceRepositoryLocatorV2.absolute(str(locator))
        )
        return publish_physical_snapshot_v2(
            source_authority=capability,
            source_locator=source_locator,
            object_format=object_format,
            physical_budget=budget or _budget(),
            publication_root=root,
            _snapshot_id=snapshot_id,
        )
    finally:
        if authority is None:
            capability.close()
        if publication_root is None:
            root.close()


def _committed_entries(pub: Path) -> list[Path]:
    return sorted((pub / "committed").iterdir())


def _staging_entries(pub: Path) -> list[Path]:
    return sorted((pub / "staging").iterdir())


def _object_set(git_dir: Path, *, bare_env: bool = True) -> set[str]:
    env = {"GIT_DIR": str(git_dir)} if bare_env else None
    out = _git(git_dir, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)", env_extra=env)
    return set(out.split())


def _source_object_set(worktree: Path) -> set[str]:
    out = _git(worktree, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
    return set(out.split())


def _fd_census() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


def _files_under(path: Path) -> set[str]:
    return {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() or p.is_symlink()}


def _complete(outcome) -> PublishedSnapshotV2:
    assert isinstance(outcome, CompletePublicationV2), outcome
    return outcome.snapshot


# -- audit seam: every os/fcntl call made by the producer and the reused G1C helpers --


def _where(fd: object) -> str | None:
    if not isinstance(fd, int) or fd < 0:
        return None
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None


def _ident(fd: object) -> tuple[int, int] | None:
    if not isinstance(fd, int) or fd < 0:
        return None
    try:
        info = os.fstat(fd)
    except OSError:
        return None
    return (info.st_dev, info.st_ino)


class _Call:
    __slots__ = ("name", "args", "kwargs", "where", "ident")

    def __init__(self, name, args, kwargs, where, ident) -> None:
        self.name, self.args, self.kwargs, self.where, self.ident = name, args, kwargs, where, ident


class _OsAudit:
    """Proxy for the `os` module used by physical_snapshot_v2 and
    trusted_object_authority_v2. Every call is recorded with WHERE it happened
    (the directory a `dir_fd` or fd points at, resolved by the test harness)
    and the kernel identity of the fd argument; faults can be injected.
    Non-callables and pure helpers pass through untouched."""

    PASSTHROUGH = frozenset({"fsencode", "fsdecode", "makedev", "major", "minor", "fspath"})
    NAME_CALLS = frozenset({"open", "stat", "lstat", "access", "readlink", "listdir", "mkdir", "unlink", "rmdir", "rename"})

    def __init__(self, real) -> None:
        self._real = real
        self.calls: list[_Call] = []
        self.faults: dict[str, Callable] = {}

    def __getattr__(self, name):
        attribute = getattr(self._real, name)
        if not callable(attribute) or name.startswith("_") or name in self.PASSTHROUGH or isinstance(attribute, type):
            return attribute

        def wrapper(*args, **kwargs):
            dir_fd = kwargs.get("dir_fd")
            if dir_fd is not None:
                where = _where(dir_fd)
                ident = _ident(dir_fd)
            elif args and isinstance(args[0], int):
                where = _where(args[0])
                ident = _ident(args[0])
            else:
                where = str(args[0]) if args else None
                ident = None
            self.calls.append(_Call(name, args, kwargs, where, ident))
            fault = self.faults.get(name)
            if fault is not None:
                return fault(attribute, *args, **kwargs)
            return attribute(*args, **kwargs)

        return wrapper

    def named(self, name: str) -> list[_Call]:
        return [call for call in self.calls if call.name == name]


class _FcntlAudit:
    """Module-like proxy for `fcntl` recording descriptor duplications."""

    def __init__(self, calls: list[_Call]) -> None:
        self._calls = calls

    def __getattr__(self, name):
        return getattr(fcntl, name)

    def fcntl(self, fd, command, *args):
        if command == fcntl.F_DUPFD_CLOEXEC:
            self._calls.append(_Call("dup", (fd,), {}, _where(fd), _ident(fd)))
        return fcntl.fcntl(fd, command, *args)


@contextmanager
def _audited(monkeypatch) -> Iterator[_OsAudit]:
    audit = _OsAudit(os)
    fcntl_audit = _FcntlAudit(audit.calls)
    monkeypatch.setattr(psv, "os", audit)
    monkeypatch.setattr(toa, "os", audit)
    monkeypatch.setattr(psv, "fcntl", fcntl_audit)
    monkeypatch.setattr(toa, "fcntl", fcntl_audit)
    try:
        yield audit
    finally:
        monkeypatch.setattr(psv, "os", os)
        monkeypatch.setattr(toa, "os", os)
        monkeypatch.setattr(psv, "fcntl", fcntl)
        monkeypatch.setattr(toa, "fcntl", fcntl)


def _name_lookups(audit: _OsAudit) -> list[tuple[str, object, object]]:
    """(call, name, dir_fd) for every name-based call."""
    return [
        (call.name, call.args[0], call.kwargs.get("dir_fd"))
        for call in audit.calls
        if call.name in _OsAudit.NAME_CALLS and call.args
    ]


# -- kernel discriminator: inotify ------------------------------------------------------

_IN_ACCESS = 0x1
_IN_OPEN = 0x20
_IN_NONBLOCK = 0o4000
_IN_CLOEXEC = 0o2000000


class _Inotify:
    """Kernel-side observation of opens under a directory, independent of the
    producer's own seams. inotify sees opens only; lookup-only probes are
    covered by the audit (declared in the freeze)."""

    def __init__(self) -> None:
        self._libc = ctypes.CDLL(None, use_errno=True)
        self.fd = self._libc.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC)
        if self.fd < 0:
            pytest.skip("gate_unavailable: inotify")
        self._watches: dict[int, str] = {}

    def watch(self, path: Path) -> None:
        wd = self._libc.inotify_add_watch(self.fd, os.fsencode(str(path)), _IN_OPEN | _IN_ACCESS)
        if wd < 0:
            pytest.skip("gate_unavailable: inotify watch")
        self._watches[wd] = str(path)

    def drain(self) -> list[tuple[str, str, int]]:
        events = []
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return events
            offset = 0
            while offset < len(data):
                wd, mask, _cookie, length = struct.unpack_from("iIII", data, offset)
                name = data[offset + 16 : offset + 16 + length].rstrip(b"\x00").decode()
                events.append((self._watches.get(wd, "?"), name, mask))
                offset += 16 + length

    def close(self) -> None:
        os.close(self.fd)


# ================================================================================
# Positive controls and Git parity (C4, C6, PARITY_LAYOUT, PARITY_OBJECT_SET)
# ================================================================================


def test_parity_oracle_version_is_recorded(record_property) -> None:
    """§26 parity_oracle: the Git version used as the upstream oracle is part
    of the evidence (junit property + stdout)."""
    record_property("git_oracle_version", GIT_VERSION)
    print(f"git_oracle_version={GIT_VERSION}")
    assert GIT_VERSION.startswith("git version ")


def test_positive_standard_repository_publishes_complete_snapshot(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo")
    pub = tmp_path / "pub"
    before = _fd_census()
    outcome = _publish([tmp_path / "src"], repo, pub)
    snapshot = _complete(outcome)
    try:
        (committed,) = _committed_entries(pub)
        assert committed.name == snapshot.binding.snapshot_id
        assert _staging_entries(pub) == []
        assert stat.S_IMODE(committed.stat().st_mode) == 0o555
        for path in committed.rglob("*"):
            expected = 0o555 if path.is_dir() else 0o444
            assert stat.S_IMODE(path.lstat().st_mode) == expected, path
        info = os.fstat(snapshot.committed_dir_fd)
        assert (info.st_dev, info.st_ino) == (snapshot.binding.st_dev, snapshot.binding.st_ino)
        assert _object_set(committed) == _source_object_set(repo)  # PARITY_OBJECT_SET
    finally:
        snapshot.close()
    assert _fd_census() == before


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_parity_object_set_both_declared_formats(tmp_path: Path, object_format: str) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", object_format=object_format)
    pub = tmp_path / "pub"
    declared = SHA1 if object_format == "sha1" else SHA256
    snapshot = _complete(_publish([tmp_path / "src"], repo, pub, object_format=declared))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _source_object_set(repo)
        config = (committed / "config").read_text()
        if object_format == "sha256":
            assert "objectformat = sha256" in config and "repositoryformatversion = 1" in config
        else:
            assert "repositoryformatversion = 0" in config and "objectformat" not in config
    finally:
        snapshot.close()


def test_parity_layout_bare_repository(tmp_path: Path) -> None:
    source = _make_repo(tmp_path / "work" / "repo")
    bare = tmp_path / "src" / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)], check=True, env=_git_env(tmp_path))
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], bare, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _object_set(bare)
    finally:
        snapshot.close()


def _layout_resolution(roots: list[Path], locator: Path) -> Path:
    """The objects directory S1-A resolves, as an absolute path (test-only)."""
    capability = AuthorizedGitStorageSetV2.from_roots([str(r) for r in roots])
    try:
        session = psv._SourceSessionV2(capability, PhysicalWorkTrackerV2(_budget()))
        try:
            objects = psv._resolve_primary_store_v2(session, SourceRepositoryLocatorV2.absolute(str(locator)))
            root_locator = session._roots[objects.position.root_index].locator
            return Path(str(root_locator), *objects.position.components)
        finally:
            session.close()
    finally:
        capability.close()


def test_parity_layout_linked_worktree_absolute_pointers(tmp_path: Path) -> None:
    """PARITY_LAYOUT: `.git` file + commondir `../..`, resolved root-outward,
    equals Git's own `--git-common-dir`."""
    main = _make_repo(tmp_path / "src" / "main")
    worktree = tmp_path / "src" / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    assert (worktree / ".git").is_file()
    resolved = _layout_resolution([tmp_path / "src"], worktree)
    common = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert resolved == common / "objects"
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], worktree, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _source_object_set(worktree)
    finally:
        snapshot.close()


def test_parity_layout_relative_worktree_across_roots(tmp_path: Path) -> None:
    """A12 positive: a relative `gitdir:` that pops ABOVE its own root and lands
    in another authorized root (by captured locator name, nothing opened above
    the root); commondir `../..` then pops lexically. Git agrees."""
    main = _make_repo(tmp_path / "x" / "main")
    worktree = tmp_path / "x" / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    expected = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()) / "objects"
    roots = [tmp_path / "x" / "main", tmp_path / "x" / "wt"]
    assert _layout_resolution(roots, worktree) == expected
    pub = tmp_path / "pub"
    snapshot = _complete(_publish(roots, worktree, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _source_object_set(worktree)
    finally:
        snapshot.close()


@pytest.mark.parametrize("relative", [False, True])
def test_parity_object_set_with_authorized_alternate(tmp_path: Path, relative: bool) -> None:
    """C6: alternate bytes enter the snapshot; the pointer never does."""
    shared = _make_repo(tmp_path / "src" / "shared")
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    alternate = shared / ".git" / "objects"
    pointer = os.path.relpath(alternate, repo / ".git" / "objects") if relative else str(alternate)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(pointer + "\n")
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], repo, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert not (committed / "objects" / "info" / "alternates").exists()
        assert list((committed / "objects" / "info").iterdir()) == []
        assert _object_set(committed) == _source_object_set(repo)
        assert snapshot.receipt.alternate_sources == 1
    finally:
        snapshot.close()


def test_parity_object_set_standalone_pool_and_cycle(tmp_path: Path) -> None:
    """R7 + standalone pool (no sibling HEAD): a cycle is followed once."""
    repo = _make_repo(tmp_path / "src" / "repo")
    pool = tmp_path / "src" / "pool" / "objects"
    donor = _make_repo(tmp_path / "work" / "donor")
    pool.parent.mkdir(parents=True)
    subprocess.run(["cp", "-r", str(donor / ".git" / "objects"), str(pool)], check=True)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(pool) + "\n")
    (pool / "info").mkdir(exist_ok=True)
    (pool / "info" / "alternates").write_text(str(repo / ".git" / "objects") + "\n")
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], repo, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _source_object_set(repo)
    finally:
        snapshot.close()


def test_parity_object_set_partial_clone_excludes_promisor_metadata(tmp_path: Path) -> None:
    upstream = _make_repo(tmp_path / "work" / "upstream")
    _git(upstream, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "src" / "clone"
    result = subprocess.run(
        ["git", "clone", "-q", "--no-local", "--filter=blob:none", f"file://{upstream}", str(clone)],
        env=_git_env(tmp_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("gate_unavailable: partial clone fixture")
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], clone, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert not list(committed.rglob("*.promisor"))
        assert _object_set(committed) == _source_object_set(clone)
    finally:
        snapshot.close()


def test_git_alternate_nesting_boundary_parity_and_refusal(tmp_path: Path) -> None:
    """Hop 6 WITHOUT further alternates is inside Git's limit (parity); a hop-6
    store WITH alternates is refused, never published as a superset."""
    src = tmp_path / "src"
    stores = []
    for index in range(8):
        donor = _make_repo(tmp_path / "work" / f"d{index}", files=1, packed=False)
        store = src / f"s{index}" / "objects"
        store.parent.mkdir(parents=True)
        subprocess.run(["cp", "-r", str(donor / ".git" / "objects"), str(store)], check=True)
        stores.append(store)
    repo = _make_repo(src / "repo", files=1)
    chain = [repo / ".git" / "objects", *stores]
    for current, nxt in zip(chain[:6], chain[1:7]):
        (current / "info").mkdir(exist_ok=True)
        (current / "info" / "alternates").write_text(str(nxt) + "\n")
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([src], repo, pub))
    try:
        (committed,) = _committed_entries(pub)
        assert _object_set(committed) == _source_object_set(repo)
        assert snapshot.receipt.max_alternate_depth_seen == 6
    finally:
        snapshot.close()
    (chain[6] / "info" / "alternates").write_text(str(chain[7]) + "\n")
    outcome = _publish([src], repo, tmp_path / "pub2")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_ALTERNATE_NESTING_EXCEEDS_GIT_LIMIT_REASON_V2


def test_positive_receipt_is_traceability_only(tmp_path: Path) -> None:
    """D-RECEIPT / C5: no commit, root tree, ref, HEAD, config, remote,
    pointer text, locator or path; the fileset digest matches the disk."""
    repo = _make_repo(tmp_path / "src" / "repo")
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], repo, pub))
    try:
        (committed,) = _committed_entries(pub)
        raw = (committed / psv.PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2).read_bytes()
        assert raw == snapshot.receipt.canonical_bytes()
        body = json.loads(raw)
        assert set(body) == {
            "schema_version", "snapshot_id", "declared_object_format", "physical_work", "alternate_sources",
            "max_alternate_depth_seen", "copied_totals", "source_root_identities", "snapshot_fileset_digest",
        }
        assert str(tmp_path) not in raw.decode()
        assert "container_digest" not in raw.decode()
        entries = []
        for path in [committed, *committed.rglob("*")]:
            rel = "." if path == committed else str(path.relative_to(committed))
            if rel == psv.PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2:
                continue
            if path.is_dir():
                entries.append(["dir", rel, 0o555, 0, ""])
            else:
                data = path.read_bytes()
                entries.append(["file", rel, 0o444, len(data), hashlib.sha256(data).hexdigest()])
        entries.sort(key=lambda entry: entry[1])
        digest = hashlib.sha256(json.dumps(entries, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        assert body["snapshot_fileset_digest"] == digest
    finally:
        snapshot.close()


# ================================================================================
# Authority (C1, C4, A1-A13)
# ================================================================================


def _a1_fixture(tmp_path: Path) -> tuple[list[Path], Path, Path]:
    """A1 (4117560411): the alternate is ITSELF a root of A; its parent P
    (outside A) holds a HEAD file a sibling probe would open."""
    repo = _make_repo(tmp_path / "a" / "repo", files=1)
    outside_parent = tmp_path / "outside"
    donor = _make_repo(tmp_path / "work" / "donor")
    alternate_root = outside_parent / "objects"
    outside_parent.mkdir()
    subprocess.run(["cp", "-r", str(donor / ".git" / "objects"), str(alternate_root)], check=True)
    (outside_parent / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(alternate_root) + "\n")
    return [tmp_path / "a", alternate_root], repo, outside_parent


def _a1_discriminator(tmp_path: Path, monkeypatch) -> dict[str, object]:
    roots, repo, outside_parent = _a1_fixture(tmp_path)
    pub = tmp_path / "pub"
    root = _publication_root(pub)
    capability = AuthorizedGitStorageSetV2.from_roots([str(r) for r in roots])
    inotify = _Inotify()
    inotify.watch(outside_parent)
    inotify.drain()
    try:
        with _audited(monkeypatch) as audit:
            outcome = _publish(roots, repo, pub, authority=capability, publication_root=root)
        events = [event for event in inotify.drain() if event[1] in ("", "HEAD")]
    finally:
        inotify.close()
        capability.close()
        root.close()
    if isinstance(outcome, CompletePublicationV2):
        outcome.snapshot.close()
    lookups = _name_lookups(audit)
    return {
        "outcome": type(outcome).__name__,
        "outside_parent_open_events": events,
        "dotdot_lookups": [lookup for lookup in lookups if ".." in str(lookup[1]).split("/")],
        "unanchored_lookups": [lookup for lookup in lookups if lookup[2] is None],
        # the kernel ignores dir_fd for an absolute name
        "absolute_lookups": [lookup for lookup in lookups if str(lookup[1]).startswith("/")],
    }


def test_a1_storage_root_parent_probe_escape_is_never_opened(tmp_path: Path, monkeypatch) -> None:
    """C1 / A1: no parent/sibling of an admitted root is opened or looked up."""
    facts = _a1_discriminator(tmp_path, monkeypatch)
    assert facts["outcome"] == "CompletePublicationV2"
    assert facts["outside_parent_open_events"] == []
    assert facts["dotdot_lookups"] == []
    assert facts["unanchored_lookups"] == []
    assert facts["absolute_lookups"] == []


def test_mutation_a1_g1c_style_parent_probe_is_killed_by_inotify(tmp_path: Path, monkeypatch) -> None:
    """Mutant: reintroduce the G1C sibling-HEAD probe (`open('..')`, `../HEAD`).
    Killed by the KERNEL discriminator (inotify opens on P)."""
    original = psv._PhysicalCopierV2.copy_store

    def probing_copy_store(self, store, hop):
        if hop > 0 and store.fd is not None:
            parent = os.open("..", os.O_RDONLY | os.O_DIRECTORY, dir_fd=store.fd)
            try:
                head = os.open("HEAD", os.O_RDONLY, dir_fd=parent)
                os.close(head)
            finally:
                os.close(parent)
        return original(self, store, hop)

    monkeypatch.setattr(psv._PhysicalCopierV2, "copy_store", probing_copy_store)
    facts = _a1_discriminator(tmp_path, monkeypatch)
    assert facts["outside_parent_open_events"], "intended discriminator (inotify) did not observe the probe"


def test_mutation_a1_lookup_only_probe_is_killed_by_audit(tmp_path: Path, monkeypatch) -> None:
    """Mutant: a lookup-only probe (`fstatat('..')`). inotify is blind to it by
    construction (declared); the audit discriminator kills it."""
    original = psv._PhysicalCopierV2.copy_store

    def peeking_copy_store(self, store, hop):
        if hop > 0 and store.fd is not None:
            try:
                psv.os.stat("../HEAD", dir_fd=store.fd, follow_symlinks=False)
            except OSError:
                pass
        return original(self, store, hop)

    monkeypatch.setattr(psv._PhysicalCopierV2, "copy_store", peeking_copy_store)
    facts = _a1_discriminator(tmp_path, monkeypatch)
    assert facts["outside_parent_open_events"] == []  # inotify: blind, as declared
    assert facts["dotdot_lookups"], "intended discriminator (audit) did not observe the lookup"


def test_mutation_a1_absolute_lookup_with_a_dir_fd_is_killed_by_audit(tmp_path: Path, monkeypatch) -> None:
    """Mutant: `fstatat(<absolute outside path>, dir_fd=store)` -- the dir_fd is
    ignored by the kernel. Killed by the absolute-name audit fact."""
    original = psv._PhysicalCopierV2.copy_store

    def absolute_peek(self, store, hop):
        if hop > 0 and store.fd is not None:
            try:
                psv.os.stat(str(tmp_path / "outside" / "HEAD"), dir_fd=store.fd, follow_symlinks=False)
            except OSError:
                pass
        return original(self, store, hop)

    monkeypatch.setattr(psv._PhysicalCopierV2, "copy_store", absolute_peek)
    facts = _a1_discriminator(tmp_path, monkeypatch)
    assert facts["dotdot_lookups"] == [] and facts["unanchored_lookups"] == []
    assert facts["absolute_lookups"], "intended discriminator (absolute-name audit) did not fire"


def test_a2_alternate_outside_capability_is_refused_without_open(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    outside = _make_repo(tmp_path / "outside" / "other") / ".git" / "objects"
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(outside) + "\n")
    inotify = _Inotify()
    inotify.watch(outside)
    inotify.watch(outside.parent)
    inotify.drain()
    try:
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
        events = inotify.drain()
    finally:
        inotify.close()
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2
    assert events == []
    assert _committed_entries(tmp_path / "pub") == [] and _staging_entries(tmp_path / "pub") == []


@pytest.mark.parametrize("pointer_file", ["gitfile", "commondir"])
def test_a3_absolute_layout_pointer_outside_capability(tmp_path: Path, pointer_file: str) -> None:
    repo = tmp_path / "src" / "repo"
    repo.mkdir(parents=True)
    outside = _make_repo(tmp_path / "outside" / "other") / ".git"
    if pointer_file == "gitfile":
        (repo / ".git").write_text(f"gitdir: {outside}\n")
    else:
        (repo / ".git").mkdir()
        (repo / ".git" / "commondir").write_text(str(outside) + "\n")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2


def test_a4_relative_pointer_escaping_a_root_without_locator(tmp_path: Path) -> None:
    """A root with no captured locator (from_repository_fd) gives nothing to
    name ABOVE it: a pointer popping past it is refused before any open."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    (repo / ".git" / "objects" / "info" / "alternates").write_text("../../../../elsewhere/objects\n")
    repo_fd = os.open(str(repo), os.O_RDONLY | os.O_DIRECTORY)
    try:
        capability = AuthorizedGitStorageSetV2.from_repository_fd(repo_fd)
    finally:
        os.close(repo_fd)
    try:
        outcome = _publish([], 0, tmp_path / "pub", authority=capability)
        assert isinstance(outcome, NotPublishedV2)
        assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_POINTER_ESCAPES_CAPABILITY_ROOT_REASON_V2
    finally:
        capability.close()


def test_a4_relative_pointer_above_root_matching_no_root(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    (repo / ".git" / "objects" / "info" / "alternates").write_text("../../../../elsewhere/objects\n")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2


@pytest.mark.parametrize("where", ["dotgit", "objects", "fanout", "object", "alternate_component", "pack_dir"])
def test_a5_symlink_components_are_refused(tmp_path: Path, where: str) -> None:
    repo = _make_repo(tmp_path / "src" / "repo")
    objects = repo / ".git" / "objects"
    if where == "dotgit":
        (repo / ".git").rename(tmp_path / "src" / "moved")
        (repo / ".git").symlink_to(tmp_path / "src" / "moved")
    elif where == "objects":
        (repo / ".git" / "objects").rename(tmp_path / "src" / "objs")
        (repo / ".git" / "objects").symlink_to(tmp_path / "src" / "objs")
    elif where == "fanout":
        fanout = next(p for p in objects.iterdir() if len(p.name) == 2)
        fanout.rename(tmp_path / "src" / "fan")
        fanout.symlink_to(tmp_path / "src" / "fan")
    elif where == "object":
        fanout = next(p for p in objects.iterdir() if len(p.name) == 2)
        obj = next(fanout.iterdir())
        obj.rename(tmp_path / "src" / "obj")
        obj.symlink_to(tmp_path / "src" / "obj")
    elif where == "pack_dir":
        (objects / "pack").rename(tmp_path / "src" / "packs")
        (objects / "pack").symlink_to(tmp_path / "src" / "packs")
    else:
        shared = _make_repo(tmp_path / "src" / "shared") / ".git"
        (tmp_path / "src" / "link").symlink_to(shared)
        (objects / "info" / "alternates").write_text(str(tmp_path / "src" / "link" / "objects") + "\n")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2), outcome
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2


@contextmanager
def _deadline(seconds: int = 20):
    def expire(signum, frame):
        raise TimeoutError("blocked on a special file")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


@pytest.mark.parametrize("where", ["dotgit", "alternates", "object"])
def test_a6_special_files_are_refused_without_blocking(tmp_path: Path, where: str) -> None:
    repo = _make_repo(tmp_path / "src" / "repo")
    objects = repo / ".git" / "objects"
    if where == "dotgit":
        (repo / ".git").rename(tmp_path / "work-git")
        os.mkfifo(repo / ".git")
    elif where == "alternates":
        os.mkfifo(objects / "info" / "alternates")
    else:
        fanout = next(p for p in objects.iterdir() if len(p.name) == 2)
        obj = next(fanout.iterdir())
        obj.unlink()
        os.mkfifo(obj)
    with _deadline():
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2


def test_a7_rebind_between_descents_never_follows_outside_the_capability(tmp_path: Path, monkeypatch) -> None:
    """A7: between two descents the next component is swapped for a symlink to
    a repository OUTSIDE A. A resolver that followed it would open outside
    (inotify would see it); the no-follow descent refuses it instead."""
    src = tmp_path / "src"
    repo = _make_repo(src / "repo")
    outside = _make_repo(tmp_path / "outside" / "victim")
    swapped = {"done": False}
    real_open_dir = psv._try_open_dir_no_follow_v2

    def swapping_open(dir_fd, name):
        if name == "repo" and not swapped["done"]:
            swapped["done"] = True
            repo.rename(src / "orig-repo")
            repo.symlink_to(outside)
        return real_open_dir(dir_fd, name)

    monkeypatch.setattr(psv, "_try_open_dir_no_follow_v2", swapping_open)
    inotify = _Inotify()
    inotify.watch(outside)
    inotify.watch(outside / ".git")
    inotify.drain()
    try:
        outcome = _publish([src], repo, tmp_path / "pub")
        events = inotify.drain()
    finally:
        inotify.close()
    assert swapped["done"] is True
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2
    assert events == []


def test_a7_positive_control_following_resolver_would_be_seen(tmp_path: Path) -> None:
    """Anti-vacuity for A7: opening through that symlink IS observed by the watch."""
    outside = _make_repo(tmp_path / "outside" / "victim")
    link = tmp_path / "link"
    link.symlink_to(outside)
    inotify = _Inotify()
    inotify.watch(outside / ".git")
    inotify.drain()
    try:
        fd = os.open(str(link / ".git"), os.O_RDONLY | os.O_DIRECTORY)
        os.close(fd)
        events = inotify.drain()
    finally:
        inotify.close()
    assert events


def test_a8_closed_source_capability_is_refused(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    capability = AuthorizedGitStorageSetV2.from_roots([str(tmp_path / "src")])
    capability.close()
    outcome = _publish([], repo, tmp_path / "pub", authority=capability)
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SOURCE_CAPABILITY_CLOSED_REASON_V2


def test_a9_missing_or_wrong_capability_is_a_contract_error(tmp_path: Path) -> None:
    root = _publication_root(tmp_path / "pub")
    try:
        with pytest.raises(PhysicalSnapshotErrorV2):
            publish_physical_snapshot_v2(
                source_authority=None,  # type: ignore[arg-type]
                source_locator=SourceRepositoryLocatorV2.root(0),
                object_format=SHA1,
                physical_budget=_budget(),
                publication_root=root,
            )
        with pytest.raises(PhysicalSnapshotErrorV2):
            SourceRepositoryLocatorV2.absolute("relative/path")
        with pytest.raises(PhysicalSnapshotErrorV2):
            SourceRepositoryLocatorV2.absolute("/a/../b")
        with pytest.raises(TypeError):
            PhysicalWorkBudgetV2(max_descriptor_opens=1)  # type: ignore[call-arg]
        with pytest.raises(PhysicalSnapshotErrorV2):
            _budget(max_entries_scanned=0)
        with pytest.raises(PhysicalSnapshotErrorV2):
            _budget(max_entries_scanned=True)  # type: ignore[arg-type]
    finally:
        root.close()


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"gitdir: a/../b\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: ./x\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: x//y\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: x/\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir:  x\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: x\r\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: x\nextra\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: \xff\xfe\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: a\x00b\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir:\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"worktree: x\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: ..\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (b"gitdir: ../..\n", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
    ],
)
def test_a10_malformed_gitfile_pointers_are_refused(tmp_path: Path, content: bytes, reason: str) -> None:
    repo = tmp_path / "src" / "repo"
    repo.mkdir(parents=True)
    (repo / ".git").write_bytes(content)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == reason


@pytest.mark.parametrize(
    "line,reason",
    [
        ('"/quoted/objects"', psv.PHYSICAL_SNAPSHOT_ALTERNATE_QUOTED_PATH_UNSUPPORTED_REASON_V2),
        ("../x/./objects", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        ("x/../objects", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        (" /abs/objects", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        ("..", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
        ("../..", psv.PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2),
    ],
)
def test_a10_malformed_alternate_lines_are_refused(tmp_path: Path, line: str, reason: str) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(line + "\n")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == reason


def test_a10_blank_and_comment_alternate_lines_are_skipped(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    (repo / ".git" / "objects" / "info" / "alternates").write_text("\n# comment\n")
    snapshot = _complete(_publish([tmp_path / "src"], repo, tmp_path / "pub"))
    assert dict(snapshot.receipt.physical_work)["pointer_lines"] == 2
    assert dict(snapshot.receipt.physical_work)["pointers_followed"] == 0
    snapshot.close()


def test_a11_repository_locator_outside_capability(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    outside = _make_repo(tmp_path / "outside" / "repo")
    outcome = _publish([tmp_path / "src"], outside, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_REPOSITORY_LOCATOR_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2
    outcome = _publish([tmp_path / "src"], 7, tmp_path / "pub2")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_REPOSITORY_LOCATOR_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2


def _inject_source_metadata(repo: Path) -> None:
    git_dir = repo / ".git"
    (git_dir / "hooks" / "post-checkout").write_text("#!/bin/sh\necho pwned\n")
    with open(git_dir / "config", "a") as config:
        config.write('[remote "origin"]\n\turl = https://example.invalid/x\n\tpromisor = true\n')
    (git_dir / "packed-refs").write_text("# pack-refs\n")
    (git_dir / "objects" / "info" / "commit-graph").write_bytes(b"CGPH-forged")
    (git_dir / "objects" / "info" / "packs").write_text("P pack-x.pack\n")
    pack_dir = git_dir / "objects" / "pack"
    base = next(p.stem for p in pack_dir.glob("*.pack"))
    for suffix in (".promisor", ".keep", ".bitmap", ".rev", ".mtimes"):
        extra = pack_dir / (base + suffix)
        if not extra.exists():
            extra.write_bytes(b"x")
    (git_dir / "objects" / "tmp_obj_abc").write_bytes(b"x")
    (git_dir / "objects" / "incoming-123").mkdir()
    (git_dir / "shallow").write_text("0" * 40 + "\n")


def _c5_discriminator(tmp_path: Path) -> dict[str, object]:
    repo = _make_repo(tmp_path / "src" / "repo")
    _inject_source_metadata(repo)
    pub = tmp_path / "pub"
    snapshot = _complete(_publish([tmp_path / "src"], repo, pub))
    try:
        (committed,) = _committed_entries(pub)
        files = _files_under(committed)
        return {
            "metadata_published": sorted(
                name
                for name in files
                if not (
                    name in ("HEAD", "config", psv.PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2)
                    or (name.startswith("objects/") and len(name.split("/")) == 3 and len(name.split("/")[1]) == 2)
                    or (name.startswith("objects/pack/pack-") and name.endswith((".pack", ".idx")))
                )
            ),
            "config": (committed / "config").read_text(),
            "head": (committed / "HEAD").read_text(),
        }
    finally:
        snapshot.close()


def test_a13_source_metadata_never_crosses_publication(tmp_path: Path) -> None:
    """C5: hooks, remote/promisor config, packed-refs, commit-graph, packs
    metadata, shallow, tmp/incoming never cross; the skeleton is authored."""
    facts = _c5_discriminator(tmp_path)
    assert facts["metadata_published"] == []
    assert facts["config"] == "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"
    assert facts["head"] == "ref: refs/heads/none\n"


def test_mutation_c5_copy_info_wholesale_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: copy every file of `objects/info/` instead of reading only the pointer."""
    original = psv._PhysicalCopierV2.copy_store

    def wholesale(self, store, hop):
        if store.fd is not None:
            info_fd = os.open("info", os.O_RDONLY | os.O_DIRECTORY, dir_fd=store.fd)
            try:
                for name in os.listdir(info_fd):
                    data_fd = os.open(name, os.O_RDONLY, dir_fd=info_fd)
                    try:
                        data = os.read(data_fd, 1 << 20)
                    finally:
                        os.close(data_fd)
                    self._writer.write_file("objects/info", name, data, copied=True)
            finally:
                os.close(info_fd)
        return original(self, store, hop)

    monkeypatch.setattr(psv._PhysicalCopierV2, "copy_store", wholesale)
    facts = _c5_discriminator(tmp_path)
    assert "objects/info/commit-graph" in facts["metadata_published"]


def test_mutation_c6_publishing_the_alternate_pointer_is_killed(tmp_path: Path, monkeypatch) -> None:
    shared = _make_repo(tmp_path / "src" / "shared")
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    pointer = str(shared / ".git" / "objects") + "\n"
    (repo / ".git" / "objects" / "info" / "alternates").write_text(pointer)
    original = psv._StagingWriterV2.write_skeleton

    def leaking_skeleton(self, object_format):
        original(self, object_format)
        self.write_file("objects/info", "alternates", pointer.encode(), copied=False)

    monkeypatch.setattr(psv._StagingWriterV2, "write_skeleton", leaking_skeleton)
    snapshot = _complete(_publish([tmp_path / "src"], repo, tmp_path / "pub"))
    try:
        (committed,) = _committed_entries(tmp_path / "pub")
        assert (committed / "objects" / "info" / "alternates").exists()  # the C6 witness fires
    finally:
        snapshot.close()


# ================================================================================
# Physical resources (C3, R1-R14)
# ================================================================================


def _r1_fixture(tmp_path: Path) -> Path:
    repo = _make_repo(tmp_path / "src" / "repo", files=1, packed=False)
    objects = repo / ".git" / "objects"
    for index in range(3000):
        (objects / f"junk-{index:05d}").write_bytes(b"")
    return repo


def test_r1_junk_entries_are_charged_before_any_useful_work(tmp_path: Path) -> None:
    repo = _r1_fixture(tmp_path)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_entries_scanned=1000))
    assert isinstance(outcome, NotPublishedV2)
    assert (outcome.reason_code, outcome.exceeded_axis) == (
        psv.PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2,
        "entries_scanned",
    )


def test_mutation_r2_ignored_entries_not_charged_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: charge only relevant entries. The R1 witness becomes a false
    green (publishes), which is exactly what the discriminator exists to catch."""

    def charge_only_relevant(tracker, dir_fd, classify):
        tracker.charge(descriptor_opens=1)
        relevant = []
        with os.scandir(dir_fd) as iterator:
            for entry in iterator:
                kind = classify(entry.name)
                if kind is not None:
                    tracker.charge(entries_scanned=1)
                    relevant.append((entry.name, kind))
        return relevant

    monkeypatch.setattr(psv, "_scan_directory_v2", charge_only_relevant)
    repo = _r1_fixture(tmp_path)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_entries_scanned=1000))
    assert isinstance(outcome, CompletePublicationV2)
    outcome.snapshot.close()


def _r3_discriminator(tmp_path: Path, monkeypatch) -> dict[str, object]:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    alternates = repo / ".git" / "objects" / "info" / "alternates"
    alternates.write_text("#" * 70_000 + "\n")
    target = _ident_of(alternates)
    with _audited(monkeypatch) as audit:
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_pointer_bytes=64 * 1024))
    manifest_reads = [call for call in audit.named("read") if call.ident == target]
    return {
        "outcome": outcome,
        "manifest_bytes_requested": sum(call.args[1] for call in manifest_reads),
    }


def _ident_of(path: Path) -> tuple[int, int]:
    info = path.stat()
    return (info.st_dev, info.st_ino)


def test_r3_oversized_manifest_is_refused_before_it_is_read(tmp_path: Path, monkeypatch) -> None:
    """X2: the manifest is charged from fstat and refused before ONE byte of it is read."""
    facts = _r3_discriminator(tmp_path, monkeypatch)
    outcome = facts["outcome"]
    assert isinstance(outcome, NotPublishedV2)
    assert (outcome.reason_code, outcome.exceeded_axis) == (psv.PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2, "pointer_bytes")
    assert facts["manifest_bytes_requested"] == 0


def test_mutation_r3_read_before_charge_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: read the pointer, then charge. Killed by the byte-level read audit."""

    def read_then_charge(tracker, fd, *, pointer):
        size = psv.os.fstat(fd).st_size
        data = psv.os.read(fd, size + 1)
        if pointer:
            tracker.charge(pointer_bytes=size, source_bytes=size)
        else:
            tracker.charge(source_bytes=size)
        return data

    monkeypatch.setattr(psv, "_read_charged_v2", read_then_charge)
    facts = _r3_discriminator(tmp_path, monkeypatch)
    assert isinstance(facts["outcome"], NotPublishedV2)
    assert facts["manifest_bytes_requested"] > 64 * 1024


def test_r4_excessive_manifest_lines(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    (repo / ".git" / "objects" / "info" / "alternates").write_text("#\n" * 50)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_pointer_lines=10))
    assert isinstance(outcome, NotPublishedV2) and outcome.exceeded_axis == "pointer_lines"


def test_r5_pointer_with_excessive_components(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    deep = tmp_path / "src" / Path(*[f"d{i}" for i in range(40)])
    deep.mkdir(parents=True)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(deep) + "\n")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_path_components=30))
    assert isinstance(outcome, NotPublishedV2) and outcome.exceeded_axis == "path_components"


def test_r6_deep_alternate_chain_exceeds_depth_budget(tmp_path: Path) -> None:
    src = tmp_path / "src"
    repo = _make_repo(src / "repo", files=1)
    previous = repo / ".git" / "objects"
    for index in range(3):
        store = _make_repo(src / f"s{index}", files=1) / ".git" / "objects"
        (previous / "info" / "alternates").write_text(str(store) + "\n")
        previous = store
    outcome = _publish([src], repo, tmp_path / "pub", budget=_budget(max_alternate_depth=2))
    assert isinstance(outcome, NotPublishedV2) and outcome.exceeded_axis == "alternate_depth"


def test_r8_duplicate_physical_occurrence_is_charged_not_copied_twice(tmp_path: Path) -> None:
    src = tmp_path / "src"
    repo = _make_repo(src / "repo", files=1, packed=False)
    clone = src / "clone.git"
    subprocess.run(["cp", "-r", str(repo / ".git"), str(clone)], check=True)
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(clone / "objects") + "\n")
    loose_total = sum(1 for p in (repo / ".git" / "objects").glob("??/*"))
    snapshot = _complete(_publish([src], repo, tmp_path / "pub"))
    try:
        work = dict(snapshot.receipt.physical_work)
        assert snapshot.receipt.files_copied == loose_total
        assert work["files_copied"] == loose_total
        assert work["entries_scanned"] >= 2 * loose_total  # both occurrences charged
    finally:
        snapshot.close()


def test_r9_loose_object_bomb_is_copied_compressed_never_inflated(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    payload = b"blob 50000000\x00" + b"\x00" * 50_000_000
    compressed = zlib.compress(payload, 9)
    fanout = repo / ".git" / "objects" / "ab"
    fanout.mkdir(exist_ok=True)
    (fanout / ("c" * 38)).write_bytes(compressed)
    inflations: list[int] = []
    monkeypatch.setattr(zlib, "decompress", lambda *a, **k: inflations.append(1))
    snapshot = _complete(_publish([tmp_path / "src"], repo, tmp_path / "pub"))
    try:
        (committed,) = _committed_entries(tmp_path / "pub")
        assert (committed / "objects" / "ab" / ("c" * 38)).read_bytes() == compressed
        assert inflations == []
    finally:
        snapshot.close()


def test_r10_copy_byte_limit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_source_bytes=100))
    assert isinstance(outcome, NotPublishedV2) and outcome.exceeded_axis == "source_bytes"


@pytest.mark.parametrize("delta", [+1, -1])
def test_r11_file_that_grows_or_shrinks_during_read_is_refused(tmp_path: Path, monkeypatch, delta: int) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", packed=False)
    real_fstat = os.fstat

    class Lying:
        def __init__(self, info):
            self._info = info

        def __getattr__(self, name):
            value = getattr(self._info, name)
            return value + delta if name == "st_size" else value

    def lying_fstat(fd):
        info = real_fstat(fd)
        return Lying(info) if stat.S_ISREG(info.st_mode) and info.st_size > 0 else info

    with _audited(monkeypatch) as audit:
        audit.faults["fstat"] = lambda real, fd: lying_fstat(fd)
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SOURCE_CHANGED_DURING_READ_REASON_V2


def _descriptor_accounting(tmp_path: Path, monkeypatch) -> dict[str, int]:
    """R12/R14/C3: on the SOURCE side (resolved by where the call happened),
    every descriptor creation -- root dups, dir dups, every openat, the
    internal dup of each scandir -- and every single-component directory
    descent is observed, and must equal what was charged, exactly."""
    src = tmp_path / "src"
    main = _make_repo(src / "deep" / "tree" / "main")
    worktree = src / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    shared = _make_repo(src / "shared", files=1)
    (main / ".git" / "objects" / "info" / "alternates").write_text(
        os.path.relpath(shared / ".git" / "objects", main / ".git" / "objects") + "\n"
    )
    pub = tmp_path / "pub"
    root = _publication_root(pub)
    capability = AuthorizedGitStorageSetV2.from_roots([str(src)])
    try:
        with _audited(monkeypatch) as audit:
            outcome = _publish([], worktree, pub, authority=capability, publication_root=root)
    finally:
        capability.close()
        root.close()
    snapshot = _complete(outcome)
    work = dict(snapshot.receipt.physical_work)
    snapshot.close()
    source_prefix = str(src)
    opens = components = 0
    for call in audit.calls:
        if call.where is None or not call.where.startswith(source_prefix):
            continue
        if call.name in ("open", "scandir", "dup"):
            opens += 1
        if call.name == "open" and (call.args[1] & os.O_DIRECTORY or call.args[0] == ".git"):
            components += 1
    return {
        "charged_descriptor_opens": work["descriptor_opens"],
        "observed_source_descriptor_creations": opens,
        "charged_path_components": work["path_components"],
        "observed_source_component_descents": components,
    }


def test_r12_r14_every_source_descriptor_creation_is_charged_exactly(tmp_path: Path, monkeypatch) -> None:
    facts = _descriptor_accounting(tmp_path, monkeypatch)
    assert facts["charged_descriptor_opens"] == facts["observed_source_descriptor_creations"] > 0
    assert facts["charged_path_components"] == facts["observed_source_component_descents"] > 0


def test_mutation_r14_uncharged_re_descent_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: re-descend after a lexical `..` without charging the components.
    Killed by the exact accounting equality, on the component axis."""
    real_charge = psv.PhysicalWorkTrackerV2.charge

    def lenient_charge(self, **amounts):
        if amounts == {"descriptor_opens": 1, "path_components": 1}:
            amounts = {"descriptor_opens": 1}
        return real_charge(self, **amounts)

    monkeypatch.setattr(psv.PhysicalWorkTrackerV2, "charge", lenient_charge)
    facts = _descriptor_accounting(tmp_path, monkeypatch)
    assert facts["charged_descriptor_opens"] == facts["observed_source_descriptor_creations"]
    assert facts["charged_path_components"] < facts["observed_source_component_descents"]


def test_r13_files_copied_limit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", packed=False)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_files_copied=2))
    assert isinstance(outcome, NotPublishedV2) and outcome.exceeded_axis == "files_copied"


# ================================================================================
# Publication (C2, C7-C10, C12, P1-P19)
# ================================================================================


def _big_repo(path: Path) -> Path:
    repo = _make_repo(path, files=1)
    (repo / "big.bin").write_bytes(os.urandom(200_000))
    _git(repo, "add", "big.bin")
    _git(repo, "commit", "-q", "-m", "big")
    _git(repo, "repack", "-q", "-d")
    (repo / "big2.bin").write_bytes(os.urandom(50_000))
    _git(repo, "add", "big2.bin")
    _git(repo, "commit", "-q", "-m", "big2")
    return repo


def _copied_files_match_source(repo: Path, committed: Path) -> list[str]:
    mismatched = []
    source_objects = repo / ".git" / "objects"
    for path in (committed / "objects").rglob("*"):
        if path.is_file():
            rel = path.relative_to(committed / "objects")
            if (source_objects / rel).read_bytes() != path.read_bytes():
                mismatched.append(str(rel))
    return mismatched


def _p1_discriminator(tmp_path: Path, monkeypatch) -> dict[str, object]:
    repo = _big_repo(tmp_path / "src" / "repo")
    with _audited(monkeypatch) as audit:
        audit.faults["write"] = lambda real, fd, data: real(fd, bytes(data[:4093]))
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    snapshot = _complete(outcome)
    try:
        (committed,) = _committed_entries(tmp_path / "pub")
        receipt_file = json.loads((committed / psv.PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2).read_bytes())
        return {
            "short_writes_forced": sum(1 for call in audit.named("write") if len(call.args[1]) > 4093),
            "mismatched": _copied_files_match_source(repo, committed),
            "receipt_parses": receipt_file["snapshot_id"] == snapshot.binding.snapshot_id,
        }
    finally:
        snapshot.close()


def test_p1_forced_short_writes_publish_exact_bytes(tmp_path: Path, monkeypatch) -> None:
    """C7 / RC-5: every write is cut to 4093 bytes; the loop still publishes exact bytes."""
    facts = _p1_discriminator(tmp_path, monkeypatch)
    assert facts["short_writes_forced"] > 0
    assert facts["mismatched"] == []
    assert facts["receipt_parses"]


def test_mutation_p1_single_write_per_file_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: one os.write per file (the RC-5 defect). Killed by byte equality."""
    monkeypatch.setattr(psv, "_write_all_v2", lambda fd, data: psv.os.write(fd, data))
    try:
        facts = _p1_discriminator(tmp_path, monkeypatch)
    except json.JSONDecodeError:
        facts = {"mismatched": ["receipt truncated"]}
    assert facts["mismatched"], "intended discriminator (byte equality) did not fire"


def test_p2_partial_write_then_error_publishes_nothing(tmp_path: Path, monkeypatch) -> None:
    repo = _big_repo(tmp_path / "src" / "repo")
    state = {"calls": 0}

    def partial_then_fail(real, fd, data):
        state["calls"] += 1
        if state["calls"] == 1:
            return real(fd, bytes(data[:100]))
        raise OSError(errno.EIO, "injected")

    before = _fd_census()
    with _audited(monkeypatch) as audit:
        audit.faults["write"] = partial_then_fail
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2
    assert outcome.staging_residue is False
    assert _committed_entries(tmp_path / "pub") == [] and _staging_entries(tmp_path / "pub") == []
    assert _fd_census() == before


def test_p3_zero_progress_write_is_refused(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    with _audited(monkeypatch) as audit:
        audit.faults["write"] = lambda real, fd, data: 0
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SHORT_WRITE_NO_PROGRESS_REASON_V2
    assert _committed_entries(tmp_path / "pub") == [] and _staging_entries(tmp_path / "pub") == []


def _fail_on(name: str, *, when: Callable[..., bool] = lambda *a, **k: True):
    def fault(real, *args, **kwargs):
        if when(*args, **kwargs):
            raise OSError(errno.EIO, "injected")
        return real(*args, **kwargs)

    return name, fault


@pytest.mark.parametrize(
    "fault_name",
    ["mkdir_snapshot", "create_file", "fchmod_file", "fsync_file", "fchmod_dir", "stage_root_fsync"],
)
def test_p4_failure_before_the_commit_point_never_publishes(tmp_path: Path, monkeypatch, fault_name: str) -> None:
    """C8 / P4 / L5 / L6: every pre-commit failure -> NotPublished, nothing in
    committed/, staging cleaned (0555 dirs are made writable first), no fd leak."""
    repo = _make_repo(tmp_path / "src" / "repo")
    before = _fd_census()
    with _audited(monkeypatch) as audit:
        if fault_name == "mkdir_snapshot":
            audit.faults["mkdir"] = _fail_on("mkdir")[1]
        elif fault_name == "create_file":
            audit.faults["open"] = _fail_on("open", when=lambda *a, **k: len(a) > 1 and a[1] & os.O_CREAT)[1]
        elif fault_name == "fchmod_file":
            audit.faults["fchmod"] = _fail_on("fchmod", when=lambda fd, mode: mode == 0o444)[1]
        elif fault_name == "fsync_file":
            audit.faults["fsync"] = _fail_on("fsync")[1]
        elif fault_name == "fchmod_dir":
            audit.faults["fchmod"] = _fail_on("fchmod", when=lambda fd, mode: mode == 0o555)[1]
        else:
            def failing_root_fsync(self):
                raise psv._RefusalV2(psv.PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)

            monkeypatch.setattr(psv._StagingWriterV2, "fsync_stage_root", failing_root_fsync)
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2), outcome
    assert outcome.staging_residue is False
    assert _committed_entries(tmp_path / "pub") == []
    assert _staging_entries(tmp_path / "pub") == []
    assert _fd_census() == before


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_p4_stage_open_failure_after_mkdir_is_cleaned_or_reported(tmp_path: Path, monkeypatch, cleanup_fails: bool) -> None:
    """Codex 4128747291 / review F1: `staging/<id>` exists when its open fails;
    the writer already owns it, so it is removed -- or reported as residue."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    snapshot_id = "c" * 32

    armed = {"once": True}

    def fail_stage_open(real, name, flags, *args, **kwargs):
        if name == snapshot_id and not flags & os.O_CREAT and armed["once"]:
            armed["once"] = False  # only the writer's open; the cleanup may open it again
            raise OSError(errno.EMFILE, "injected")
        return real(name, flags, *args, **kwargs)

    with _audited(monkeypatch) as audit:
        audit.faults["open"] = fail_stage_open
        if cleanup_fails:
            audit.faults["rmdir"] = _fail_on("rmdir")[1]
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", snapshot_id=snapshot_id)
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2
    left = _staging_entries(tmp_path / "pub")
    assert outcome.staging_residue is cleanup_fails
    assert (left != []) is cleanup_fails  # residue reported exactly when it exists


def _p5_p6_discriminator(tmp_path: Path) -> dict[str, object]:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    pub = tmp_path / "pub"
    root = _publication_root(pub)
    snapshot_id = "a" * 32
    occupant = pub / "committed" / snapshot_id
    occupant.mkdir()
    occupant_ident = _ident_of(occupant)
    try:
        outcome = _publish([tmp_path / "src"], repo, pub, publication_root=root, snapshot_id=snapshot_id)
    finally:
        root.close()
    if isinstance(outcome, CompletePublicationV2):
        outcome.snapshot.close()
    return {
        "outcome": outcome,
        "occupant_replaced": _ident_of(occupant) != occupant_ident,
        "staging_left": _staging_entries(pub),
    }


def test_p5_rename_collision_never_replaces_the_committed_entry(tmp_path: Path) -> None:
    """C9: RENAME_NOREPLACE on an existing (even empty) committed/<id>."""
    facts = _p5_p6_discriminator(tmp_path)
    outcome = facts["outcome"]
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_RENAME_COLLISION_REASON_V2
    assert facts["occupant_replaced"] is False
    assert facts["staging_left"] == []


def test_mutation_p6_ordinary_rename_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: plain rename(2). An empty directory at the target is silently
    replaced -- killed by the occupant-identity discriminator."""

    def ordinary_rename(old_dir_fd, old_name, new_dir_fd, new_name):
        os.rename(old_name, new_name, src_dir_fd=old_dir_fd, dst_dir_fd=new_dir_fd)
        return 0

    monkeypatch.setattr(psv, "_renameat2_noreplace_v2", ordinary_rename)
    facts = _p5_p6_discriminator(tmp_path)
    assert facts["occupant_replaced"] is True


def _c12_sequence(tmp_path: Path, monkeypatch) -> list[str]:
    """Record every publication-side write/fchmod/fsync/close and the commit
    point, then check them against the §12 order. Returns the violations."""
    repo = _make_repo(tmp_path / "src" / "repo")
    pub = tmp_path / "pub"
    root = _publication_root(pub)
    real_rename = psv._renameat2_noreplace_v2
    try:
        with _audited(monkeypatch) as audit:
            def recording_rename(*args):
                audit.calls.append(_Call("RENAME", args, {}, None, None))
                return real_rename(*args)

            monkeypatch.setattr(psv, "_renameat2_noreplace_v2", recording_rename)
            outcome = _publish([tmp_path / "src"], repo, pub, publication_root=root)
    finally:
        root.close()
    snapshot = _complete(outcome)
    try:
        (committed,) = _committed_entries(pub)
        names = {_ident_of(committed): "."}
        for path in committed.rglob("*"):
            names[_ident_of(path)] = str(path.relative_to(committed))
        names[_ident_of(pub / "committed")] = "<committed>"
        names[_ident_of(pub / "staging")] = "<staging>"
        events = []
        for call in audit.calls:
            if call.name == "RENAME":
                events.append(("rename", None, None))
            elif call.name in ("write", "fchmod", "fsync", "close") and call.ident in names:
                extra = call.args[1] if call.name == "fchmod" else None
                events.append((call.name, names[call.ident], extra))
        dirs = {n for i, n in names.items() if n not in ("<committed>", "<staging>") and (committed / n).is_dir()}
    finally:
        snapshot.close()
    return _c12_violations(events, dirs)


def _c12_violations(events: list[tuple], dirs: set[str]) -> list[str]:
    violations = []

    def index(kind: str, target: str, extra=None, start: int = 0) -> int:
        for position in range(start, len(events)):
            event = events[position]
            if event[0] == kind and event[1] == target and (extra is None or event[2] == extra):
                return position
        return -1

    rename_at = index("rename", None)
    if rename_at < 0:
        return ["no commit point"]
    files = {e[1] for e in events if e[0] == "write"}
    for name in files:
        last_write = max(i for i, e in enumerate(events) if e[0] == "write" and e[1] == name)
        chmod_at, sync_at, close_at = index("fchmod", name, 0o444), index("fsync", name), index("close", name)
        if not (last_write < chmod_at < sync_at < close_at < rename_at):
            violations.append(f"file order {name}")
    for name in dirs - {"."}:
        chmod_at, sync_at = index("fchmod", name, 0o555), index("fsync", name)
        if not (0 <= chmod_at < sync_at < rename_at):
            violations.append(f"dir order {name}")
        for child in dirs - {"."}:
            if child.startswith(name + "/") and not index("fsync", child) < sync_at:
                violations.append(f"not bottom-up {child} before {name}")
        for member in files:
            if member.rsplit("/", 1)[0] == name and not index("close", member) < sync_at:
                violations.append(f"dir synced before file {member}")
    root_pre = [i for i, e in enumerate(events[:rename_at]) if e[:2] == ("fsync", ".")]
    if not root_pre:
        violations.append("stage root not fsynced before the commit point")
    chmod_root = index("fchmod", ".", 0o555, rename_at)
    sync_root = index("fsync", ".", None, max(chmod_root, rename_at))
    sync_committed = index("fsync", "<committed>", None, rename_at)
    sync_staging = index("fsync", "<staging>", None, rename_at)
    if not (rename_at < chmod_root < sync_root < sync_committed < sync_staging):
        violations.append("post-commit sequence")
    if index("fchmod", ".", 0o555) < rename_at:
        violations.append("root made 0555 before the commit point")
    return violations


def test_c12_publication_follows_the_frozen_durable_sequence(tmp_path: Path, monkeypatch) -> None:
    assert _c12_sequence(tmp_path, monkeypatch) == []


def test_mutation_c12_missing_stage_root_fsync_is_killed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(psv._StagingWriterV2, "fsync_stage_root", lambda self: None)
    assert "stage root not fsynced before the commit point" in _c12_sequence(tmp_path, monkeypatch)


def test_mutation_c12_fchmod_after_fsync_is_killed(tmp_path: Path, monkeypatch) -> None:
    def reordered_write_all(fd, data):
        psv.os.write(fd, data)
        psv.os.fsync(fd)

    monkeypatch.setattr(psv, "_write_all_v2", reordered_write_all)
    violations = _c12_sequence(tmp_path, monkeypatch)
    assert any(v.startswith("file order") for v in violations)


def test_mutation_c12_missing_source_parent_fsync_after_rename_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: after the cross-directory rename, sync the moved root and
    committed/ but NOT the source parent staging/ (the Codex 4124401154 shape)."""

    def post_commit_without_staging_sync(context):
        stage_fd = context.writer.stage_fd
        psv.os.fchmod(stage_fd, 0o555)
        psv.os.fsync(stage_fd)
        psv.os.fsync(context.committed_fd)
        info = psv.os.fstat(stage_fd)
        identity = psv._statx_identity_v2(stage_fd)
        binding = psv.PublishedSnapshotBindingV2(
            snapshot_id=context.snapshot_id, mount_id=identity.mount_id, st_dev=info.st_dev, st_ino=info.st_ino,
            st_uid=info.st_uid, st_gid=info.st_gid, committed_parent_identity=context.committed_identity,
        )
        snapshot = psv.PublishedSnapshotV2(
            _sentinel=psv._SNAPSHOT_SENTINEL_V2, fd=context.writer.take_stage_fd(), binding=binding, receipt=context.receipt
        )
        return CompletePublicationV2(snapshot=snapshot)

    monkeypatch.setattr(psv, "_post_commit_v2", post_commit_without_staging_sync)
    assert "post-commit sequence" in _c12_sequence(tmp_path, monkeypatch)


def test_mutation_p16_root_0555_before_rename_is_killed_as_non_root(tmp_path: Path, monkeypatch) -> None:
    """P16: moving a directory to another parent needs write permission on it.
    Mutant: finalize the root to 0555 before the commit point."""
    if os.geteuid() == 0:
        pytest.skip("gate_unavailable: nonroot_execution (root bypasses the directory write check)")
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    snapshot = _complete(_publish([tmp_path / "src"], repo, tmp_path / "pub"))
    snapshot.close()
    real_finalize = psv._StagingWriterV2.finalize_subdirectories

    def finalize_root_too(self):
        real_finalize(self)
        os.fchmod(self.stage_fd, 0o555)

    monkeypatch.setattr(psv._StagingWriterV2, "finalize_subdirectories", finalize_root_too)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub2")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_RENAME_FAILED_REASON_V2
    assert _committed_entries(tmp_path / "pub2") == [] and _staging_entries(tmp_path / "pub2") == []


def _unconfirmed_facts(tmp_path: Path, outcome) -> dict[str, object]:
    committed = _committed_entries(tmp_path / "pub")
    facts: dict[str, object] = {"type": type(outcome).__name__, "committed_entries": len(committed)}
    if isinstance(outcome, UnconfirmedPublicationV2):
        residual = outcome.residual
        assert isinstance(residual, CommittedSnapshotResidualV2) and not isinstance(residual, PublishedSnapshotV2)
        facts["reason"] = residual.reason_code
        facts["residual_points_at_committed"] = _ident(residual.fd) == _ident_of(committed[0])
        residual.close()
    return facts


def test_p8_fsync_failure_after_commit_is_unconfirmed(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    root = _publication_root(tmp_path / "pub")
    committed_ident = (root.committed_identity.st_dev, root.committed_identity.st_ino)

    def fail_committed_fsync(real, fd):
        if _ident(fd) == committed_ident:
            raise OSError(errno.EIO, "injected")
        return real(fd)

    with _audited(monkeypatch) as audit:
        audit.faults["fsync"] = fail_committed_fsync
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", publication_root=root)
    root.close()
    facts = _unconfirmed_facts(tmp_path, outcome)
    assert facts == {
        "type": "UnconfirmedPublicationV2",
        "committed_entries": 1,
        "reason": psv.PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2,
        "residual_points_at_committed": True,
    }


def test_p9_root_finalize_failure_after_commit_is_unconfirmed(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    committed_prefix = str(tmp_path / "pub" / "committed") + "/"

    def fail_post_commit_chmod(real, fd, mode):
        if (_where(fd) or "").startswith(committed_prefix):
            raise OSError(errno.EIO, "injected")
        return real(fd, mode)

    with _audited(monkeypatch) as audit:
        audit.faults["fchmod"] = fail_post_commit_chmod
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    facts = _unconfirmed_facts(tmp_path, outcome)
    assert facts["type"] == "UnconfirmedPublicationV2"
    assert facts["reason"] == psv.PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2
    assert facts["residual_points_at_committed"] is True


def test_p10_sigkill_before_the_commit_point_leaves_only_staging_garbage(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    pub = tmp_path / "pub"
    script = f"""
import os, signal
import app.agent_review.physical_snapshot_v2 as psv
from app.agent_review.trusted_object_authority_v2 import AuthorizedGitStorageSetV2
def die(*args):
    os.kill(os.getpid(), signal.SIGKILL)
psv._renameat2_noreplace_v2 = die
os.makedirs({str(pub / 'staging')!r}); os.makedirs({str(pub / 'committed')!r})
fd = os.open({str(pub)!r}, os.O_RDONLY | os.O_DIRECTORY)
root = psv.SnapshotPublicationRootV2.from_directory_fd(fd)
budget = psv.PhysicalWorkBudgetV2(*([10**6] * 6), 8, 10**9, 10**6)
psv.publish_physical_snapshot_v2(
    source_authority=AuthorizedGitStorageSetV2.from_roots([{str(tmp_path / 'src')!r}]),
    source_locator=psv.SourceRepositoryLocatorV2.absolute({str(repo)!r}),
    object_format=psv.DeclaredGitObjectFormatV2.SHA1, physical_budget=budget, publication_root=root)
"""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-c", script], cwd=repo_root, env={**os.environ, "PYTHONPATH": str(repo_root)})
    assert result.returncode == -signal.SIGKILL
    assert _committed_entries(pub) == []
    assert len(_staging_entries(pub)) == 1  # StagingGarbage != CommittedSnapshot; GC is U3


def test_p11_noreplace_unavailable_fails_closed(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    real_syscall = psv._syscall_v2

    def no_renameat2(name, *args):
        if name == "renameat2":
            raise psv._SyscallUnavailableV2(name)
        return real_syscall(name, *args)

    monkeypatch.setattr(psv, "_syscall_v2", no_renameat2)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2
    assert _committed_entries(tmp_path / "pub") == [] and _staging_entries(tmp_path / "pub") == []
    monkeypatch.setattr(psv, "_syscall_v2", real_syscall)
    monkeypatch.setattr(psv, "_renameat2_noreplace_v2", lambda *args: errno.EINVAL)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub2")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2
    assert _staging_entries(tmp_path / "pub2") == []


def _inject_identities(monkeypatch, *, staging_mount: int, committed_mount: int, same_object: bool = False) -> None:
    """statx is called for root, staging, committed, in that order. Rewrite the
    mount ids (and, for P17, make committed the very object staging is)."""
    real = psv._statx_identity_v2
    seen: list[KernelObjectIdentityV2] = []

    def fake(fd):
        identity = real(fd)
        if len(seen) == 1:
            identity = KernelObjectIdentityV2(staging_mount, identity.st_dev, identity.st_ino)
        elif len(seen) == 2:
            identity = seen[1] if same_object else KernelObjectIdentityV2(committed_mount, identity.st_dev, identity.st_ino)
        seen.append(identity)
        return identity

    monkeypatch.setattr(psv, "_statx_identity_v2", fake)


def _w_construction(tmp_path: Path) -> str:
    base = tmp_path / "pub"
    (base / "staging").mkdir(parents=True)
    (base / "committed").mkdir()
    fd = os.open(str(base), os.O_RDONLY | os.O_DIRECTORY)
    try:
        SnapshotPublicationRootV2.from_directory_fd(fd).close()
        return "constructed"
    except PhysicalSnapshotErrorV2 as exc:
        return exc.reason_code
    finally:
        os.close(fd)


def test_p12_p19_same_st_dev_different_mount_is_refused(tmp_path: Path, monkeypatch) -> None:
    """P19 (via statx injection: a bind mount needs privileges the test does
    not have): same st_dev, different mount ids -> cross-mount refusal at W."""
    _inject_identities(monkeypatch, staging_mount=11, committed_mount=12)
    assert _w_construction(tmp_path) == psv.PHYSICAL_SNAPSHOT_PUBLICATION_CROSS_MOUNT_REASON_V2


def test_mutation_p19_st_dev_only_domain_check_is_killed(tmp_path: Path, monkeypatch) -> None:
    _inject_identities(monkeypatch, staging_mount=11, committed_mount=12)
    monkeypatch.setattr(psv, "_same_rename_domain_v2", lambda a, b: a.st_dev == b.st_dev)
    assert _w_construction(tmp_path) == "constructed"


def test_p17_publication_namespace_alias_is_refused(tmp_path: Path, monkeypatch) -> None:
    """P17 (reachable only by injection in the declared domain)."""
    _inject_identities(monkeypatch, staging_mount=11, committed_mount=11, same_object=True)
    assert _w_construction(tmp_path) == psv.PHYSICAL_SNAPSHOT_PUBLICATION_NAMESPACES_NOT_DISTINCT_REASON_V2


def test_mutation_p17_missing_distinctness_check_is_killed(tmp_path: Path, monkeypatch) -> None:
    _inject_identities(monkeypatch, staging_mount=11, committed_mount=11, same_object=True)
    monkeypatch.setattr(psv, "_distinct_namespaces_v2", lambda a, b: True)
    assert _w_construction(tmp_path) == "constructed"


def test_w_mount_identity_unavailable_fails_closed(tmp_path: Path, monkeypatch) -> None:
    def unavailable(fd):
        raise psv._MountIdentityUnavailableV2()

    monkeypatch.setattr(psv, "_statx_identity_v2", unavailable)
    assert _w_construction(tmp_path) == psv.PHYSICAL_SNAPSHOT_PUBLICATION_MOUNT_IDENTITY_UNAVAILABLE_REASON_V2


def test_p13_snapshot_id_collision_in_staging_leaves_the_occupant(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    pub = tmp_path / "pub"
    root = _publication_root(pub)
    (pub / "staging" / ("b" * 32)).mkdir()
    try:
        outcome = _publish([tmp_path / "src"], repo, pub, publication_root=root, snapshot_id="b" * 32)
    finally:
        root.close()
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_SNAPSHOT_ID_COLLISION_REASON_V2
    assert (pub / "staging" / ("b" * 32)).is_dir()


def test_p14_l9_interruption_at_the_commit_point_is_truthful_and_owned(tmp_path: Path, monkeypatch) -> None:
    """A BaseException right after the real rename: re-raised with the OBSERVED
    variant attached, whose descriptor is still owned and valid."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    real_rename = psv._renameat2_noreplace_v2

    def rename_then_interrupt(*args):
        assert real_rename(*args) == 0
        raise KeyboardInterrupt

    monkeypatch.setattr(psv, "_renameat2_noreplace_v2", rename_then_interrupt)
    with pytest.raises(KeyboardInterrupt) as excinfo:
        _publish([tmp_path / "src"], repo, tmp_path / "pub")
    outcome = excinfo.value.physical_snapshot_outcome
    facts = _unconfirmed_facts(tmp_path, outcome)
    assert facts["type"] == "UnconfirmedPublicationV2"
    assert facts["reason"] == psv.PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2
    assert facts["residual_points_at_committed"] is True


def _p18_discriminator(tmp_path: Path, monkeypatch) -> dict[str, object]:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    real_rename = psv._renameat2_noreplace_v2

    def rename_then_report_error(*args):
        assert real_rename(*args) == 0
        return errno.EIO

    monkeypatch.setattr(psv, "_renameat2_noreplace_v2", rename_then_report_error)
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    return _unconfirmed_facts(tmp_path, outcome)


def test_p18_rename_error_is_observed_not_inferred(tmp_path: Path, monkeypatch) -> None:
    """C10: renameat2 reports an error although the rename happened (NFS-like)."""
    facts = _p18_discriminator(tmp_path, monkeypatch)
    assert facts == {
        "type": "UnconfirmedPublicationV2",
        "committed_entries": 1,
        "reason": psv.PHYSICAL_SNAPSHOT_RENAME_ERROR_BUT_COMMITTED_REASON_V2,
        "residual_points_at_committed": True,
    }


def test_mutation_p18_errno_implies_not_published_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: `rename returned error -> therefore nothing happened`."""
    monkeypatch.setattr(
        psv,
        "_on_rename_failure_v2",
        lambda context, err: NotPublishedV2(reason_code=psv._rename_reason_v2(err), exceeded_axis=None, staging_residue=False),
    )
    facts = _p18_discriminator(tmp_path, monkeypatch)
    assert facts["type"] == "NotPublishedV2" and facts["committed_entries"] == 1  # a published lie


def test_indeterminate_commit_state_is_never_reported_as_not_published(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    monkeypatch.setattr(psv, "_renameat2_noreplace_v2", lambda *args: errno.EIO)
    monkeypatch.setattr(psv, "_observe_commit_v2", lambda context: "indeterminate")
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, IndeterminatePublicationV2)
    assert outcome.residual.reason_code == psv.PHYSICAL_SNAPSHOT_COMMIT_STATE_UNOBSERVABLE_REASON_V2


def test_p15_publication_authority_cannot_come_from_a_path(tmp_path: Path) -> None:
    assert not hasattr(SnapshotPublicationRootV2, "from_path")
    with pytest.raises(PhysicalSnapshotErrorV2):
        SnapshotPublicationRootV2(
            _sentinel=object(), root_fd=0, staging_fd=0, committed_fd=0,
            root_identity=None, staging_identity=None, committed_identity=None,  # type: ignore[arg-type]
        )
    with pytest.raises(PhysicalSnapshotErrorV2):
        SnapshotPublicationRootV2.from_directory_fd(str(tmp_path))  # type: ignore[arg-type]


_C2_SNAPSHOT_ID = "d" * 32


def _c2_discriminator(tmp_path: Path, monkeypatch) -> list[str]:
    """C2: every publication-side mutation lands under `staging/` (creation,
    write, mode change, removal); the only thing ever touched in committed/ is
    the moved snapshot root itself (its post-commit fchmod). Nothing is
    created or removed in the source, or directly in committed/."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    pub = tmp_path / "pub"
    with _audited(monkeypatch) as audit:
        outcome = _publish([tmp_path / "src"], repo, pub, snapshot_id=_C2_SNAPSHOT_ID)
    if isinstance(outcome, CompletePublicationV2):
        outcome.snapshot.close()
    staging = str(pub / "staging")
    moved_root = str(pub / "committed" / _C2_SNAPSHOT_ID)
    violations = []
    for call in audit.calls:
        creates = call.name == "mkdir" or (
            call.name == "open" and len(call.args) > 1 and call.args[1] & (os.O_CREAT | os.O_WRONLY | os.O_RDWR)
        )
        if creates or call.name in ("unlink", "rmdir", "write"):
            anchored = call.kwargs.get("dir_fd") is not None or call.name == "write"
            if not anchored or not str(call.where or "").startswith(staging) or str(call.args[0]).startswith("/"):
                violations.append(f"{call.name}:{call.where}:{call.args[0]}")
        elif call.name == "fchmod" and not (str(call.where or "").startswith(staging) or call.where == moved_root):
            violations.append(f"fchmod:{call.where}")
    return violations


def test_c2_every_publication_write_is_descriptor_relative(tmp_path: Path, monkeypatch) -> None:
    assert _c2_discriminator(tmp_path, monkeypatch) == []


def test_mutation_c2_path_based_receipt_write_is_killed(tmp_path: Path, monkeypatch) -> None:
    real_write_file = psv._StagingWriterV2.write_file

    def path_based(self, dir_rel, name, data, *, copied):
        if name == psv.PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2:
            target = os.readlink(f"/proc/self/fd/{self.stage_fd}") + "/" + name
            fd = psv.os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            psv.os.write(fd, data)
            psv.os.fchmod(fd, 0o444)
            psv.os.fsync(fd)
            psv.os.close(fd)
            return
        return real_write_file(self, dir_rel, name, data, copied=copied)

    monkeypatch.setattr(psv._StagingWriterV2, "write_file", path_based)
    assert _c2_discriminator(tmp_path, monkeypatch), "intended discriminator (unanchored create) did not fire"


def test_mutation_c2_write_into_the_source_tree_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Review F2(a): an anchored create through an ADMITTED SOURCE descriptor."""
    original = psv._PhysicalCopierV2.copy_store

    def planting_copy_store(self, store, hop):
        if store.fd is not None:
            fd = psv.os.open("planted", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=store.fd)
            psv.os.close(fd)
        return original(self, store, hop)

    monkeypatch.setattr(psv._PhysicalCopierV2, "copy_store", planting_copy_store)
    violations = _c2_discriminator(tmp_path, monkeypatch)
    assert any("/src/" in violation for violation in violations)


def test_mutation_c2_direct_creation_in_committed_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Review F2(b): a create directly in committed/ through committed_fd (§6 forbids it)."""
    real_commit = psv._commit_v2

    def planting_commit(context):
        fd = psv.os.open("planted", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=context.committed_fd)
        psv.os.close(fd)
        return real_commit(context)

    monkeypatch.setattr(psv, "_commit_v2", planting_commit)
    violations = _c2_discriminator(tmp_path, monkeypatch)
    assert any("/committed" in violation for violation in violations)


# ================================================================================
# Type-state and descriptor lifecycle (C10, C11, L1-L10)
# ================================================================================


def test_l7_published_snapshot_exists_only_inside_complete(tmp_path: Path) -> None:
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(PhysicalSnapshotErrorV2):
            PublishedSnapshotV2(_sentinel=object(), fd=fd, binding=None, receipt=None)  # type: ignore[arg-type]
    finally:
        os.close(fd)
    assert _unconfirmed_accepts_published_snapshot(tmp_path) is False


def _unconfirmed_accepts_published_snapshot(tmp_path: Path) -> bool:
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    snapshot = PublishedSnapshotV2(_sentinel=psv._SNAPSHOT_SENTINEL_V2, fd=fd, binding=None, receipt=None)  # type: ignore[arg-type]
    try:
        UnconfirmedPublicationV2(residual=snapshot)  # type: ignore[arg-type]
        return True
    except TypeError:
        return False
    finally:
        snapshot.close()


def test_mutation_c10_published_snapshot_inside_unconfirmed_is_killed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(UnconfirmedPublicationV2, "__post_init__", lambda self: None)
    assert _unconfirmed_accepts_published_snapshot(tmp_path) is True


def test_l7_snapshot_close_is_idempotent(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    before = _fd_census()
    snapshot = _complete(_publish([tmp_path / "src"], repo, tmp_path / "pub"))
    snapshot.close()
    snapshot.close()
    assert snapshot.closed
    with pytest.raises(PhysicalSnapshotErrorV2):
        _ = snapshot.committed_dir_fd
    assert _fd_census() == before


def _l1_discriminator(tmp_path: Path, monkeypatch) -> dict[str, object]:
    """L1/L2/L3: the FIRST close inside a multi-component descent releases the
    fd and then reports failure; a canary grabs the freed number at once."""
    src = tmp_path / "src"
    repo = _make_repo(src / "a" / "b" / "c" / "repo", files=1)
    canary: dict[str, int] = {}
    state = {"armed": True}

    def close_then_fail(real, fd):
        real(fd)
        if state["armed"]:
            state["armed"] = False
            canary["fd"] = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
            raise OSError(errno.EIO, "injected after release")
        return None

    before = _fd_census()
    capability = AuthorizedGitStorageSetV2.from_roots([str(src)])
    root = _publication_root(tmp_path / "pub")
    try:
        with _audited(monkeypatch) as audit:
            audit.faults["close"] = close_then_fail
            outcome = _publish([], repo, tmp_path / "pub", authority=capability, publication_root=root)
    finally:
        capability.close()
        root.close()
    canary_alive = _ident(canary["fd"]) is not None
    os.close(canary["fd"])
    return {
        "outcome": outcome,
        "canary_alive": canary_alive,
        "leaked": sorted(_fd_census() - before),
    }


def test_l1_l2_l3_close_failure_during_descent_is_linear(tmp_path: Path, monkeypatch) -> None:
    facts = _l1_discriminator(tmp_path, monkeypatch)
    outcome = facts["outcome"]
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.reason_code == psv.PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2
    assert facts["canary_alive"] is True  # the stale number was never re-closed
    assert facts["leaked"] == []  # the successor had an owner when the predecessor failed


def test_mutation_l1_close_before_owner_transfer_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: release the predecessor BEFORE the successor has an owner."""

    def unlinear_admit(self, position, missing_reason):
        root = self._roots[position.root_index]
        if not position.components:
            self._tracker.charge(descriptor_opens=1)
            return self._register(position, psv.fcntl.fcntl(root.fd, psv.fcntl.F_DUPFD_CLOEXEC, 0))
        current, owned = root.fd, None
        for component in position.components:
            self._tracker.charge(descriptor_opens=1, path_components=1)
            successor = psv._try_open_dir_no_follow_v2(current, component)
            if successor is None:
                raise psv._RefusalV2(missing_reason)
            if owned is not None:
                psv._close_once_v2(owned)  # may raise while `successor` has no owner
            owned = current = successor
        return self._register(position, owned)

    monkeypatch.setattr(psv._SourceSessionV2, "admit", unlinear_admit)
    facts = _l1_discriminator(tmp_path, monkeypatch)
    assert facts["leaked"], "intended discriminator (fd census) did not observe the orphaned successor"


def test_l4_capability_closed_during_acquisition_does_not_revoke_the_duplicates(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    capability = AuthorizedGitStorageSetV2.from_roots([str(tmp_path / "src")])
    real_scan = psv._scan_directory_v2

    def close_capability_then_scan(tracker, dir_fd, classify):
        capability.close()
        return real_scan(tracker, dir_fd, classify)

    monkeypatch.setattr(psv, "_scan_directory_v2", close_capability_then_scan)
    snapshot = _complete(_publish([], repo, tmp_path / "pub", authority=capability))
    snapshot.close()


def test_l5_cleanup_failure_is_reported_as_residue(tmp_path: Path, monkeypatch) -> None:
    repo = _make_repo(tmp_path / "src" / "repo", files=1)

    def failing_root_fsync(self):
        raise psv._RefusalV2(psv.PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)

    monkeypatch.setattr(psv._StagingWriterV2, "fsync_stage_root", failing_root_fsync)
    with _audited(monkeypatch) as audit:
        audit.faults["rmdir"] = _fail_on("rmdir")[1]
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    assert isinstance(outcome, NotPublishedV2)
    assert outcome.staging_residue is True
    assert _committed_entries(tmp_path / "pub") == []


def test_l6_l10_fd_census_is_clean_on_budget_refusal_mid_listing(tmp_path: Path) -> None:
    repo = _r1_fixture(tmp_path)
    before = _fd_census()
    outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub", budget=_budget(max_entries_scanned=1000))
    assert isinstance(outcome, NotPublishedV2)
    assert _fd_census() == before


def test_l8_no_descriptor_is_shared_between_admitted_directories(tmp_path: Path, monkeypatch) -> None:
    main = _make_repo(tmp_path / "src" / "main")
    worktree = tmp_path / "src" / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    shared: list[str] = []
    real_register = psv._SourceSessionV2._register

    def checking_register(self, position, fd):
        live = {admitted.fd for admitted in self._live} | {root.fd for root in self._roots}
        if fd in live:
            shared.append(f"{fd}")
        return real_register(self, position, fd)

    monkeypatch.setattr(psv._SourceSessionV2, "_register", checking_register)
    snapshot = _complete(_publish([tmp_path / "src"], worktree, tmp_path / "pub"))
    snapshot.close()
    assert shared == []


def test_budget_tracker_refuses_whole_events_and_never_returns_budget() -> None:
    tracker = PhysicalWorkTrackerV2(_budget(max_descriptor_opens=2, max_path_components=1))
    tracker.charge(descriptor_opens=1, path_components=1)
    with pytest.raises(psv._RefusalV2) as excinfo:
        tracker.charge(descriptor_opens=1, path_components=1)
    assert excinfo.value.exceeded_axis == "path_components"
    assert tracker.consumed()["descriptor_opens"] == 1  # the refused event was not applied
    with pytest.raises(ValueError):
        tracker.charge(descriptor_opens=-1)
    with pytest.raises(ValueError):
        tracker.charge(alternate_depth=1)


def _l8_shared_descriptors(tmp_path: Path, monkeypatch) -> list[str]:
    """Registrations whose fd is already owned by a live admitted dir or a root dup."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    shared: list[str] = []
    real_register = psv._SourceSessionV2._register

    def checking_register(self, position, fd):
        live = {admitted.fd for admitted in self._live} | {root.fd for root in self._roots}
        if fd in live:
            shared.append(f"{fd}")
        return real_register(self, position, fd)

    monkeypatch.setattr(psv._SourceSessionV2, "_register", checking_register)
    capability = AuthorizedGitStorageSetV2.from_roots([str(repo)])
    try:
        outcome = _publish([], 0, tmp_path / "pub", authority=capability)
    finally:
        capability.close()
    if isinstance(outcome, CompletePublicationV2):
        outcome.snapshot.close()
    return shared


def test_l8_root_locator_admits_a_charged_duplicate_not_the_root_descriptor(tmp_path: Path, monkeypatch) -> None:
    assert _l8_shared_descriptors(tmp_path, monkeypatch) == []


def test_mutation_l8_admitted_dir_sharing_the_root_descriptor_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant: `admit(root, ())` hands out the session's own root fd (a shared
    owner) instead of a charged duplicate. Killed by the single-owner check."""
    real_admit = psv._SourceSessionV2.admit

    def sharing_admit(self, position, missing_reason):
        if not position.components:
            return self._register(position, self._roots[position.root_index].fd)
        return real_admit(self, position, missing_reason)

    def never_close_twice(self):
        live, self._live = self._live, []
        roots = {root.fd for root in self._roots}
        for admitted in live:
            if admitted.fd is not None and admitted.fd not in roots:
                os.close(admitted.fd)
            admitted.fd = None
        for root in self._roots:
            os.close(root.fd)
        self._roots = ()
        self._closed = True

    def release_without_closing_roots(self, admitted):
        if admitted.fd is not None and admitted.fd in {root.fd for root in self._roots}:
            admitted.fd = None
            self._live.remove(admitted)
            return
        return real_release(self, admitted)

    real_release = psv._SourceSessionV2.release
    monkeypatch.setattr(psv._SourceSessionV2, "admit", sharing_admit)
    monkeypatch.setattr(psv._SourceSessionV2, "release", release_without_closing_roots)
    monkeypatch.setattr(psv._SourceSessionV2, "close", never_close_twice)
    assert _l8_shared_descriptors(tmp_path, monkeypatch), "intended discriminator (single owner) did not fire"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "regular"])
def test_a5_a6_incomplete_pack_pair_candidates_are_classified_not_skipped(tmp_path: Path, kind: str) -> None:
    """Codex 4133784323 / §11b: a lone `pack-<hex>.pack` is not copied, but an
    object-looking name is still opened no-follow (charged): a symlink or a
    special file is refused, a regular lone file is ignored."""
    repo = _make_repo(tmp_path / "src" / "repo")
    lone = repo / ".git" / "objects" / "pack" / ("pack-" + "e" * 40 + ".pack")
    if kind == "symlink":
        (tmp_path / "src" / "elsewhere.pack").write_bytes(b"x")
        lone.symlink_to(tmp_path / "src" / "elsewhere.pack")
    elif kind == "fifo":
        os.mkfifo(lone)
    else:
        lone.write_bytes(b"x")
    with _deadline():
        outcome = _publish([tmp_path / "src"], repo, tmp_path / "pub")
    if kind == "regular":
        snapshot = _complete(outcome)
        try:
            (committed,) = _committed_entries(tmp_path / "pub")
            assert not (committed / "objects" / "pack" / lone.name).exists()
        finally:
            snapshot.close()
    else:
        assert isinstance(outcome, NotPublishedV2)
        expected = (
            psv.PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2
            if kind == "symlink"
            else psv.PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2
        )
        assert outcome.reason_code == expected
