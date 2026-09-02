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
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import BoundedGitError, run_bounded_git_v2

__all__ = [
    "SUBJECT_BLOB_MISSING_REASON_V2",
    "SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2",
    "SUBJECT_PATH_COLLISION_REASON_V2",
    "SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2",
    "SUBJECT_TREE_UNREADABLE_REASON_V2",
    "SUBJECT_UNKNOWN_COMMIT_REASON_V2",
    "MaterialisedCommitSubjectV2",
    "SubjectMaterialisationError",
    "TreeEntryV2",
    "compute_subject_digest_v2",
    "list_commit_tree_entries_v2",
    "materialise_commit_subject_v2",
    "resolve_commit_v2",
]


SUBJECT_UNKNOWN_COMMIT_REASON_V2 = "subject_unknown_commit"
SUBJECT_TREE_UNREADABLE_REASON_V2 = "subject_tree_unreadable"
SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2 = "subject_destination_not_empty"
SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2 = "subject_path_escapes_subject"
SUBJECT_BLOB_MISSING_REASON_V2 = "subject_blob_missing"
SUBJECT_PATH_COLLISION_REASON_V2 = "subject_path_collision"

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
    """
    try:
        completed = run_bounded_git_v2(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo_root
        )
    except BoundedGitError as exc:
        if exc.reason_code == "bounded_git_command_failed":
            raise SubjectMaterialisationError(SUBJECT_UNKNOWN_COMMIT_REASON_V2) from None
        raise
    resolved = completed.stdout.decode("utf-8").strip()
    if len(resolved) != 40 or any(c not in "0123456789abcdef" for c in resolved):
        # Defensive: `rev-parse --verify ...^{commit}` should always return a
        # full 40-hex sha on success. If it ever doesn't, refuse rather than
        # propagate a value nothing downstream can trust as an identity.
        raise SubjectMaterialisationError(SUBJECT_UNKNOWN_COMMIT_REASON_V2)
    return resolved


def list_commit_tree_entries_v2(*, repo_root: Path, commit_sha: str) -> list[TreeEntryV2]:
    """List every blob and gitlink in `commit_sha`'s tree, straight from git.

    `-z` because paths may contain newlines; the non-`-z` form quotes and
    escapes them, and re-decoding that is an avoidable source of divergence
    between what git recorded and what is written out.
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
        # surrogateescape: git paths are bytes. Decoding strictly would refuse
        # a legitimately non-UTF-8 path, which is a property of the commit's
        # history, not an error on our side.
        entries.append(
            TreeEntryV2(
                mode=mode,
                object_type=object_type,
                object_id=object_id,
                path=raw_path.decode("utf-8", "surrogateescape"),
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
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2]
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


def _safe_destination_v2(*, subject_root: Path, relative_path: str) -> Path:
    """Reject any entry that would write outside the subject.

    A path from a repository is untrusted input. `..` segments, or an
    absolute path, would let materialisation write over the caller's
    filesystem, so containment is checked after resolution rather than
    assumed from the string.
    """
    candidate = (subject_root / relative_path).resolve()
    if not candidate.is_relative_to(subject_root.resolve()):
        raise SubjectMaterialisationError(SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2)
    return candidate


def materialise_commit_subject_v2(
    *, repo_root: Path, ref: str, destination: Path
) -> MaterialisedCommitSubjectV2:
    """Write `ref`'s resolved commit's committed bytes into an empty directory.

    The result is severed from `repo_root`: deleting or rewriting the
    original checkout afterwards cannot change what was materialised.
    """
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
    destination.mkdir(parents=True, exist_ok=True)

    commit_sha = resolve_commit_v2(repo_root=repo_root, ref=ref)
    entries = list_commit_tree_entries_v2(repo_root=repo_root, commit_sha=commit_sha)
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2]
    content_by_path = read_commit_blobs_v2(repo_root=repo_root, entries=blobs)

    written = 0
    try:
        for entry in blobs:
            content = content_by_path[entry.path]
            target = _safe_destination_v2(subject_root=destination, relative_path=entry.path)
            # `mkdir(parents=True, exist_ok=True)` can still raise
            # `FileExistsError`: git's own tree-sort comparator treats a
            # subdirectory entry as if it had a trailing "/", so a blob and
            # a tree can share the exact same one-byte name in a single
            # tree object without git considering that a duplicate (proven
            # with real `git mktree` plumbing, not a hypothetical). If the
            # canonically-sorted blob entry is written first, the later
            # entry nested under a tree of the same name collides with it.
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == SYMLINK_MODE_V2:
                # A symlink blob's content is its target path. Recreated as a
                # link so the subject is byte-faithful; the digest below hashes
                # link targets as text rather than following them.
                target.symlink_to(content.decode("utf-8", "surrogateescape"))
            else:
                target.write_bytes(content)
                if entry.mode == EXECUTABLE_MODE_V2:
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            written += 1
    except SubjectMaterialisationError:
        # e.g. `_safe_destination_v2`'s path-escape refusal, raised partway
        # through the loop. Already typed -- clean up and propagate as-is.
        shutil.rmtree(destination, ignore_errors=True)
        raise
    except OSError as exc:
        # Destination is known to have been empty before this call started
        # (checked above), so everything under it at this point was written
        # by this call and is safe to discard -- a caller must never be
        # left holding a partially-materialised subject that looks like it
        # might be valid.
        shutil.rmtree(destination, ignore_errors=True)
        raise SubjectMaterialisationError(SUBJECT_PATH_COLLISION_REASON_V2) from exc

    return MaterialisedCommitSubjectV2(root=destination, commit_sha=commit_sha, file_count=written)


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
    for why that distinction is the whole point of this primitive.

    S3 (`#200-G1-S`, issue #305, salvaged from forensic PR #302's finding
    #6): git tree entry paths are raw bytes and are never required to be
    valid UTF-8 -- `list_commit_tree_entries_v2` already decodes them with
    `errors="surrogateescape"` for exactly that reason, and this function's
    own path enumeration (via `pathlib`) round-trips a materialised
    non-UTF-8 name back to a `str` the same way, by construction of how
    `os.fsdecode` behaves on POSIX. The final encode step here used to
    re-encode with *strict* UTF-8, which cannot represent a lone surrogate
    codepoint produced by that same `surrogateescape` decoding -- crashing
    on a subject materialised from a perfectly legitimate commit. Encoding
    with `errors="surrogateescape"` here too closes the loop: the same
    error handler used to decode a path back into this digest's preimage is
    used to encode it back into bytes, so the digest's preimage is the
    actual path bytes, faithfully, not a lossy or crashing approximation of
    them.
    """
    import hashlib
    import os as _os

    entries: list[str] = []
    for path in sorted(subject_root.rglob("*")):
        relative = path.relative_to(subject_root).as_posix()
        if path.is_symlink():
            entries.append(f"l\x00{relative}\x00{_os.readlink(path)}")
        elif path.is_dir():
            entries.append(f"d\x00{relative}")
        elif path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            executable = "1" if _os.access(path, _os.X_OK) else "0"
            entries.append(f"f\x00{relative}\x00{executable}\x00{digest}")
        else:
            entries.append(f"?\x00{relative}")
    return hashlib.sha256(
        "\n".join(entries).encode("utf-8", "surrogateescape")
    ).hexdigest()
