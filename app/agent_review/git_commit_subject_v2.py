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

import shutil
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import BoundedGitError, run_bounded_git_v2
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
    "SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2",
    "SUBJECT_WORKSPACE_AUTHORITY_REQUIRED_REASON_V2",
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
        self.root_fd = root_fd
        self.root_locator = root_locator
        self.commit_sha = commit_sha
        self.file_count = file_count
        if owns_pool_fd:
            self.pool_fd = pool_fd
        else:
            self.pool_fd = _os.dup(pool_fd)
            _os.set_inheritable(self.pool_fd, False)
        self.root_name = root_name
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

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
            self._closed = True
            root_fd = self.root_fd
            self.root_fd = -1
            pool_fd = self.pool_fd
            self.pool_fd = -1
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
    """Removes all contents of the given directory file descriptor iteratively.

    This is immune to CM-C3-CLEANUP-SWAP because it operates strictly relative
    to file descriptors, never traversing the parent namespace.
    Uses an explicit stack instead of recursion to prevent RecursionError on deep trees.
    Guarantees no file descriptor leaks even on error.
    """
    import os as _os

    # Stack item: [cur_fd, subdirs, name, parent_fd]
    # For the root (level 0), name=None, parent_fd=None. Caller owns root dir_fd.
    root_subdirs: list[str] = []
    try:
        with _os.scandir(dir_fd) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        root_subdirs.append(entry.name)
                    else:
                        _os.unlink(entry.name, dir_fd=dir_fd)
                except OSError:
                    pass
    except OSError:
        pass

    stack: list[list] = [[dir_fd, root_subdirs, None, None]]

    try:
        while stack:
            cur_fd, subdirs, name, parent_fd = stack[-1]
            if subdirs:
                child_name = subdirs.pop()
                try:
                    child_fd = _os.open(
                        child_name,
                        _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW | _os.O_CLOEXEC,
                        dir_fd=cur_fd,
                    )
                except OSError:
                    # Best effort removal if open failed
                    try:
                        _os.rmdir(child_name, dir_fd=cur_fd)
                    except OSError:
                        try:
                            _os.unlink(child_name, dir_fd=cur_fd)
                        except OSError:
                            pass
                    continue

                child_subdirs: list[str] = []
                try:
                    with _os.scandir(child_fd) as it:
                        for entry in it:
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    child_subdirs.append(entry.name)
                                else:
                                    _os.unlink(entry.name, dir_fd=child_fd)
                            except OSError:
                                pass
                except OSError:
                    pass

                stack.append([child_fd, child_subdirs, child_name, cur_fd])
            else:
                frame = stack.pop()
                fd_to_close = frame[0]
                frame[0] = -1
                if frame[3] is not None:
                    try:
                        _os.close(fd_to_close)
                    except OSError:
                        pass
                    try:
                        _os.rmdir(frame[2], dir_fd=frame[3])
                    except OSError:
                        pass
    finally:
        # Guarantee no leaked descriptors if an unexpected error occurs
        for frame in stack[1:]:
            fd = frame[0]
            if fd != -1:
                frame[0] = -1
                try:
                    _os.close(fd)
                except OSError:
                    pass


class OperationWorkspaceLeaseV2:
    """A private, operation-owned lease over a workspace capability.

    Acquired via `workspace.pin()`. Owns a private duplicated file descriptor
    to the workspace pool, ensuring authority continuity even if the underlying
    WorkspaceCapability is closed or concurrent operations run.
    """
    def __init__(self, pool_fd: int, pool_locator: Path) -> None:
        self.pool_fd = pool_fd
        self.pool_locator = pool_locator
        self._closed = False

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

        root_fd = self.root_fd
        root_name = self.root_name
        pool_fd = self.lease.pool_fd

        try:
            cap = MaterialisedCommitSubjectCapabilityV2(
                root_fd=root_fd,
                root_locator=dest_path,
                commit_sha=commit_sha,
                file_count=file_count,
                pool_fd=pool_fd,
                root_name=root_name,
                owns_pool_fd=True,
            )
        except Exception:
            self.rollback()
            raise

        # Ownership transferred successfully
        self.root_fd = -1
        self.root_name = None
        self.lease.pool_fd = -1
        self.lease._closed = True
        self._committed = True
        return cap


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

    def require_open_fd(self) -> int:
        with self._lock:
            if self._closed or self.pool_fd < 0:
                raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2)
            return self.pool_fd

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

            try:
                _os.set_inheritable(pinned_fd, False)
                return OperationWorkspaceLeaseV2(pinned_fd, self.pool_locator)
            except Exception as exc:
                try:
                    _os.close(pinned_fd)
                except OSError:
                    pass
                if isinstance(exc, SubjectMaterialisationError):
                    raise
                raise SubjectMaterialisationError(SUBJECT_WORKSPACE_AUTHORITY_CLOSED_REASON_V2) from exc

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


def list_commit_tree_structure_v2(*, repo_root: Path, commit_sha: str) -> list[TreeEntryV2]:
    """C3-owned structural enumeration using -r -t -z."""
    completed = run_bounded_git_v2(["ls-tree", "-r", "-t", "-z", commit_sha], cwd=repo_root)
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




def read_commit_blobs_v2(
    *, repo_root: Path, entries: list[TreeEntryV2]
) -> dict[str, bytes]:
    """Fetch every blob's raw content in one batched `cat-file` call.

    Keyed by path (not object id) because the caller wants "what is at this
    path in the tree", and a repository can legitimately have two paths
    share a blob (identical file content).
    """
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2 and entry.object_type != "tree"]
    if not blobs:
        return {}
    batch_request = "".join(f"{entry.object_id}\n" for entry in blobs)
    completed = run_bounded_git_v2(
        ["cat-file", "--batch"], cwd=repo_root, input_bytes=batch_request.encode("utf-8")
    )
    stream = completed.stdout
    offset = 0
    content_by_path: dict[str, bytes] = {}
    for entry in blobs:
        header_end = stream.find(b"\n", offset)
        if header_end == -1:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        header = stream[offset:header_end].decode("utf-8", "replace").split(" ")
        if len(header) == 2 and header[1] == "missing":
            # `cat-file --batch` reports an object it cannot find as
            # `<sha> missing` -- the tree named a blob the object store does
            # not have. Distinct from a malformed/unparseable stream.
            raise SubjectMaterialisationError(SUBJECT_BLOB_MISSING_REASON_V2)
        if len(header) != 3:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        size = int(header[2])
        body_start = header_end + 1
        content = stream[body_start : body_start + size]
        # +1 for the newline `cat-file --batch` writes after each object.
        offset = body_start + size + 1
        content_by_path[entry.path] = content
    return content_by_path



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

def _build_and_validate_canonical_trie(entries: list[TreeEntryV2], content_by_path: dict[str, bytes]) -> _TrieNode:
    root = _TrieNode(node_type='tree', mode='040000', object_id='', explicit=True)
    for entry in entries:
        parts = entry.path.split('/')
        if len(parts) > 100:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        if '.' in parts or '..' in parts:
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
        if not parts or any(not p for p in parts):
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)

        current = root
        for i, part in enumerate(parts):
            is_leaf = (i == len(parts) - 1)
            if is_leaf:
                if part in current.children:
                    existing = current.children[part]
                    if existing.node_type != 'tree' or entry.object_type != 'tree':
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    if existing.explicit:
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    existing.mode = entry.mode
                    existing.object_id = entry.object_id
                    existing.explicit = True
                else:
                    if entry.object_type == 'tree':
                        current.children[part] = _TrieNode(node_type='tree', mode=entry.mode, object_id=entry.object_id, explicit=True)
                    elif entry.mode == SYMLINK_MODE_V2:
                        target_bytes = content_by_path.get(entry.path)
                        if not target_bytes or b"\x00" in target_bytes:
                            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                        current.children[part] = _TrieNode(node_type='symlink', mode=entry.mode, object_id=entry.object_id, explicit=True)
                    elif entry.mode == GITLINK_MODE_V2:
                        raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                    else:
                        current.children[part] = _TrieNode(node_type='blob', mode=entry.mode, object_id=entry.object_id, explicit=True)
            else:
                if part not in current.children:
                    current.children[part] = _TrieNode(node_type='tree', mode='040000', object_id='', explicit=False)
                elif current.children[part].node_type != 'tree':
                    raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_TREE_REASON_V2)
                current = current.children[part]
    return root

def _materialise_trie_no_follow(root_node: _TrieNode, content_by_path: dict[str, bytes], initial_dir_fd: int, initial_path: str, count: list[int]) -> None:
    # Use an explicit stack to prevent RecursionError on deeply nested trees.
    # Stack items: (node, dir_fd, current_path, list_of_children)
    # We must dup the dir_fd so we can close it when we pop it from the stack,
    # except for the initial_dir_fd which the caller owns (we will dup it for the root).

    stack = [(root_node, _os.dup(initial_dir_fd), initial_path, list(root_node.children.items()))]

    try:
        while stack:
            node, dir_fd, current_path, children_items = stack[-1]
            if not children_items:
                stack.pop()
                _os.close(dir_fd)
                continue

            name, child = children_items.pop()
            name_bytes = _os.fsencode(name)
            child_path = current_path + "/" + name if current_path else name

            if child.node_type == 'tree':
                try:
                    _os.mkdir(name_bytes, mode=0o777, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    child_fd = _os.open(name_bytes, _os.O_RDONLY | _os.O_DIRECTORY | _os.O_NOFOLLOW, dir_fd=dir_fd)
                except OSError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                # Bind created directories: ensure it is completely empty
                if _os.listdir(child_fd):
                    _os.close(child_fd)
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)

                stack.append((child, child_fd, child_path, list(child.children.items())))

            elif child.node_type == 'symlink':
                content = content_by_path[child_path]
                target = content.decode("utf-8", "surrogateescape")
                try:
                    _os.symlink(target, name_bytes, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                # Revalidate symlink
                stat_name = _os.stat(name_bytes, dir_fd=dir_fd, follow_symlinks=False)
                import stat as _stat
                if not _stat.S_ISLNK(stat_name.st_mode):
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                actual_target = _os.readlink(name_bytes, dir_fd=dir_fd)
                if actual_target != content:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                count[0] += 1

            elif child.node_type == 'blob':
                content = content_by_path[child_path]
                flags = _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL | _os.O_NOFOLLOW
                mode = 0o666
                if child.mode == EXECUTABLE_MODE_V2:
                    mode = 0o777
                try:
                    fd = _os.open(name_bytes, flags, mode, dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc
                except OSError as exc:
                    raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2) from exc

                try:
                    written_bytes = 0
                    while written_bytes < len(content):
                        chunk = _os.write(fd, content[written_bytes:])
                        if chunk == 0:
                            raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                        written_bytes += chunk

                    # Revalidate after writing
                    stat_name = _os.stat(name_bytes, dir_fd=dir_fd, follow_symlinks=False)
                    stat_fd = _os.fstat(fd)
                    if stat_name.st_dev != stat_fd.st_dev or stat_name.st_ino != stat_fd.st_ino:
                        raise SubjectMaterialisationError(SUBJECT_MATERIALISATION_RACE_REASON_V2)
                finally:
                    _os.close(fd)
                count[0] += 1
    except Exception:
        # Clean up any remaining fds in stack
        for _, fd, _, _ in stack:
            try:
                _os.close(fd)
            except OSError:
                pass
        raise

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
    """
    import os as _os
    import shutil as _shutil

    destination = Path(destination).resolve()

    # Missing parent behavior
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not destination.is_dir():
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
    if destination.exists() and any(destination.iterdir()):
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)

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
            # Check PATH_MAX limitations before projection
            path_max_limit = -1
            if hasattr(_os, "pathconf"):
                try:
                    path_max_limit = _os.pathconf(str(destination.parent), "PC_PATH_MAX")
                except OSError:
                    pass
            
            if path_max_limit > 0:
                for root, dirs, files in _os.walk(capability.root_locator):
                    for name in dirs + files:
                        rel_path = _os.path.relpath(_os.path.join(root, name), capability.root_locator)
                        proj_path = destination / rel_path
                        # POSIX PATH_MAX includes the terminating NUL byte.
                        if len(_os.fsencode(proj_path)) >= path_max_limit:
                            raise SubjectMaterialisationError(SUBJECT_LEGACY_PATH_UNREPRESENTABLE_REASON_V2)

            # Project capability into destination
            if not destination.exists():
                destination.mkdir()

            for child in capability.root_locator.iterdir():
                _os.rename(str(child), str(destination / child.name))

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
    epoch = MaterialisationEpochV2(lease)

    try:
        # 2. Trusted Git authority & canonical trie validation
        try:
            with open_trusted_object_authority_v2(
                repo_root,
                authorized_storage=authorized_storage,
                authorized_storage_roots=authorized_storage_roots,
            ) as authority:
                trusted_root = authority.trusted_repo_root
                commit_sha = resolve_commit_v2(repo_root=trusted_root, ref=ref)
                entries = list_commit_tree_structure_v2(repo_root=trusted_root, commit_sha=commit_sha)
                blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2 and entry.object_type != "tree"]
                content_by_path = read_commit_blobs_v2(repo_root=trusted_root, entries=blobs)
        except TrustedObjectAuthorityError as exc:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc

        try:
            trie = _build_and_validate_canonical_trie(entries, content_by_path)
        except Exception as exc:
            if isinstance(exc, SubjectMaterialisationError):
                raise
            raise SubjectMaterialisationError(SUBJECT_UNREPRESENTABLE_REASON_V2) from exc

        # 3. Create epoch root relative to pinned lease
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
        return epoch.commit(
            commit_sha=commit_sha,
            file_count=written[0],
            dest_path=dest_path,
        )
    except Exception:
        epoch.rollback()
        raise



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
