"""`#200-G1` -- materialise committed bytes, and only committed bytes.

Ported from the `#200-F` reconstruction (`operational_subject_v2.py`,
commit `5703e5b` on the frozen-forensic branch
`feat/200-f-derivable-operational-boundary`) **with revalidation**. That
commit's own message records why `ls-tree` + `cat-file` replaced `git
archive`, and the reasoning is preserved below because it is still the
reasoning for this file, not a historical note.

## Why `ls-tree` + `cat-file` and not `git archive`

`git archive` reads the committed tree, which is the important part -- an
uncommitted worktree modification is invisible to it. But it also *applies*
`.gitattributes`: `export-ignore` removes paths from the output and
`export-subst` rewrites their content. For a repository whose
`.gitattributes` is attacker-influenced, that means the repository could
omit its own files from the materialised subject, invisibly.

Enumerating the object list with `ls-tree -r` and fetching bytes with
`cat-file --batch` ignores `.gitattributes` entirely. Several attack vectors
stop being *defended* and become *structurally unreachable*:

`assume-unchanged` / `skip-worktree`
    index bits. A commit's tree has no index, so they cannot participate.
`export-ignore` / `export-subst`
    only consulted by `archive`, which is not used here.
`.gitattributes` filters / textconv
    not consulted when reading raw blobs.

## Source severance

The materialised subject holds bytes, not references. Once written, deleting
or rewriting the repository this was read from cannot change what was
materialised. This is what makes an executed subject reproducible from its
own directory rather than from a checkout that has since moved on.

## What this module does NOT do

It does not decide whether a commit is *authorized* for anything -- that is
a distinct question (see `commit_derived_execution_identity_v2.py`) about
whether a sha is reachable from a trusted ref. This module answers only "what
are commit C's bytes", which is a fact about the object store, not a policy
decision.
"""

from __future__ import annotations

import collections.abc
import contextvars
import errno as _errno
import io
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.agent_review.bounded_git_v2 import (
    BoundedGitError,
    open_bounded_git_subprocess_v2,
    run_bounded_git_v2,
)
from app.agent_review.trusted_object_authority_v2 import (
    AuthorizedGitStorageSetV2,
    TrustedObjectAuthorityError,
    open_trusted_object_authority_v2,
)

__all__ = [
    "SUBJECT_BLOB_MISSING_REASON_V2",
    "SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2",
    "SUBJECT_LEGACY_PATH_UNREPRESENTABLE_REASON_V2",
    "SUBJECT_PATH_COLLISION_REASON_V2",
    "SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2",
    "SUBJECT_TREE_UNREADABLE_REASON_V2",
    "SUBJECT_UNKNOWN_COMMIT_REASON_V2",
    "SUBJECT_UNREPRESENTABLE_TREE_REASON_V2",
    "SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2",
    "SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2",
    "BoundedBlobCarrierV2",
    "MaterialisedCommitSubjectV2",
    "MaterialisationWorkspaceCapabilityV2",
    "MaterialisedCommitSubjectCapabilityV2",
    "OperationWorkspaceLeaseV2",
    "MaterialisationEpochV2",
    "acquire_materialised_commit_subject_v2",
    "SubjectMaterialisationError",
    "TreeEntryV2",
    "compute_subject_digest_v2",
    "list_commit_tree_entries_v2",
    "list_commit_tree_structure_v2",
    "materialise_commit_subject_v2",
    "resolve_commit_v2",
    "resolve_commit_tree_sha_v2",
    "MAX_EXPANDED_ENTRIES_V2",
    "MAX_EXPANDED_BYTES_V2",
]


SUBJECT_UNKNOWN_COMMIT_REASON_V2 = "subject_unknown_commit"
SUBJECT_TREE_UNREADABLE_REASON_V2 = "subject_tree_unreadable"
SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2 = "subject_destination_not_empty"
SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2 = "subject_path_escapes_subject"
SUBJECT_BLOB_MISSING_REASON_V2 = "subject_blob_missing"
SUBJECT_PATH_COLLISION_REASON_V2 = "subject_path_collision"
SUBJECT_UNREPRESENTABLE_TREE_REASON_V2 = "subject_unrepresentable_tree"
SUBJECT_MATERIALISATION_RACE_REASON_V2 = "subject_materialisation_race"
SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2 = "subject_workspace_authority_required"
SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2 = "subject_workspace_authority_closed"
SUBJECT_LEGACY_PATH_UNREPRESENTABLE_REASON_V2 = "subject_legacy_path_unrepresentable"

GITLINK_MODE_V2 = "160000"
SYMLINK_MODE_V2 = "120000"
EXECUTABLE_MODE_V2 = "100755"

MAX_EXPANDED_ENTRIES_V2: int = 100_000
MAX_EXPANDED_BYTES_V2: int = 2 * 1024 * 1024 * 1024  # 2 GiB


class SubjectMaterialisationError(ValueError):
    """A subject could not be materialised from committed bytes."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class TreeEntryV2:
    mode: str
    object_type: str
    object_id: str
    path: str


import contextlib

class MaterialisedCommitSubjectCapabilityV2:
    """A bounded capability object representing a canonical materialization epoch.

    The capability must be used via a context manager to ensure deterministic lifecycle.

    The `root_locator` is provided strictly for backwards-compatible diagnostics and
    logging. It is NON-AUTHORITATIVE. The exact identity of the materialized subject
    is descriptor-bound to `root_fd` and not the mutable `root_locator` Path.
    """
    def __init__(
        self,
        root_fd: int,
        root_locator: Path,
        commit_sha: str,
        file_count: int,
        pool_fd: int,
        root_name: str,
        *,
        owns_pool_fd: bool = True,
    ):
        import os as _os
        import threading as _threading
        self._lock = _threading.Lock()
        self._initialized = False
        self._closed = False
        self.root_fd = -1
        self.pool_fd = -1
        self.root_locator = root_locator
        self.commit_sha = commit_sha
        self.file_count = file_count
        self.root_name = root_name
        if owns_pool_fd:
            assigned_pool_fd = pool_fd
        else:
            assigned_pool_fd = _os.dup(pool_fd)
            _os.set_inheritable(assigned_pool_fd, False)
        self.root_fd = root_fd
        self.pool_fd = assigned_pool_fd
        self._initialized = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        if getattr(self, "_initialized", False):
            try:
                self.close()
            except BaseException:
                pass

    def close(self):
        """Close and clean up the materialised subject capability.

        Linearization point: protected by self._lock. Multiple concurrent calls
        are serialized; exactly the first caller executes the filesystem cleanup
        and descriptor closures. Subsequent calls immediately see self._closed is True
        and return as a no-op.
        """
        import os as _os
        with self._lock:
            if self._closed:
                return
            root_fd = self.root_fd
            pool_fd = self.pool_fd
            root_name = self.root_name

            # Phase 1: remove contents while this capability still owns its
            # descriptors. A process-control BaseException propagates unchanged
            # and leaves the capability open, so a later close() can retry; the
            # released state is only entered once cleanup reached a terminal
            # outcome. An ordinary Exception is a filesystem deletion failure
            # (FilesystemDeletionFailure != AuthorityHandleLeak): descriptors
            # are still released below.
            if root_fd != -1:
                try:
                    _fd_rmtree(root_fd)
                except Exception:
                    pass

            # Phase 2: terminal release, exactly once.
            self._closed = True
            self.root_fd = -1
            self.pool_fd = -1
            self.root_name = None
            try:
                if root_fd != -1:
                    try:
                        _os.close(root_fd)
                    except OSError:
                        pass
            finally:
                try:
                    if pool_fd != -1 and root_name:
                        try:
                            _os.rmdir(root_name, dir_fd=pool_fd)
                        except OSError:
                            pass
                finally:
                    if pool_fd != -1:
                        try:
                            _os.close(pool_fd)
                        except OSError:
                            pass


def _fd_rmtree(dir_fd: int) -> None:
    """Removes all contents of the given directory file descriptor.

    Adjudicates RESOURCE_BOUNDED_AUTHORITY_CLOSURE:
    - Simultaneously-live authority handles (file descriptors) during cleanup are O(1)
      and strictly bounded independently of tree depth (peak live FD delta is constant).
    - Avoids accumulating open ancestor descriptors in kernel tables, preventing EMFILE
      under constrained descriptor limits (countermodel CM-C3-CLEANUP-FD-DEPTH).
    - Traversal state is stored as logical component paths in heap memory, reacquiring
      directory descriptors descriptor-relatively with O_NOFOLLOW | O_CLOEXEC and closing
      intermediate handles promptly.
    - Immune to symlink redirection, parent namespace substitution, and descriptor leaks:
      all descriptors are closed in try...finally blocks immediately.
    - Preserves canonical C3 cleanup contract: complete epoch removal for canonical
      unmodified trees; best-effort removal for namespaces mutated post-handoff by
      equivalent host authority; mandatory descriptor release in all cases.
    """
    import os as _os

    def _open_rel(components: tuple[str, ...]) -> int:
        cur = _os.dup(dir_fd)
        for comp in components:
            try:
                nxt = _os.open(
                    comp,
                    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                    dir_fd=cur,
                )
            finally:
                _os.close(cur)
            cur = nxt
        return cur

    # Traversal frame: (components_tuple, unvisited_subdirs_or_None)
    # The stack holds heap-allocated logical path tuples, NOT open kernel file descriptors.
    stack: list[tuple[tuple[str, ...], list[str] | None]] = [((), None)]

    while stack:
        comps, subdirs = stack[-1]
        if subdirs is None:
            # First visit: scan directory and unlink non-directories immediately
            try:
                cur_fd = dir_fd if comps == () else _open_rel(comps)
            except OSError:
                # Directory cannot be opened (e.g. removed or became a symlink)
                stack.pop()
                continue

            found_subdirs: list[str] = []
            try:
                with _os.scandir(cur_fd) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                found_subdirs.append(entry.name)
                            else:
                                _os.unlink(entry.name, dir_fd=cur_fd)
                        except OSError:
                            pass
            except OSError:
                pass
            finally:
                if cur_fd != dir_fd:
                    try:
                        _os.close(cur_fd)
                    except OSError:
                        pass

            stack[-1] = (comps, found_subdirs)
        elif subdirs:
            child_name = subdirs.pop()
            stack.append((comps + (child_name,), None))
        else:
            # Post-order: all subdirs of comps have been processed
            stack.pop()
            if comps:
                parent_comps = comps[:-1]
                child_name = comps[-1]
                try:
                    p_fd = dir_fd if parent_comps == () else _open_rel(parent_comps)
                except OSError:
                    p_fd = None

                if p_fd is not None:
                    try:
                        _os.rmdir(child_name, dir_fd=p_fd)
                    except OSError:
                        try:
                            _os.unlink(child_name, dir_fd=p_fd)
                        except OSError:
                            pass
                    finally:
                        if p_fd != dir_fd:
                            try:
                                _os.close(p_fd)
                            except OSError:
                                pass


class OperationWorkspaceLeaseV2:
    """A private, operation-owned lease over a workspace capability.

    Acquired via `workspace.pin()` or `workspace.require_open_fd()`.
    Owns a private duplicated file descriptor to the workspace pool,
    ensuring authority continuity even if the underlying
    WorkspaceCapability is closed or concurrent operations run.
    Implements `fileno()` and `__index__()` so it can be passed directly
    where an integer `dir_fd` is expected.
    """
    def __init__(self, pool_fd: int, pool_locator: Path) -> None:
        self.pool_fd = pool_fd
        self.pool_locator = pool_locator
        self._closed = False

    def fileno(self) -> int:
        if self._closed or self.pool_fd < 0:
            raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2)
        return self.pool_fd

    def __index__(self) -> int:
        return self.fileno()

    def __int__(self) -> int:
        return self.fileno()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        import os as _os
        if not self._closed:
            self._closed = True
            fd = self.pool_fd
            self.pool_fd = -1
            if fd != -1:
                try:
                    _os.close(fd)
                except OSError:
                    pass

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


class MaterialisationEpochV2:
    """Linearizable transaction managing a private epoch under an operation lease.

    Maintains complete failure atomicity: either commits by transferring ownership
    to MaterialisedCommitSubjectCapabilityV2, or rolls back all filesystem modifications
    and descriptor handles.
    """
    def __init__(self, lease: OperationWorkspaceLeaseV2) -> None:
        self.lease = lease
        self.root_name: str | None = None
        self.root_fd: int = -1
        self._committed = False

    def create_epoch_root(self) -> tuple[int, str, Path]:
        import os as _os
        import stat as _stat
        import uuid as _uuid
        root_name = f"c3_{_uuid.uuid4().hex}"
        try:
            _os.mkdir(root_name, 0o700, dir_fd=self.lease.pool_fd)
        except OSError as exc:
            raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

        # Immediately register root_name for rollback in case open or subsequent steps fail
        self.root_name = root_name
        dest_path = self.lease.pool_locator / root_name

        try:
            # Ensure private epoch root has owner read/write/execute access (0o700)
            # regardless of caller's ambient umask before opening or populating.
            _os.chmod(root_name, 0o700, dir_fd=self.lease.pool_fd, follow_symlinks=False)
        except (OSError, ValueError) as exc:
            raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

        try:
            root_fd = _os.open(
                root_name,
                _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                dir_fd=self.lease.pool_fd,
            )
        except OSError as exc:
            # open failed after mkdir!
            raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

        self.root_fd = root_fd
        return root_fd, root_name, dest_path

    def rollback(self) -> None:
        import os as _os
        if self._committed:
            return

        root_fd = self.root_fd
        self.root_fd = -1
        root_name = self.root_name
        self.root_name = None

        try:
            if root_fd != -1:
                try:
                    _fd_rmtree(root_fd)
                except Exception:
                    pass
        finally:
            if root_fd != -1:
                try:
                    _os.close(root_fd)
                except OSError:
                    pass
            try:
                if root_name is not None and self.lease.pool_fd != -1:
                    try:
                        _os.rmdir(root_name, dir_fd=self.lease.pool_fd)
                    except OSError:
                        pass
            finally:
                self.lease.close()

    def commit(
        self,
        *,
        commit_sha: str,
        file_count: int,
        dest_path: Path,
    ) -> MaterialisedCommitSubjectCapabilityV2:
        if self._committed:
            raise RuntimeError("Epoch already committed")

        # Snapshot descriptor values before entering try block
        root_fd = self.root_fd
        root_name = self.root_name
        pool_fd = self.lease.pool_fd

        import os as _os
        cap: MaterialisedCommitSubjectCapabilityV2 | None = None
        # Enter unconditional rollback guard BEFORE detaching descriptors.
        # If an asynchronous BaseException arrives before try, the caller's finally
        # invokes epoch.rollback() which still owns root_fd and pool_fd.
        # Once inside try, any interruption is caught here: epoch descriptors are poisoned,
        # and cleanup is executed exactly once on the detached descriptors.
        try:
            self.root_fd = -1
            self.root_name = None
            self.lease.pool_fd = -1
            self.lease._closed = True
            self._committed = True

            cap = MaterialisedCommitSubjectCapabilityV2(
                root_fd=root_fd,
                root_locator=dest_path,
                commit_sha=commit_sha,
                file_count=file_count,
                pool_fd=pool_fd,
                root_name=root_name,
                owns_pool_fd=True,
            )
            return cap
        except BaseException:
            if cap is not None:
                try:
                    cap.close()
                except BaseException:
                    pass
            else:
                try:
                    if root_fd != -1:
                        try:
                            _fd_rmtree(root_fd)
                        except Exception:
                            pass
                finally:
                    if root_fd != -1:
                        try:
                            _os.close(root_fd)
                        except OSError:
                            pass
                    try:
                        if pool_fd != -1 and root_name:
                            try:
                                _os.rmdir(root_name, dir_fd=pool_fd)
                            except OSError:
                                pass
                    finally:
                        if pool_fd != -1:
                            try:
                                _os.close(pool_fd)
                            except OSError:
                                pass
            raise


class MaterialisationWorkspaceCapabilityV2:
    """A host-owned workspace capability for private materialization.

    This establishes the authority of the pool directory where private
    epochs are constructed, ensuring the epoch parent relation is
    descriptor-bound and immune to pathname swapping.
    """
    def __init__(self, pool_fd: int, pool_locator: Path):
        import os as _os
        import threading as _threading
        self._lock = _threading.Lock()
        self.pool_fd = _os.dup(pool_fd)
        _os.set_inheritable(self.pool_fd, False)
        self.pool_locator = pool_locator
        self._closed = False
        self._pin_hook = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def require_open_fd(self) -> OperationWorkspaceLeaseV2:
        """Return an independently owned lease over the workspace pool descriptor.

        Never exposes the workspace's closable handle as a raw integer, preventing
        descriptor reuse races where concurrent close() could close the integer
        and allow another thread to reuse it before caller uses it.
        """
        return self.pin()

    def pin(self) -> OperationWorkspaceLeaseV2:
        """Atomically duplicate and return a private, operation-owned lease over the workspace pool.

        Linearization point: inside self._lock, atomic verification of open state followed immediately
        by descriptor duplication. Mutually excludes close() so no handle reuse or stale duplication
        can ever occur.
        """
        import os as _os
        with self._lock:
            if self._pin_hook is not None:
                self._pin_hook()
            if self._closed or self.pool_fd < 0:
                raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2)
            try:
                pinned_fd = _os.dup(self.pool_fd)
            except OSError as exc:
                raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2) from exc

            lease_created = False
            try:
                _os.set_inheritable(pinned_fd, False)
                lease = OperationWorkspaceLeaseV2(pinned_fd, self.pool_locator)
                lease_created = True
                return lease
            except Exception as exc:
                if isinstance(exc, SubjectMaterialisationError):
                    raise
                raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2) from exc
            finally:
                if not lease_created:
                    try:
                        _os.close(pinned_fd)
                    except OSError:
                        pass

    def close(self):
        """Linearly invalidate and close the workspace authority handle.

        Linearization point: inside self._lock, transitions _closed to True, captures the kernel FD,
        poisons pool_fd to -1, and closes the kernel descriptor before releasing the lock.
        """
        import os as _os
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd = self.pool_fd
            self.pool_fd = -1
            if fd != -1:
                try:
                    _os.close(fd)
                except OSError:
                    pass

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass


@dataclass(frozen=True)
class MaterialisedCommitSubjectV2:

    """Bytes materialised from one commit's tree, severed from their source."""

    root: Path
    commit_sha: str
    file_count: int


def resolve_commit_v2(*, repo_root: Path, ref: str) -> str:
    """Confirm `ref` names a commit in `repo_root`'s own object store.

    `^{commit}` is required rather than accepting any object: a tree or blob
    sha would otherwise resolve to something that is not a revision, and
    nothing downstream should be able to claim an identity that no commit
    history actually contains. The returned value is git's own full sha, not
    an echo of whatever string the caller passed in -- resolving `HEAD`, a
    branch name, or an abbreviated sha all go through this same git call.

    DO NOT use this function's return value as a `trusted_ref_sha` argument
    to `commit_derived_execution_identity_v2.authorize_commit_for_execution_v2`
    (`#313`/`#200-G1C2-F3`). This function resolves `ref` against whatever
    object store `repo_root` names -- including, when called against
    `open_trusted_object_authority_v2(...).trusted_repo_root`, a private copy
    built from a potentially-HOSTILE live checkout, whose ref *values* (not
    its object *content*) are copied verbatim from that same hostile source.
    A shape-valid 40/64-hex sha this function returns is a real commit sha,
    but resolving a ref NAME through a hostile-derived authority and then
    treating the RESULT as if it were independently, out-of-band trustworthy
    reconstructs exactly the false-positive `#313` fixed -- one call outside
    the function that closed it. A `trusted_ref_sha` must come from a source
    that never calls this function (or any other read primitive in this
    package) against a checkout under test at all.
    """
    try:
        completed = run_bounded_git_v2(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_root
        )
    except BoundedGitError as exc:
        if exc.reason_code == "bounded_git_command_failed":
            raise SubjectMaterialisationError(SUBJECT_UNKNOWN_COMMIT_REASON_V2) from None
        raise
    resolved = completed.stdout.decode("utf-8", errors="surrogateescape").strip()
    if len(resolved) != 40 or any(c not in "0123456789abcdef" for c in resolved):
        # Defensive: `rev-parse --verify ...^{commit}` should always return a
        # full 40-hex sha on success. If it ever doesn't, refuse rather than
        # propagate a value nothing downstream can trust as an identity.
        raise SubjectMaterialisationError(SUBJECT_UNKNOWN_COMMIT_REASON_V2)
    return resolved


def resolve_commit_tree_sha_v2(*, repo_root: Path, commit_sha: str) -> str:
    """Resolve the root tree object ID of a commit."""
    try:
        completed = run_bounded_git_v2(
            ["rev-parse", "--verify", "--quiet", f"{commit_sha}^{{tree}}"], cwd=repo_root
        )
    except BoundedGitError as exc:
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
    resolved = completed.stdout.decode("utf-8", errors="surrogateescape").strip()
    if (len(resolved) not in (40, 64)) or any(c not in "0123456789abcdef" for c in resolved):
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
    return resolved


def list_commit_tree_entries_v2(*, repo_root: Path, commit_sha: str) -> list[TreeEntryV2]:
    """Predecessor leaf-oriented semantics.
    Does not include trees. Only blobs and gitlinks.
    """
    completed = run_bounded_git_v2(["ls-tree", "-r", "-z", commit_sha], cwd=repo_root)
    entries: list[TreeEntryV2] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("utf-8").split(" ", 2)
        except ValueError as exc:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
        entries.append(
            TreeEntryV2(
                mode=mode,
                object_type=object_type,
                object_id=object_id,
                path=raw_path.decode("utf-8", errors="surrogateescape"),
            )
        )
    return entries





class BoundedBlobCarrierV2(Mapping[str, bytes]):
    """Bounded carrier for committed git blob payloads.

    Streams blob data into an anonymous on-disk spool file during cat-file --batch
    ingestion, eliminating dual-payload allocation (such as chunks + b"".join)
    in Python heap memory.

    Implements Mapping[str, bytes] for backward compatibility with existing
    consumers, reading individual blobs from the spool on demand.

    Provides stream_to_fd(path, target_fd) for streaming blob content directly to a
    destination file descriptor in bounded chunks (<= 64 KiB) without loading
    the full blob body into Python heap.
    """

    def __init__(
        self,
        spool: io.BufferedRandom,
        index: dict[str, tuple[int, int]],
        keys: list[str],
    ) -> None:
        self._spool = spool
        self._index = index  # path -> (offset, size)
        self._keys = keys
        self._closed = False

    @classmethod
    def empty(cls) -> BoundedBlobCarrierV2:
        spool = tempfile.TemporaryFile(mode="w+b")
        return cls(spool, {}, [])

    def __getitem__(self, path: str) -> bytes:
        if self._closed:
            raise ValueError("BoundedBlobCarrierV2 is closed")
        if path not in self._index:
            raise KeyError(path)
        offset, size = self._index[path]
        self._spool.seek(offset)
        return self._spool.read(size)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, path: object) -> bool:
        return path in self._index

    def get(self, path: str, default: object = None) -> bytes | object:
        if path in self._index:
            return self[path]
        return default

    def size_of(self, path: str) -> int:
        """Logical size of the blob at `path`, from the index; reads no payload."""
        if self._closed:
            raise ValueError("BoundedBlobCarrierV2 is closed")
        if path not in self._index:
            raise KeyError(path)
        return self._index[path][1]

    def read_bounded(self, path: str, limit: int) -> bytes:
        """Read the whole blob at `path` only if its size is <= `limit`.

        The size is checked from the index before any payload is read or
        allocated; an over-limit blob is refused as unrepresentable.
        """
        size = self.size_of(path)
        if size > limit:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        offset, _ = self._index[path]
        self._spool.seek(offset)
        data = self._spool.read(size)
        if len(data) != size:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        return data

    def stream_to_fd(self, path: str, target_fd: int) -> int:
        if self._closed:
            raise ValueError("BoundedBlobCarrierV2 is closed")
        if path not in self._index:
            raise KeyError(path)
        offset, size = self._index[path]
        self._spool.seek(offset)
        remaining = size
        total_written = 0
        while remaining > 0:
            chunk = self._spool.read(min(remaining, 65536))
            if not chunk:
                raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
            written = 0
            while written < len(chunk):
                n = _os.write(target_fd, chunk[written:])
                if n == 0:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                written += n
            remaining -= len(chunk)
            total_written += len(chunk)
        return total_written

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._spool.close()
            except OSError:
                pass

    def __enter__(self) -> BoundedBlobCarrierV2:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def read_commit_blobs_v2(
    *,
    repo_root: Path,
    entries: list[TreeEntryV2],
    max_expanded_bytes: int = MAX_EXPANDED_BYTES_V2,
) -> BoundedBlobCarrierV2:
    """Fetch every blob's raw content in one batched `cat-file` call.

    Keyed by path (not object id) because the caller wants "what is at this
    path in the tree", and a repository can legitimately have two paths
    share a blob (identical file content).

    Adjudicates RESOURCE_BOUNDED_MATERIALIZATION byte/work budget:
    - Measures logical output bytes (materialization work) before creating epoch
      or writing to disk using `cat-file --batch-check`.
    - If total logical bytes exceed `max_expanded_bytes`, fails closed as
      `SUBJECT_UNREPRESENTABLE_TREE_REASON_V2` with zero filesystem mutations
      and without buffering large blob bodies into process memory.
    - Streams blob payloads from `cat-file --batch` directly into an anonymous
      spool without accumulating or rebuilding chunks in Python heap, ensuring
      O(chunk_size) peak heap memory.
    """
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2 and entry.object_type != "tree"]
    if not blobs:
        return BoundedBlobCarrierV2.empty()
    batch_request = "".join(f"{entry.object_id}\n" for entry in blobs)

    # Preflight: query object metadata/sizes via `cat-file --batch-check` to enforce
    # byte budget in O(entries) memory BEFORE buffering blob bodies into memory.
    try:
        check_completed = run_bounded_git_v2(
            ["cat-file", "--batch-check"],
            cwd=repo_root,
            input_bytes=batch_request.encode("utf-8"),
        )
    except BoundedGitError as exc:
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc

    check_lines = check_completed.stdout.splitlines()
    if len(check_lines) != len(blobs):
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)

    total_logical_bytes = 0
    for line in check_lines:
        parts = line.decode("utf-8", "replace").split(" ")
        if len(parts) == 2 and parts[1] == "missing":
            raise SubjectMaterialisationError(SUBJECT_BLOB_MISSING_REASON_V2)
        if len(parts) != 3:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        if parts[1] != "blob":
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        try:
            size = int(parts[2])
        except ValueError:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        total_logical_bytes += size
        if size > max_expanded_bytes or total_logical_bytes > max_expanded_bytes:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

    # Every acquisition (spool, then process) happens inside the region whose
    # `finally` releases it; only the spool is transferred to the carrier.
    spool: io.BufferedRandom | None = None
    proc = None
    transferred = False
    try:
        spool = tempfile.TemporaryFile(mode="w+b")
        proc = open_bounded_git_subprocess_v2(
            ["cat-file", "--batch"],
            cwd=repo_root,
        )
        assert proc.stdin is not None
        assert proc.stdout is not None

        oid_spans: dict[str, tuple[int, int]] = {}
        unique_blobs: list[TreeEntryV2] = []
        seen_oids: set[str] = set()
        for entry in blobs:
            if entry.object_id not in seen_oids:
                seen_oids.add(entry.object_id)
                unique_blobs.append(entry)

        verified_bytes = 0
        for entry in unique_blobs:
            proc.stdin.write(f"{entry.object_id}\n".encode("utf-8"))
            proc.stdin.flush()
            header_line = proc.stdout.readline()
            if not header_line:
                raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
            header = header_line.decode("utf-8", "replace").strip().split(" ")
            if len(header) == 2 and header[1] == "missing":
                raise SubjectMaterialisationError(SUBJECT_BLOB_MISSING_REASON_V2)
            if len(header) != 3:
                raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
            if header[1] != "blob":
                raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
            try:
                size = int(header[2])
            except ValueError:
                raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
            verified_bytes += size
            if size > max_expanded_bytes or verified_bytes > max_expanded_bytes:
                raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

            offset = spool.tell()
            remaining = size
            while remaining > 0:
                chunk = proc.stdout.read(min(remaining, 65536))
                if not chunk:
                    raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
                spool.write(chunk)
                remaining -= len(chunk)
            trailing_nl = proc.stdout.read(1)
            if trailing_nl != b"\n":
                raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
            oid_spans[entry.object_id] = (offset, size)

        index: dict[str, tuple[int, int]] = {}
        keys: list[str] = []
        for entry in blobs:
            index[entry.path] = oid_spans[entry.object_id]
            keys.append(entry.path)

        carrier = BoundedBlobCarrierV2(spool=spool, index=index, keys=keys)
        transferred = True
        return carrier
    except (OSError, BoundedGitError) as exc:
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
    finally:
        if spool is not None and not transferred:
            try:
                spool.close()
            except OSError:
                pass
        if proc is not None:
            _release_git_subprocess_v2(proc)


def _release_git_subprocess_v2(proc: subprocess.Popen) -> None:
    """Close pipes and terminate/reap the process; never raises OSError."""
    if proc.stdin:
        try:
            proc.stdin.close()
        except OSError:
            pass
    if proc.stdout:
        try:
            proc.stdout.close()
        except OSError:
            pass
    if proc.stderr:
        try:
            proc.stderr.close()
        except OSError:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait()
        except OSError:
            pass



from dataclasses import dataclass, field
import os as _os
import stat as _stat

@dataclass
class _TrieNode:
    node_type: str  # 'tree', 'blob', 'symlink'
    mode: str
    object_id: str
    explicit: bool
    children: dict[str, '_TrieNode'] = field(default_factory=dict)

_ACTIVE_TREE_BATCH_SESSION: contextvars.ContextVar[subprocess.Popen[bytes] | None] = (
    contextvars.ContextVar("_ACTIVE_TREE_BATCH_SESSION", default=None)
)
_ACTIVE_REMAINING_ENTRY_BUDGET: contextvars.ContextVar[int | None] = (
    contextvars.ContextVar("_ACTIVE_REMAINING_ENTRY_BUDGET", default=None)
)


def _parse_tree_data(
    raw: bytes,
    oid_len: int,
    effective_max_entries: int,
) -> list[tuple[str, str, str, bytes]]:
    pos = 0
    raw_len = len(raw)
    entries: list[tuple[str, str, str, bytes]] = []

    while pos < raw_len:
        space_pos = raw.find(b" ", pos)
        if space_pos == -1:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        mode_bytes = raw[pos:space_pos]
        try:
            mode_str = mode_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc

        null_pos = raw.find(b"\x00", space_pos)
        if null_pos == -1:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        raw_name = raw[space_pos + 1 : null_pos]

        sha_end = null_pos + 1 + oid_len
        if sha_end > raw_len:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        sha_bytes = raw[null_pos + 1 : sha_end]
        obj_id = sha_bytes.hex()
        pos = sha_end

        # Invariant: Single directory entry must not contain path separators or NUL bytes
        if b"/" in raw_name or b"\0" in raw_name or raw_name in (b".", b"..", b""):
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

        if mode_str in ("40000", "040000"):
            obj_type = "tree"
            mode = "040000"
        elif mode_str == "160000":
            obj_type = "commit"
            mode = mode_str
        elif mode_str == "120000":
            obj_type = "blob"
            mode = mode_str
        elif mode_str in ("100644", "100755"):
            obj_type = "blob"
            mode = mode_str
        else:
            obj_type = "unknown"
            mode = mode_str

        entries.append((mode, obj_type, obj_id, raw_name))
        if len(entries) > effective_max_entries:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

    return entries


def _list_single_tree_entries_v2(
    *,
    repo_root: Path,
    tree_oid: str,
    max_entries: int | None = None,
) -> list[tuple[str, str, str, bytes]]:
    """Enumerate immediate children of a single git tree object.

    Returns tuples of (mode, object_type, object_id, raw_name_bytes).
    Does not recurse or flatten hierarchy.
    Adjudicates RESOURCE_BOUNDED_MATERIALIZATION:
    - Bounded single-tree ingress: rejects oversized raw trees before allocation.
    - Uses shared batch session when called within hierarchical traversal.
    """
    if (len(tree_oid) not in (40, 64)) or any(c not in "0123456789abcdef" for c in tree_oid):
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
    oid_len = len(tree_oid) // 2

    if max_entries is not None:
        effective_max_entries = max_entries
    else:
        ctx_budget = _ACTIVE_REMAINING_ENTRY_BUDGET.get()
        effective_max_entries = ctx_budget if ctx_budget is not None else MAX_EXPANDED_ENTRIES_V2

    if effective_max_entries < 0:
        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

    batch_proc = _ACTIVE_TREE_BATCH_SESSION.get()
    owns_proc = False
    if batch_proc is None:
        try:
            batch_proc = open_bounded_git_subprocess_v2(["cat-file", "--batch"], cwd=repo_root)
            owns_proc = True
        except BoundedGitError as exc:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc

    try:
        assert batch_proc.stdin is not None
        assert batch_proc.stdout is not None
        batch_proc.stdin.write(f"{tree_oid}\n".encode("utf-8"))
        batch_proc.stdin.flush()
        header_line = batch_proc.stdout.readline()
        if not header_line:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        header = header_line.decode("utf-8", "replace").strip().split(" ")
        if len(header) == 2 and header[1] == "missing":
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        if len(header) != 3:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        if header[1] != "tree":
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        try:
            size = int(header[2])
        except ValueError:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)

        # Mathematical lower bound: each entry is at most 295 bytes.
        # If size // 295 > effective_max_entries, the tree mathematically cannot
        # fit within the budget and is rejected immediately without allocating the body.
        if size // 295 > effective_max_entries:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

        raw = batch_proc.stdout.read(size)
        if len(raw) != size:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        trailing_nl = batch_proc.stdout.read(1)
        if trailing_nl != b"\n":
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)

        return _parse_tree_data(raw, oid_len, effective_max_entries)
    except (OSError, BoundedGitError) as exc:
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
    finally:
        if owns_proc and batch_proc is not None:
            if batch_proc.stdin:
                try:
                    batch_proc.stdin.close()
                except OSError:
                    pass
            if batch_proc.stdout:
                try:
                    batch_proc.stdout.close()
                except OSError:
                    pass
            if batch_proc.stderr:
                try:
                    batch_proc.stderr.close()
                except OSError:
                    pass
            try:
                batch_proc.terminate()
                batch_proc.wait(timeout=1)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    batch_proc.kill()
                    batch_proc.wait()
                except OSError:
                    pass


def _build_canonical_trie_hierarchical(
    *,
    repo_root: Path,
    root_tree_oid: str,
    max_component_len: int | None = 255,
    max_expanded_entries: int = MAX_EXPANDED_ENTRIES_V2,
) -> tuple[_TrieNode, list[TreeEntryV2], list[TreeEntryV2]]:
    """Constructs the canonical trie directly from hierarchical raw Git trees.

    Adjudicates STRUCTURAL_PROJECTION_FIDELITY and RESOURCE_BOUNDED_MATERIALIZATION:
    - LossyProjection cannot_be IdentityAuthority (FlattenedRepresentation != StructuralIdentity).
    - Traverses Git tree objects level-by-level without flattening.
    - Every directory node in the canonical trie corresponds to an explicit Git tree object;
      no implicit tree ancestors are ever synthesized.
    - Validates that every directory entry name is strictly a single POSIX path component
      (no '/', no NUL, not '.' or '..', not empty).
    - Validates component encodability and filesystem component-length limits (NAME_MAX).
    - Enforces prospective child depth limit (child_depth <= 100).
    - Caches parsed tree OIDs so repeated Git tree OIDs are parsed at most once.
    - Bounded logical entry budget (expanded_entries <= max_expanded_entries).
    - Distinct-tree subprocess expansion bound: queries all tree objects through
      one shared batch session (O(1) git subprocesses).
    - Detects cycles in tree references.
    - Collects all leaf blobs and symlinks for batched byte loading via read_commit_blobs_v2.

    Returns:
    - root: The root _TrieNode
    - all_entries: Full structural entries (trees, blobs, symlinks) in hierarchical traversal order
    - leaf_blobs: Leaf blob and symlink entries for content loading
    """
    root = _TrieNode(node_type='tree', mode='040000', object_id=root_tree_oid, explicit=True)
    all_entries: list[TreeEntryV2] = []
    leaf_blobs: list[TreeEntryV2] = []

    # Cache parsed entries by tree OID to bound Git subprocess expansion
    tree_cache: dict[str, list[tuple[str, str, str, bytes]]] = {}
    expanded_entries_count = 0

    # Queue stores: (current_node, current_tree_oid, logical_path_tuple, ancestor_oids_tuple)
    queue: list[tuple[_TrieNode, str, tuple[str, ...], tuple[str, ...]]] = [
        (root, root_tree_oid, (), (root_tree_oid,))
    ]

    try:
        batch_proc = open_bounded_git_subprocess_v2(["cat-file", "--batch"], cwd=repo_root)
    except BoundedGitError as exc:
        raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc

    token_session = _ACTIVE_TREE_BATCH_SESSION.set(batch_proc)
    try:
        while queue:
            current_node, current_tree_oid, path_tuple, ancestor_oids = queue.pop(0)

            if current_tree_oid in tree_cache:
                entries = tree_cache[current_tree_oid]
            else:
                rem_budget = max_expanded_entries - expanded_entries_count
                token_budget = _ACTIVE_REMAINING_ENTRY_BUDGET.set(rem_budget)
                try:
                    entries = _list_single_tree_entries_v2(repo_root=repo_root, tree_oid=current_tree_oid)
                finally:
                    _ACTIVE_REMAINING_ENTRY_BUDGET.reset(token_budget)
                tree_cache[current_tree_oid] = entries

            for mode, obj_type, obj_id, raw_name in entries:
                child_depth = len(path_tuple) + 1
                if child_depth > 100:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

                expanded_entries_count += 1
                if expanded_entries_count > max_expanded_entries:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                if max_component_len is not None and len(raw_name) > max_component_len:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

                try:
                    name = raw_name.decode("utf-8", errors="surrogateescape")
                    fsencoded = _os.fsencode(name)
                except (UnicodeError, ValueError) as exc:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2) from exc

                if max_component_len is not None and len(fsencoded) > max_component_len:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

                if "/" in name or "\0" in name or name in (".", "..", ""):
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

                if name in current_node.children:
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

                child_path_tuple = path_tuple + (name,)
                child_rel_path = "/".join(child_path_tuple)

                if obj_type == "tree":
                    if mode not in ("040000", "40000"):
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    if obj_id in ancestor_oids:
                        # Cycle detected in Git tree hierarchy
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    child_node = _TrieNode(node_type='tree', mode=mode, object_id=obj_id, explicit=True)
                    current_node.children[name] = child_node
                    all_entries.append(
                        TreeEntryV2(
                            mode=mode,
                            object_type="tree",
                            object_id=obj_id,
                            path=child_rel_path,
                        )
                    )
                    queue.append((child_node, obj_id, child_path_tuple, ancestor_oids + (obj_id,)))
                elif obj_type == "blob":
                    if mode == GITLINK_MODE_V2:
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    elif mode == SYMLINK_MODE_V2:
                        child_node = _TrieNode(node_type='symlink', mode=mode, object_id=obj_id, explicit=True)
                    elif mode in ("100644", "100755"):
                        child_node = _TrieNode(node_type='blob', mode=mode, object_id=obj_id, explicit=True)
                    else:
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    current_node.children[name] = child_node
                    entry = TreeEntryV2(
                        mode=mode,
                        object_type="blob",
                        object_id=obj_id,
                        path=child_rel_path,
                    )
                    all_entries.append(entry)
                    leaf_blobs.append(entry)
                else:
                    # Submodule commits, tags in tree, or unknown types
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
    finally:
        _ACTIVE_TREE_BATCH_SESSION.reset(token_session)
        if batch_proc.stdin:
            try:
                batch_proc.stdin.close()
            except OSError:
                pass
        if batch_proc.stdout:
            try:
                batch_proc.stdout.close()
            except OSError:
                pass
        if batch_proc.stderr:
            try:
                batch_proc.stderr.close()
            except OSError:
                pass
        try:
            batch_proc.terminate()
            batch_proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            try:
                batch_proc.kill()
                batch_proc.wait()
            except OSError:
                pass

    return root, all_entries, leaf_blobs


def list_commit_tree_structure_v2(*, repo_root: Path, commit_sha: str) -> list[TreeEntryV2]:
    """C3-owned structural enumeration via hierarchical raw Git tree traversal."""
    root_tree_oid = resolve_commit_tree_sha_v2(repo_root=repo_root, commit_sha=commit_sha)
    _trie, all_entries, _leaf_blobs = _build_canonical_trie_hierarchical(
        repo_root=repo_root, root_tree_oid=root_tree_oid
    )
    return all_entries

def _materialise_trie_no_follow(
    root_node: _TrieNode,
    content_by_path: Mapping[str, bytes] | dict[str, bytes],
    initial_dir_fd: int,
    initial_path: str,
    count: list[int],
) -> None:
    import stat as _stat

    def _open_rel(components: tuple[str, ...]) -> int:
        cur = _os.dup(initial_dir_fd)
        _os.set_inheritable(cur, False)
        for comp in components:
            try:
                nxt = _os.open(
                    comp,
                    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                    dir_fd=cur,
                )
            finally:
                _os.close(cur)
            cur = nxt
        return cur

    # Traversal frame: (node, components_tuple, current_path, unvisited_children_list)
    # The stack holds logical path component tuples on heap, NOT open file descriptors.
    # Simultaneously-live authority handles during materialization are O(1) and
    # strictly bounded independently of tree depth (peak live FD delta <= 2).
    stack: list[tuple[_TrieNode, tuple[str, ...], str, list[tuple[str, _TrieNode]]]] = [
        (root_node, (), initial_path, list(root_node.children.items()))
    ]

    while stack:
        node, comps, current_path, children_items = stack[-1]
        if not children_items:
            stack.pop()
            continue

        name, child = children_items.pop()
        name_bytes = _os.fsencode(name)
        child_path = current_path + "/" + name if current_path else name
        child_comps = comps + (name,)

        dir_fd = initial_dir_fd if comps == () else _open_rel(comps)
        try:
            if child.node_type == 'tree':
                try:
                    _os.mkdir(name_bytes, mode=0o777, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    # Ensure child directory has owner read/write/execute access (0o700)
                    # regardless of ambient umask before opening or populating.
                    stat_before = _os.stat(name_bytes, dir_fd=dir_fd, follow_symlinks=False)
                    if (stat_before.st_mode & _stat.S_IRWXU) != _stat.S_IRWXU:
                        _os.chmod(
                            name_bytes,
                            stat_before.st_mode | _stat.S_IRWXU,
                            dir_fd=dir_fd,
                            follow_symlinks=False,
                        )
                except (OSError, ValueError) as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    child_fd = _os.open(
                        name_bytes,
                        _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                        dir_fd=dir_fd,
                    )
                except OSError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    # Bind created directories: ensure it is completely empty
                    if _os.listdir(child_fd):
                        raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                finally:
                    _os.close(child_fd)

                stack.append((child, child_comps, child_path, list(child.children.items())))

            elif child.node_type == 'symlink':
                content = content_by_path[child_path]
                target = content.decode("utf-8", "surrogateescape")
                try:
                    _os.symlink(target, name_bytes, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc
                except OSError as exc:
                    if exc.errno == _errno.ENAMETOOLONG:
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2) from exc
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                # Revalidate symlink
                stat_name = _os.stat(name_bytes, dir_fd=dir_fd, follow_symlinks=False)
                if not _stat.S_ISLNK(stat_name.st_mode):
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                actual_target = _os.readlink(name_bytes, dir_fd=dir_fd)
                if actual_target != content:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                count[0] += 1

            elif child.node_type == 'blob':
                flags = _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL | _os.O_NOFOLLOW | _os.O_CLOEXEC
                mode = 0o666
                try:
                    fd = _os.open(name_bytes, flags, mode, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc
                except OSError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    if hasattr(content_by_path, "stream_to_fd"):
                        content_by_path.stream_to_fd(child_path, fd)
                    else:
                        content = content_by_path[child_path]
                        written_bytes = 0
                        while written_bytes < len(content):
                            chunk = _os.write(fd, content[written_bytes:])
                            if chunk == 0:
                                raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                            written_bytes += chunk

                    stat_before = _os.fstat(fd)
                    target_mode = stat_before.st_mode | _stat.S_IRUSR | _stat.S_IWUSR
                    if child.mode == EXECUTABLE_MODE_V2:
                        target_mode |= (
                            _stat.S_IXUSR
                            | _stat.S_IXGRP
                            | _stat.S_IXOTH
                        )
                    if target_mode != stat_before.st_mode:
                        _os.fchmod(fd, target_mode)

                    # Revalidate after writing
                    stat_name = _os.stat(name_bytes, dir_fd=dir_fd, follow_symlinks=False)
                    stat_fd = _os.fstat(fd)
                    if stat_name.st_dev != stat_fd.st_dev or stat_name.st_ino != stat_fd.st_ino:
                        raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                finally:
                    _os.close(fd)
                count[0] += 1
        finally:
            if dir_fd != initial_dir_fd:
                try:
                    _os.close(dir_fd)
                except OSError:
                    pass

def _get_process_umask_non_mutating() -> int:
    """Read ambient process umask on Linux without mutating global process state.

    Linux >= 4.7 exposes the thread-group umask in /proc/self/status as:
    Umask:  0022
    This avoids the multithreaded process-global race inherent to os.umask(0).
    If /proc/self/status is unavailable or unparseable, falls back to 0o022.
    """
    try:
        with open("/proc/self/status", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("Umask:"):
                    return int(line.split(":", 1)[1].strip(), 8)
    except (OSError, ValueError):
        pass
    return 0o022


def _check_projected_path_max(root_fd: int, dest_path: Path, path_max_limit: int) -> None:
    """Walks the capability descriptor-relatively and validates prospective destination paths against PATH_MAX.

    Never resolves or traverses capability.root_locator as a pathname.
    Maintains O(1) live file descriptors by storing logical path tuples on the stack
    and closing directory descriptors immediately after reading directory entries.
    """
    stack: list[tuple[str, ...]] = [()]
    while stack:
        rel_parts = stack.pop()
        cur_fd = root_fd
        owned_cur: int | None = None
        try:
            for part in rel_parts:
                next_fd = _os.open(
                    part,
                    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                    dir_fd=cur_fd,
                )
                if owned_cur is not None:
                    _os.close(owned_cur)
                cur_fd = next_fd
                owned_cur = next_fd

            for entry_name in _os.listdir(cur_fd):
                child_rel_parts = rel_parts + (entry_name,)
                proj_path = dest_path.joinpath(*child_rel_parts)
                if len(_os.fsencode(str(proj_path))) >= path_max_limit:
                    raise SubjectMaterialisationError(SUBJECT_LEGACY_PATH_UNREPRESENTABLE_REASON_V2)
                st = _os.stat(entry_name, dir_fd=cur_fd, follow_symlinks=False)
                if _stat.S_ISDIR(st.st_mode):
                    stack.append(child_rel_parts)
        finally:
            if owned_cur is not None:
                try:
                    _os.close(owned_cur)
                except OSError:
                    pass


def _has_default_acl_fd(dir_fd: int) -> bool:
    """True if the directory carries a default POSIX ACL (new children inherit it).

    Under a default ACL the kernel derives the creation mode and ACL mask from the
    creation intent and the ACL, ignoring umask. An explicit chmod afterwards would
    rewrite the ACL mask, so such directories keep their native creation result.
    """
    try:
        _os.getxattr(dir_fd, "system.posix_acl_default")
        return True
    except (OSError, AttributeError):
        return False


def _native_file_intent(source_mode: int) -> int:
    """Creation intent of an ordinary create: 0666, or 0777 for executables."""
    return 0o777 if source_mode & 0o111 else 0o666


def _copy_entry_descriptor_relative(name: str, src_dir_fd: int, dst_dir_fd: int) -> None:
    """Copies an entry from src_dir_fd into dst_dir_fd strictly relative to descriptors.

    Used when renameat returns EXDEV (cross-filesystem link).
    Never resolves or opens capability.root_locator by path.
    Adjudicates RESOURCE_BOUNDED_AUTHORITY_CLOSURE:
    - Simultaneously-live authority handles (file descriptors) are O(1) and strictly bounded
      independently of tree depth (peak live FD count <= 6).
    - Traversal state is stored as logical component paths in heap memory.
    - Ensures newly created destination directories have owner read/write/execute permissions (0o700)
      regardless of caller's ambient umask during copy, restoring exact source modes in post-order.
    """
    st = _os.stat(name, dir_fd=src_dir_fd, follow_symlinks=False)
    if _stat.S_ISLNK(st.st_mode):
        target = _os.readlink(name, dir_fd=src_dir_fd)
        _os.symlink(target, name, dir_fd=dst_dir_fd)
        _os.unlink(name, dir_fd=src_dir_fd)
    elif _stat.S_ISREG(st.st_mode):
        src_file_fd = _os.open(name, _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC, dir_fd=src_dir_fd)
        native_acl = _has_default_acl_fd(dst_dir_fd)
        try:
            dst_file_fd = _os.open(
                name,
                _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL | _os.O_CLOEXEC,
                _native_file_intent(st.st_mode) if native_acl else st.st_mode,
                dir_fd=dst_dir_fd,
            )
            try:
                dest_dir_st = _os.fstat(dst_dir_fd)
                if dest_dir_st.st_mode & _stat.S_ISGID:  # only setgid directories give children their GID
                    try:
                        _os.fchown(dst_file_fd, -1, dest_dir_st.st_gid)
                    except OSError:
                        pass
                while True:
                    chunk = _os.read(src_file_fd, 65536)
                    if not chunk:
                        break
                    written = 0
                    while written < len(chunk):
                        n = _os.write(dst_file_fd, chunk[written:])
                        if n == 0:
                            raise OSError("write returned 0 bytes")
                        written += n
                if not native_acl:
                    _os.fchmod(dst_file_fd, st.st_mode)
            finally:
                _os.close(dst_file_fd)
        finally:
            _os.close(src_file_fd)
        _os.unlink(name, dir_fd=src_dir_fd)
    elif _stat.S_ISDIR(st.st_mode):
        def _open_desc(base_fd: int, components: tuple[str, ...]) -> int:
            cur = _os.dup(base_fd)
            _os.set_inheritable(cur, False)
            for comp in components:
                try:
                    nxt = _os.open(
                        comp,
                        _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                        dir_fd=cur,
                    )
                finally:
                    _os.close(cur)
                cur = nxt
            return cur

        root_native_acl = _has_default_acl_fd(dst_dir_fd)
        _os.mkdir(name, mode=0o777 if root_native_acl else 0o700, dir_fd=dst_dir_fd)
        st_root = _os.stat(name, dir_fd=dst_dir_fd, follow_symlinks=False)
        if (st_root.st_mode & _stat.S_IRWXU) != _stat.S_IRWXU:
            _os.chmod(name, st_root.st_mode | _stat.S_IRWXU, dir_fd=dst_dir_fd, follow_symlinks=False)

        stack: list[tuple[tuple[str, ...], list[tuple[str, int]] | None, int]] = [
            ((name,), None, st.st_mode)
        ]

        while stack:
            comps, subdirs, orig_mode = stack[-1]
            if subdirs is None:
                src_cur_fd = _open_desc(src_dir_fd, comps)
                try:
                    dst_cur_fd = _open_desc(dst_dir_fd, comps)
                    try:
                        found_subdirs: list[tuple[str, int]] = []
                        with _os.scandir(src_cur_fd) as it:
                            for entry in it:
                                sub_name = entry.name
                                st_entry = _os.stat(sub_name, dir_fd=src_cur_fd, follow_symlinks=False)
                                if _stat.S_ISLNK(st_entry.st_mode):
                                    target = _os.readlink(sub_name, dir_fd=src_cur_fd)
                                    _os.symlink(target, sub_name, dir_fd=dst_cur_fd)
                                    _os.unlink(sub_name, dir_fd=src_cur_fd)
                                elif _stat.S_ISREG(st_entry.st_mode):
                                    s_file_fd = _os.open(
                                        sub_name,
                                        _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                                        dir_fd=src_cur_fd,
                                    )
                                    sub_native_acl = _has_default_acl_fd(dst_cur_fd)
                                    try:
                                        d_file_fd = _os.open(
                                            sub_name,
                                            _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL | _os.O_CLOEXEC,
                                            _native_file_intent(st_entry.st_mode) if sub_native_acl else st_entry.st_mode,
                                            dir_fd=dst_cur_fd,
                                        )
                                        try:
                                            dest_cur_st = _os.fstat(dst_cur_fd)
                                            if dest_cur_st.st_mode & _stat.S_ISGID:  # only setgid directories give children their GID
                                                try:
                                                    _os.fchown(d_file_fd, -1, dest_cur_st.st_gid)
                                                except OSError:
                                                    pass
                                            while True:
                                                chunk = _os.read(s_file_fd, 65536)
                                                if not chunk:
                                                    break
                                                written = 0
                                                while written < len(chunk):
                                                    n = _os.write(d_file_fd, chunk[written:])
                                                    if n == 0:
                                                        raise OSError("write returned 0 bytes")
                                                    written += n
                                            if not sub_native_acl:
                                                _os.fchmod(d_file_fd, st_entry.st_mode)
                                        finally:
                                            _os.close(d_file_fd)
                                    finally:
                                        _os.close(s_file_fd)
                                    _os.unlink(sub_name, dir_fd=src_cur_fd)
                                elif _stat.S_ISDIR(st_entry.st_mode):
                                    sub_dir_native_acl = _has_default_acl_fd(dst_cur_fd)
                                    _os.mkdir(sub_name, mode=0o777 if sub_dir_native_acl else 0o700, dir_fd=dst_cur_fd)
                                    st_sub = _os.stat(sub_name, dir_fd=dst_cur_fd, follow_symlinks=False)
                                    if (st_sub.st_mode & _stat.S_IRWXU) != _stat.S_IRWXU:
                                        _os.chmod(
                                            sub_name,
                                            st_sub.st_mode | _stat.S_IRWXU,
                                            dir_fd=dst_cur_fd,
                                            follow_symlinks=False,
                                        )
                                    found_subdirs.append((sub_name, st_entry.st_mode))
                    finally:
                        _os.close(dst_cur_fd)
                finally:
                    _os.close(src_cur_fd)
                stack[-1] = (comps, found_subdirs, orig_mode)
            elif subdirs:
                child_name, child_mode = subdirs.pop()
                stack.append((comps + (child_name,), None, child_mode))
            else:
                stack.pop()
                parent_comps = comps[:-1]
                child_name = comps[-1]
                dst_p_fd = dst_dir_fd if parent_comps == () else _open_desc(dst_dir_fd, parent_comps)
                try:
                    st_dst = _os.stat(child_name, dir_fd=dst_p_fd, follow_symlinks=False)
                    final_mode = orig_mode
                    if st_dst.st_mode & _stat.S_ISGID:
                        final_mode |= _stat.S_ISGID
                    if not _has_default_acl_fd(dst_p_fd):
                        _os.chmod(child_name, final_mode, dir_fd=dst_p_fd, follow_symlinks=False)
                finally:
                    if dst_p_fd != dst_dir_fd:
                        _os.close(dst_p_fd)
                src_p_fd = src_dir_fd if parent_comps == () else _open_desc(src_dir_fd, parent_comps)
                try:
                    _os.rmdir(child_name, dir_fd=src_p_fd)
                finally:
                    if src_p_fd != src_dir_fd:
                        _os.close(src_p_fd)


def _translate_destination_permissions_descriptor_relative(
    dir_fd: int,
    caller_umask: int,
    *,
    translate_root: bool = True,
) -> None:
    """Walks destination directory descriptor-relatively and translates modes to caller umask.

    Adjudicates resource-bounded translation:
    - Traversal is post-order for directory modes so parent directories retain search (execute)
      permissions while subdirectories and files are traversed and chmodded, preventing EACCES
      under restrictive umasks like 0700.
    - Uses O(1) live descriptors by closing directory descriptors during step-down traversal.
    - If translate_root is False (e.g. destination directory already existed with intentional
      mode or special bits), dir_fd mode is strictly preserved.
    """
    # 1. Traverse all directories and regular files, translating regular file permissions.
    # Collect discovered subdirectories in top-down order.
    dirs_to_visit: list[tuple[str, ...]] = [()]
    all_subdirs: list[tuple[str, ...]] = []

    while dirs_to_visit:
        rel_parts = dirs_to_visit.pop()
        cur_fd = dir_fd
        owned_cur: int | None = None
        try:
            for part in rel_parts:
                next_fd = _os.open(
                    part,
                    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                    dir_fd=cur_fd,
                )
                if owned_cur is not None:
                    _os.close(owned_cur)
                cur_fd = next_fd
                owned_cur = next_fd

            for name in _os.listdir(cur_fd):
                st = _os.stat(name, dir_fd=cur_fd, follow_symlinks=False)
                if _stat.S_ISLNK(st.st_mode):
                    continue
                elif _stat.S_ISDIR(st.st_mode):
                    child_parts = rel_parts + (name,)
                    dirs_to_visit.append(child_parts)
                    all_subdirs.append(child_parts)
                elif _stat.S_ISREG(st.st_mode):
                    if _has_default_acl_fd(cur_fd):
                        continue  # native creation under a default ACL; chmod would rewrite the mask
                    is_exec = bool(st.st_mode & 0o111)
                    if is_exec:
                        target_mode = (0o666 & ~caller_umask) | 0o111
                    else:
                        target_mode = 0o666 & ~caller_umask
                    try:
                        file_fd = _os.open(
                            name,
                            _os.O_RDONLY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                            dir_fd=cur_fd,
                        )
                        try:
                            _os.fchmod(file_fd, target_mode)
                        finally:
                            _os.close(file_fd)
                    except OSError:
                        pass
        finally:
            if owned_cur is not None:
                try:
                    _os.close(owned_cur)
                except OSError:
                    pass

    # 2. Translate directory permissions in reverse order (deepest directories first, parents last)
    dir_target_mode = 0o777 & ~caller_umask
    for sub_parts in reversed(all_subdirs):
        cur_fd = dir_fd
        owned_cur = None
        try:
            for part in sub_parts:
                next_fd = _os.open(
                    part,
                    _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                    dir_fd=cur_fd,
                )
                if owned_cur is not None:
                    _os.close(owned_cur)
                cur_fd = next_fd
                owned_cur = next_fd
            try:
                if _has_default_acl_fd(cur_fd):
                    continue
                st_cur = _os.fstat(cur_fd)
                final_dir_mode = dir_target_mode
                if st_cur.st_mode & _stat.S_ISGID:
                    final_dir_mode |= _stat.S_ISGID
                _os.fchmod(cur_fd, final_dir_mode)
            except OSError:
                pass
        finally:
            if owned_cur is not None:
                try:
                    _os.close(owned_cur)
                except OSError:
                    pass

    # 3. Translate root directory last, only if translate_root is True
    if translate_root and not _has_default_acl_fd(dir_fd):
        try:
            st_root = _os.fstat(dir_fd)
            final_root_mode = dir_target_mode
            if st_root.st_mode & _stat.S_ISGID:
                final_root_mode |= _stat.S_ISGID
            _os.fchmod(dir_fd, final_root_mode)
        except OSError:
            pass



def materialise_commit_subject_v2(
    *,
    repo_root: Path,
    ref: str,
    destination: Path,
    authorized_storage: AuthorizedGitStorageSetV2 | Sequence[Path | str] | None = None,
    authorized_storage_roots: Sequence[Path | str] | None = None,
) -> MaterialisedCommitSubjectV2:
    """Legacy compatibility wrapper. Do not use for new authoritative checks.

    This function projects the canonical descriptor-bound subject into the
    legacy Path destination. It preserves existing empty directories, but
    remains vulnerable to TOCTOU and namespace swapping AFTER projection.

    Returns a legacy non-authoritative MaterialisedCommitSubjectV2.
    authority_effect: none
    """
    import os as _os
    import errno as _errno
    import shutil as _shutil

    destination = Path(destination)

    # 1. No-follow destination admission check (RejectAllFinalSymlinks)
    # Reject any final-component symlink upfront regardless of target state
    if destination.is_symlink() or _os.path.islink(str(destination)):
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)

    # Check normal existing destination
    if destination.exists():
        if not destination.is_dir():
            raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
        try:
            if any(destination.iterdir()):
                raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
        except OSError as exc:
            raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2) from exc

    # Missing parent behavior: create parents if missing
    destination.parent.mkdir(parents=True, exist_ok=True)

    pool_locator = destination.parent
    pool_fd = _os.open(pool_locator, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_CLOEXEC)
    try:
        workspace = MaterialisationWorkspaceCapabilityV2(pool_fd, pool_locator)
    finally:
        _os.close(pool_fd)

    with workspace:
        with acquire_materialised_commit_subject_v2(
            repo_root=repo_root,
            ref=ref,
            workspace=workspace,
            authorized_storage=authorized_storage,
            authorized_storage_roots=authorized_storage_roots
        ) as capability:
            # Check PATH_MAX limitations before projection using descriptor-relative check
            path_max_limit = -1
            if hasattr(_os, "pathconf"):
                try:
                    path_max_limit = _os.pathconf(str(destination.parent), "PC_PATH_MAX")
                except OSError:
                    pass

            if path_max_limit > 0:
                _check_projected_path_max(capability.root_fd, destination, path_max_limit)

            dest_existed = destination.exists()
            if not dest_existed:
                destination.mkdir()

            dest_fd = _os.open(destination, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_CLOEXEC)
            try:
                dest_st = _os.fstat(dest_fd)
                pool_st = _os.stat(destination.parent)
                has_default_acl = False
                if hasattr(_os, "getxattr"):
                    try:
                        _os.getxattr(dest_fd, "system.posix_acl_default")
                        has_default_acl = True
                    except OSError:
                        pass

                use_copy = bool(
                    (dest_st.st_mode & _stat.S_ISGID)
                    or (dest_st.st_gid != pool_st.st_gid)
                    or (dest_st.st_dev != pool_st.st_dev)
                    or has_default_acl
                )

                moved_children: list[Path] = []
                dest_child: Path | None = None
                try:
                    child_names = _os.listdir(capability.root_fd)
                    for child_name in child_names:
                        dest_child = destination / child_name
                        if use_copy:
                            _copy_entry_descriptor_relative(child_name, capability.root_fd, dest_fd)
                        else:
                            try:
                                _os.rename(
                                    child_name,
                                    child_name,
                                    src_dir_fd=capability.root_fd,
                                    dst_dir_fd=dest_fd,
                                )
                            except OSError as err:
                                if err.errno == _errno.EXDEV:
                                    _copy_entry_descriptor_relative(child_name, capability.root_fd, dest_fd)
                                else:
                                    raise
                        moved_children.append(dest_child)
                        dest_child = None

                    # Translate permissions to caller umask at legacy projection boundary
                    caller_umask = _get_process_umask_non_mutating()
                    _translate_destination_permissions_descriptor_relative(
                        dest_fd, caller_umask, translate_root=not dest_existed
                    )

                except BaseException as exc:
                    if dest_child is not None and (dest_child.is_symlink() or dest_child.exists()) and dest_child not in moved_children:
                        moved_children.append(dest_child)
                    for mc in moved_children:
                        try:
                            if mc.is_symlink() or mc.is_file():
                                mc.unlink()
                            elif mc.is_dir():
                                _shutil.rmtree(mc)
                        except OSError:
                            pass
                    if not dest_existed:
                        try:
                            destination.rmdir()
                        except OSError:
                            pass
                    if not isinstance(exc, Exception):
                        raise
                    if isinstance(exc, SubjectMaterialisationError):
                        raise
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc
            finally:
                _os.close(dest_fd)

            return MaterialisedCommitSubjectV2(
                root=destination,
                commit_sha=capability.commit_sha,
                file_count=capability.file_count
            )


def acquire_materialised_commit_subject_v2(
    *,
    repo_root: Path,
    ref: str,
    workspace: MaterialisationWorkspaceCapabilityV2 | None = None,
    authorized_storage: AuthorizedGitStorageSetV2 | Sequence[Path | str] | None = None,
    authorized_storage_roots: Sequence[Path | str] | None = None,
    max_expanded_entries: int = MAX_EXPANDED_ENTRIES_V2,
    max_expanded_bytes: int = MAX_EXPANDED_BYTES_V2,
) -> MaterialisedCommitSubjectCapabilityV2:
    """`#331-A`: `repo_root` is passed through to
    `open_trusted_object_authority_v2` unchanged and is never `resolve()`d
    here, so this function inherits that authority's locator contract and its
    limitations. Refusals from it arrive as `SUBJECT_TREE_UNREADABLE_REASON_V2`
    with the specific code on `__cause__`.

    Write `ref`'s resolved commit's committed bytes into an empty private
    authority-owned directory.
    """
    if workspace is None:
        raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2)

    # 1. Pinned workspace authority as the FIRST prerequisite
    lease = workspace.pin()
    epoch: MaterialisationEpochV2 | None = None
    cap: MaterialisedCommitSubjectCapabilityV2 | None = None
    transferred_to_caller = False
    try:
        # Determine workspace filesystem NAME_MAX limit before creating epoch
        name_max_limit = -1
        if hasattr(_os, "fpathconf"):
            try:
                name_max_limit = _os.fpathconf(lease.pool_fd, "PC_NAME_MAX")
            except OSError:
                pass
        if name_max_limit <= 0 and hasattr(_os, "pathconf"):
            try:
                name_max_limit = _os.pathconf(str(workspace.pool_locator), "PC_NAME_MAX")
            except OSError:
                pass
        if name_max_limit <= 0:
            name_max_limit = 255

        # 2. Trusted Git authority & canonical trie validation
        content_by_path = None
        try:
            with open_trusted_object_authority_v2(
                repo_root,
                authorized_storage=authorized_storage,
                authorized_storage_roots=authorized_storage_roots,
            ) as authority:
                trusted_root = authority.trusted_repo_root
                commit_sha = resolve_commit_v2(repo_root=trusted_root, ref=ref)
                root_tree_oid = resolve_commit_tree_sha_v2(repo_root=trusted_root, commit_sha=commit_sha)
                trie, _all_entries, leaf_blobs = _build_canonical_trie_hierarchical(
                    repo_root=trusted_root,
                    root_tree_oid=root_tree_oid,
                    max_component_len=name_max_limit,
                    max_expanded_entries=max_expanded_entries,
                )
                content_by_path = read_commit_blobs_v2(
                    repo_root=trusted_root,
                    entries=leaf_blobs,
                    max_expanded_bytes=max_expanded_bytes,
                )

                # Derive maximum allowed symlink target length from workspace filesystem before epoch creation
                max_symlink_target_len = -1
                if hasattr(_os, "fpathconf"):
                    try:
                        max_symlink_target_len = _os.fpathconf(lease.pool_fd, "PC_SYMLINK_MAX")
                    except OSError:
                        pass
                if max_symlink_target_len <= 0 and hasattr(_os, "fpathconf"):
                    try:
                        path_max = _os.fpathconf(lease.pool_fd, "PC_PATH_MAX")
                        if path_max > 1:
                            max_symlink_target_len = path_max - 1
                    except OSError:
                        pass
                if max_symlink_target_len <= 0:
                    max_symlink_target_len = 4095

                # The limit above is an admission policy (PC_SYMLINK_MAX, else
                # PATH_MAX-1, else 4095), not a measurement of the real limit.
                # The size is checked from carrier metadata before any read.
                for entry in leaf_blobs:
                    if entry.mode == SYMLINK_MODE_V2:
                        if (
                            content_by_path.size_of(entry.path) == 0
                            or content_by_path.size_of(entry.path) > max_symlink_target_len
                        ):
                            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                        target_bytes = content_by_path.read_bounded(entry.path, max_symlink_target_len)
                        if b"\x00" in target_bytes:
                            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        except TrustedObjectAuthorityError as exc:
            if content_by_path is not None and hasattr(content_by_path, "close"):
                content_by_path.close()
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
        except BoundedGitError as exc:
            if content_by_path is not None and hasattr(content_by_path, "close"):
                content_by_path.close()
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
        except BaseException:
            if content_by_path is not None and hasattr(content_by_path, "close"):
                content_by_path.close()
            raise

        try:
            # 3. Create epoch root relative to pinned lease
            epoch = MaterialisationEpochV2(lease)
            root_fd, root_name, dest_path = epoch.create_epoch_root()

            # 4. Materialise canonical trie
            written = [0]
            try:
                _materialise_trie_no_follow(trie, content_by_path, root_fd, "", written)
            except Exception as exc:
                if isinstance(exc, SubjectMaterialisationError):
                    raise
                raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

            # 5. Exactly-once ownership transfer commit
            cap = epoch.commit(
                commit_sha=commit_sha,
                file_count=written[0],
                dest_path=dest_path,
            )
            transferred_to_caller = True
            return cap
        finally:
            if content_by_path is not None and hasattr(content_by_path, "close"):
                content_by_path.close()
    finally:
        if not transferred_to_caller:
            if cap is not None:
                try:
                    cap.close()
                except BaseException:
                    pass
            elif epoch is not None:
                epoch.rollback()
            else:
                lease.close()



def compute_subject_digest_v2(subject_root: Path) -> str:
    """Digest a materialised subject's on-disk bytes, deterministically.

    Sorted relative POSIX paths with their file modes and content hashes, so
    the digest is stable across filesystems and independent of directory
    iteration order. Symlinks are hashed as their *target text* rather than
    followed: following them would let a link planted inside the subject
    pull in bytes from outside it and still digest as unchanged.

    NOTE ON TRUST: this function only describes "what is on disk right now".
    It carries no opinion about whether those bytes came from git, and a
    value returned by this function must never be compared against a value
    supplied by an untrusted party as a substitute for re-deriving expected
    content from git directly -- see `commit_derived_execution_identity_v2.py`
    for why that distinction is the whole point of this primitive. Also
    worth stating plainly alongside that: this function's return value is
    exactly 64 lowercase hex characters (a sha256 hexdigest), and a subject
    whose bytes an adversary controls (this function makes no claim about
    subject provenance) makes that value ADVERSARY-INFLUENCED, not merely
    adversary-observable -- it must never be treated as if it were an
    out-of-band-verified anchor merely because it happens to be shape-valid
    hex, for the identical reason `commit_derived_execution_identity_v2.py`'s
    `authorize_commit_for_execution_v2` documents for `trusted_ref_sha`
    (`#313`/`#200-G1C2-F2`): a hostile-derived producer of a shape-valid hex
    string is not an out-of-band trust source, regardless of which function
    produced the string.

    S3 (`#200-G1-S`, issue #305, salvaged from forensic PR #302's finding
    #6, hardened further after independent review of this fix itself found
    a second, narrower gap in the first attempt): git tree entry paths are
    raw bytes and are never required to be valid UTF-8 --
    `list_commit_tree_entries_v2` already decodes them with
    `errors="surrogateescape"` for exactly that reason, and this function's
    own path enumeration (via `pathlib`) round-trips a materialised
    non-UTF-8 name back to a `str` via `os.fsdecode` on POSIX.

    The first fix attempt re-encoded that `str` back to bytes with
    `errors="surrogateescape"`, hardcoded to the ``utf-8`` codec -- correct
    ONLY on a system whose filesystem encoding (`sys.getfilesystemencoding
    ()`) also happens to be UTF-8 (true in this codebase's own CI/dev
    environments, but not a property this primitive is entitled to assume
    of every environment it might run in). On a POSIX system whose
    filesystem encoding is something else, `pathlib` decodes a raw filename
    through THAT codec instead, potentially producing ordinary Unicode
    characters rather than surrogate escapes for the same raw byte -- an
    unconditional ``.encode("utf-8", "surrogateescape")`` would then hash
    DIFFERENT bytes than what is actually on disk (e.g. a raw Latin-1
    ``0xe9`` decodes to ``"\xe9"`` under a Latin-1 filesystem encoding, and
    re-encodes as UTF-8 bytes ``c3 a9`` -- not the original single byte).

    Fixed by using `os.fsencode` instead of a hardcoded codec: `fsencode`
    and `fsdecode` are paired inverses of EACH OTHER, always against
    whatever `sys.getfilesystemencoding()` actually is on the running
    system -- so `fsencode(fsdecode(raw_bytes)) == raw_bytes` by
    construction, regardless of what that encoding happens to be. Symlink
    target text is read via `os.readlink` on the `fsencode`d path directly
    (returns `bytes`, not `str`), for the same reason -- no `str`
    round-trip left anywhere in this function's preimage construction. The
    preimage is now assembled as raw `bytes` throughout, not a `str` joined
    and encoded once at the end, closing this class of gap structurally
    rather than by picking a better guess for the encode call.
    """
    import hashlib
    import os as _os

    # Keyed by the ENCODED path bytes, and sorted by those bytes -- not by
    # the decoded `str`/`Path`. Independent review of the first byte-faithful
    # attempt (PR #306) found that sorting `Path` values first and encoding
    # afterwards leaves a second, subtler environment dependency: on a
    # filesystem whose codec does not preserve raw-byte collation (e.g.
    # CP1252, where raw 0x80 and 0x82 decode to characters that sort in the
    # opposite order), the same raw directory entries concatenate in a
    # different order than on a UTF-8/surrogateescape host, producing a
    # different digest for identical on-disk bytes. Sorting the byte keys
    # makes the ordering a property of the bytes themselves, so it is
    # identical on every host regardless of filesystem encoding.
    keyed_entries: list[tuple[bytes, bytes]] = []
    for path in subject_root.rglob("*"):
        relative = path.relative_to(subject_root).as_posix()
        relative_bytes = _os.fsencode(relative)
        if path.is_symlink():
            target_bytes = _os.readlink(_os.fsencode(path))
            record = b"l\x00" + relative_bytes + b"\x00" + target_bytes
        elif path.is_dir():
            record = b"d\x00" + relative_bytes
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii")
            executable = b"1" if _os.access(path, _os.X_OK) else b"0"
            record = b"f\x00" + relative_bytes + b"\x00" + executable + b"\x00" + digest
        else:
            record = b"?\x00" + relative_bytes
        keyed_entries.append((relative_bytes, record))

    entries = [record for _key, record in sorted(keyed_entries)]
    return hashlib.sha256(b"\n".join(entries)).hexdigest()
