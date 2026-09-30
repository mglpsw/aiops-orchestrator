"""`#301-S1-A` -- physical Git snapshot: countermodels, positive controls,
Git parity and mutation discrimination.

Contract: `docs/engineering/agent-review-v2-301-s1a/ARCHITECTURE_FREEZE.md`,
refined append-only by `IMPLEMENTATION_ADJUDICATION.md` in the same folder
(C11 structural redesign and its fault-injection census, capability sealing,
duplicate-occurrence semantics). Countermodel ids (A1-A13, R1-R14, P1-P19, L1-L10) and claim ids (C1-C12)
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
from dataclasses import dataclass
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
        session = psv._SourceSessionV2(PhysicalWorkTrackerV2(_budget()))
        session.acquire_roots(capability)
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
    real_open_dir = psv._open_source_dir_into_v2

    def swapping_open(slot, dir_fd, name):
        if name == "repo" and not swapped["done"]:
            swapped["done"] = True
            repo.rename(src / "orig-repo")
            repo.symlink_to(outside)
        return real_open_dir(slot, dir_fd, name)

    monkeypatch.setattr(psv, "_open_source_dir_into_v2", swapping_open)
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

    def post_commit_without_staging_sync(run):
        stage_fd = run.writer.stage.fd
        psv.os.fchmod(stage_fd, 0o555)
        psv.os.fsync(stage_fd)
        psv.os.fsync(run.committed.fd)
        info = psv.os.fstat(stage_fd)
        identity = psv._statx_identity_v2(stage_fd)
        binding = psv.PublishedSnapshotBindingV2(
            snapshot_id=run.snapshot_id, mount_id=identity.mount_id, st_dev=info.st_dev, st_ino=info.st_ino,
            st_uid=info.st_uid, st_gid=info.st_gid, committed_parent_identity=run.committed_identity,
        )
        snapshot = psv.PublishedSnapshotV2(_sentinel=psv._SNAPSHOT_SENTINEL_V2, binding=binding, receipt=run.receipt)
        outcome = CompletePublicationV2(snapshot=snapshot)
        run.writer.stage.move_to(snapshot._descriptor)
        return outcome

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
        SnapshotPublicationRootV2(_sentinel=object())
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

    def planting_commit(run):
        fd = psv.os.open("planted", os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=run.committed.fd)
        psv.os.close(fd)
        return real_commit(run)

    monkeypatch.setattr(psv, "_commit_v2", planting_commit)
    violations = _c2_discriminator(tmp_path, monkeypatch)
    assert any("/committed" in violation for violation in violations)


# ================================================================================
# Type-state and descriptor lifecycle (C10, C11, L1-L10)
# ================================================================================


def _test_receipt_and_binding() -> tuple[psv.PublishedSnapshotReceiptV2, psv.PublishedSnapshotBindingV2]:
    """Genuinely-typed receipt and binding (values irrelevant) for type-state tests."""
    receipt = psv.PublishedSnapshotReceiptV2(
        schema_version=psv.PHYSICAL_SNAPSHOT_RECEIPT_SCHEMA_V2, snapshot_id="e" * 32, declared_object_format="sha1",
        physical_work=(), alternate_sources=0, max_alternate_depth_seen=0, files_copied=0, copied_bytes=0,
        source_root_identities=(), snapshot_fileset_digest="0" * 64,
    )
    binding = psv.PublishedSnapshotBindingV2(
        snapshot_id="e" * 32, mount_id=0, st_dev=0, st_ino=0, st_uid=0, st_gid=0,
        committed_parent_identity=psv.KernelObjectIdentityV2(mount_id=0, st_dev=0, st_ino=0),
    )
    return receipt, binding


def test_l7_published_snapshot_exists_only_inside_complete(tmp_path: Path) -> None:
    receipt, binding = _test_receipt_and_binding()
    with pytest.raises(PhysicalSnapshotErrorV2):
        PublishedSnapshotV2(_sentinel=object(), binding=binding, receipt=receipt)
    assert _unconfirmed_accepts_published_snapshot(tmp_path) is False


def _unconfirmed_accepts_published_snapshot(tmp_path: Path) -> bool:
    receipt, binding = _test_receipt_and_binding()
    snapshot = PublishedSnapshotV2(_sentinel=psv._SNAPSHOT_SENTINEL_V2, binding=binding, receipt=receipt)
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
        admitted = self._new_admitted(position)
        if not position.components:
            self._tracker.charge(descriptor_opens=1)
            admitted.fd = psv.fcntl.fcntl(root.fd, psv.fcntl.F_DUPFD_CLOEXEC, 0)
            return admitted
        current, owned = root.fd, None
        for component in position.components:
            self._tracker.charge(descriptor_opens=1, path_components=1)
            carrier = psv._FdSlotV2()
            if not psv._open_source_dir_into_v2(carrier, current, component):
                raise psv._RefusalV2(missing_reason)
            successor, carrier.fd = carrier.fd, None  # the successor now has no owner
            if owned is not None:
                try:
                    psv.os.close(owned)  # may raise while `successor` has no owner
                except OSError as exc:
                    raise psv._RefusalV2(psv.PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2) from exc
            owned = current = successor
        admitted.fd = owned
        return admitted

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


def _check_single_owner(monkeypatch) -> list[str]:
    """Every admitted directory handed out by `admit`/`child` holds a
    descriptor no other live owner (admitted dir or root duplicate) holds."""
    shared: list[str] = []

    def checked(real):
        def wrapper(self, *args, **kwargs):
            admitted = real(self, *args, **kwargs)
            others = {other.fd for other in self._live if other is not admitted and other.fd is not None}
            others |= {root.fd for root in self._roots}
            if admitted.fd in others:
                shared.append(f"{admitted.fd}")
            return admitted

        return wrapper

    monkeypatch.setattr(psv._SourceSessionV2, "admit", checked(psv._SourceSessionV2.admit))
    monkeypatch.setattr(psv._SourceSessionV2, "child", checked(psv._SourceSessionV2.child))
    return shared


def test_l8_no_descriptor_is_shared_between_admitted_directories(tmp_path: Path, monkeypatch) -> None:
    main = _make_repo(tmp_path / "src" / "main")
    worktree = tmp_path / "src" / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    shared = _check_single_owner(monkeypatch)
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
    shared = _check_single_owner(monkeypatch)
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
            admitted = self._new_admitted(position)
            admitted.fd = self._roots[position.root_index].fd
            return admitted
        return real_admit(self, position, missing_reason)

    def never_close_twice(self):
        roots = {root.fd for root in self._roots}
        while self._live:
            admitted = self._live.pop()
            if admitted.fd is not None and admitted.fd not in roots:
                os.close(admitted.fd)
            admitted.fd = None
        self._release_roots()

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


# ================================================================================
# C11 recurrence-family census (IMPLEMENTATION_ADJUDICATION: structural redesign)
#
# The ba3800e STOP showed that fixing known sites one by one does not close the
# class "a descriptor exists before an owner able to survive every failure".
# So the class is checked MECHANICALLY over every site, two ways:
#   static  -- every acquisition in the S1-A write-set installs into a
#              pre-existing slot by the statement that makes the call (AST);
#   dynamic -- a single synchronous fault is injected before EACH executed
#              S1-A opcode that can fail (one run per site, first and last
#              occurrence), and every run must end with no descriptor left
#              unreleased, none closed twice, and a truthful outcome.
# The only tolerated exceptions are the marked statements
# (`# fd-install` / `# fd-release`): the declared
# PYTHON_FD_OWNERSHIP_INSTALLATION_WINDOW, which holds no operation that can
# fail synchronously in CPython 3.11.
# ================================================================================

import ast  # noqa: E402
import dis  # noqa: E402
import gc  # noqa: E402
import inspect  # noqa: E402
import textwrap  # noqa: E402

_PSV_FILE = psv.__file__
_TOA_FILE = toa.__file__
_MUTANT_FILE = "<s1a-mutant>"
_WINDOW_MARKERS = ("# fd-install", "# fd-release")

#: Opcodes that cannot raise synchronously in CPython 3.11 on the values S1-A
#: gives them (no allocation, no user code). Every OTHER opcode is a fault site.
_NONFAILING_OPCODES_V311 = frozenset(
    {
        "CACHE", "COPY", "COPY_FREE_VARS", "EXTENDED_ARG", "IS_OP", "JUMP_BACKWARD",
        "JUMP_BACKWARD_NO_INTERRUPT", "JUMP_FORWARD", "KW_NAMES", "LOAD_CLOSURE", "LOAD_CONST",
        "LOAD_DEREF", "LOAD_FAST", "MAKE_CELL", "NOP", "POP_EXCEPT", "POP_JUMP_BACKWARD_IF_FALSE",
        "POP_JUMP_BACKWARD_IF_NONE", "POP_JUMP_BACKWARD_IF_NOT_NONE", "POP_JUMP_BACKWARD_IF_TRUE",
        "POP_JUMP_FORWARD_IF_FALSE", "POP_JUMP_FORWARD_IF_NONE", "POP_JUMP_FORWARD_IF_NOT_NONE",
        "POP_JUMP_FORWARD_IF_TRUE", "POP_TOP", "PRECALL", "PUSH_EXC_INFO", "PUSH_NULL", "RERAISE",
        "RESUME", "RETURN_VALUE", "STORE_DEREF", "STORE_FAST", "SWAP",
    }
)


def _name_chain(node) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name)


def _is_acquiring_call(node) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    func = node.func
    owner = func.value.id if isinstance(func.value, ast.Name) else None
    if owner == "os" and func.attr == "open":
        return True
    if owner == "fcntl" and func.attr == "fcntl" and len(node.args) > 1:
        command = node.args[1]
        return isinstance(command, ast.Attribute) and command.attr == "F_DUPFD_CLOEXEC"
    return func.attr == "duplicate_authorized_roots" and not node.args and not node.keywords


# -- causal ownership windows (maintainer adjudication of 3ff700a: N1 closure) --------------
#
# `SourceLineIdentity != OwnershipWindowIdentity`. The declared
# PYTHON_FD_OWNERSHIP_INSTALLATION_WINDOW is no longer "a marked line"; it is
# a TRANSITION recognised on the AST and anchored on bytecode offsets:
#
#   acquire   <pre-evaluated owner> = <acquiring call>     CALL  -> STORE into that owner
#   created   os.mkdir(...) ; <name>.created = True         CALL  -> STORE created
#   release   <chain>.fd = None ; os.close(<name>)          STORE -> close CALL
#   roots     self._roots_released += 1 ; os.close(<name>)  STORE -> close CALL,
#             ONLY in `_SourceSessionV2._release_roots`
#   transfer  <chain>.fd = None ; <chain>.fd = <name>       STORE -> STORE
#
# The two-statement forms must be CONSECUTIVE statements of one block, each the
# only statement starting on its line. Tolerance goes to the offsets strictly
# after the start instruction up to and including the end instruction, and only
# when every instruction in between is the transition's own continuation (its
# target, or its second statement). Nothing else inherits it -- not another
# statement on the same line, not a one-line `if`, not a same-spelling counter
# in another context.

_ROOTS_RELEASE_QUALNAME = "_SourceSessionV2._release_roots"
_MUTANT_SOURCES: dict[str, str] = {}


def _span(node) -> tuple[int, int, int, int]:
    return (node.lineno, node.end_lineno, node.col_offset, node.end_col_offset)


def _within(positions, span) -> bool:
    if positions is None or positions.lineno is None:
        return False
    return (positions.lineno, positions.col_offset) >= (span[0], span[2]) and (
        positions.end_lineno, positions.end_col_offset
    ) <= (span[1], span[3])


@dataclass(frozen=True)
class _Transition:
    kind: str
    start_ops: frozenset
    start_span: tuple
    end_ops: frozenset
    end_span: tuple
    continuation: tuple
    lines: tuple
    marker: str
    qualname: str | None = None


def _is_detach(stmt) -> bool:
    return (
        isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute)
        and stmt.targets[0].attr == "fd" and _name_chain(stmt.targets[0].value)
        and isinstance(stmt.value, ast.Constant) and stmt.value.value is None
    )


def _is_close(stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and not stmt.value.keywords
        and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "close"
        and isinstance(stmt.value.func.value, ast.Name) and stmt.value.func.value.id == "os"
        and len(stmt.value.args) == 1 and isinstance(stmt.value.args[0], ast.Name)
    )


def _ownership_transitions(text: str) -> list[_Transition]:
    """Every admitted ownership transition in `text` (see the table above)."""
    tree = ast.parse(text)
    starts: dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            starts[node.lineno] = starts.get(node.lineno, 0) + 1

    def alone(stmt) -> bool:
        return stmt.lineno == stmt.end_lineno and starts.get(stmt.lineno) == 1

    found: list[_Transition] = []
    store = frozenset({"STORE_ATTR", "STORE_SUBSCR"})
    call = frozenset({"CALL"})

    def visit(body, qualname: str) -> None:
        for index, stmt in enumerate(body):
            follower = body[index + 1] if index + 1 < len(body) else None
            if (
                isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and _is_acquiring_call(stmt.value)
                and _pre_evaluated_owner(stmt.targets[0]) and alone(stmt)
            ):
                target = stmt.targets[0]
                found.append(_Transition(
                    "acquire", call, _span(stmt.value), store, _span(target), _span(target), (stmt.lineno,), "# fd-install",
                ))
            if follower is not None and alone(follower) and alone(stmt):
                if (
                    isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr == "mkdir"
                    and isinstance(stmt.value.func.value, ast.Name) and stmt.value.func.value.id == "os"
                    and isinstance(follower, ast.Assign) and len(follower.targets) == 1
                    and isinstance(follower.targets[0], ast.Attribute) and follower.targets[0].attr == "created"
                    and isinstance(follower.targets[0].value, ast.Name)
                    and isinstance(follower.value, ast.Constant) and follower.value.value is True
                ):
                    found.append(_Transition(
                        "created", call, _span(stmt.value), store, _span(follower.targets[0]), _span(follower),
                        (follower.lineno,), "# fd-install",
                    ))
                if _is_detach(stmt) and _is_close(follower):
                    found.append(_Transition(
                        "release", store, _span(stmt.targets[0]), call, _span(follower.value), _span(follower),
                        (stmt.lineno, follower.lineno), "# fd-release",
                    ))
                if (
                    _is_detach(stmt) and isinstance(follower, ast.Assign) and len(follower.targets) == 1
                    and isinstance(follower.targets[0], ast.Attribute) and follower.targets[0].attr == "fd"
                    and _name_chain(follower.targets[0].value) and isinstance(follower.value, ast.Name)
                ):
                    found.append(_Transition(
                        "transfer", store, _span(stmt.targets[0]), store, _span(follower.targets[0]), _span(follower),
                        (stmt.lineno, follower.lineno), "# fd-install",
                    ))
                if (
                    qualname == _ROOTS_RELEASE_QUALNAME and isinstance(stmt, ast.AugAssign)
                    and isinstance(stmt.op, ast.Add) and isinstance(stmt.target, ast.Attribute)
                    and stmt.target.attr == "_roots_released" and isinstance(stmt.target.value, ast.Name)
                    and stmt.target.value.id == "self" and isinstance(stmt.value, ast.Constant)
                    and stmt.value.value == 1 and _is_close(follower)
                ):
                    found.append(_Transition(
                        "roots", store, _span(stmt.target), call, _span(follower.value), _span(follower),
                        (stmt.lineno, follower.lineno), "# fd-release", _ROOTS_RELEASE_QUALNAME,
                    ))
            for child_body in _child_bodies(stmt):
                visit(child_body, _child_qualname(stmt, qualname))

    visit(tree.body, "")
    return found


def _child_bodies(stmt) -> list[list]:
    bodies = [getattr(stmt, field) for field in ("body", "orelse", "finalbody") if isinstance(getattr(stmt, field, None), list)]
    bodies += [handler.body for handler in getattr(stmt, "handlers", [])]
    return bodies


def _child_qualname(stmt, qualname: str) -> str:
    if isinstance(stmt, ast.ClassDef):
        return f"{qualname}.{stmt.name}" if qualname else stmt.name
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f"{qualname}.{stmt.name}" if qualname else stmt.name
    return qualname


def _transition_lines(text: str) -> dict[int, str]:
    """Line -> transition kind, for every statement line that belongs to an
    admitted ownership transition."""
    return {line: t.kind for t in _ownership_transitions(text) for line in t.lines}


def _marker_violations(text: str) -> list[str]:
    """Markers document transitions and nothing else: a marked line must
    belong to an admitted transition (with the matching marker), and every
    transition statement must be marked."""
    lines = text.splitlines()
    marked = {n: m for n, line in enumerate(lines, 1) for m in _WINDOW_MARKERS if m in line}
    expected = {line: t.marker for t in _ownership_transitions(text) for line in t.lines}
    violations = [
        f"line {n}: marked statement is not part of an admitted ownership transition"
        for n in sorted(set(marked) - set(expected))
    ]
    violations += [f"line {n}: ownership transition statement not marked" for n in sorted(set(expected) - set(marked))]
    violations += [
        f"line {n}: transition marked {marked[n]}, expected {expected[n]}"
        for n in sorted(set(marked) & set(expected)) if marked[n] != expected[n]
    ]
    return violations


_TRANSITIONS_BY_FILE: dict[tuple[str, str], list[_Transition]] = {}
_WINDOW_OFFSETS: dict[object, frozenset[int]] = {}


def _source_of(filename: str) -> str:
    return _MUTANT_SOURCES[filename] if filename == _MUTANT_FILE else Path(filename).read_text()


def _window_offsets(code) -> frozenset[int]:
    """Bytecode offsets of `code` that belong to an admitted ownership
    transition (strictly after its start instruction, through its end)."""
    cached = _WINDOW_OFFSETS.get(code)
    if cached is not None:
        return cached
    text = _source_of(code.co_filename)
    key = (code.co_filename, text)
    if key not in _TRANSITIONS_BY_FILE:
        _TRANSITIONS_BY_FILE[key] = _ownership_transitions(text)
    instructions = [i for i in dis.get_instructions(code) if i.opname != "CACHE"]
    tolerated: set[int] = set()
    for transition in _TRANSITIONS_BY_FILE[key]:
        if transition.qualname is not None and code.co_qualname != transition.qualname:
            continue
        for s_index, instruction in enumerate(instructions):
            if instruction.opname not in transition.start_ops or tuple(instruction.positions) != transition.start_span:
                continue
            e_index = next(
                (
                    k for k in range(s_index + 1, len(instructions))
                    if instructions[k].opname in transition.end_ops
                    and tuple(instructions[k].positions) == transition.end_span
                ),
                None,
            )
            if e_index is None:
                continue
            between = instructions[s_index + 1 : e_index + 1]
            if all(
                _within(i.positions, transition.continuation)
                or (i.opname == "POP_TOP" and _within(i.positions, transition.start_span))
                for i in between
            ):
                tolerated.update(i.offset for i in between)
    result = _WINDOW_OFFSETS[code] = frozenset(tolerated)
    return result


def _tolerated(site) -> bool:
    """A fault is inside the declared window iff its bytecode offset belongs
    to an admitted ownership transition of its code object."""
    return site is not None and site[4] in _window_offsets(site[5])


_TOA_DUP_CODE = toa.AuthorizedGitStorageSetV2.duplicate_authorized_roots.__code__


def _in_scope(code) -> bool:
    return code.co_filename in (_PSV_FILE, _MUTANT_FILE) or code is _TOA_DUP_CODE


class _CloseAudit:
    """`os` proxy for the sweep: counts EBADF on close (a double close or a
    close of a number nobody owns). Its frames are outside the traced scope."""

    def __init__(self, real) -> None:
        self._real = real
        self.ebadf = 0

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def close(self, fd: int) -> None:
        try:
            self._real.close(fd)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                self.ebadf += 1
            raise


_WITH_EXIT_CALLS: dict[object, frozenset[int]] = {}


def _with_exit_calls(code) -> frozenset[int]:
    """Offsets of the normal-exit `__exit__(None, None, None)` CALL of every
    `with` block in `code` (3.11: LOAD_CONST None x3, PRECALL 2, CALL 2).
    Every context manager S1-A uses is C-implemented (`threading.Lock`,
    `os.scandir`'s iterator): that call cannot fail synchronously, and a fault
    injected there would model a lock that is never released -- an artifact
    of the model, not a behaviour of CPython. Declared, not hidden."""
    cached = _WITH_EXIT_CALLS.get(code)
    if cached is None:
        real = [i for i in dis.get_instructions(code) if i.opname != "CACHE"]
        found = set()
        for k in range(4, len(real)):
            window = real[k - 4 : k + 1]
            if (
                window[4].opname == "CALL" and window[4].arg == 2 and window[3].opname == "PRECALL"
                and all(i.opname == "LOAD_CONST" and i.argval is None for i in window[:3])
            ):
                found.add(window[4].offset)
        cached = _WITH_EXIT_CALLS[code] = frozenset(found)
    return cached


def _traced(
    fn: Callable[[], object], *, inject_at: int | None, sites: list | None = None, fault: type = MemoryError
):
    """Run `fn` with opcode tracing on S1-A frames only. Returns
    (result, exception, fault_site, fault_sites_seen). At most ONE fault is
    raised; CPython unsets the tracer when it raises, so every cleanup that
    follows runs fault-free (the one-fault model)."""
    state = {"count": 0, "site": None}

    def local(frame, event, arg):
        if event == "opcode":
            code = frame.f_code
            name = dis.opname[code.co_code[frame.f_lasti]]
            if name not in _NONFAILING_OPCODES_V311 and frame.f_lasti not in _with_exit_calls(code):
                state["count"] += 1
                if sites is not None:
                    sites.append((code, frame.f_lasti))
                if state["count"] == inject_at:
                    state["site"] = (code.co_filename, frame.f_lineno, code.co_name, name, frame.f_lasti, code)
                    raise fault("s1a-injected-fault")
        return local

    def tracer(frame, event, arg):
        if _in_scope(frame.f_code):
            frame.f_trace_opcodes = True
            return local
        return None

    sys.settrace(tracer)
    try:
        result, exc = fn(), None
    except BaseException as caught:  # noqa: BLE001 - the injected fault or its consequence
        result, exc = None, caught
    finally:
        sys.settrace(None)
    return result, exc, state["site"], state["count"]


def _sweep_fixture(base: Path) -> tuple[Path, Path]:
    """Synthetic store touching every acquisition path: `.git` file ->
    worktree gitdir -> commondir `../..` -> objects/ with fanout, a complete
    pack pair, a lone idx (candidate check), info/alternates (comment line +
    relative pointer) -> an alternate holding a duplicate and a new loose."""
    src = base / "src"
    objects = src / "main" / ".git" / "objects"
    (objects / "ab").mkdir(parents=True)
    (objects / "ab" / ("c" * 38)).write_bytes(b"loose-1")
    (objects / "pack").mkdir()
    (objects / "pack" / ("pack-" + "1" * 40 + ".pack")).write_bytes(b"PACK")
    (objects / "pack" / ("pack-" + "1" * 40 + ".idx")).write_bytes(b"IDX")
    (objects / "pack" / ("pack-" + "2" * 40 + ".idx")).write_bytes(b"lone")
    (objects / "info").mkdir()
    (objects / "info" / "alternates").write_text("# comment\n../../../alt/objects\n")
    alternate = src / "alt" / "objects"
    (alternate / "ab").mkdir(parents=True)
    (alternate / "ab" / ("c" * 38)).write_bytes(b"loose-1")
    (alternate / "cd").mkdir()
    (alternate / "cd" / ("e" * 38)).write_bytes(b"loose-2")
    gitdir = src / "main" / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n")
    repo = src / "wt"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")
    return src, repo


_SWEEP_ID = "f" * 32


def _release_outcome(outcome) -> None:
    if type(outcome) is CompletePublicationV2:
        outcome.snapshot.close()
    elif type(outcome) is UnconfirmedPublicationV2:
        outcome.residual.close()


class _SweepScenario:
    """One publication shape the sweep replays; `prepare` may plant state or
    patch a scenario fault (the injected fault is then the second one)."""

    def __init__(self, name: str, expected: type, *, budget=None, prepare=None, fixture=None) -> None:
        self.name = name
        self.expected = expected
        self.budget = budget
        self.prepare = prepare
        self.fixture = fixture or _worktree_alternates_fixture


def _worktree_alternates_fixture(base: Path) -> tuple[list[Path], SourceRepositoryLocatorV2]:
    src, repo = _sweep_fixture(base)
    return [src], SourceRepositoryLocatorV2.absolute(str(repo))


def _bare_root_fixture(base: Path) -> tuple[list[Path], SourceRepositoryLocatorV2]:
    """A bare store that IS a root of A (root-index locator: the no-component
    duplicate path; `.git` absent)."""
    bare = base / "src" / "bare.git"
    (bare / "objects" / "ab").mkdir(parents=True)
    (bare / "objects" / "ab" / ("d" * 38)).write_bytes(b"loose")
    return [bare], SourceRepositoryLocatorV2.root(0)


def _dotgit_dir_fixture(base: Path) -> tuple[list[Path], SourceRepositoryLocatorV2]:
    """`.git` is a directory (the probe hands its descriptor to the gitdir owner)."""
    repo = base / "src" / "a" / "repo"
    (repo / ".git" / "objects" / "pack").mkdir(parents=True)
    (repo / ".git" / "objects" / "pack" / ("pack-" + "4" * 40 + ".pack")).write_bytes(b"P")
    (repo / ".git" / "objects" / "pack" / ("pack-" + "4" * 40 + ".idx")).write_bytes(b"I")
    return [base / "src"], SourceRepositoryLocatorV2.absolute(str(repo))


def _sweep_one(
    base: Path, roots: list[Path], locator: SourceRepositoryLocatorV2, scenario: _SweepScenario, index: int,
    inject_at: int | None, monkeypatch, sites: list | None = None,
    fault: type = MemoryError,
) -> tuple[list[str], int]:
    pub = base / f"pub-{scenario.name}-{index}"
    root = _publication_root(pub)
    capability = AuthorizedGitStorageSetV2.from_roots([str(r) for r in roots])
    audit = _CloseAudit(os)
    undo = []
    try:
        if scenario.prepare is not None:
            undo = scenario.prepare(pub) or []
        before = {fd: _ident(int(fd)) for fd in _fd_census()}
        monkeypatch.setattr(psv, "os", audit)
        monkeypatch.setattr(toa, "os", audit)

        def call():
            return publish_physical_snapshot_v2(
                source_authority=capability,
                source_locator=locator,
                object_format=SHA1,
                physical_budget=scenario.budget or _budget(),
                publication_root=root,
                _snapshot_id=_SWEEP_ID,
            )

        def expire(signum, frame):
            raise TimeoutError("s1a-sweep-hang")

        previous = signal.signal(signal.SIGALRM, expire)
        signal.setitimer(signal.ITIMER_REAL, 10)
        try:
            result, exc, site, count = _traced(call, inject_at=inject_at, sites=sites, fault=fault)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        monkeypatch.setattr(psv, "os", os)
        monkeypatch.setattr(toa, "os", os)
        for restore in undo:
            restore()
        problems: list[str] = []
        in_window = _tolerated(site)
        outcome = result if exc is None else getattr(exc, "physical_snapshot_outcome", None)
        if isinstance(exc, TimeoutError):
            return [f"{scenario.name}@{None if site is None else site[:5]}: hang"], count
        if exc is not None and not isinstance(exc, fault):
            problems.append(f"unexpected exception {type(exc).__name__}: {exc}")
        committed = pub / "committed" / _SWEEP_ID
        ours_committed = os.path.lexists(committed) and not os.path.lexists(committed / "planted")
        staged = os.path.lexists(pub / "staging" / _SWEEP_ID)
        if outcome is None:
            if ours_committed:
                problems.append("committed without any outcome")
            if staged and not in_window:
                problems.append("staging residue without any outcome")
        elif type(outcome) is CompletePublicationV2:
            if not ours_committed or staged:
                problems.append("complete but not (committed and not staged)")
        elif type(outcome) is NotPublishedV2:
            if ours_committed:
                problems.append("not-published while committed")
            if staged and not outcome.staging_residue:
                problems.append("staging residue hidden")
        elif type(outcome) is UnconfirmedPublicationV2:
            if not ours_committed:
                problems.append("unconfirmed but nothing committed")
        elif type(outcome) is not IndeterminatePublicationV2:
            problems.append(f"untyped outcome {type(outcome).__name__}")
        if inject_at is None and type(result) is not scenario.expected:
            problems.append(f"baseline outcome {type(result).__name__}, expected {scenario.expected.__name__}")
        handed = (
            outcome.snapshot._descriptor.fd if type(outcome) is CompletePublicationV2
            else outcome.residual._descriptor.fd if type(outcome) is UnconfirmedPublicationV2
            else None
        )
        if type(outcome) in (CompletePublicationV2, UnconfirmedPublicationV2) and ours_committed:
            if handed is None or _ident(handed) != _ident_of(committed):
                problems.append("handed-out descriptor is not the committed tree")
        _release_outcome(outcome)
        # Strict: measured while the exception (and its frames) is still
        # alive, so nothing counts as released just because a finalizer ran.
        after = _fd_census()
        leaked = sorted(set(after) - set(before))
        altered = sorted(
            fd for fd, ident in before.items() if ident is not None and fd in after and _ident(int(fd)) != ident
        )
        closed_foreign = sorted(fd for fd, ident in before.items() if ident is not None and fd not in after)
        del exc, result, outcome
        if leaked and not in_window:
            problems.append(f"leaked {leaked}")
        if closed_foreign or altered:
            problems.append(f"pre-existing descriptors closed {closed_foreign} altered {altered}")
        if audit.ebadf:
            problems.append(f"close of an unowned descriptor x{audit.ebadf}")
        shown = None if site is None else site[:5]
        return [f"{scenario.name}@{shown}: {p}" for p in problems], count
    finally:
        monkeypatch.setattr(psv, "os", os)
        monkeypatch.setattr(toa, "os", os)
        capability.close()
        root.close()


def _injection_points(sites: list) -> list[int]:
    """1-based fault indices: the first and the last occurrence of every
    distinct (code, offset) site."""
    first: dict[tuple, int] = {}
    last: dict[tuple, int] = {}
    for index, site in enumerate(sites, 1):
        first.setdefault(site, index)
        last[site] = index
    return sorted(set(first.values()) | set(last.values()))


def _sweep(base: Path, scenario: _SweepScenario, monkeypatch, *, only_codes: set | None = None) -> tuple[list[str], int]:
    """Baseline run, then one run per injection point. Returns (violations, runs)."""
    roots, locator = scenario.fixture(base / scenario.name)
    sites: list = []
    # The cyclic collector stays off for the whole sweep: no finalizer may
    # release a descriptor behind the census (strict ownership, not GC luck).
    gc.collect()
    gc.disable()
    # An interruption injected into a finalizer is swallowed by CPython
    # ("exception ignored"); the census below still decides whether any
    # descriptor was affected. Collected, not printed.
    swallowed: list = []
    previous_hook = sys.unraisablehook

    def only_injected(unraisable) -> None:
        # Only the injected fault is collected; anything else still surfaces.
        if isinstance(unraisable.exc_value, fault_type) and str(unraisable.exc_value) == "s1a-injected-fault":
            swallowed.append(unraisable)
        else:
            previous_hook(unraisable)

    fault_type = getattr(scenario, "fault", MemoryError)
    sys.unraisablehook = only_injected
    try:
        fault = getattr(scenario, "fault", MemoryError)
        violations, _ = _sweep_one(base, roots, locator, scenario, 0, None, monkeypatch, sites, fault)
        points = _injection_points(sites)
        if only_codes is not None:
            points = [
                p for p in points if sites[p - 1][0].co_name in only_codes or sites[p - 1][0].co_filename == _MUTANT_FILE
            ]
        for run, point in enumerate(points, 1):
            found, _ = _sweep_one(base, roots, locator, scenario, run, point, monkeypatch, None, fault)
            violations.extend(found)
    finally:
        sys.unraisablehook = previous_hook
        gc.enable()
        gc.collect()
    return violations, len(points)


def _scenario_budget_refusal() -> _SweepScenario:
    # Refused mid-copy, after files were staged: the abort path runs fault-free.
    return _SweepScenario("budget", NotPublishedV2, budget=_budget(max_files_copied=3))


def _scenario_collision() -> _SweepScenario:
    def plant(pub: Path):
        (pub / "committed" / _SWEEP_ID).mkdir()
        (pub / "committed" / _SWEEP_ID / "planted").write_bytes(b"")
        return []

    return _SweepScenario("collision", NotPublishedV2, prepare=plant)


def _scenario_post_commit_failure(monkeypatch) -> _SweepScenario:
    def patch(pub: Path):
        real = psv._statx_identity_v2

        def failing(fd):
            raise psv._MountIdentityUnavailableV2()

        monkeypatch.setattr(psv, "_statx_identity_v2", failing)
        return [lambda: monkeypatch.setattr(psv, "_statx_identity_v2", real)]

    return _SweepScenario("postcommit", UnconfirmedPublicationV2, prepare=patch)


def _scenario_rename_error_but_committed(monkeypatch) -> _SweepScenario:
    def patch(pub: Path):
        real = psv._renameat2_noreplace_v2

        def renamed_then_eio(*args):
            assert real(*args) == 0
            return errno.EIO

        monkeypatch.setattr(psv, "_renameat2_noreplace_v2", renamed_then_eio)
        return [lambda: monkeypatch.setattr(psv, "_renameat2_noreplace_v2", real)]

    return _SweepScenario("renameerr", UnconfirmedPublicationV2, prepare=patch)


def _scenario_unobservable(monkeypatch) -> _SweepScenario:
    def patch(pub: Path):
        real_rename, real_observe = psv._renameat2_noreplace_v2, psv._observe_commit_v2
        monkeypatch.setattr(psv, "_renameat2_noreplace_v2", lambda *args: errno.EIO)
        monkeypatch.setattr(psv, "_observe_commit_v2", lambda run: "indeterminate")
        return [
            lambda: monkeypatch.setattr(psv, "_renameat2_noreplace_v2", real_rename),
            lambda: monkeypatch.setattr(psv, "_observe_commit_v2", real_observe),
        ]

    return _SweepScenario("unobservable", IndeterminatePublicationV2, prepare=patch)


def _scenario_open_stage_failure(monkeypatch) -> _SweepScenario:
    """`staging/<id>` exists but was never opened: abort removes it by name."""

    def patch(pub: Path):
        real = psv._StagingWriterV2.open_stage

        def failing(self):
            raise psv._RefusalV2(psv.PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)

        monkeypatch.setattr(psv._StagingWriterV2, "open_stage", failing)
        return [lambda: monkeypatch.setattr(psv._StagingWriterV2, "open_stage", real)]

    return _SweepScenario("openstage", NotPublishedV2, prepare=patch)


def _all_sweep_scenarios(monkeypatch) -> list[_SweepScenario]:
    return [
        _SweepScenario("complete", CompletePublicationV2),
        _SweepScenario("bare", CompletePublicationV2, fixture=_bare_root_fixture),
        _SweepScenario("dotgitdir", CompletePublicationV2, fixture=_dotgit_dir_fixture),
        _scenario_budget_refusal(),
        _scenario_collision(),
        _scenario_post_commit_failure(monkeypatch),
        _scenario_rename_error_but_committed(monkeypatch),
        _scenario_unobservable(monkeypatch),
        _scenario_open_stage_failure(monkeypatch),
    ]


_SWEEP_SCENARIO_IDS = [
    "complete", "bare", "dotgitdir", "budget", "collision", "postcommit", "renameerr", "unobservable", "openstage",
]


@pytest.mark.parametrize("scenario_index", range(len(_SWEEP_SCENARIO_IDS)), ids=_SWEEP_SCENARIO_IDS)
def test_c11_every_synchronous_fault_site_leaves_no_unowned_descriptor(tmp_path: Path, monkeypatch, scenario_index: int) -> None:
    """C11 (refined) + C10 under faults: for EVERY fault site S1-A executes in
    this publication shape, a single injected synchronous failure leaves no
    descriptor unreleased, none closed twice, no pre-existing one touched,
    and a truthful outcome. Tolerated only inside the marked window."""
    scenario = _all_sweep_scenarios(monkeypatch)[scenario_index]
    assert scenario.name == _SWEEP_SCENARIO_IDS[scenario_index]
    violations, runs = _sweep(tmp_path, scenario, monkeypatch)
    assert runs > 50, "the sweep must actually exercise the fault sites"
    assert violations == []


@pytest.mark.parametrize("scenario_index", range(len(_SWEEP_SCENARIO_IDS)), ids=_SWEEP_SCENARIO_IDS)
def test_c11_every_asynchronous_interruption_site_leaves_no_unowned_descriptor(
    tmp_path: Path, monkeypatch, scenario_index: int
) -> None:
    """The exercised asynchronous-interruption path: the same single fault
    as a KeyboardInterrupt (a BaseException), which reaches the
    `except BaseException` branches the MemoryError sweep never does. One
    interruption per run; MULTIPLE_ASYNC_INTERRUPTION_DURING_CLEANUP is
    outside the qualified fault model."""
    scenario = _all_sweep_scenarios(monkeypatch)[scenario_index]
    scenario.fault = KeyboardInterrupt
    violations, runs = _sweep(tmp_path, scenario, monkeypatch)
    assert runs > 50
    assert violations == []


def test_c11_publication_root_factory_fault_sites_leave_no_unowned_descriptor(tmp_path: Path) -> None:
    """The W factory is an acquisition path of its own (three descriptors)."""
    (tmp_path / "staging").mkdir()
    (tmp_path / "committed").mkdir()
    caller_fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    violations: list[str] = []
    gc.collect()
    gc.disable()
    try:
        sites: list = []
        result, exc, _, _ = _traced(lambda: SnapshotPublicationRootV2.from_directory_fd(caller_fd), inject_at=None, sites=sites)
        assert exc is None
        result.close()
        points = _injection_points(sites)
        for point in points:
            before = _fd_census()
            result, exc, site, _ = _traced(lambda: SnapshotPublicationRootV2.from_directory_fd(caller_fd), inject_at=point)
            if result is not None:
                result.close()
            leaked = sorted(_fd_census() - before)
            del result, exc
            if leaked and not _tolerated(site):
                violations.append(f"{site[:5]}: leaked {leaked}")
            if _ident(caller_fd) is None:
                violations.append(f"{site[:5]}: the caller's descriptor was closed")
    finally:
        gc.enable()
        os.close(caller_fd)
    assert len(points) > 20
    assert violations == []


# -- static census: every acquisition / release site in the S1-A write-set -------------------

_FORBIDDEN_ACQUIRERS = frozenset(
    {
        "_open_regular_file_no_follow_v2", "_try_open_dir_no_follow_v2", "_open_dir_no_follow_v2",
        "dup", "dup2", "pipe", "openpty", "fdopen", "memfd_create", "socket", "eventfd",
    }
)
_RELEASE_FUNCTIONS = frozenset({"close_once", "close_quietly", "_release_roots"})
#: Allow-lists, not deny-lists (review F-B): every attribute S1-A touches on
#: these modules is vetted; a new one fails the census until it is.
_VETTED_ATTRIBUTES = {
    "os": frozenset(
        {
            "O_CLOEXEC", "O_CREAT", "O_DIRECTORY", "O_EXCL", "O_NOFOLLOW", "O_NONBLOCK", "O_RDONLY", "O_WRONLY",
            "close", "fchmod", "fsencode", "fstat", "fsync", "makedev", "mkdir", "open", "read", "rmdir",
            "scandir", "stat", "unlink", "write",
        }
    ),
    "fcntl": frozenset({"fcntl", "F_DUPFD_CLOEXEC", "F_GETFL", "F_SETFL"}),
}
_VETTED_FCNTL_COMMANDS = frozenset({"F_DUPFD_CLOEXEC", "F_GETFL", "F_SETFL"})
_VETTED_IMPORTS = frozenset(
    {
        "__future__", "ctypes", "enum", "errno", "fcntl", "hashlib", "json", "os", "platform", "re", "secrets",
        "stat", "struct", "sys", "threading", "weakref", "collections.abc", "dataclasses", "typing",
        "app.agent_review.trusted_object_authority_v2",
    }
)
_DESCRIPTOR_SYSCALLS_ALLOWED = frozenset({"renameat2", "statx"})  # neither creates a descriptor


#: Canonical source forms for module handles (N2). The static census enforces
#: THESE forms; it is not a verifier of dynamic Python
#: (`StaticCensusCanonicalCoverage != CompleteSemanticCoverageOfDynamicPython`,
#: limitation PYTHON_DYNAMIC_INDIRECTION_OUTSIDE_STATIC_CENSUS).
_MODULE_HANDLES = frozenset({"os", "fcntl", "ctypes", "sys"})
_CALL_ONLY_MODULES = frozenset({"os", "fcntl"})  # their functions may only be called directly
_VETTED_HANDLE_ATTRIBUTES = {
    "sys": frozenset({"platform"}),
    "ctypes": frozenset(
        {"CDLL", "c_char_p", "c_long", "c_ulong", "create_string_buffer", "get_errno", "set_errno"}
    ),
}
_LIBC_METHODS = frozenset({"syscall"})
#: Direct `.syscall` invocation is permitted only inside the vetted wrapper,
#: whose number table is asserted to hold only non-descriptor syscalls
#: (maintainer adjudication of 3ff700a, Codex 4137344841).
_RAW_SYSCALL_WRAPPER = "_syscall_v2"
_SCOPE_NODES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)
_FORBIDDEN_BUILTINS = frozenset({"vars", "globals", "locals", "eval", "exec", "compile", "__import__", "open"})
#: Forms known to be OUTSIDE the canonical grammar's detection power. They
#: are named, not enforced; the exact production source must contain none.
_OUTSIDE_STATIC_CENSUS_MARKERS = ("__subclasses__", "__dict__", "__getattribute__", "fileno", "importlib", "mmap")


def _module_handle_violations(tree, parent) -> list[str]:
    """Module handles appear only as `mod.attr`; `os`/`fcntl` functions only
    as the callee of a direct call; the libc handle only as `.syscall`; no
    namespace-reflection builtins. Covers the observed alias families
    (`_os = os`, `_open = os.open`, `sys.modules`, `vars(os)`, extra
    libc/ctypes handles, `[os.open][0]`)."""
    violations: list[str] = []
    libc_names: set[str] = set()

    wrappers, wrapper, in_wrapper = _wrapper_region(tree, parent)
    if len(wrappers) > 1:
        violations.append(f"line {wrappers[1].lineno}: {_RAW_SYSCALL_WRAPPER} is not the unique top-level wrapper")
    if len(wrappers) == 1 and wrapper is None:
        violations.append(f"line {wrappers[0].lineno}: {_RAW_SYSCALL_WRAPPER} must be a plain FunctionDef")
    violations.extend(_wrapper_header_violations(wrapper))

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                value = node.value
                from_cdll = (
                    isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
                    and value.func.attr == "CDLL"
                )
                from_handle = isinstance(value, ast.Name) and value.id in libc_names
                if (from_cdll or from_handle) and node.targets[0].id not in libc_names:
                    libc_names.add(node.targets[0].id)
                    changed = True
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', '?')}"
        up = parent.get(node)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in _MODULE_HANDLES:
            if not (isinstance(up, ast.Attribute) and up.value is node):
                violations.append(f"{where}: module handle {node.id} escapes as a value")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = node.value.id
            if module in _VETTED_HANDLE_ATTRIBUTES and node.attr not in _VETTED_HANDLE_ATTRIBUTES[module]:
                violations.append(f"{where}: unvetted {module}.{node.attr}")
            if module in _CALL_ONLY_MODULES and node.attr[:1].islower():
                if not (isinstance(up, ast.Call) and up.func is node):
                    violations.append(f"{where}: {module}.{node.attr} escapes as a value")
            if module in libc_names and node.attr not in _LIBC_METHODS:
                violations.append(f"{where}: libc handle used beyond {sorted(_LIBC_METHODS)}")
        # N2 (maintainer final grant on 1f37fbb): receiver-independent, bound
        # to the STRUCTURAL context Module -> FunctionDef(_syscall_v2), never
        # to a bare name (`StaticExplicitSyscallGrammar !=
        # UniversalDynamicPythonCallGraph`).
        if isinstance(node, ast.Attribute) and node.attr == "syscall" and not in_wrapper(node):
            violations.append(f"{where}: raw syscall outside {_RAW_SYSCALL_WRAPPER}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call):
            callee = node.value.func
            if isinstance(callee, ast.Attribute) and callee.attr == "CDLL":
                violations.append(f"{where}: attribute of an unnamed libc handle")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_BUILTINS:
            violations.append(f"{where}: namespace/IO builtin {node.func.id}")
    if _N2_SWITCHES["cdll_reference_closure"]:
        for reference, disposition in _cdll_reference_dispositions(tree, parent):
            if disposition == "VIOLATION":
                violations.append(
                    f"line {reference.lineno}: ctypes.CDLL reference outside the capability-reference closure"
                )
    return violations


#: Property switches for the N2 anti-vacuity mutants ONLY (each mutant turns
#: one property off and must be killed by the fact that property protects).
_N2_SWITCHES = {
    "cdll_reference_closure": True,
    "importfrom_ctypes_forbidden": True,
    "runtime_region_is_body_only": True,
    "canonical_carrier_required": True,
    "annotation_slot_only": True,
}
_CDLL_DISPOSITIONS = ("CANONICAL_RUNTIME_CONSTRUCTION", "POSTPONED_ANNOTATION", "VIOLATION")


def _wrapper_region(tree, parent):
    """(top-level `_syscall_v2` defs, the unique plain one or None, in_region).

    `WrapperRuntimeRegion(node, W) != DescendantOfFunctionDef(node, W)`: a node
    is in the region only if it descends from a statement DIRECTLY in
    `W.body` with no intermediate scope on the path. Decorators, defaults,
    kw-defaults and annotations are children of `W` but run in the enclosing
    scope when `def` executes."""
    wrappers = [
        stmt for stmt in tree.body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == _RAW_SYSCALL_WRAPPER
    ]
    wrapper = wrappers[0] if len(wrappers) == 1 and isinstance(wrappers[0], ast.FunctionDef) else None

    def in_region(node) -> bool:
        if wrapper is None:
            return False
        current = node
        while current in parent:
            up = parent[current]
            if up is wrapper:
                if not _N2_SWITCHES["runtime_region_is_body_only"]:
                    return True  # mutant M-RUNTIME-REGION: any descendant of W
                return any(current is statement for statement in wrapper.body)
            if isinstance(up, _SCOPE_NODES) and _N2_SWITCHES["runtime_region_is_body_only"]:
                return False
            current = up
        return False

    return wrappers, wrapper, in_region


def _is_cdll_reference(node) -> bool:
    return (
        isinstance(node, ast.Attribute) and node.attr == "CDLL"
        and isinstance(node.value, ast.Name) and node.value.id == "ctypes"
    )


def _in_annotation(node, parent) -> bool:
    """True iff `node` sits structurally inside an annotation slot: an
    `AnnAssign.annotation`, an `arg.annotation` or a function `returns`."""
    child = node
    while child in parent:
        up = parent[child]
        if (
            (isinstance(up, ast.AnnAssign) and up.annotation is child)
            or (isinstance(up, ast.arg) and up.annotation is child)
            or (isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)) and up.returns is child)
        ):
            return True
        child = up
    return False


def _under_annotated_statement(node, parent) -> bool:
    """Mutant M4 only: ANY child of an annotated statement counts as annotation
    (collapses `PostponedAnnotationReference != ExecutableCapabilityReference`)."""
    current = node
    while current in parent:
        current = parent[current]
        if isinstance(current, ast.AnnAssign):
            return True
    return False


def _cdll_reference_dispositions(tree, parent) -> list[tuple[ast.AST, str]]:
    """`CapabilityReferenceKnown != CapabilityCallShapeKnown`.

    EVERY explicit `ctypes.CDLL` reference (an `Attribute(Name("ctypes"),
    "CDLL")`, called or not) gets exactly one disposition:
      CANONICAL_RUNTIME_CONSTRUCTION  the `func` of `<name> = ctypes.CDLL(...)`
                                      (simple Assign, one Name target) inside
                                      the wrapper runtime region;
      POSTPONED_ANNOTATION            inside an annotation slot, AND the module
                                      has `from __future__ import annotations`;
      VIOLATION                       anything else (decorator, alias, return,
                                      container, walrus, header, other scope)."""
    future_annotations = any(
        isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"
        and any(alias.name == "annotations" for alias in stmt.names)
        for stmt in tree.body
    )
    _, _, in_region = _wrapper_region(tree, parent)
    dispositions: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if not _is_cdll_reference(node):
            continue
        call = parent.get(node)
        assign = parent.get(call)
        canonical_carrier = (
            isinstance(call, ast.Call) and call.func is node
            and isinstance(assign, ast.Assign) and assign.value is call
            and len(assign.targets) == 1 and isinstance(assign.targets[0], ast.Name)
        )
        if not _N2_SWITCHES["canonical_carrier_required"]:
            canonical_carrier = isinstance(call, ast.Call) and call.func is node  # mutant M-CANONICAL-CARRIER
        if canonical_carrier and in_region(node):
            disposition = "CANONICAL_RUNTIME_CONSTRUCTION"
        elif future_annotations and (
            _in_annotation(node, parent) if _N2_SWITCHES["annotation_slot_only"] else _under_annotated_statement(node, parent)
        ):
            disposition = "POSTPONED_ANNOTATION"
        else:
            disposition = "VIOLATION"
        dispositions.append((node, disposition))
    return dispositions


def _wrapper_header_violations(wrapper) -> list[str]:
    """Canonical header of the unique `_syscall_v2` (this S1-A's grammar, not
    a law of Python): no decorators, no positional defaults, no keyword
    defaults with values. The production wrapper needs none of them; a
    future need must fail here and be re-adjudicated, never widen N2."""
    if wrapper is None:
        return []
    where = f"line {wrapper.lineno}: canonical {_RAW_SYSCALL_WRAPPER} header"
    violations = []
    if wrapper.decorator_list:
        violations.append(f"{where}: decorators forbidden")
    if wrapper.args.defaults:
        violations.append(f"{where}: positional defaults forbidden")
    if any(default is not None for default in wrapper.args.kw_defaults):
        violations.append(f"{where}: keyword defaults with values forbidden")
    return violations


def _pre_evaluated_owner(target) -> bool:
    """`name.attr...fd`, `self._roots` or `made[index]`: an owner expression
    whose evaluation after the syscall is only attribute loads (no call, no
    subscript, no allocation)."""

    def name_chain(node) -> bool:
        while isinstance(node, ast.Attribute):
            node = node.value
        return isinstance(node, ast.Name)

    if isinstance(target, ast.Attribute) and target.attr in ("fd", "_roots"):
        return name_chain(target.value)
    return (
        isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "made"
        and isinstance(target.slice, ast.Name)
    )


def _acquisition_census(source: str) -> list[str]:
    """Structural C11 census of one module's source: returns violations.

    - `os.open`, `fcntl.fcntl(..., F_DUPFD_CLOEXEC, ...)` and
      `.duplicate_authorized_roots()` must be the whole right-hand side of a
      single-target assignment INTO a pre-existing owner (`<slot>.fd`,
      `made[...]`, `self._roots`) on a `# fd-install` line;
    - `os.scandir` only as a `with` context (the iterator is a C-level owner
      created together with its internal dup);
    - `os.mkdir` must be followed immediately by the statement that installs
      its cleanup obligation (`# fd-install`) or the open into a slot;
    - `os.close` only on `# fd-release` lines inside the release primitives;
    - no other descriptor-producing primitive, and no G1C helper that hands
      back a bare descriptor.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    violations: list[str] = []

    def line_of(node) -> str:
        return lines[node.lineno - 1]

    def enclosing_function(node) -> str | None:
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return None

    def next_statement(stmt):
        holder = parent.get(stmt)
        for field in ("body", "orelse", "finalbody"):
            body = getattr(holder, field, None)
            if isinstance(body, list) and stmt in body:
                index = body.index(stmt)
                return body[index + 1] if index + 1 < len(body) else None
        return None

    violations.extend(_marker_violations(source))
    violations.extend(_module_handle_violations(tree, parent))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in _VETTED_IMPORTS or alias.asname is not None:
                    violations.append(f"line {node.lineno}: unvetted import {alias.name} as {alias.asname}")
        elif isinstance(node, ast.ImportFrom):
            if node.module not in _VETTED_IMPORTS or node.module in _VETTED_ATTRIBUTES:
                violations.append(f"line {node.lineno}: unvetted from-import {node.module}")
            if _N2_SWITCHES["importfrom_ctypes_forbidden"] and (node.module or "").split(".")[0] == "ctypes":
                violations.append(f"line {node.lineno}: from-import of ctypes forbidden (canonical namespace ctypes.<capability>)")
        elif (
            isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id in _VETTED_ATTRIBUTES and node.attr not in _VETTED_ATTRIBUTES[node.value.id]
        ):
            violations.append(f"line {node.lineno}: unvetted {node.value.id}.{node.attr}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("open", "__import__"):
            violations.append(f"line {node.lineno}: builtin {func.id}")
        if (
            isinstance(func, ast.Name) and func.id == "getattr" and node.args
            and isinstance(node.args[0], ast.Name) and node.args[0].id in (*_VETTED_ATTRIBUTES, "ctypes", "socket")
        ):
            violations.append(f"line {node.lineno}: dynamic attribute of {node.args[0].id}")
        if (
            isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id == "fcntl" and func.attr == "fcntl"
        ):
            command = node.args[1] if len(node.args) > 1 else None
            if not (
                isinstance(command, ast.Attribute) and isinstance(command.value, ast.Name)
                and command.value.id == "fcntl" and command.attr in _VETTED_FCNTL_COMMANDS
            ):
                violations.append(f"line {node.lineno}: unvetted fcntl command")
        attr = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        owner = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else None
        where = f"line {node.lineno}"
        if attr in _FORBIDDEN_ACQUIRERS:
            violations.append(f"{where}: forbidden descriptor source {attr}")
            continue
        is_dup = (
            owner == "fcntl" and attr == "fcntl" and len(node.args) > 1
            and isinstance(node.args[1], ast.Attribute) and node.args[1].attr == "F_DUPFD_CLOEXEC"
        )
        acquires = (owner == "os" and attr == "open") or is_dup or attr == "duplicate_authorized_roots"
        if acquires:
            stmt = parent.get(node)
            target = stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else None
            into_owner = target is not None and _pre_evaluated_owner(target)
            if not into_owner or stmt.value is not node:
                violations.append(f"{where}: descriptor not installed into a pre-existing owner slot")
            elif "# fd-install" not in line_of(node):
                violations.append(f"{where}: install statement not marked")
        elif owner == "os" and attr == "scandir":
            if not isinstance(parent.get(node), ast.withitem):
                violations.append(f"{where}: scandir iterator not owned by a with-statement")
        elif owner == "os" and attr == "mkdir":
            follower = next_statement(parent.get(node))
            ok = (
                isinstance(follower, ast.Assign) and len(follower.targets) == 1
                and "# fd-install" in lines[follower.lineno - 1]
                and (
                    (
                        isinstance(follower.value, ast.Constant) and follower.value.value is True
                        and isinstance(follower.targets[0], ast.Attribute)
                        and isinstance(follower.targets[0].value, ast.Name)
                    )
                    or _pre_evaluated_owner(follower.targets[0])
                )
            )
            if not ok:
                violations.append(f"{where}: mkdir not followed by its installed cleanup obligation")
        elif owner == "os" and attr == "close":
            if "# fd-release" not in line_of(node) or enclosing_function(node) not in _RELEASE_FUNCTIONS:
                violations.append(f"{where}: close outside the release primitives")
    return violations


def _psv_code_objects() -> list:
    found, stack = [], [compile(Path(_PSV_FILE).read_text(), _PSV_FILE, "exec")]
    while stack:
        code = stack.pop()
        found.append(code)
        stack.extend(c for c in code.co_consts if hasattr(c, "co_code"))
    return found


def _duplicate_authorized_roots_source() -> str:
    return textwrap.dedent(inspect.getsource(toa.AuthorizedGitStorageSetV2.duplicate_authorized_roots))


def test_c11_static_census_every_acquisition_installs_into_a_pre_existing_owner() -> None:
    psv_source = Path(_PSV_FILE).read_text()
    assert _acquisition_census(psv_source) == []
    top_level = [
        stmt for stmt in ast.parse(psv_source).body
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == _RAW_SYSCALL_WRAPPER
    ]
    assert len(top_level) == 1 and type(top_level[0]) is ast.FunctionDef  # unique_top_level_definition: required
    # The limitation never hides a form present in the exact source.
    assert [m for m in _OUTSIDE_STATIC_CENSUS_MARKERS if m in psv_source] == []
    kinds = {t.kind for t in _ownership_transitions(psv_source)}
    assert kinds == {"acquire", "created", "release", "roots", "transfer"}
    # Anti-vacuity of the bytecode anchoring: every production code object
    # that contains a transition gets a non-empty window, and only those do.
    anchored = {
        code.co_qualname for code in _psv_code_objects() if _window_offsets(code)
    }
    assert {
        "_FdSlotV2.move_to", "_FdSlotV2.close_once", "_FdSlotV2.close_quietly", "_SourceSessionV2._release_roots",
        "_SourceSessionV2.acquire_roots", "_SourceSessionV2.admit", "_StagingWriterV2.create_stage",
    } <= anchored
    assert _window_offsets(_TOA_DUP_CODE)
    assert set(psv._SYSCALL_NUMBERS_V2["x86_64"]) <= _DESCRIPTOR_SYSCALLS_ALLOWED
    assert set(psv._SYSCALL_NUMBERS_V2["aarch64"]) <= _DESCRIPTOR_SYSCALLS_ALLOWED
    assert _acquisition_census(_duplicate_authorized_roots_source()) == []
    # Anti-vacuity: the census does see the acquisitions it rules on.
    tree = ast.parse(psv_source)
    opens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "open"]
    assert len(opens) >= 10
    installs = [line for line in psv_source.splitlines() if "# fd-install" in line]
    releases = [line for line in psv_source.splitlines() if "# fd-release" in line]
    assert len(installs) >= 12 and len(releases) >= 6


_RESOURCE_BEFORE_OWNER_CHILD = '''
def child(self, parent, name, missing_reason):
    if name in ("", ".", "..") or "/" in name or "\\x00" in name or parent.fd is None:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    self._tracker.charge(descriptor_opens=1, path_components=1)
    carrier = _FdSlotV2()
    if not _open_source_dir_into_v2(carrier, parent.fd, name):
        raise _RefusalV2(missing_reason)
    fd = carrier.fd
    carrier.fd = None
    admitted = self._new_admitted(
        _AdmittedPositionV2(parent.position.root_index, parent.position.components + (name,))
    )
    admitted.fd = fd
    return admitted
'''


def test_mutation_c11_resource_before_owner_is_killed_by_the_static_census() -> None:
    """Mutant (the ba3800e shape): `child` opens into a local, THEN builds and
    registers the owner. The census rejects the bare-local descriptor."""
    source = Path(_PSV_FILE).read_text()
    original = textwrap.indent(textwrap.dedent(inspect.getsource(psv._SourceSessionV2.child)), "    ")
    assert original in source
    mutated = source.replace(original, textwrap.indent(_RESOURCE_BEFORE_OWNER_CHILD.lstrip("\n"), "    "), 1)
    mutated = mutated.replace("carrier = _FdSlotV2()", "carrier = _FdSlotV2()", 1)
    # Reintroduce the pre-redesign bare open as well (resource before owner):
    mutated = mutated.replace(
        "        if not _open_source_dir_into_v2(carrier, parent.fd, name):\n            raise _RefusalV2(missing_reason)\n        fd = carrier.fd\n        carrier.fd = None\n",
        "        fd = os.open(name, _SOURCE_DIR_OPEN_FLAGS_V2, dir_fd=parent.fd)\n",
        1,
    )
    violations = _acquisition_census(mutated)
    assert any("not installed into a pre-existing owner slot" in v for v in violations), violations


def test_mutation_c11_close_outside_release_primitives_is_killed_by_the_static_census() -> None:
    """Mutant: release by a bare close before detaching (the pre-redesign
    `_close_once_v2` shape)."""
    source = Path(_PSV_FILE).read_text()
    old = "        admitted.close_once()\n        try:\n            self._live.remove(admitted)"
    assert old in source
    mutated = source.replace(old, "        os.close(admitted.fd)\n        try:\n            self._live.remove(admitted)", 1)
    assert any("close outside the release primitives" in v for v in _acquisition_census(mutated))


# -- dynamic mutants: the sweep kills each reintroduced shape by the intended fact --------------


def _compile_mutant(source: str):
    namespace: dict[str, object] = {}
    _MUTANT_SOURCES[_MUTANT_FILE] = textwrap.dedent(source)
    exec(compile(textwrap.dedent(source), _MUTANT_FILE, "exec"), vars(psv), namespace)  # noqa: S102
    (function,) = [value for value in namespace.values() if callable(value)]
    return function


def _mutant_sweep(tmp_path: Path, monkeypatch, scenario: _SweepScenario, source: str, focus: set[str]) -> list[str]:
    assert _MUTANT_SOURCES.get(_MUTANT_FILE) == textwrap.dedent(source), "compile the mutant first"
    violations, runs = _sweep(tmp_path, scenario, monkeypatch, only_codes=focus)
    assert runs > 0
    return violations


def test_mutation_c11_resource_before_owner_is_killed_by_the_fault_sweep(tmp_path: Path, monkeypatch) -> None:
    """Mutant: the successor descriptor exists before its owner is built and
    registered. Killed ONLY by a leak observed at the owner construction /
    registration step (outside the declared window)."""
    monkeypatch.setattr(psv._SourceSessionV2, "child", _compile_mutant(_RESOURCE_BEFORE_OWNER_CHILD))
    violations = _mutant_sweep(
        tmp_path, monkeypatch, _SweepScenario("complete", CompletePublicationV2),
        _RESOURCE_BEFORE_OWNER_CHILD, {"_new_admitted", "__init__"},
    )
    assert any("leaked" in v and ("child" in v or "_new_admitted" in v or "__init__" in v) for v in violations), violations


_REGISTRATION_AFTER_RELEASE_POST_COMMIT = '''
def _post_commit_v2(run):
    stage_fd = run.writer.stage.fd
    if stage_fd is None or run.staging.fd is None or run.committed.fd is None:
        raise RuntimeError("descriptor missing at the commit point")
    try:
        os.fchmod(stage_fd, _DIR_MODE_V2)
        os.fsync(stage_fd)
        os.fsync(run.committed.fd)
        os.fsync(run.staging.fd)
        info = os.fstat(stage_fd)
        identity = _statx_identity_v2(stage_fd)
    except (OSError, _MountIdentityUnavailableV2):
        return _unconfirmed_v2(run, PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2)
    binding = PublishedSnapshotBindingV2(
        snapshot_id=run.snapshot_id,
        mount_id=identity.mount_id,
        st_dev=info.st_dev,
        st_ino=info.st_ino,
        st_uid=info.st_uid,
        st_gid=info.st_gid,
        committed_parent_identity=run.committed_identity,
    )
    fd = run.writer.stage.fd
    run.writer.stage.fd = None
    snapshot = PublishedSnapshotV2(_sentinel=_SNAPSHOT_SENTINEL_V2, binding=binding, receipt=run.receipt)
    snapshot._descriptor.fd = fd
    return CompletePublicationV2(snapshot=snapshot)
'''


def test_mutation_c11_registration_after_release_is_killed_by_the_fault_sweep(tmp_path: Path, monkeypatch) -> None:
    """Mutant (the `take_stage_fd` shape, F1e): the writer's slot is released
    first, the new owner allocated afterwards. Killed by a leak observed at
    the new owner's construction."""
    monkeypatch.setattr(psv, "_post_commit_v2", _compile_mutant(_REGISTRATION_AFTER_RELEASE_POST_COMMIT))
    violations = _mutant_sweep(
        tmp_path, monkeypatch, _SweepScenario("complete", CompletePublicationV2),
        _REGISTRATION_AFTER_RELEASE_POST_COMMIT, {"__init__"},
    )
    assert any("leaked" in v and _MUTANT_FILE in v for v in violations), violations


# ================================================================================
# Capability sealing (Codex 4133910390; IMPLEMENTATION_ADJUDICATION)
# ================================================================================

_SEALED_TYPES = (
    SnapshotPublicationRootV2,
    PublishedSnapshotV2,
    CommittedSnapshotResidualV2,
    psv.PublicationResidualV2,
    psv.PublishedSnapshotReceiptV2,
    psv.PublishedSnapshotBindingV2,
    CompletePublicationV2,
    UnconfirmedPublicationV2,
    IndeterminatePublicationV2,
    NotPublishedV2,
)


class _QuietInitSubclass:
    """A base whose `__init_subclass__` does not chain to `super()`: listed
    first, it keeps `_SealedV2.__init_subclass__` from running (review F-C)."""

    def __init_subclass__(cls, **kwargs) -> None:
        pass


def _subclassable(sealed: type) -> list[str]:
    """Cooperative subclassing only; the non-cooperative MRO bypass is covered
    by `test_seal_bypass_through_the_mro_is_still_refused_at_admission`."""
    defined = []
    for bases in ((sealed,), (sealed, psv._SealedV2)):
        try:
            type("Forged", bases, {})
            defined.append("+".join(b.__name__ for b in bases))
        except TypeError:
            pass
    return defined


def test_sealed_capability_and_state_types_cannot_be_subclassed() -> None:
    assert {sealed.__name__: _subclassable(sealed) for sealed in _SEALED_TYPES} == {
        sealed.__name__: [] for sealed in _SEALED_TYPES
    }


def test_mutation_seal_removed_is_killed(monkeypatch) -> None:
    """Mutant: no `__init_subclass__` refusal (the ba3800e state)."""
    monkeypatch.setattr(psv._SealedV2, "__init_subclass__", classmethod(lambda cls, **kwargs: None))
    assert _subclassable(SnapshotPublicationRootV2)


def _victim_dir(tmp_path: Path) -> Path:
    victim = tmp_path / "victim"
    (victim / "staging").mkdir(parents=True)
    (victim / "committed").mkdir()
    return victim


def _attacker_slots(victim: Path) -> tuple[int, int]:
    return (
        os.open(str(victim / "staging"), os.O_RDONLY | os.O_DIRECTORY),
        os.open(str(victim / "committed"), os.O_RDONLY | os.O_DIRECTORY),
    )


def _spoofed_publication_root(victim: Path):
    """No subclass: an unrelated object whose `__class__` claims to be W, and
    which hands out descriptors of a directory the caller chose."""

    class Spoof:
        __class__ = property(lambda self: SnapshotPublicationRootV2)  # type: ignore[assignment]
        closed = False
        committed_identity = KernelObjectIdentityV2(mount_id=0, st_dev=0, st_ino=0)

        def _duplicate_publication_fds_into(self, staging, committed):
            staging.fd, committed.fd = _attacker_slots(victim)

    return Spoof()


def _unminted_publication_root(victim: Path) -> SnapshotPublicationRootV2:
    """Exact type, but never built by `from_directory_fd` (no sentinel, no registry)."""
    forged = object.__new__(SnapshotPublicationRootV2)
    forged._lock = __import__("threading").Lock()
    forged._closed = False
    forged._root, forged._staging, forged._committed = psv._FdSlotV2(), psv._FdSlotV2(), psv._FdSlotV2()
    forged._staging.fd, forged._committed.fd = _attacker_slots(victim)
    forged._identities = (KernelObjectIdentityV2(0, 0, 0),) * 3
    return forged


def _forged_w_outcome(tmp_path: Path, forged) -> dict[str, object]:
    """What a publication through a forged W does to the victim directory."""
    victim = tmp_path / "victim"
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    capability = AuthorizedGitStorageSetV2.from_roots([str(tmp_path / "src")])
    try:
        try:
            outcome = psv.publish_physical_snapshot_v2(
                source_authority=capability,
                source_locator=SourceRepositoryLocatorV2.absolute(str(repo)),
                object_format=SHA1,
                physical_budget=_budget(),
                publication_root=forged,
            )
            _release_outcome(outcome)
            refused = None
        except PhysicalSnapshotErrorV2 as exc:
            refused = exc.reason_code
    finally:
        capability.close()
    return {"refused": refused, "victim_written": any((victim / "committed").iterdir()) or any((victim / "staging").iterdir())}


def test_forged_publication_root_by_class_spoof_is_refused(tmp_path: Path) -> None:
    facts = _forged_w_outcome(tmp_path, _spoofed_publication_root(_victim_dir(tmp_path)))
    assert facts == {"refused": psv.PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2, "victim_written": False}


def test_forged_publication_root_without_its_factory_is_refused(tmp_path: Path) -> None:
    facts = _forged_w_outcome(tmp_path, _unminted_publication_root(_victim_dir(tmp_path)))
    assert facts == {"refused": psv.PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2, "victim_written": False}


_ISINSTANCE_W_ADMISSION = '''
def _admit_publication_root_v2(publication_root):
    if not isinstance(publication_root, SnapshotPublicationRootV2):
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    return publication_root
'''


@pytest.mark.parametrize("forgery", ["spoof", "unminted"])
def test_mutation_isinstance_admission_of_w_is_killed(tmp_path: Path, monkeypatch, forgery: str) -> None:
    """Mutant (the ba3800e admission): `isinstance` at the W boundary, no
    factory registry. Killed by the forged W writing into the victim."""
    monkeypatch.setattr(psv, "_admit_publication_root_v2", _compile_mutant(_ISINSTANCE_W_ADMISSION))
    victim = _victim_dir(tmp_path)
    forged = _spoofed_publication_root(victim) if forgery == "spoof" else _unminted_publication_root(victim)
    facts = _forged_w_outcome(tmp_path, forged)
    assert facts["victim_written"] is True


def _outside_authority_subclass(outside: Path):
    """A subclass of A that answers `duplicate_authorized_roots` with a
    duplicate of a directory A never authorized."""

    class WideningAuthority(AuthorizedGitStorageSetV2):
        def duplicate_authorized_roots(self):
            fd = os.open(str(outside), os.O_RDONLY | os.O_DIRECTORY)
            info = os.fstat(fd)
            return (toa.AuthorizedStorageRootDuplicateV2(index=0, fd=fd, dev_ino=(info.st_dev, info.st_ino), locator=None),)

    return WideningAuthority


def _forged_a_outcome(tmp_path: Path, forged_factory) -> dict[str, object]:
    src = tmp_path / "src"
    _make_repo(src / "repo", files=1)
    outside = _make_repo(tmp_path / "outside" / "victim", files=1)
    forged = forged_factory(src, outside)
    root = _publication_root(tmp_path / "pub")
    try:
        try:
            outcome = psv.publish_physical_snapshot_v2(
                source_authority=forged,
                source_locator=SourceRepositoryLocatorV2.root(0),
                object_format=SHA1,
                physical_budget=_budget(),
                publication_root=root,
            )
            published = type(outcome) is CompletePublicationV2
            _release_outcome(outcome)
            refused = None
        except PhysicalSnapshotErrorV2 as exc:
            refused, published = exc.reason_code, False
    finally:
        root.close()
        close = getattr(forged, "close", None)
        if callable(close):
            close()
    return {"refused": refused, "published_outside": published}


def _subclass_authority(src: Path, outside: Path):
    return _outside_authority_subclass(outside).from_roots([str(src)])


def _spoofed_authority(src: Path, outside: Path):
    widening = _outside_authority_subclass(outside)

    class Spoof:
        __class__ = property(lambda self: AuthorizedGitStorageSetV2)  # type: ignore[assignment]
        root_count = 1
        duplicate_authorized_roots = widening.duplicate_authorized_roots

    return Spoof()


@pytest.mark.parametrize("forgery", [_subclass_authority, _spoofed_authority], ids=["subclass", "spoof"])
def test_forged_polymorphic_source_authority_is_refused(tmp_path: Path, forgery) -> None:
    """S1-A's ingress admits A by exact type (`ExactType(A) != Provenance(A)`;
    C2_A itself is unchanged and still subclassable)."""
    assert _forged_a_outcome(tmp_path, forgery) == {
        "refused": psv.PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2, "published_outside": False,
    }


@pytest.mark.parametrize("forgery", [_subclass_authority, _spoofed_authority], ids=["subclass", "spoof"])
def test_mutation_isinstance_admission_of_a_is_killed(tmp_path: Path, monkeypatch, forgery) -> None:
    """Mutant: `isinstance` at the A boundary. Killed by a publication built
    from a directory A never authorized."""
    source = textwrap.dedent(inspect.getsource(psv.publish_physical_snapshot_v2))
    old = "    if type(source_authority) is not AuthorizedGitStorageSetV2:\n"
    assert old in source
    mutant = _compile_mutant(source.replace(old, "    if not isinstance(source_authority, AuthorizedGitStorageSetV2):\n", 1))
    monkeypatch.setattr(psv, "publish_physical_snapshot_v2", mutant)
    assert _forged_a_outcome(tmp_path, forgery)["published_outside"] is True


def test_c2a_semantics_unchanged_subclassing_a_is_still_possible(tmp_path: Path) -> None:
    """The adjudication does NOT seal C2_A globally: only S1-A's ingress refuses."""
    assert _outside_authority_subclass(tmp_path).__mro__[1] is AuthorizedGitStorageSetV2


def test_complete_publication_requires_a_minted_snapshot() -> None:
    unminted = object.__new__(PublishedSnapshotV2)
    with pytest.raises(TypeError):
        CompletePublicationV2(snapshot=unminted)


def test_mutation_complete_without_registry_is_killed(monkeypatch) -> None:
    """Mutant: `CompletePublicationV2` checks the exact type only."""

    def exact_type_only(self):
        if type(self.snapshot) is not PublishedSnapshotV2:
            raise TypeError("wrong type")

    monkeypatch.setattr(CompletePublicationV2, "__post_init__", exact_type_only)
    CompletePublicationV2(snapshot=object.__new__(PublishedSnapshotV2))  # accepted: the mutant is observable


def test_published_snapshot_state_is_read_only(tmp_path: Path) -> None:
    receipt, binding = _test_receipt_and_binding()
    snapshot = PublishedSnapshotV2(_sentinel=psv._SNAPSHOT_SENTINEL_V2, binding=binding, receipt=receipt)
    for name in ("binding", "receipt", "committed_dir_fd"):
        with pytest.raises(AttributeError):
            setattr(snapshot, name, None)


# -- duplicate occurrence (IMPLEMENTATION_ADJUDICATION §6) ---------------------------------


def test_skipped_duplicate_occurrence_is_not_reacquired_even_as_a_symlink(tmp_path: Path, monkeypatch) -> None:
    """First-wins (§10): a later physical occurrence of an already-copied pack
    is charged as an entry and NOT opened, even if it is a symlink pointing
    outside A. `SkippedDuplicateOccurrence -> no filesystem-type claim`."""
    src = tmp_path / "src"
    primary = src / "repo" / ".git" / "objects"
    alternate = src / "alt" / "objects"
    base = "pack-" + "3" * 40
    for store in (primary, alternate):
        (store / "pack").mkdir(parents=True)
        (store / "info").mkdir(exist_ok=True)
    (primary / "pack" / (base + ".pack")).write_bytes(b"PACK")
    (primary / "pack" / (base + ".idx")).write_bytes(b"IDX")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.pack").write_bytes(b"outside")
    (alternate / "pack" / (base + ".pack")).symlink_to(outside / "x.pack")
    (alternate / "pack" / (base + ".idx")).symlink_to(outside / "x.pack")
    (primary / "info" / "alternates").write_text(str(alternate) + "\n")
    inotify = _Inotify()
    inotify.watch(outside)
    inotify.drain()
    try:
        with _audited(monkeypatch) as audit:
            outcome = _publish([src], src / "repo", tmp_path / "pub")
        events = inotify.drain()
    finally:
        inotify.close()
    snapshot = _complete(outcome)
    snapshot.close()
    opened_duplicates = [c for c in audit.calls if c.name == "open" and str(c.args[0]).startswith(base) and "alt" in str(c.where or "")]
    assert opened_duplicates == []
    assert events == []


def test_seal_bypass_through_the_mro_is_still_refused_at_admission(tmp_path: Path) -> None:
    """Review F-C: `class X(Quiet, SealedType)` escapes the definition-time
    seal (defense in depth only). The load-bearing checks are admission by
    exact type (W, A) and the minted-snapshot registry (Complete)."""
    forged_w_type = type("ForgedW", (_QuietInitSubclass, SnapshotPublicationRootV2), {})
    victim = _victim_dir(tmp_path)
    forged = object.__new__(forged_w_type)
    forged.__dict__.update(vars(_unminted_publication_root(victim)))
    assert _forged_w_outcome(tmp_path, forged) == {
        "refused": psv.PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2, "victim_written": False,
    }
    forged_snapshot_type = type("ForgedSnapshot", (_QuietInitSubclass, PublishedSnapshotV2), {})
    with pytest.raises(TypeError):
        CompletePublicationV2(snapshot=object.__new__(forged_snapshot_type))


# -- review F-A / F-B: the discriminators reject the shapes they previously accepted ---------------

_ENSURE_DIR_REGISTERED_FIRST = """slot = _FdSlotV2()
self._dirs[relpath] = slot
self._manifest[relpath] = ("dir", relpath, _DIR_MODE_V2, 0, "")
try:
    os.mkdir(name, _STAGING_DIR_MODE_V2, dir_fd=parent_fd)
    slot.fd = os.open(name, _DIR_OPEN_FLAGS_V2, dir_fd=parent_fd)  # fd-install"""
_ENSURE_DIR_REGISTERED_IN_TARGET = """slot = _FdSlotV2()
self._manifest[relpath] = ("dir", relpath, _DIR_MODE_V2, 0, "")
try:
    os.mkdir(name, _STAGING_DIR_MODE_V2, dir_fd=parent_fd)
    self._dirs.setdefault(relpath, slot).fd = os.open(name, _DIR_OPEN_FLAGS_V2, dir_fd=parent_fd)  # fd-install"""


def _setdefault_mutant(indent: str, text: str) -> str:
    old = textwrap.indent(_ENSURE_DIR_REGISTERED_FIRST, indent)
    assert old in text
    return text.replace(old, textwrap.indent(_ENSURE_DIR_REGISTERED_IN_TARGET, indent), 1)


def test_mutation_allocating_owner_expression_on_a_marked_line_is_killed_by_the_census() -> None:
    """Review F-A mutant: the registration moves INTO the marked statement's
    target (`self._dirs.setdefault(...).fd = os.open(...)`), which Python
    evaluates after the syscall."""
    mutated = _setdefault_mutant(" " * 8, Path(_PSV_FILE).read_text())
    assert any("not installed into a pre-existing owner slot" in v for v in _acquisition_census(mutated))


def test_mutation_allocating_owner_expression_on_a_marked_line_is_killed_by_the_fault_sweep(
    tmp_path: Path, monkeypatch
) -> None:
    """The same F-A mutant under the sweep: a fault at the `setdefault` call
    AFTER the open is not a tolerated window opcode, so the orphaned staging
    descriptor is reported."""
    method = textwrap.dedent(inspect.getsource(psv._StagingWriterV2.ensure_dir))
    mutant_source = _setdefault_mutant(" " * 4, method)
    monkeypatch.setattr(psv._StagingWriterV2, "ensure_dir", _compile_mutant(mutant_source))
    violations = _mutant_sweep(
        tmp_path, monkeypatch, _SweepScenario("complete", CompletePublicationV2), mutant_source, {"ensure_dir"},
    )
    assert any("leaked" in v and "'CALL'" in v and _MUTANT_FILE in v for v in violations), violations


@pytest.mark.parametrize(
    "snippet",
    [
        "fd = fcntl.fcntl(x, fcntl.F_DUPFD, 0)",
        "fd = fcntl.fcntl(x, F_DUPFD_CLOEXEC, 0)",
        "fd = open('x')",
        "r, w = os.pipe2(0)",
        "fd = os.pidfd_open(1)",
        "import socket",
        "import os as _os",
        "from os import open as o",
        "fd = getattr(os, 'open')('x', 0)",
        # N2 families observed on 0672d16:
        "_os = os\nfd = _os.open('x', 0)",
        "_open = os.open\nfd = _open('x', 0)",
        "fd = sys.modules['os'].open('x', 0)",
        "fd = vars(os)['open']('x', 0)",
        "libc = ctypes.CDLL(None)\n_LIBC_V2 = libc\nfd = _LIBC_V2.openat(-100, b'x', 0)",
        "fd = ctypes.CDLL(None).open(b'x', 0)",
        "opener = fcntl.fcntl\nfd = opener(x, fcntl.F_DUPFD_CLOEXEC, 0)",
        "fd = [os.open][0]('x', 0)",
    ],
)
def test_static_census_is_an_allow_list(snippet: str) -> None:
    """Review F-B: every descriptor source outside the vetted set fails."""
    assert _acquisition_census(snippet + "\n") != []


# -- review F-D / F-E ------------------------------------------------------------------------


def _two_interruptions(tmp_path: Path, monkeypatch) -> dict[str, object]:
    """First interruption after the commit point; a second one during settle."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)

    def interrupted_post_commit(run):
        raise KeyboardInterrupt("first, after the rename")

    real_settle = psv._PublicationRunV2.settle
    state = {"armed": True}

    def interrupted_settle(self):
        if state["armed"]:
            state["armed"] = False
            raise KeyboardInterrupt("second, during cleanup")
        return real_settle(self)

    monkeypatch.setattr(psv, "_post_commit_v2", interrupted_post_commit)
    monkeypatch.setattr(psv._PublicationRunV2, "settle", interrupted_settle)
    capability = AuthorizedGitStorageSetV2.from_roots([str(tmp_path / "src")])
    root = _publication_root(tmp_path / "pub")
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            psv.publish_physical_snapshot_v2(
                source_authority=capability,
                source_locator=SourceRepositoryLocatorV2.absolute(str(repo)),
                object_format=SHA1,
                physical_budget=_budget(),
                publication_root=root,
            )
    finally:
        capability.close()
        root.close()
    carried = getattr(caught.value, "physical_snapshot_outcome", None)
    facts = {"committed": len(_committed_entries(tmp_path / "pub")), "carried": type(carried).__name__}
    _release_outcome(carried)
    _release_outcome(getattr(caught.value.__context__, "physical_snapshot_outcome", None))
    return facts


def test_commit_interruption_then_one_cleanup_interruption_carries_the_outcome(tmp_path: Path, monkeypatch) -> None:
    """An EXERCISED asynchronous path (first interruption at the commit
    point, one during cleanup). Not a universal claim: see the N3 boundary
    counterexample below (MULTIPLE_ASYNC_INTERRUPTION_DURING_CLEANUP)."""
    assert _two_interruptions(tmp_path, monkeypatch) == {"committed": 1, "carried": "UnconfirmedPublicationV2"}


def test_mutation_outcome_carried_only_when_returned_is_killed(tmp_path: Path, monkeypatch) -> None:
    """Mutant (the ad1d696 handler): attach only a RETURNED outcome."""
    source = textwrap.dedent(inspect.getsource(psv.publish_physical_snapshot_v2))
    old = "            known = outcome if outcome is not None else carried\n"
    assert old in source
    mutant = _compile_mutant(source.replace(old, "            known = outcome\n", 1))
    monkeypatch.setattr(psv, "publish_physical_snapshot_v2", mutant)
    assert _two_interruptions(tmp_path, monkeypatch)["carried"] == "NoneType"


def _abort_answers_with_persistent_residue(tmp_path: Path, monkeypatch) -> list[bool]:
    staging = tmp_path / "staging"
    staging.mkdir()
    slot = psv._FdSlotV2()
    slot.fd = os.open(str(staging), os.O_RDONLY | os.O_DIRECTORY)
    writer = psv._StagingWriterV2(slot, "a" * 32)
    writer.create_stage()
    writer.open_stage()

    class NoRmdir:
        def __getattr__(self, name):
            return getattr(os, name)

        def rmdir(self, *args, **kwargs):
            raise OSError(errno.EBUSY, "injected")

    monkeypatch.setattr(psv, "os", NoRmdir())
    answers = [writer.abort(), writer.abort()]
    monkeypatch.setattr(psv, "os", os)
    answers.append(writer.abort())
    answers.append((staging / ("a" * 32)).exists())
    slot.close_quietly()
    return answers


def test_abort_never_reports_a_stale_false_after_residue(tmp_path: Path, monkeypatch) -> None:
    """Review F-E: residue stays reported until it is really removed."""
    assert _abort_answers_with_persistent_residue(tmp_path, monkeypatch) == [True, True, False, False]



# ================================================================================
# Final qualification round (maintainer adjudication of N1/N2/N3 on 0672d16)
# ================================================================================

_RELEASE_WITH_BOOKKEEPING = """def close_quietly(self):
    fd = self.fd
    if fd is None:
        return
    try:
        self.fd = None  # fd-release
        _RELEASED_LOG_V2.append(fd)  # fd-release
        os.close(fd)  # fd-release
    except OSError:
        pass
"""


def test_mutation_n1_allocating_call_on_a_release_marked_statement_is_killed_by_the_census() -> None:
    """N1: `MarkerPresent != MarkerSemanticsSatisfied`. An allocating call
    between detach and close, carrying `fd-release`, is not an admitted
    window statement class."""
    source = Path(_PSV_FILE).read_text()
    old = "            self.fd = None  # fd-release\n            os.close(fd)  # fd-release\n        except OSError:\n            pass\n"
    assert source.count(old) == 1
    mutated = source.replace(
        old,
        "            self.fd = None  # fd-release\n            _RELEASED_LOG_V2.append(fd)  # fd-release\n"
        "            os.close(fd)  # fd-release\n        except OSError:\n            pass\n",
        1,
    )
    assert any("not part of an admitted ownership transition" in v for v in _acquisition_census(mutated))


def test_mutation_n1_allocating_call_on_a_release_marked_statement_is_killed_by_the_fault_sweep(
    tmp_path: Path, monkeypatch
) -> None:
    """The same N1 mutant under the sweep: the fault at the `append` call
    after the detach is on an `invalid` marked statement, so the orphaned
    descriptor is reported (the 0672d16 tolerance accepted it)."""
    monkeypatch.setattr(psv, "_RELEASED_LOG_V2", [], raising=False)
    monkeypatch.setattr(psv._FdSlotV2, "close_quietly", _compile_mutant(_RELEASE_WITH_BOOKKEEPING))
    assert _transition_lines(_RELEASE_WITH_BOOKKEEPING) == {}
    violations = _mutant_sweep(
        tmp_path, monkeypatch, _SweepScenario("complete", CompletePublicationV2), _RELEASE_WITH_BOOKKEEPING,
        {"close_quietly"},
    )
    assert any("leaked" in v and _MUTANT_FILE in v and "'CALL'" in v for v in violations), violations


def test_n1_transition_model_recognises_exactly_the_admitted_transitions() -> None:
    """Anti-vacuity of the transition model: each admitted transition is
    recognised; the same statements in any other arrangement are not."""
    source = textwrap.dedent(
        """
        def f(self, slot, owner, fd):
            slot.fd = os.open('x', 0)  # fd-install
            os.mkdir('d')
            self.created = True  # fd-install
            self.fd = None  # fd-install
            owner.fd = fd  # fd-install
            try:
                self.fd = None  # fd-release
                os.close(fd)  # fd-release
            except OSError:
                pass
            self._roots_released += 1  # fd-release
            os.close(fd)  # fd-release
            self.fd = None  # fd-release
            log.append(fd); os.close(fd)  # fd-release
            self.fd = None  # fd-release
            if log.append(fd) is None: os.close(fd)  # fd-release
            self._dirs.setdefault(k, slot).fd = os.open('x', 0)  # fd-install

        class _SourceSessionV2:
            def _release_roots(self, fd):
                self._roots_released += 1  # fd-release
                os.close(fd)  # fd-release
        """
    )
    assert _transition_lines(source) == {
        3: "acquire", 5: "created", 6: "transfer", 7: "transfer", 9: "release", 10: "release",
        23: "roots", 24: "roots",
    }
    assert _marker_violations(source) == [
        f"line {n}: marked statement is not part of an admitted ownership transition" for n in (13, 14, 15, 16, 17, 18, 19)
    ]


_OUTSIDE_STATIC_CENSUS_EXAMPLES = [
    # Reflection through the type system: not a canonical S1-A source form and
    # not detected by the census (PYTHON_DYNAMIC_INDIRECTION_OUTSIDE_STATIC_CENSUS).
    "io_type = [c for c in object.__subclasses__() if c.__name__ == 'FileIO'][0]\nfd = io_type('x').fileno()\n",
]


@pytest.mark.parametrize("snippet", _OUTSIDE_STATIC_CENSUS_EXAMPLES)
def test_n2_dynamic_indirection_outside_the_static_census_is_declared_not_claimed(snippet: str) -> None:
    """N2: `StaticCensusCanonicalCoverage != CompleteSemanticCoverageOfDynamicPython`.
    Such a form passes the census -- it is OUTSIDE its completeness claim,
    by declaration -- and every such form carries a marker the exact
    production source is asserted not to contain."""
    assert _acquisition_census(snippet) == []
    assert any(marker in snippet for marker in _OUTSIDE_STATIC_CENSUS_MARKERS)
    assert not any(marker in Path(_PSV_FILE).read_text() for marker in _OUTSIDE_STATIC_CENSUS_MARKERS)


def test_n3_returned_outcome_then_two_cleanup_interruptions_is_outside_the_qualified_fault_model(
    tmp_path: Path, monkeypatch
) -> None:
    """N3 boundary counterexample, preserved: a Complete outcome is RETURNED,
    then two asynchronous interruptions hit cleanup (1st and 3rd session
    closes). The escaping exception carries no outcome while the snapshot is
    committed. MULTIPLE_ASYNC_INTERRUPTION_DURING_CLEANUP is OUTSIDE the
    qualified fault model: nothing is inferred from this path, and no
    universal assertion in this suite contradicts it."""
    repo = _make_repo(tmp_path / "src" / "repo", files=1)
    real_close = psv._SourceSessionV2.close_quietly
    calls = {"n": 0}

    def interrupted_close(self):
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise KeyboardInterrupt(f"async interruption #{calls['n']} during cleanup")
        return real_close(self)

    monkeypatch.setattr(psv._SourceSessionV2, "close_quietly", interrupted_close)
    capability = AuthorizedGitStorageSetV2.from_roots([str(tmp_path / "src")])
    root = _publication_root(tmp_path / "pub")
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            psv.publish_physical_snapshot_v2(
                source_authority=capability,
                source_locator=SourceRepositoryLocatorV2.absolute(str(repo)),
                object_format=SHA1,
                physical_budget=_budget(),
                publication_root=root,
            )
        chain, exc = [], caught.value
        while exc is not None:
            chain.append(getattr(exc, "physical_snapshot_outcome", None))
            exc = exc.__context__
        facts = {"committed": len(_committed_entries(tmp_path / "pub")), "carried": chain, "calls": calls["n"]}
        del caught, exc
        gc.collect()  # the lost Complete outcome is released by its owner's finalizer
    finally:
        capability.close()
        root.close()
    assert facts == {"committed": 1, "carried": [None, None], "calls": 4}, "OUTSIDE_QUALIFIED_FAULT_MODEL"


# -- N1 closure witnesses: every variant fails by the intended discriminator ---------------

_CLOSE_QUIETLY_PREFIX = """def close_quietly(self{extra}):
    fd = self.fd
    if fd is None:
        return
    try:
        self.fd = None  # fd-release
"""
_N1_VARIANTS = {
    "semicolon": ("", "        _RELEASED_LOG_V2.append(fd); os.close(fd)  # fd-release\n"),
    "one-line-if": ("", "        if _RELEASED_LOG_V2.append(fd) is None: os.close(fd)  # fd-release\n"),
    "unrelated-counter": (
        ", counter=_ROOTS_COUNTER_V2",
        "        counter._roots_released += 1  # fd-release\n        os.close(fd)  # fd-release\n",
    ),
    "allocating-call": ("", "        _RELEASED_LOG_V2.append(fd)  # fd-release\n        os.close(fd)  # fd-release\n"),
}


def _n1_variant(name: str) -> str:
    extra, body = _N1_VARIANTS[name]
    return _CLOSE_QUIETLY_PREFIX.format(extra=extra) + body + "    except OSError:\n        pass\n"


@pytest.mark.parametrize("variant", sorted(_N1_VARIANTS))
def test_n1_variants_are_rejected_by_the_census(variant: str) -> None:
    """Static side: none of the variants is an admitted transition, and the
    marked lines they carry are violations."""
    source = _n1_variant(variant)
    assert _transition_lines(source) == {}
    violations = _acquisition_census(source)
    assert any("not part of an admitted ownership transition" in v for v in violations), violations


@pytest.mark.parametrize("variant", sorted(_N1_VARIANTS))
def test_n1_variants_are_killed_by_the_bytecode_window_sweep(tmp_path: Path, monkeypatch, variant: str) -> None:
    """Dynamic side: installed as `_FdSlotV2.close_quietly`, each variant
    leaves a descriptor unowned at a fault site that NO ownership transition
    covers (the 3ff700a line model tolerated the semicolon, one-line-if and
    counter forms)."""
    monkeypatch.setattr(psv, "_RELEASED_LOG_V2", [], raising=False)
    monkeypatch.setattr(psv, "_ROOTS_COUNTER_V2", type("Counter", (), {"_roots_released": 0})(), raising=False)
    source = _n1_variant(variant)
    monkeypatch.setattr(psv._FdSlotV2, "close_quietly", _compile_mutant(source))
    violations = _mutant_sweep(
        tmp_path, monkeypatch, _SweepScenario("complete", CompletePublicationV2), source, {"close_quietly"},
    )
    assert any("leaked" in v and _MUTANT_FILE in v for v in violations), violations


def test_n1_same_spelling_counter_outside_release_roots_is_red() -> None:
    """`self._roots_released += 1; os.close(fd)` is a transition ONLY in the
    exact `_SourceSessionV2._release_roots` context -- statically (AST
    context) and dynamically (code qualname)."""
    real = textwrap.dedent(inspect.getsource(psv._SourceSessionV2._release_roots))
    elsewhere = real.replace("def _release_roots(self)", "def release_other(self)", 1)
    assert _transition_lines(elsewhere) == {}
    assert any("not part of an admitted ownership transition" in v for v in _acquisition_census(elsewhere))
    assert _window_offsets(psv._SourceSessionV2._release_roots.__code__)
    moved = _compile_mutant(real)  # same source, but no longer the method's own code object
    assert moved.__code__.co_qualname == "_release_roots"
    assert _window_offsets(moved.__code__) == frozenset()


# -- N2 closure witness ----------------------------------------------------------------------

_RAW_SYSCALL_MUTANT = """

def _raw_open_v2(path):
    libc = _LIBC_V2
    return libc.syscall(ctypes.c_long(2), ctypes.c_char_p(path), ctypes.c_long(0))
"""


def test_n2_direct_raw_syscall_outside_the_wrapper_is_rejected() -> None:
    """Codex 4137344841: a direct `.syscall` outside `_syscall_v2` is refused by
    the canonical-source guard, and by that rule only. The exact source obeys
    the rule (`StaticCanonicalSourceGuard != UniversalSemanticSolverForPython`)."""
    source = Path(_PSV_FILE).read_text()
    assert _acquisition_census(source) == []
    violations = _acquisition_census(source + _RAW_SYSCALL_MUTANT)
    assert violations and all(f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v for v in violations), violations


# -- N2 final closure (maintainer grant on 1f37fbb; Codex 4137971087) ------------------------

_N2_STRUCTURAL_ESCAPES = {
    "nested-same-name": (
        "\n\ndef _outer_v2():\n    def _syscall_v2():\n"
        "        return _LIBC_V2.syscall(ctypes.c_long(2), ctypes.c_char_p(b'x'), ctypes.c_long(0))\n"
        "    return _syscall_v2()\n"
    ),
    "method-same-name": (
        "\n\nclass _RawV2:\n    def _syscall_v2(self):\n"
        "        return self.libc.syscall(ctypes.c_long(2), ctypes.c_char_p(b'x'), ctypes.c_long(0))\n"
    ),
    "attribute-held-handle": (
        "\n\nclass _HeldV2:\n    def other(self):\n"
        "        return self.libc.syscall(ctypes.c_long(2), ctypes.c_char_p(b'x'), ctypes.c_long(0))\n"
    ),
    "arbitrary-receiver": "\n\ndef _other_v2(x):\n    return x.syscall(ctypes.c_long(2), 0, 0)\n",
    "lambda-inside-wrapper-name": (
        "\n\ndef _outer2_v2():\n    _syscall_v2 = lambda: _LIBC_V2.syscall(ctypes.c_long(2), 0, 0)\n"
        "    return _syscall_v2()\n"
    ),
}


@pytest.mark.parametrize("shape", sorted(_N2_STRUCTURAL_ESCAPES))
def test_n2_explicit_syscall_outside_the_unique_top_level_wrapper_is_rejected(shape: str) -> None:
    """`<receiver>.syscall(...)` is admitted ONLY with the unique top-level
    `_syscall_v2` as its nearest scope, whatever the receiver and whatever
    the name of the enclosing function (the 1f37fbb census accepted the
    same-name and attribute-held forms)."""
    source = Path(_PSV_FILE).read_text()
    violations = _acquisition_census(source + _N2_STRUCTURAL_ESCAPES[shape])
    assert violations and all(f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v for v in violations), violations


def test_n2_ctypes_cdll_outside_the_unique_top_level_wrapper_is_rejected() -> None:
    source = Path(_PSV_FILE).read_text()
    for escape in (
        "\n\ndef _other_v2():\n    return ctypes.CDLL(None)\n",
        "\n\nclass _H2:\n    def _syscall_v2(self):\n        self.libc = ctypes.CDLL(None)\n",
        "\n\n_MODULE_LIBC_V2 = ctypes.CDLL(None)\n",
    ):
        violations = _acquisition_census(source + escape)
        assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations), violations


def test_n2_positive_control_the_real_top_level_wrapper_is_accepted_by_context_not_name() -> None:
    """Positive control: the exact source's unique module-level `_syscall_v2`
    (which both creates the handle and calls `.syscall`) is accepted. The
    discrimination is structural: the SAME wrapper body under another
    top-level name is rejected, and a second top-level `_syscall_v2` makes
    the wrapper non-unique."""
    source = Path(_PSV_FILE).read_text()
    assert _acquisition_census(source) == []
    wrapper = textwrap.dedent(inspect.getsource(psv._syscall_v2))
    assert ".syscall(" in wrapper and "ctypes.CDLL(" in wrapper
    renamed = wrapper.replace("def _syscall_v2(", "def _syscall_other_v2(", 1)
    violations = _acquisition_census(source + "\n\n" + renamed)
    assert any(f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v for v in violations)
    assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations)
    duplicated = _acquisition_census(source + "\n\n" + wrapper)
    assert any("is not the unique top-level wrapper" in v for v in duplicated), duplicated


# -- N2 execution-region model (maintainer adjudication of the 2a46728 STOP) ----------------

_WRAPPER_HEADER = "def _syscall_v2(name: str, *args: object) -> tuple[int, int]:"
#: The four counterexamples reproduced on 2a46728, plus a keyword-only default:
#: each puts an explicit `.syscall` / `ctypes.CDLL` on the wrapper's HEADER,
#: which Python evaluates in the enclosing scope when `def` runs.
_N2_HEADER_ESCAPES = {
    "default-syscall": "def _syscall_v2(name: str, *args: object, _p=_LIBC_V2.syscall(2, b'x', 0)) -> tuple[int, int]:",
    "default-walrus-cdll-syscall": (
        "def _syscall_v2(name: str, *args: object, _h=(_RAW := ctypes.CDLL(None)), "
        "_fd=_RAW.syscall(ctypes.c_long(2), ctypes.c_char_p(b'x'), ctypes.c_long(0))) -> tuple[int, int]:"
    ),
    "decorator-syscall": "@_LIBC_V2.syscall\n" + _WRAPPER_HEADER,
    "decorator-arbitrary-receiver": "@x.syscall\n" + _WRAPPER_HEADER,
    "kw-default-syscall": "def _syscall_v2(name: str, *args: object, _k=x.syscall(2)) -> tuple[int, int]:",
}


def _with_wrapper_header(header: str) -> str:
    source = Path(_PSV_FILE).read_text()
    assert source.count(_WRAPPER_HEADER) == 1
    return source.replace(_WRAPPER_HEADER, header, 1)


@pytest.mark.parametrize("shape", sorted(_N2_HEADER_ESCAPES))
def test_n2_explicit_forms_on_the_wrapper_header_are_outside_its_runtime_region(shape: str) -> None:
    """`WrapperRuntimeRegion != DescendantOfFunctionDef`: the N2 region
    predicate ITSELF rejects each header form (`raw syscall outside`), in
    addition to the canonical header guard. The 2a46728 census accepted the
    four reproduced forms (`[]`)."""
    violations = _acquisition_census(_with_wrapper_header(_N2_HEADER_ESCAPES[shape]))
    assert any(f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v for v in violations), violations
    if "cdll" in shape:
        assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations), violations


def test_n2_same_content_inside_the_real_wrapper_body_is_the_positive_control() -> None:
    """Positive control: the very expressions of the header escapes, placed
    as statements in the real wrapper's BODY, are inside the region."""
    body = (
        _WRAPPER_HEADER
        + "\n    _p = _LIBC_V2.syscall(2, b'x', 0)"
        + "\n    raw = ctypes.CDLL(None)"
        + "\n    _fd = raw.syscall(ctypes.c_long(2), ctypes.c_char_p(b'x'), ctypes.c_long(0))"
    )
    assert _acquisition_census(_with_wrapper_header(body)) == []


@pytest.mark.parametrize(
    "header, message",
    [
        ("@_identity_v2\n" + _WRAPPER_HEADER, "decorators forbidden"),
        ("def _syscall_v2(name: str = 'renameat2', *args: object) -> tuple[int, int]:", "positional defaults forbidden"),
        ("def _syscall_v2(name: str, *args: object, flag: int = 0) -> tuple[int, int]:", "keyword defaults with values forbidden"),
        ("async def _syscall_v2(name: str, *args: object) -> tuple[int, int]:", "must be a plain FunctionDef"),
    ],
)
def test_n2_canonical_wrapper_header_guard_fails_closed(header: str, message: str) -> None:
    """Even a HARMLESS decorator/default is refused: the grammar change must
    be re-adjudicated, not silently widen N2."""
    violations = _acquisition_census(_with_wrapper_header(header))
    assert any(message in v for v in violations), violations


def test_n2_walrus_cdll_handle_is_outside_the_canonical_grammar() -> None:
    """The reviewer's observation on 2a46728, closed as a finite language
    restriction (no data-flow analysis): a handle created by walrus -- even
    INSIDE the wrapper body -- is not the canonical `name = ctypes.CDLL(...)`
    form and is refused; so are attribute-held handles."""
    for statement in (
        "\n    global _W\n    (_W := ctypes.CDLL(None))",
        "\n    holder.libc = ctypes.CDLL(None)",
    ):
        violations = _acquisition_census(_with_wrapper_header(_WRAPPER_HEADER + statement))
        assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations), violations


# -- N2 StaticCapabilityReferenceClosure (maintainer STOP/REDESIGN after 58ef4ca) ------------
#
# `CallSyntaxClosure != CapabilityReferenceClosure`. The census no longer asks
# "does this call look like a CDLL construction?" but "is EVERY explicit static
# reference to the capability in an admitted canonical position?" -- the
# discipline `.syscall` already follows (every `Attribute(attr="syscall")`).

def _psv_plus(fragment: str) -> str:
    return Path(_PSV_FILE).read_text() + fragment


_CDLL_REFERENCE_ESCAPES = {
    # region escapes on the real wrapper's header
    "wrapper-decorator": ("header", "@ctypes.CDLL\n" + _WRAPPER_HEADER),
    "wrapper-positional-default": ("header", "def _syscall_v2(name: str = ctypes.CDLL(None), *args: object) -> tuple[int, int]:"),
    "wrapper-keyword-default": ("header", "def _syscall_v2(name: str, *args: object, _k=ctypes.CDLL(None)) -> tuple[int, int]:"),
    # capability-reference carriers anywhere else
    "decorator": ("append", "\n\n@ctypes.CDLL\ndef _probe_v2():\n    pass\n"),
    "stacked-decorator": ("append", "\n\n@print\n@ctypes.CDLL\ndef _probe_v2():\n    pass\n"),
    "alias": ("append", "\n\nF = ctypes.CDLL\n"),
    "return": ("append", "\n\ndef _probe_v2():\n    return ctypes.CDLL\n"),
    "container": ("append", "\n\n_X = [ctypes.CDLL]\n"),
    "walrus-module": ("append", "\n\n(_libc := ctypes.CDLL(None))\n"),
    "walrus-in-wrapper-body": ("header", _WRAPPER_HEADER + "\n    (libc := ctypes.CDLL(None))"),
    "module-level-construction": ("append", "\n\n_H = ctypes.CDLL(None)\n"),
    "call-argument": ("append", "\n\n_foo_v2(ctypes.CDLL)\n"),
    "class-attribute": ("append", "\n\nclass _X_v2:\n    factory = ctypes.CDLL\n"),
    "nested-scope-in-wrapper": ("header", _WRAPPER_HEADER + "\n    def _inner():\n        libc = ctypes.CDLL(None)"),
}


def _escape_source(kind: str, text: str) -> str:
    return _with_wrapper_header(text) if kind == "header" else _psv_plus(text)


@pytest.mark.parametrize("shape", sorted(_CDLL_REFERENCE_ESCAPES))
def test_n2_every_non_canonical_cdll_reference_is_rejected(shape: str) -> None:
    kind, text = _CDLL_REFERENCE_ESCAPES[shape]
    violations = _acquisition_census(_escape_source(kind, text))
    assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations), violations


def test_n2_cdll_in_an_evaluated_annotation_is_rejected() -> None:
    """The annotation exception depends on `from __future__ import
    annotations`: without it the same annotation is evaluated at runtime."""
    source = Path(_PSV_FILE).read_text()
    assert source.count("from __future__ import annotations\n") == 1
    evaluated = source.replace("from __future__ import annotations\n", "", 1)
    violations = _acquisition_census(evaluated)
    assert any("ctypes.CDLL reference outside the capability-reference closure" in v for v in violations), violations


def test_n2_annotation_exception_does_not_cover_a_sibling_executable_reference() -> None:
    violations = _acquisition_census(_psv_plus("\n\n_Y: ctypes.CDLL = ctypes.CDLL(None)\n"))
    lines = [v for v in violations if "ctypes.CDLL reference outside the capability-reference closure" in v]
    assert len(lines) == 1, violations  # the value reference, not the annotation


@pytest.mark.parametrize(
    "fragment",
    [
        "\n\nfrom ctypes import CDLL\nH = CDLL(None)\n",
        "\n\nfrom ctypes import CDLL as X\n",
        "\n\nfrom ctypes import c_long\n",
        "\n\nfrom ctypes.util import find_library\n",
    ],
)
def test_n2_from_import_of_ctypes_is_rejected_at_the_import(fragment: str) -> None:
    violations = _acquisition_census(_psv_plus(fragment))
    assert any("from-import of ctypes forbidden" in v for v in violations), violations


def test_n2_reference_completeness_invariant_on_the_exact_source() -> None:
    """Every explicit `ctypes.CDLL` reference receives exactly one of the
    three dispositions; on the exact source: one canonical runtime
    construction, only non-evaluated annotations otherwise, no violation.
    Positive controls: the real wrapper (`libc = ctypes.CDLL(...)`,
    `libc.syscall(...)`) and `_LIBC_V2: ctypes.CDLL | None = None`."""
    source = Path(_PSV_FILE).read_text()
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    references = [n for n in ast.walk(tree) if _is_cdll_reference(n)]
    dispositions = _cdll_reference_dispositions(tree, parent)
    assert len(dispositions) == len(references) >= 2
    assert all(d in _CDLL_DISPOSITIONS for _, d in dispositions)
    assert [d for _, d in dispositions].count("CANONICAL_RUNTIME_CONSTRUCTION") == 1
    assert {d for _, d in dispositions} == {"CANONICAL_RUNTIME_CONSTRUCTION", "POSTPONED_ANNOTATION"}
    assert _classification_partition(source)["ok"]
    assert _acquisition_census(source) == []
    assert "libc.syscall(" in textwrap.dedent(inspect.getsource(psv._syscall_v2))


def test_n2_module_level_syscall_reference_is_rejected() -> None:
    violations = _acquisition_census(_psv_plus("\n\n_P = _LIBC_V2.syscall(2, b'x', 0)\n"))
    assert any(f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v for v in violations), violations


# -- N2 anti-vacuity: one mutant per property, killed by the fact it protects -------------------

_N2_PROPERTY_MUTANTS = {
    # M-CDLL-REF: the reference closure off -> alias / decorator references survive
    "cdll_reference_closure": [
        _psv_plus("\n\nF = ctypes.CDLL\n"),
        _psv_plus("\n\n@ctypes.CDLL\ndef _probe_v2():\n    pass\n"),
    ],
    # M-IMPORTFROM: the ImportFrom(ctypes) ban off -> the import escape survives
    "importfrom_ctypes_forbidden": [_psv_plus("\n\nfrom ctypes import CDLL\nH = CDLL(None)\n")],
    # M-RUNTIME-REGION: region widened to any descendant of W -> a canonical
    # carrier in a nested scope, and a `.syscall` default, survive
    "runtime_region_is_body_only": [
        _with_wrapper_header(_WRAPPER_HEADER + "\n    def _inner():\n        libc = ctypes.CDLL(None)"),
        _with_wrapper_header(_N2_HEADER_ESCAPES["default-syscall"]),
    ],
    # M-CANONICAL-CARRIER: any CDLL call in the region accepted -> walrus survives
    "canonical_carrier_required": [_with_wrapper_header(_WRAPPER_HEADER + "\n    (libc := ctypes.CDLL(None))")],
    # M4 annotation collapse: any child of an annotated statement counts as
    # annotation -> the executable VALUE reference beside a postponed
    # annotation survives
    "annotation_slot_only": [_psv_plus("\n\n_Y: ctypes.CDLL = ctypes.CDLL(None)\n")],
}
_N2_PROPERTY_FACTS = {
    "cdll_reference_closure": "ctypes.CDLL reference outside the capability-reference closure",
    "importfrom_ctypes_forbidden": "from-import of ctypes forbidden",
    "runtime_region_is_body_only": None,  # either region fact (CDLL or .syscall)
    "canonical_carrier_required": "ctypes.CDLL reference outside the capability-reference closure",
    "annotation_slot_only": "ctypes.CDLL reference outside the capability-reference closure",
}


def _region_facts(violations: list[str]) -> list[str]:
    return [
        v for v in violations
        if "ctypes.CDLL reference outside the capability-reference closure" in v
        or f"raw syscall outside {_RAW_SYSCALL_WRAPPER}" in v
    ]


@pytest.mark.parametrize("switch", sorted(_N2_PROPERTY_MUTANTS))
def test_n2_property_mutants_are_killed_by_the_intended_fact(switch: str, monkeypatch) -> None:
    fact = _N2_PROPERTY_FACTS[switch]
    for witness in _N2_PROPERTY_MUTANTS[switch]:
        real = _acquisition_census(witness)
        real_facts = _region_facts(real) if fact is None else [v for v in real if fact in v]
        assert real_facts, (switch, real)
        monkeypatch.setitem(_N2_SWITCHES, switch, False)
        mutant = _acquisition_census(witness)
        monkeypatch.setitem(_N2_SWITCHES, switch, True)
        mutant_facts = _region_facts(mutant) if fact is None else [v for v in mutant if fact in v]
        assert mutant_facts == [], (switch, mutant)  # the witness survives only because of the property



# -- N2 classification totality (maintainer adjudication, reviewer protocol §19) ---------------


def _classification_partition(source: str) -> dict[str, object]:
    """Every explicit `ctypes.CDLL` reference appears in EXACTLY one bucket:
    |refs| == |CANONICAL| + |POSTPONED| + |VIOLATION|, buckets pairwise
    disjoint, their union is the reference set (nothing invisible)."""
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    references = {id(n) for n in ast.walk(tree) if _is_cdll_reference(n)}
    buckets: dict[str, set[int]] = {label: set() for label in _CDLL_DISPOSITIONS}
    classified: list[int] = []
    for reference, disposition in _cdll_reference_dispositions(tree, parent):
        buckets[disposition].add(id(reference))
        classified.append(id(reference))
    labels = list(buckets)
    disjoint = all(not (buckets[a] & buckets[b]) for i, a in enumerate(labels) for b in labels[i + 1 :])
    total = set().union(*buckets.values()) == references
    once = len(classified) == len(set(classified)) == len(references)
    sizes = {label: len(ids) for label, ids in buckets.items()}
    return {"ok": disjoint and total and once and sum(sizes.values()) == len(references), "sizes": sizes, "refs": len(references)}


def test_n2_cdll_classification_is_total_and_disjoint_over_the_whole_corpus() -> None:
    """Totality over the exact source PLUS every historical and new CDLL
    witness at once: no reference is invisible, none is classified twice."""
    corpus = Path(_PSV_FILE).read_text()
    for kind, text in _CDLL_REFERENCE_ESCAPES.values():
        if kind == "append":
            corpus += text
    corpus += "\n\n_Y: ctypes.CDLL = ctypes.CDLL(None)\n"
    partition = _classification_partition(corpus)
    assert partition["ok"], partition
    sizes = partition["sizes"]
    assert sizes["CANONICAL_RUNTIME_CONSTRUCTION"] == 1
    assert sizes["POSTPONED_ANNOTATION"] == 2  # _LIBC_V2 and _Y annotations
    assert sizes["VIOLATION"] == partition["refs"] - 3 >= 9


def test_n2_classification_totality_discriminator_is_not_vacuous(monkeypatch) -> None:
    """If the disposition function silently skipped a reference (e.g. a new
    carrier it forgot), the partition check must fail."""
    real = _cdll_reference_dispositions

    def forgetful(tree, parent):
        return [(ref, d) for ref, d in real(tree, parent) if d != "VIOLATION"][:-1] or []

    monkeypatch.setattr(sys.modules[__name__], "_cdll_reference_dispositions", forgetful)
    assert _classification_partition(_psv_plus("\n\nF = ctypes.CDLL\n"))["ok"] is False
