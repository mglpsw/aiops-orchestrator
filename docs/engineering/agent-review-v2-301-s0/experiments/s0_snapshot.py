"""EXPERIMENTAL ONLY -- #301 S0 architecture B: private, remote-less physical snapshot. Not production code.

Stage 1 of the S_G acquisition boundary chosen after architecture A (live-repo git transport) was
rejected in round 4: the object store leaves the live domain BEFORE anything asks for the subject.

  storage capability (roots; host authorization is #331's, not proven here)
    -> repository opened by descriptor, no-follow, component by component      (G1C, reused)
    -> objects/ + flattened alternates copied by descriptor, NO git invocation  (G1C, reused)
    -> hand-authored bare skeleton: no remote, no promisor, no live config;
       object format taken from the EXPECTED commit id, never from the live repo
    -> snapshot identity (traceability receipt, not a trust root)

Differences from G1C's `open_trusted_object_authority_v2`, deliberate and discriminated in
exp_snapshot.py: no `git verify-pack` and no `git rev-parse --git-dir` (S_G's authenticity comes from
hash-on-read of every consumed object, so a forged pack/idx cannot yield a false positive); no refs
copied (the closure is addressed by oid); the skeleton declares the object format.

The snapshot is a same-UID-writable temporary directory: it is an isolated ACQUISITION SOURCE, not
content trusted at read time (N1 stays true; the closure reader re-hashes every object it uses).
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from s0_bootstrap import CaptureRefused

FORMAT_BY_HEXLEN = {40: "sha1", 64: "sha256"}


@dataclasses.dataclass(frozen=True)
class SnapshotBudget:
    """Physical budget: proportional to the object STORE, deliberately separate from the subject budget."""
    max_bytes: int = 256 * 1024 * 1024
    max_entries: int = 200_000
    max_alternate_depth: int = 8


@dataclasses.dataclass(frozen=True)
class SnapshotIdentity:
    """Traceability StorageCapability -> Snapshot -> AcquisitionRecord -> S_G. Not a trust root: S_G's
    authenticity never depends on it (the snapshot stays same-UID writable)."""
    object_format: str
    physical_bytes: int
    physical_entries: int
    object_dirs: int  # 1 + flattened alternates
    receipt_sha256: str  # over (relative path, size, sha256) of every snapshot file, taken at creation
    storage_roots: tuple


def _skeleton(cas: Path, object_format: str) -> None:
    (cas / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (cas / "objects" / "pack").mkdir(parents=True, exist_ok=True)
    (cas / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (cas / "HEAD").write_text("ref: refs/heads/does-not-exist\n")
    if object_format == "sha1":
        cfg = "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"
    else:
        cfg = "[core]\n\trepositoryformatversion = 1\n\tbare = true\n[extensions]\n\tobjectformat = sha256\n"
    (cas / "config").write_text(cfg)


def _receipt(cas: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in (cas / "objects").rglob("*") if p.is_file()):
        data = f.read_bytes()
        h.update(str(f.relative_to(cas)).encode() + b"\0" + str(len(data)).encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


@contextlib.contextmanager
def private_snapshot(repo_root: Path | str, *, expected_commit: str, storage_roots, budget: SnapshotBudget | None = None,
                     parent_dir: str | None = None):
    """Yield (snapshot_repo_path, SnapshotIdentity); the snapshot is removed on exit."""
    from app.agent_review import trusted_object_authority_v2 as g1c

    budget = budget or SnapshotBudget()
    object_format = FORMAT_BY_HEXLEN.get(len(expected_commit))
    if object_format is None:
        raise CaptureRefused("object_format_unsupported", str(len(expected_commit)))
    logical = Path(os.fspath(repo_root))
    if not logical.is_absolute():
        raise CaptureRefused("snapshot_relative_repo_root")
    storage = g1c.AuthorizedGitStorageSetV2.from_roots(list(storage_roots))
    repo_fd = None
    cas = None
    try:
        repo_fd = g1c._open_repo_root_fd_v2(logical)
        if not storage.contains_fd(repo_fd, logical_path=logical):
            raise CaptureRefused("snapshot_storage_unauthorized")
        try:
            proc_path = Path(os.readlink(f"/proc/self/fd/{repo_fd}"))
        except OSError:
            proc_path = logical
        dirs = g1c._resolve_git_directories_fd_v2(repo_root_fd=repo_fd, repo_root_path=proc_path, authorized_storage=storage)
        try:
            g1c._close_ignoring_errors_v2(repo_fd)
            repo_fd = None
            tracker = g1c._ObjectCopyBudgetTrackerV2(g1c._ObjectCopyBudgetV2(
                max_total_bytes=budget.max_bytes, max_object_count=budget.max_entries,
                max_alternate_depth=budget.max_alternate_depth))
            cas = Path(tempfile.mkdtemp(prefix="ar301_s0b_snapshot_", dir=parent_dir))
            _skeleton(cas, object_format)
            visited: set = set()
            objects_fd = g1c._try_open_dir_no_follow_v2(dirs.common_dir_fd, "objects")
            if objects_fd is None:
                raise CaptureRefused("snapshot_objects_absent")
            g1c._copy_objects_dir_fd_v2(source_objects_fd=objects_fd, dest_objects_dir=cas / "objects",
                                        tracker=tracker, visited_dev_ino=visited, depth=0,
                                        budget=tracker._budget, authorized_storage=storage)
        finally:
            for fd in (dirs.common_dir_fd, dirs.git_dir_fd):
                if fd is not None:
                    g1c._close_ignoring_errors_v2(fd)
        ident = SnapshotIdentity(object_format, tracker.total_bytes, tracker.total_objects, len(visited),
                                 _receipt(cas), tuple(str(r) for r in storage_roots))
    except g1c.TrustedObjectAuthorityError as exc:
        if cas is not None:
            shutil.rmtree(cas, ignore_errors=True)
        raise CaptureRefused("snapshot_refused", str(exc)) from exc
    except BaseException:
        if cas is not None:
            shutil.rmtree(cas, ignore_errors=True)
        raise
    finally:
        if repo_fd is not None:
            g1c._close_ignoring_errors_v2(repo_fd)
        storage.close()
    try:
        yield cas, ident
    finally:
        shutil.rmtree(cas, ignore_errors=True)
