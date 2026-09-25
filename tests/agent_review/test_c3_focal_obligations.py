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
