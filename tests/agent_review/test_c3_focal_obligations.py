"""C3 focal obligations A/B/C (PR #349): bounded symlink read, spool/process
ownership, and no pathname fallback in the legacy wrapper."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent_review import git_commit_subject_v2 as mod
from app.agent_review.bounded_git_v2 import BoundedGitError
from app.agent_review.git_commit_subject_v2 import (
    BoundedBlobCarrierV2,
    MaterialisationWorkspaceCapabilityV2,
    SUBJECT_TREE_UNREADABLE_REASON_V2,
    SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    SubjectMaterialisationError,
    acquire_materialised_commit_subject_v2,
    materialise_commit_subject_v2,
    read_commit_blobs_v2,
)
from tests.agent_review.test_git_commit_subject_v2 import _commit_all, _init_repo


def _git(repo: Path, *argv: str, data: bytes | None = None) -> str:
    return subprocess.run(
        ["git", *argv], cwd=repo, input=data, check=True, capture_output=True
    ).stdout.decode().strip()


def _commit_entries(repo: Path, entries: list[tuple[str, str, bytes]]) -> str:
    """Commit (mode, name, content) leaves without touching the worktree."""
    lines = ""
    for mode, name, content in entries:
        oid = _git(repo, "hash-object", "-w", "--stdin", data=content)
        lines += f"{mode} blob {oid}\t{name}\n"
    tree = _git(repo, "mktree", data=lines.encode())
    return _git(repo, "commit-tree", tree, "-m", "c")


def _workspace(pool: Path) -> MaterialisationWorkspaceCapabilityV2:
    pool.mkdir(exist_ok=True)
    fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return MaterialisationWorkspaceCapabilityV2(fd, pool)
    finally:
        os.close(fd)


def _epochs(pool: Path) -> list[str]:
    """Epoch roots in `pool`. `create_epoch_root` names them `c3_<uuid4 hex>`."""
    return [p.name for p in pool.iterdir() if p.name.startswith("c3_")]


def test_epoch_oracle_is_not_vacuous(tmp_path: Path):
    """The zero-epoch oracle must be able to fail: positive control (nothing
    created), synthetic entry, and a real epoch created by production code."""
    import uuid

    pool = tmp_path / "pool"
    pool.mkdir()
    assert _epochs(pool) == []  # positive control
    synthetic = f"c3_{uuid.uuid4().hex}"
    (pool / synthetic).mkdir()
    assert _epochs(pool) == [synthetic]  # causal negative control
    (pool / synthetic).rmdir()

    repo = tmp_path / "repo"
    _init_repo(repo)
    commit = _commit_entries(repo, [("100644", "f", b"x")])
    with _workspace(pool) as workspace:
        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
        )
        try:
            assert _epochs(pool) == [subject.root_name]  # real production epoch
        finally:
            subject.close()
        assert _epochs(pool) == []


# ---------------------------------------------------------------- A


def test_carrier_size_of_and_read_bounded_reads_only_within_limit():
    spool = tempfile.TemporaryFile(mode="w+b")
    spool.write(b"x" * 10)
    carrier = BoundedBlobCarrierV2(spool, {"a": (0, 10)}, ["a"])
    try:
        reads = []
        real_read = spool.read
        with patch.object(spool, "read", side_effect=lambda n=-1: reads.append(n) or real_read(n)):
            assert carrier.size_of("a") == 10
            assert reads == []  # metadata only
            with pytest.raises(SubjectMaterialisationError) as exc:
                carrier.read_bounded("a", 9)
            assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
            assert reads == []  # refused before any read
            assert carrier.read_bounded("a", 10) == b"x" * 10
            assert reads == [10]  # exactly the admitted size
    finally:
        carrier.close()


def test_overlong_symlink_refused_before_any_payload_read_and_before_epoch(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    commit = _commit_entries(repo, [("120000", "link", b"a" * 4096)])
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        with patch.object(BoundedBlobCarrierV2, "__getitem__", side_effect=AssertionError("full read")), \
             patch.object(BoundedBlobCarrierV2, "read_bounded", side_effect=AssertionError("payload read")):
            with pytest.raises(SubjectMaterialisationError) as exc:
                acquire_materialised_commit_subject_v2(
                    repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
                )
        assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
        assert _epochs(pool) == []


@pytest.mark.parametrize(
    "target,admitted",
    [(b"", False), (b"a\x00b", False), (b"a", True), (b"b" * 4095, True), (b"c" * 4096, False)],
)
def test_symlink_target_boundaries(tmp_path: Path, target: bytes, admitted: bool):
    repo = tmp_path / "repo"
    _init_repo(repo)
    commit = _commit_entries(repo, [("120000", "link", target)])
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        if admitted:
            subject = acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            )
            with subject:
                assert os.readlink("link", dir_fd=subject.root_fd) == target.decode()
        else:
            with pytest.raises(SubjectMaterialisationError) as exc:
                acquire_materialised_commit_subject_v2(
                    repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
                )
            assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
            assert _epochs(pool) == []


def test_same_blob_regular_file_streams_but_symlink_is_refused(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    blob = b"z" * 4096
    as_file = _commit_entries(repo, [("100644", "f", blob)])
    as_link = _commit_entries(repo, [("120000", "f", blob)])
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=as_file, workspace=workspace, authorized_storage=[repo]
        ) as subject:
            fd = os.open("f", os.O_RDONLY, dir_fd=subject.root_fd)
            try:
                assert os.read(fd, 8192) == blob
            finally:
                os.close(fd)
        with pytest.raises(SubjectMaterialisationError):
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=as_link, workspace=workspace, authorized_storage=[repo]
            )


# ---------------------------------------------------------------- B


class _Tracker:
    """Records every spool and subprocess acquired inside read_commit_blobs_v2."""

    def __init__(self) -> None:
        self.spools: list = []
        self.procs: list = []

    def __enter__(self):
        real_tmp = tempfile.TemporaryFile
        real_open = mod.open_bounded_git_subprocess_v2

        def tmp(*a, **k):
            f = real_tmp(*a, **k)
            self.spools.append(f)
            return f

        def opn(*a, **k):
            p = real_open(*a, **k)
            self.procs.append(p)
            return p

        self._patches = [
            patch.object(mod.tempfile, "TemporaryFile", side_effect=tmp),
            patch.object(mod, "open_bounded_git_subprocess_v2", side_effect=opn),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()

    def assert_all_released(self, *, spool_open_ok: bool = False) -> None:
        for p in self.procs:
            assert p.poll() is not None, "git process left running"
            for pipe in (p.stdin, p.stdout, p.stderr):
                assert pipe is None or pipe.closed, "pipe left open"
        if not spool_open_ok:
            for s in self.spools:
                assert s.closed, "spool left open"


def _blob_entries(repo: Path) -> list:
    (repo / "a.txt").write_text("alpha")
    (repo / "b.txt").write_text("beta")
    head = _commit_all(repo, "two blobs")
    root_tree = _git(repo, "rev-parse", f"{head}^{{tree}}")
    _t, _all, leaves = mod._build_canonical_trie_hierarchical(
        repo_root=repo, root_tree_oid=root_tree, max_component_len=255,
        max_expanded_entries=mod.MAX_EXPANDED_ENTRIES_V2,
    )
    return leaves


def test_spool_creation_failure_is_typed_and_launches_nothing(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    with _Tracker() as t:
        with patch.object(mod.tempfile, "TemporaryFile", side_effect=OSError(24, "EMFILE")):
            with pytest.raises(SubjectMaterialisationError) as exc:
                read_commit_blobs_v2(repo_root=repo, entries=leaves)
        assert exc.value.reason_code == SUBJECT_TREE_UNREADABLE_REASON_V2
        assert t.procs == []


def test_process_launch_failure_releases_spool(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    with _Tracker() as t:
        with patch.object(mod, "open_bounded_git_subprocess_v2", side_effect=BoundedGitError("x")):
            with pytest.raises(SubjectMaterialisationError) as exc:
                read_commit_blobs_v2(repo_root=repo, entries=leaves)
        assert exc.value.reason_code == SUBJECT_TREE_UNREADABLE_REASON_V2
        assert len(t.spools) == 1
        t.assert_all_released()


@pytest.mark.parametrize("exc_type", [OSError, KeyboardInterrupt, SystemExit])
def test_spool_write_failure_releases_process_and_spool(tmp_path: Path, exc_type):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    real_tmp = tempfile.TemporaryFile

    class _FailingSpool:
        def __init__(self, inner):
            self._inner = inner

        def write(self, _b):
            raise exc_type(5, "EIO") if exc_type is OSError else exc_type()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    with _Tracker() as t:
        wrapped: list = []

        def tmp(*a, **k):
            f = _FailingSpool(real_tmp(*a, **k))
            wrapped.append(f)
            return f

        with patch.object(mod.tempfile, "TemporaryFile", side_effect=tmp):
            expected = SubjectMaterialisationError if exc_type is OSError else exc_type
            with pytest.raises(expected):
                read_commit_blobs_v2(repo_root=repo, entries=leaves)
        assert len(t.procs) == 1 and t.procs[0].poll() is not None
        assert all(w._inner.closed for w in wrapped)


def test_truncated_protocol_releases_everything(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    from app.agent_review.git_commit_subject_v2 import TreeEntryV2

    bogus = TreeEntryV2(path="ghost", mode="100644", object_type="blob", object_id="0" * 40) \
        if hasattr(mod, "TreeEntryV2") else None
    assert bogus is not None
    with _Tracker() as t:
        with pytest.raises(SubjectMaterialisationError):
            read_commit_blobs_v2(repo_root=repo, entries=leaves + [bogus])
        t.assert_all_released()


def test_carrier_construction_failure_releases_everything(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    with _Tracker() as t:
        with patch.object(mod, "BoundedBlobCarrierV2", side_effect=RuntimeError("ctor")):
            with pytest.raises(RuntimeError):
                read_commit_blobs_v2(repo_root=repo, entries=leaves)
        t.assert_all_released()


def test_success_transfers_only_the_spool_and_close_is_repeatable(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    leaves = _blob_entries(repo)
    with _Tracker() as t:
        carrier = read_commit_blobs_v2(repo_root=repo, entries=leaves)
        try:
            t.assert_all_released(spool_open_ok=True)
            assert not t.spools[0].closed
            assert carrier["a.txt"] == b"alpha"
        finally:
            carrier.close()
            carrier.close()
        assert t.spools[0].closed


def test_no_blobs_returns_empty_carrier(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    with read_commit_blobs_v2(repo_root=repo, entries=[]) as carrier:
        assert len(carrier) == 0


# ---------------------------------------------------------------- C


def test_legacy_projection_is_descriptor_relative_and_never_pathname_fallback(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_text("one")
    head = _commit_all(repo, "one")
    orig = os.rename
    calls: list[dict] = []

    def spy(src, dst, *args, **kwargs):
        calls.append(kwargs)
        if "src_dir_fd" not in kwargs or "dst_dir_fd" not in kwargs:
            raise AssertionError("pathname rename attempted")
        return orig(src, dst, *args, **kwargs)

    dest = tmp_path / "dest"
    with patch("os.rename", side_effect=spy):
        materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
    assert (dest / "f1.txt").read_text() == "one"
    assert calls and all("src_dir_fd" in k and "dst_dir_fd" in k for k in calls)


def test_legacy_projection_primitive_incompatibility_does_not_retry_by_pathname(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f1.txt").write_text("one")
    head = _commit_all(repo, "one")
    calls: list[tuple] = []

    def two_arg_only(src, dst):  # legacy mock signature: rejects dir_fd kwargs with TypeError
        calls.append((src, dst))

    dest = tmp_path / "dest"
    with patch("os.rename", side_effect=two_arg_only):
        with pytest.raises(BaseException):
            materialise_commit_subject_v2(repo_root=repo, ref=head, destination=dest)
    # A second, pathname-style attempt would have been recorded with absolute paths.
    assert calls == []
    assert not (dest / "f1.txt").exists()


# ---------------------------------------------------------------- D

_INHERITANCE_CHILD = r"""
import json, os, stat, sys
from pathlib import Path
os.umask(int(sys.argv[3], 8))
from app.agent_review.git_commit_subject_v2 import materialise_commit_subject_v2
parent, repo, head = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[4]
legacy, native = parent / "legacy", parent / "native"
materialise_commit_subject_v2(repo_root=repo, ref=head, destination=legacy)
def wf(p, data):
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666); os.write(fd, data); os.close(fd)
os.mkdir(native, 0o777)
wf(native / "a.txt", b"a")
os.mkdir(native / "d1", 0o777); os.mkdir(native / "d1" / "d2", 0o777)
wf(native / "d1" / "d2" / "n.txt", b"n")
def snap(root):
    out = {}
    for p in [root] + sorted(root.rglob("*")):
        st = os.lstat(p)
        try: acl = os.getxattr(p, "system.posix_acl_access").hex()
        except OSError: acl = None
        out[str(p.relative_to(root))] = [oct(stat.S_IMODE(st.st_mode)), st.st_gid, acl]
    return out
print(json.dumps([snap(legacy), snap(native)]))
"""


def _default_acl_with_named_user() -> bytes:
    import struct

    entries = [(1, 7, 0xFFFFFFFF), (2, 7, 3000), (4, 5, 0xFFFFFFFF), (0x10, 7, 0xFFFFFFFF), (0x20, 0, 0xFFFFFFFF)]
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in entries)


@pytest.mark.parametrize("umask", ["022", "077"])
def test_legacy_projection_matches_native_creation_under_default_acl(tmp_path: Path, umask: str):
    """Under a default ACL the projection must equal an ordinary create: umask is
    not applied and the ACL mask must not be rewritten by a later chmod."""
    parent = tmp_path / "parent"
    parent.mkdir()
    try:
        os.setxattr(parent, "system.posix_acl_default", _default_acl_with_named_user())
    except OSError:
        pytest.skip("default POSIX ACLs unsupported on this filesystem")
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("a")
    (repo / "d1" / "d2").mkdir(parents=True)
    (repo / "d1" / "d2" / "n.txt").write_text("n")
    head = _commit_all(repo, "tree")
    result = subprocess.run(
        [__import__("sys").executable, "-c", _INHERITANCE_CHILD, str(parent), str(repo), umask, head],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    import json

    legacy, native = json.loads(result.stdout)
    assert legacy == native


def test_default_acl_with_restrictive_owner_entry_keeps_new_dir_traversable(tmp_path: Path):
    """The owner rwx repair stays: a default ACL whose owner entry lacks rwx must
    not leave a copied directory unwritable."""
    import struct

    entries = [(1, 5, 0xFFFFFFFF), (4, 7, 0xFFFFFFFF), (0x10, 7, 0xFFFFFFFF), (0x20, 7, 0xFFFFFFFF)]
    acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in entries)
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f").write_text("x")
    dst.mkdir()
    try:
        os.setxattr(dst, "system.posix_acl_default", acl)
    except OSError:
        pytest.skip("default POSIX ACLs unsupported on this filesystem")
    sfd = os.open(src, os.O_RDONLY | os.O_DIRECTORY)
    dfd = os.open(dst, os.O_RDONLY | os.O_DIRECTORY)
    try:
        mod._copy_entry_descriptor_relative("d", sfd, dfd)
    finally:
        os.close(sfd)
        os.close(dfd)
    assert os.stat(dst / "d").st_mode & 0o700 == 0o700
    assert (dst / "d" / "f").read_text() == "x"


_GID_CHILD = r"""
import json, os, stat, sys
from pathlib import Path
gid = int(sys.argv[3])
os.setgroups([gid])  # the destination GID is a supplementary group, not the effective GID
os.umask(0o022)
from app.agent_review.git_commit_subject_v2 import materialise_commit_subject_v2
parent, repo, head = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[4]
legacy, native = parent / "legacy", parent / "native"
materialise_commit_subject_v2(repo_root=repo, ref=head, destination=legacy)
def wf(p, data):
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666); os.write(fd, data); os.close(fd)
wf(native / "a.txt", b"a")
os.mkdir(native / "d1", 0o777); wf(native / "d1" / "n.txt", b"n")
os.mkdir(native / "d1" / "d2", 0o777); wf(native / "d1" / "d2" / "m.txt", b"m")
def snap(root):
    return {str(p.relative_to(root)): [os.lstat(p).st_gid, bool(os.lstat(p).st_mode & stat.S_ISGID)]
            for p in sorted(root.rglob("*"))}
print(json.dumps([snap(legacy), snap(native)]))
"""


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to set a supplementary group and chown the destination")
@pytest.mark.parametrize("setgid", [False, True])
def test_projected_gid_matches_native_creation_in_existing_destination(tmp_path: Path, setgid: bool):
    """Only setgid directories give children the directory's GID; merely being a
    member of the destination's group must not reassign it (native control)."""
    import json
    import sys

    dest_gid = 2000
    parent = tmp_path / "parent"
    parent.mkdir()
    os.chmod(parent, 0o755)
    for name in ("legacy", "native"):
        d = parent / name
        d.mkdir()
        os.chown(d, 0, dest_gid)
        os.chmod(d, 0o2775 if setgid else 0o775)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("a")
    (repo / "d1" / "d2").mkdir(parents=True)
    (repo / "d1" / "n.txt").write_text("n")
    (repo / "d1" / "d2" / "m.txt").write_text("m")
    head = _commit_all(repo, "tree")
    result = subprocess.run(
        [sys.executable, "-c", _GID_CHILD, str(parent), str(repo), str(dest_gid), head],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    legacy, native = json.loads(result.stdout)
    assert legacy == native
    expected_gid = dest_gid if setgid else 0
    assert {gid for gid, _ in native.values()} == {expected_gid}  # control really differs by setgid


# ---------------------------------------------------------------- C3-L retryable cleanup


def _open_fds() -> int:
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


def _fd_is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def _subject_with_files(tmp_path: Path, pool: Path, workspace):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("a")
    (repo / "d1").mkdir()
    (repo / "d1" / "b.txt").write_text("b")
    head = _commit_all(repo, "tree")
    return acquire_materialised_commit_subject_v2(
        repo_root=repo, ref=head, workspace=workspace, authorized_storage=[repo]
    )


class _CustomControl(BaseException):
    pass


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit, _CustomControl])
def test_interrupted_cleanup_keeps_retry_ownership_then_completes(tmp_path: Path, exc_type):
    real = mod._fd_rmtree
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        subject = _subject_with_files(tmp_path, pool, workspace)
        root_fd, pool_fd, root_name = subject.root_fd, subject.pool_fd, subject.root_name
        baseline_fds = _open_fds()

        def partial_then_interrupt(dir_fd):
            os.unlink("a.txt", dir_fd=dir_fd)  # partial progress, then process control
            raise exc_type()

        with patch.object(mod, "_fd_rmtree", side_effect=partial_then_interrupt):
            with pytest.raises(exc_type):  # propagated unchanged
                subject.close()

        # ownership needed for retry is still valid; nothing was released or poisoned
        assert not _fd_is_closed(root_fd) and not _fd_is_closed(pool_fd)
        assert subject.root_fd == root_fd and subject.pool_fd == pool_fd
        assert _epochs(pool) == [root_name]
        assert _open_fds() == baseline_fds

        subject.close()  # retry reaches the terminal state
        assert _epochs(pool) == []
        assert _fd_is_closed(root_fd) and _fd_is_closed(pool_fd)
        assert subject.root_fd == -1 and subject.pool_fd == -1
        after = _open_fds()
        subject.close()  # exactly-once release: further closes are no-ops
        assert _open_fds() == after
        assert mod._fd_rmtree is real


def test_ordinary_cleanup_success_and_ordinary_failure_still_release_authority(tmp_path: Path):
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        ok = _subject_with_files(tmp_path, pool, workspace)
        fds = (ok.root_fd, ok.pool_fd)
        ok.close()
        assert _epochs(pool) == [] and all(_fd_is_closed(f) for f in fds)

    # ordinary Exception: FilesystemDeletionFailure != AuthorityHandleLeak (declared semantics)
    pool2 = tmp_path / "pool2"
    with _workspace(pool2) as workspace:
        repo = tmp_path / "repo"
        head = _git(repo, "rev-parse", "HEAD")
        subject = acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=head, workspace=workspace, authorized_storage=[repo]
        )
        fds = (subject.root_fd, subject.pool_fd)
        with patch.object(mod, "_fd_rmtree", side_effect=OSError(5, "EIO")):
            subject.close()  # ordinary failure does not propagate
        assert all(_fd_is_closed(f) for f in fds)
        subject.close()


# ---------------------------------------------------------------- C3-S raw path bytes


_RAW_BYTES_CHILD = r"""
import json, os, sys
from pathlib import Path
from app.agent_review.git_commit_subject_v2 import (
    MaterialisationWorkspaceCapabilityV2, SubjectMaterialisationError,
    acquire_materialised_commit_subject_v2,
)
repo, pool, ref = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY)
ws = MaterialisationWorkspaceCapabilityV2(fd, pool); os.close(fd)
out = {"fsenc": sys.getfilesystemencoding()}
try:
    with ws:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=ref, workspace=ws, authorized_storage=[repo]
        ) as subject:
            root = os.fsencode(pool / subject.root_name)
            out["names"] = sorted(n.hex() for n in os.listdir(root))
            out["target"] = os.readlink(os.path.join(root, b"lnk")).hex() if b"lnk" in os.listdir(root) else None
except SubjectMaterialisationError as exc:
    out["refused"] = exc.reason_code
print(json.dumps(out))
"""


def _run_raw_bytes_child(tmp_path: Path, commit: str, repo: Path, pool: Path) -> dict:
    import json
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "LC_ALL", "LANG", "PYTHONCOERCECLOCALE")}
    env.update({"PYTHONUTF8": "0", "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"})
    result = subprocess.run(
        [sys.executable, "-c", _RAW_BYTES_CHILD, str(repo), str(pool), commit],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_raw_git_name_and_symlink_target_bytes_survive_a_non_utf8_filesystem_encoding(tmp_path: Path):
    """GitRawPathBytes are the identity authority: under an ascii filesystem encoding a
    UTF-8 spelled name (and symlink target) must reach the filesystem as the same bytes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    blob = _git(repo, "hash-object", "-w", "--stdin", data=b"x")
    tgt = _git(repo, "hash-object", "-w", "--stdin", data=b"t\xc3\xa9")
    tree = _git(
        repo, "mktree", "-z",
        data=(f"100644 blob {blob}\t".encode() + b"\xc3\xa9\0" + f"120000 blob {tgt}\t".encode() + b"lnk\0"),
    )
    commit = _git(repo, "commit-tree", tree, "-m", "c")
    pool = tmp_path / "pool"
    pool.mkdir()
    out = _run_raw_bytes_child(tmp_path, commit, repo, pool)
    if out["fsenc"].lower() not in ("ascii", "ansi_x3.4-1968"):
        pytest.skip(f"could not force a non-UTF-8 filesystem encoding (got {out['fsenc']})")
    assert "refused" not in out, out  # representable commit must not be refused
    assert out["names"] == sorted([b"\xc3\xa9".hex(), b"lnk".hex()])
    assert out["target"] == b"t\xc3\xa9".hex()


def test_non_utf8_raw_name_round_trips_on_the_default_encoding(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    blob = _git(repo, "hash-object", "-w", "--stdin", data=b"x")
    tree = _git(repo, "mktree", "-z", data=f"100644 blob {blob}\t".encode() + b"\xff\xfe\0")
    commit = _git(repo, "commit-tree", tree, "-m", "c")
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
        ) as subject:
            assert os.listdir(os.fsencode(pool / subject.root_name)) == [b"\xff\xfe"]


def test_leaf_listing_keeps_raw_name_bytes(tmp_path: Path):
    """`list_commit_tree_entries_v2` feeds path identity to consumers: the returned
    str must map back to the committed raw bytes (fsdecode/fsencode inverse)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    blob = _git(repo, "hash-object", "-w", "--stdin", data=b"x")
    tree = _git(repo, "mktree", "-z", data=f"100644 blob {blob}\t".encode() + b"\xff\xc3\xa9\0")
    commit = _git(repo, "commit-tree", tree, "-m", "c")
    (entry,) = mod.list_commit_tree_entries_v2(repo_root=repo, commit_sha=commit)
    assert os.fsencode(entry.path) == b"\xff\xc3\xa9"


# ---------------------------------------------------------------- C3-R CM-C3-02 name semantics

from app.agent_review import target_pack_epoch_v2 as _name_authority


def _alias_commit(repo: Path, names: list[bytes]) -> str:
    _init_repo(repo)
    lines = b""
    for i, name in enumerate(names):
        oid = _git(repo, "hash-object", "-w", "--stdin", data=f"c{i}".encode())
        lines += f"100644 blob {oid}\t".encode() + name + b"\0"
    tree = _git(repo, "mktree", "-z", data=lines)
    return _git(repo, "commit-tree", tree, "-m", "c")


def _workspace_semantics(pool: Path) -> str:
    pool.mkdir(exist_ok=True)
    fd = os.open(pool, os.O_RDONLY | os.O_DIRECTORY)
    try:
        real = os.readlink(f"/proc/self/fd/{fd}")
        return _name_authority.classify_directory_name_semantics_v2(
            _name_authority.MountTopologySnapshotV2.observe(), real, probe_path=f"/proc/self/fd/{fd}"
        )
    finally:
        os.close(fd)


def _require_established_workspace(pool: Path) -> None:
    got = _workspace_semantics(pool)
    if got != _name_authority.NAME_SEMANTICS_ESTABLISHED_CASE_SENSITIVE_V2:
        pytest.skip(
            f"workspace filesystem name semantics are {got}; the success-path alias discriminators "
            "(distinct names stay distinct) are unqualified on this environment"
        )


ALIAS_SETS = [
    [b"file.txt", b"FILE.TXT"],
    ["é".encode(), "é".encode()],  # NFC / NFD spellings
]


@pytest.mark.parametrize("names", ALIAS_SETS)
def test_distinct_names_stay_distinct_on_an_established_case_sensitive_workspace(tmp_path: Path, names):
    pool = tmp_path / "pool"
    _require_established_workspace(pool)
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, names)
    with _workspace(pool) as workspace:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
        ) as subject:
            assert sorted(os.listdir(os.fsencode(pool / subject.root_name))) == sorted(names)


@pytest.mark.parametrize("names", ALIAS_SETS)
@pytest.mark.parametrize("probe,label", [(True, "casefold_active"), (None, "unknown")])
def test_alias_capable_workspace_is_refused_before_any_epoch(tmp_path: Path, monkeypatch, names, probe, label):
    """CM-C3-02: a casefolding (or unestablished) workspace cannot honour distinct Git
    names, so the tree is refused typed BEFORE an epoch exists. Fault injection on the
    single authority's flag read; the real-flag variant is in the mounted-tmpfs test."""
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, names)
    pool = tmp_path / "pool"
    monkeypatch.setattr(_name_authority, "_directory_is_casefolded_v2", lambda _p: probe)
    with _workspace(pool) as workspace:
        with pytest.raises(SubjectMaterialisationError) as exc:
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            )
        assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
        assert _epochs(pool) == []


def test_unknown_filesystem_type_is_refused_before_any_epoch(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a"])
    pool = tmp_path / "pool"
    monkeypatch.setattr(_name_authority, "_name_semantics_capability_v2", lambda _fs: _name_authority._NAME_SEMANTICS_UNKNOWN_V2)
    with _workspace(pool) as workspace:
        with pytest.raises(SubjectMaterialisationError) as exc:
            acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            )
        assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
        assert _epochs(pool) == []


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to mount a casefolding tmpfs; the real-flag CM-C3-02 witness is unqualified without it")
def test_real_casefolded_tmpfs_workspace_is_refused_before_any_epoch(tmp_path: Path):
    holder = tmp_path / "cf"
    holder.mkdir()
    mounted = subprocess.run(["mount", "-t", "tmpfs", "-o", "casefold", "tmpfs", str(holder)], capture_output=True)
    if mounted.returncode != 0:
        pytest.skip("kernel/host refused a casefold tmpfs mount; real-flag witness NOT_TESTED here")
    try:
        pool = holder / "workspace"
        pool.mkdir()
        if subprocess.run(["chattr", "+F", str(pool)], capture_output=True).returncode != 0:
            pytest.skip("chattr +F unavailable; real-flag witness NOT_TESTED here")
        repo = tmp_path / "repo"
        commit = _alias_commit(repo, [b"file.txt", b"FILE.TXT"])
        with _workspace(pool) as workspace:
            with pytest.raises(SubjectMaterialisationError) as exc:
                acquire_materialised_commit_subject_v2(
                    repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
                )
            assert exc.value.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2
            assert _epochs(pool) == []
    finally:
        subprocess.run(["umount", str(holder)], capture_output=True)


# ---------------------------------------------------------------- C3-B zero payload -> zero spool


def _blobless_commit(repo: Path) -> str:
    _init_repo(repo)
    empty_tree = _git(repo, "hash-object", "-t", "tree", "-w", "--stdin", data=b"")
    tree = _git(repo, "mktree", data=f"040000 tree {empty_tree}\tempty_dir\n".encode())
    return _git(repo, "commit-tree", tree, "-m", "c")


def test_blobless_tree_acquires_no_spool_and_survives_spool_exhaustion(tmp_path: Path):
    """PayloadResourceDemand ~ ActualPayloadDomain: no payload -> no TemporaryFile/fd."""
    repo = tmp_path / "repo"
    commit = _blobless_commit(repo)
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        with patch.object(mod.tempfile, "TemporaryFile", side_effect=OSError(24, "EMFILE")) as spool:
            with acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            ) as subject:
                assert os.listdir(os.fsencode(pool / subject.root_name)) == [b"empty_dir"]
            assert spool.call_count == 0


def test_empty_carrier_is_resource_free_and_close_is_idempotent():
    baseline = _open_fds()
    with patch.object(mod.tempfile, "TemporaryFile", side_effect=OSError(24, "EMFILE")):
        carrier = BoundedBlobCarrierV2.empty()
    assert _open_fds() == baseline
    assert len(carrier) == 0 and list(carrier) == [] and "x" not in carrier
    for call in (lambda: carrier["x"], lambda: carrier.size_of("x"), lambda: carrier.read_bounded("x", 1)):
        with pytest.raises(KeyError):
            call()
    carrier.close()
    carrier.close()
    with pytest.raises(ValueError):
        carrier.size_of("x")


def test_nonempty_tree_still_uses_the_bounded_spool_and_fails_typed_without_one(tmp_path: Path):
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a"])
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        with patch.object(mod.tempfile, "TemporaryFile", side_effect=OSError(24, "EMFILE")):
            with pytest.raises(SubjectMaterialisationError) as exc:
                acquire_materialised_commit_subject_v2(
                    repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
                )
        assert exc.value.reason_code == SUBJECT_TREE_UNREADABLE_REASON_V2
        assert _epochs(pool) == []


# ---------------------------------------------------------------- C3-L internal transactions reach terminal state


def _one_shot_interrupting_rmtree(exc_type):
    """First `_fd_rmtree` makes partial progress then is interrupted; later calls are real."""
    real = mod._fd_rmtree
    state = {"calls": 0}

    def wrapper(dir_fd):
        state["calls"] += 1
        if state["calls"] == 1:
            for name in os.listdir(dir_fd):
                os.unlink(name, dir_fd=dir_fd)  # partial progress
                break
            raise exc_type()
        return real(dir_fd)

    return wrapper, state


def _raised(call) -> BaseException | None:
    """Run `call`; return whatever BaseException escaped (so a leaked process-control
    exception is an assertion failure, not an aborted test session)."""
    try:
        call()
    except BaseException as exc:  # noqa: BLE001 - the point is to observe process control
        return exc
    return None


def _populated_epoch(tmp_path: Path, workspace):
    lease = workspace.pin()
    epoch = mod.MaterialisationEpochV2(lease)
    root_fd, root_name, _dest = epoch.create_epoch_root()
    for name in ("a", "b"):
        os.close(os.open(name, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=root_fd))
    return lease, epoch, root_fd, root_name


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit, _CustomControl])
def test_epoch_rollback_interrupted_by_process_control_reaches_terminal_state(tmp_path: Path, exc_type):
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        lease, epoch, root_fd, root_name = _populated_epoch(tmp_path, workspace)
        pool_fd = lease.pool_fd
        wrapper, _ = _one_shot_interrupting_rmtree(exc_type)
        with patch.object(mod, "_fd_rmtree", side_effect=wrapper):
            raised = _raised(epoch.rollback)
        assert type(raised) is exc_type  # original process-control propagates unchanged
        assert _epochs(pool) == []  # no partial epoch is left behind
        assert _fd_is_closed(root_fd) and _fd_is_closed(pool_fd)  # authority terminally released
        epoch.rollback()  # idempotent: no double close


def test_epoch_rollback_ordinary_exception_and_success_controls(tmp_path: Path):
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        lease, epoch, root_fd, _ = _populated_epoch(tmp_path, workspace)
        epoch.rollback()
        assert _epochs(pool) == [] and _fd_is_closed(root_fd) and _fd_is_closed(lease.pool_fd if lease.pool_fd >= 0 else 10**6)
        lease, epoch, root_fd, _ = _populated_epoch(tmp_path, workspace)
        with patch.object(mod, "_fd_rmtree", side_effect=OSError(5, "EIO")):
            epoch.rollback()  # ordinary deletion failure does not propagate; authority still released
        assert _fd_is_closed(root_fd)


def test_acquisition_failure_with_interrupted_rollback_leaves_no_epoch(tmp_path: Path):
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a", b"b"])
    pool = tmp_path / "pool"
    wrapper, _ = _one_shot_interrupting_rmtree(KeyboardInterrupt)
    with _workspace(pool) as workspace:
        def write_then_fail(_trie, _content, root_fd, _prefix, _written):
            for name in ("p1", "p2"):  # two entries: the interrupted first pass removes only one
                os.close(os.open(name, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=root_fd))
            raise RuntimeError("materialise")

        with patch.object(mod, "_materialise_trie_no_follow", side_effect=write_then_fail), \
             patch.object(mod, "_fd_rmtree", side_effect=wrapper):
            raised = _raised(lambda: acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            ))
        assert type(raised) is KeyboardInterrupt and raised.__traceback__ is not None  # frames alive: no __del__ rescue
        assert _epochs(pool) == []


def test_commit_transfer_interrupted_before_capability_exists_reaches_terminal_state(tmp_path: Path):
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        lease, epoch, root_fd, root_name = _populated_epoch(tmp_path, workspace)
        pool_fd = lease.pool_fd
        wrapper, _ = _one_shot_interrupting_rmtree(KeyboardInterrupt)
        with patch.object(mod, "MaterialisedCommitSubjectCapabilityV2", side_effect=SystemExit(7)), \
             patch.object(mod, "_fd_rmtree", side_effect=wrapper):
            raised = _raised(lambda: epoch.commit(commit_sha="0" * 40, file_count=0, dest_path=pool / root_name))
        assert type(raised) is SystemExit and raised.code == 7  # the interrupt that started the unwind stays the one raised
        assert _epochs(pool) == []
        assert _fd_is_closed(root_fd) and _fd_is_closed(pool_fd)


def test_interrupt_after_commit_with_interrupted_capability_close_leaves_no_epoch(tmp_path: Path):
    """The only window where `acquire` holds a capability it has not yet handed over is
    asynchronous: after `cap = epoch.commit(...)` and before `transferred_to_caller = True`.
    Drive an interrupt exactly there with a line trace, and interrupt the capability's own
    cleanup once; the epoch must still end terminal."""
    import inspect
    import sys

    lines, first = inspect.getsourcelines(mod.acquire_materialised_commit_subject_v2)
    target = first + next(i for i, l in enumerate(lines) if l.strip() == "transferred_to_caller = True")
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a", b"b"])
    pool = tmp_path / "pool"
    wrapper, _ = _one_shot_interrupting_rmtree(KeyboardInterrupt)

    def tracer(frame, event, _arg):
        if frame.f_code is mod.acquire_materialised_commit_subject_v2.__code__:
            def local(frame, event, _arg):
                if event == "line" and frame.f_lineno == target:
                    raise SystemExit(9)
                return local
            return local
        return None

    with _workspace(pool) as workspace:
        with patch.object(mod, "_fd_rmtree", side_effect=wrapper):
            sys.settrace(tracer)
            try:
                raised = _raised(lambda: acquire_materialised_commit_subject_v2(
                    repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
                ))
            finally:
                sys.settrace(None)
        assert isinstance(raised, (SystemExit, KeyboardInterrupt)) and raised.__traceback__ is not None
        assert _epochs(pool) == []


# ---------------------------------------------------------------- C3-X rename eligibility matrix

_MATRIX_CHILD = r"""
import json, os, stat, sys
from pathlib import Path
os.umask(0o022)
from app.agent_review.git_commit_subject_v2 import materialise_commit_subject_v2
parent, repo, head = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
legacy, native = parent / "legacy", parent / "native"
materialise_commit_subject_v2(repo_root=repo, ref=head, destination=legacy)
def wf(p, data):
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666); os.write(fd, data); os.close(fd)
wf(native / "a.txt", b"a")
os.mkdir(native / "d1", 0o777); wf(native / "d1" / "n.txt", b"n")
def xa(p, name):
    try: return os.getxattr(p, name).hex()
    except OSError: return None
def snap(root):
    out = {}
    for p in sorted(root.rglob("*")):
        st = os.lstat(p)
        out[str(p.relative_to(root))] = [oct(stat.S_IMODE(st.st_mode)), st.st_gid,
                                          xa(p, "system.posix_acl_access"), xa(p, "system.posix_acl_default")]
    return out
print(json.dumps([snap(legacy), snap(native)]))
"""


def _acl(*entries) -> bytes:
    import struct

    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", t, p, i) for t, p, i in entries)


_ACL_A = _acl((1, 7, 0xFFFFFFFF), (2, 7, 3000), (4, 5, 0xFFFFFFFF), (0x10, 7, 0xFFFFFFFF), (0x20, 0, 0xFFFFFFFF))
# owner keeps rwx here: the restrictive-owner case is the separate, deliberate u+rwx repair test
_ACL_B = _acl((1, 7, 0xFFFFFFFF), (2, 5, 3001), (4, 5, 0xFFFFFFFF), (0x10, 5, 0xFFFFFFFF), (0x20, 0, 0xFFFFFFFF))
_DEFAULT_ACL = "system.posix_acl_default"

# (id, pool default ACL [what the epoch root inherits], destination default ACL, pool setgid, dest setgid)
INHERITANCE_MATRIX = [
    ("src-none_dest-none", None, None, False, False),
    ("src-none_dest-ACL", None, _ACL_B, False, False),
    ("src-ACL_dest-same-ACL", _ACL_A, _ACL_A, False, False),
    ("src-ACL_dest-none", _ACL_A, None, False, False),
    ("src-ACL-A_dest-ACL-B", _ACL_A, _ACL_B, False, False),
    ("src-setgid_dest-nonsetgid", None, None, True, False),
    ("src-nonsetgid_dest-setgid", None, None, False, True),
]


@pytest.mark.parametrize("case", INHERITANCE_MATRIX, ids=[c[0] for c in INHERITANCE_MATRIX])
def test_projection_matches_native_creation_across_source_and_destination_inheritance(tmp_path: Path, case):
    """RenameEligible iff source inheritance == destination inheritance; otherwise the
    children are created under the destination. Comparator: native creation there."""
    import json
    import sys

    cid, pool_acl, dest_acl, pool_sgid, dest_sgid = case
    needs_root = pool_sgid or dest_sgid
    if needs_root and os.geteuid() != 0:
        pytest.skip("setgid/GID cases need root to chown to a foreign group; GID inheritance is unqualified without it")
    parent = tmp_path / "pool"
    parent.mkdir()
    if pool_acl is not None:
        try:
            os.setxattr(parent, _DEFAULT_ACL, pool_acl)
        except OSError:
            pytest.skip("default POSIX ACLs unsupported on this filesystem; ACL relation unqualified")
    if pool_sgid:
        os.chown(parent, 0, 2000)
        os.chmod(parent, 0o2775)
    for name in ("legacy", "native"):
        d = parent / name
        d.mkdir()
        try:
            os.removexattr(d, _DEFAULT_ACL)  # start from "none", then apply the case's own policy
        except OSError:
            pass
        if dest_acl is not None:
            os.setxattr(d, _DEFAULT_ACL, dest_acl)
        if dest_sgid:
            os.chown(d, 0, 2000)
            os.chmod(d, 0o2775)
        elif pool_sgid:
            os.chown(d, 0, 0)
            os.chmod(d, 0o775)
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.txt").write_text("a")
    (repo / "d1").mkdir()
    (repo / "d1" / "n.txt").write_text("n")
    head = _commit_all(repo, "tree")
    result = subprocess.run(
        [sys.executable, "-c", _MATRIX_CHILD, str(parent), str(repo), head],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    legacy, native = json.loads(result.stdout)
    assert legacy == native


def test_constructor_interrupted_after_taking_descriptors_cannot_double_close_a_recycled_number(tmp_path: Path):
    """BorrowedFDNumber != OperationOwnedLease. If the capability constructor took the
    descriptor numbers and is then interrupted, the half-built object and `commit()`'s
    detached locals must not BOTH own them: once the numbers are recycled, a late
    `__del__` would close somebody else's descriptor."""
    import gc

    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a"])
    pool = tmp_path / "pool"
    orig_init = mod.MaterialisedCommitSubjectCapabilityV2.__init__

    def init_then_interrupt(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        raise KeyboardInterrupt("interrupt right after the constructor took ownership")

    with _workspace(pool) as workspace:
        with patch.object(mod.MaterialisedCommitSubjectCapabilityV2, "__init__", init_then_interrupt):
            raised = _raised(lambda: acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            ))
        assert type(raised) is KeyboardInterrupt
        raised = None  # drop the traceback: it would keep the half-built object alive and hide the finalizer
        assert _epochs(pool) == []
        # recycle every low number, then let any stale finalizer run
        probes = [os.open("/dev/null", os.O_RDONLY) for _ in range(8)]
        try:
            gc.collect()
            assert not any(_fd_is_closed(p) for p in probes), "a stale finalizer closed a recycled descriptor"
        finally:
            for p in probes:
                if not _fd_is_closed(p):
                    os.close(p)


# ------------------------------------------ C3-L propagation precedence at the unwind (finding 4108807657)


def _one_shot_interrupt_instance(instance: BaseException):
    """First `_fd_rmtree` makes partial progress and raises the KNOWN `instance`; later calls are real."""
    real = mod._fd_rmtree
    state = {"calls": 0}

    def wrapper(dir_fd):
        state["calls"] += 1
        if state["calls"] == 1:
            for name in os.listdir(dir_fd):
                os.unlink(name, dir_fd=dir_fd)
                break
            raise instance
        return real(dir_fd)

    return wrapper


def _commit_unwind_outcome(tmp_path: Path, *, original: BaseException, interrupt: BaseException | None):
    """Real epoch, real `commit()`: the capability constructor fails with `original`, and the
    first cleanup pass is interrupted with `interrupt`. Returns (raised, pool, fds, workspace-state)."""
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        lease, epoch, root_fd, root_name = _populated_epoch(tmp_path, workspace)
        pool_fd = lease.pool_fd
        patches = [patch.object(mod, "MaterialisedCommitSubjectCapabilityV2", side_effect=original)]
        if interrupt is not None:
            patches.append(patch.object(mod, "_fd_rmtree", side_effect=_one_shot_interrupt_instance(interrupt)))
        for p in patches:
            p.start()
        try:
            raised = _raised(lambda: epoch.commit(commit_sha="0" * 40, file_count=0, dest_path=pool / root_name))
        finally:
            for p in patches:
                p.stop()
        return raised, _epochs(pool), (_fd_is_closed(root_fd), _fd_is_closed(pool_fd))


PRECEDENCE_ROWS = [
    # id, original, interrupt, expected: "original" | "interrupt"
    ("ordinary_no_control", MemoryError("simulated"), None, "original"),
    ("ordinary_then_KeyboardInterrupt", MemoryError("simulated"), KeyboardInterrupt(), "interrupt"),
    ("ordinary_then_SystemExit", MemoryError("simulated"), SystemExit(42), "interrupt"),
    ("control_then_no_control", SystemExit(7), None, "original"),
    ("control_then_other_control", SystemExit(7), KeyboardInterrupt(), "original"),
]


@pytest.mark.parametrize("row", PRECEDENCE_ROWS, ids=[r[0] for r in PRECEDENCE_ROWS])
def test_commit_unwind_inert_capability_branch_applies_the_precedence_policy(tmp_path: Path, row):
    """Branch A: the capability never took ownership, the unwind owns the descriptors."""
    _rid, original, interrupt, expected = row
    raised, epochs, (root_closed, pool_closed) = _commit_unwind_outcome(tmp_path, original=original, interrupt=interrupt)
    winner = original if expected == "original" else interrupt
    assert raised is winner  # identity of the object, not just its class
    if isinstance(winner, SystemExit):
        assert raised.code == winner.code
    if expected == "interrupt":
        assert raised.__context__ is original  # the original stays available as context
    assert epochs == [] and root_closed and pool_closed


def _owned_capability_outcome(tmp_path: Path, *, original: BaseException, interrupt: BaseException | None):
    """Branch B: ownership has been transferred (`_take_ownership` ran) and THEN `original` arrives."""
    pool = tmp_path / "pool"
    real_take = mod.MaterialisedCommitSubjectCapabilityV2._take_ownership
    seen = {"closes": 0, "owned_at_close": None}
    real_close = mod.MaterialisedCommitSubjectCapabilityV2.close

    def take_then_fail(self):
        real_take(self)
        raise original

    def spy_close(self):
        seen["closes"] += 1
        seen["owned_at_close"] = self._initialized
        return real_close(self)

    with _workspace(pool) as workspace:
        lease, epoch, root_fd, root_name = _populated_epoch(tmp_path, workspace)
        pool_fd = lease.pool_fd
        patches = [
            patch.object(mod.MaterialisedCommitSubjectCapabilityV2, "_take_ownership", take_then_fail),
            patch.object(mod.MaterialisedCommitSubjectCapabilityV2, "close", spy_close),
        ]
        if interrupt is not None:
            patches.append(patch.object(mod, "_fd_rmtree", side_effect=_one_shot_interrupt_instance(interrupt)))
        for p in patches:
            p.start()
        try:
            raised = _raised(lambda: epoch.commit(commit_sha="0" * 40, file_count=0, dest_path=pool / root_name))
        finally:
            for p in patches:
                p.stop()
        return raised, seen, _epochs(pool), (_fd_is_closed(root_fd), _fd_is_closed(pool_fd))


@pytest.mark.parametrize("row", PRECEDENCE_ROWS, ids=[r[0] for r in PRECEDENCE_ROWS])
def test_commit_unwind_owned_capability_branch_applies_the_precedence_policy(tmp_path: Path, row):
    _rid, original, interrupt, expected = row
    raised, seen, epochs, (root_closed, pool_closed) = _owned_capability_outcome(
        tmp_path, original=original, interrupt=interrupt
    )
    assert seen["closes"] >= 1 and seen["owned_at_close"] is True  # the owned-capability branch really ran
    winner = original if expected == "original" else interrupt
    assert raised is winner
    if isinstance(winner, SystemExit):
        assert raised.code == winner.code
    if expected == "interrupt":
        assert raised.__context__ is original
    assert epochs == [] and root_closed and pool_closed


def test_commit_success_path_is_unchanged_and_ownership_is_transferred(tmp_path: Path):
    pool = tmp_path / "pool"
    with _workspace(pool) as workspace:
        lease, epoch, root_fd, root_name = _populated_epoch(tmp_path, workspace)
        cap = epoch.commit(commit_sha="0" * 40, file_count=0, dest_path=pool / root_name)
        assert cap.root_fd == root_fd and not _fd_is_closed(root_fd)  # the capability owns them now
        assert epoch._committed and epoch.root_fd == -1
        cap.close()
        assert _epochs(pool) == [] and _fd_is_closed(root_fd)


def test_acquire_boundary_surfaces_the_interrupt_that_commit_cleanup_produced(tmp_path: Path):
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a", b"b"])
    pool = tmp_path / "pool"
    interrupt = KeyboardInterrupt()
    original = MemoryError("simulated")
    with _workspace(pool) as workspace:
        with patch.object(mod, "MaterialisedCommitSubjectCapabilityV2", side_effect=original), \
             patch.object(mod, "_fd_rmtree", side_effect=_one_shot_interrupt_instance(interrupt)):
            raised = _raised(lambda: acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            ))
        assert raised is interrupt  # no outer layer swallows it again
        assert _epochs(pool) == []


def test_acquire_rollback_does_not_let_a_second_control_replace_the_first(tmp_path: Path):
    """Table row P1/P2 at the acquisition boundary: the body is stopped by P1, the rollback
    cleanup is interrupted by P2; the epoch is cleaned and P1 is what propagates."""
    repo = tmp_path / "repo"
    commit = _alias_commit(repo, [b"a", b"b"])
    pool = tmp_path / "pool"
    first, second = SystemExit(5), KeyboardInterrupt()

    def write_then_stop(_trie, _content, root_fd, _prefix, _written):
        for name in ("p1", "p2"):
            os.close(os.open(name, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=root_fd))
        raise first

    with _workspace(pool) as workspace:
        with patch.object(mod, "_materialise_trie_no_follow", side_effect=write_then_stop), \
             patch.object(mod, "_fd_rmtree", side_effect=_one_shot_interrupt_instance(second)):
            raised = _raised(lambda: acquire_materialised_commit_subject_v2(
                repo_root=repo, ref=commit, workspace=workspace, authorized_storage=[repo]
            ))
        assert raised is first and raised.code == 5
        assert _epochs(pool) == []


def test_retry_bound_is_unchanged_two_attempts():
    assert mod._cleanup_to_terminal_v2.__defaults__ is None and mod._cleanup_to_terminal_v2.__kwdefaults__["attempts"] == 2


def test_precedence_table_of_the_shared_selector():
    sel = mod._control_to_propagate_v2
    ordinary, p1, p2 = RuntimeError("o"), KeyboardInterrupt(), SystemExit(3)
    assert sel(ordinary, None) is None  # ordinary, no control            -> original propagates
    assert sel(ordinary, p2) is p2  # ordinary, control P                  -> P (same object)
    assert sel(p1, None) is None  # control P1, nothing                    -> P1 propagates
    assert sel(p1, p2) is None  # control P1, control P2                    -> P1 kept
    assert sel(None, p2) is p2  # nothing in flight, control P (acquire)   -> P
    assert sel(None, None) is None
