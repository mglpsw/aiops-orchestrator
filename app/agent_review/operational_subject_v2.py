"""`#200-F` §11 -- controlled subjects, reconstructed and requalified.

Ported from `#276` with revalidation. Surviving adversarial review there
qualifies nothing here; every property below has new tests.

## Why ``ls-tree`` + ``cat-file`` and not ``git archive``

The predecessor materialised subjects with ``git archive``. That reads the
committed tree, which is the important part -- an uncommitted worktree
modification is invisible to it, and that property is kept. But ``git
archive`` also *applies* ``.gitattributes``: ``export-ignore`` removes paths
from the output and ``export-subst`` rewrites their content. For a **target**
repository, whose ``.gitattributes`` is attacker-influenced material, that
means the repository under review could omit its own files from the subject
the reviewer sees, and the reviewer would have no way to notice.

Enumerating the object list with ``ls-tree -r`` and fetching bytes with
``cat-file`` ignores ``.gitattributes`` entirely. Several red-corpus entries
stop being *defended* and become *structurally unreachable*:

``assume-unchanged`` / ``skip-worktree``
    index bits. A commit's tree has no index, so they cannot participate.
``export-ignore`` / ``export-subst``
    only consulted by ``archive``, which is not used.
``.gitattributes`` filters / textconv
    not consulted when reading raw blobs.

Unreachable-by-construction is worth more than one more check, because it
cannot regress when somebody edits a check.

## Source severance

The controlled target subject holds bytes, not references. Once materialised,
deleting or rewriting the original checkout cannot change what the run
reviews. This is what makes a review reproducible from its artifact rather
than from a directory that has since moved on.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.operational_bounded_git_v2 import (
    BoundedGitError,
    run_bounded_git_v2,
)
from app.agent_review.operational_inner_control_v2 import compute_subject_digest_v2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

__all__ = [
    "SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2",
    "SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2",
    "SUBJECT_TREE_UNREADABLE_REASON_V2",
    "SUBJECT_UNKNOWN_COMMIT_REASON_V2",
    "ControlledTargetSubjectV2",
    "SubjectMaterialisationError",
    "ToolrepoExecutionSubjectV2",
    "materialise_controlled_target_subject_v2",
    "materialise_toolrepo_execution_subject_v2",
]


SUBJECT_UNKNOWN_COMMIT_REASON_V2 = "subject_unknown_commit"
SUBJECT_TREE_UNREADABLE_REASON_V2 = "subject_tree_unreadable"
SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2 = "subject_destination_not_empty"
SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2 = "subject_path_escapes_subject"

_GITLINK_MODE_V2 = "160000"
_SYMLINK_MODE_V2 = "120000"
_EXECUTABLE_MODE_V2 = "100755"


class SubjectMaterialisationError(ExpectedOperationalRefusalV2, ValueError):
    """A subject could not be materialised from committed bytes."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ControlledTargetSubjectV2:
    """The target repository's committed bytes, severed from their source."""

    root: Path
    head_sha: str
    file_count: int


@dataclass(frozen=True)
class ToolrepoExecutionSubjectV2:
    """The exact toolrepo bytes an inner epoch executes from.

    ``subject_digest`` is what makes ``toolrepo_sha`` verifiable rather than
    merely declared: the inner recomputes it from the materialised tree.
    """

    root: Path
    toolrepo_sha: str
    subject_digest: str


def _resolve_commit_v2(*, repo_root: Path, sha: str) -> str:
    """Confirm the sha names a commit in this repository.

    ``^{commit}`` is required rather than accepting any object: a tree or blob
    sha would otherwise materialise something that is not a revision, and the
    artifact would name an identity no history contains.
    """
    try:
        completed = run_bounded_git_v2(
            ["rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"], cwd=repo_root
        )
    except BoundedGitError as exc:
        if exc.reason_code == "bounded_git_command_failed":
            raise SubjectMaterialisationError(SUBJECT_UNKNOWN_COMMIT_REASON_V2) from None
        raise
    return completed.stdout.decode("utf-8").strip()


def _tree_entries_v2(*, repo_root: Path, commit: str) -> list[tuple[str, str, str, str]]:
    """List every blob and gitlink in the commit's tree.

    ``-z`` because paths may contain newlines; the non-``-z`` form quotes and
    escapes them, and re-decoding that is an avoidable source of divergence
    between what git recorded and what is written out.
    """
    completed = run_bounded_git_v2(["ls-tree", "-r", "-z", commit], cwd=repo_root)
    entries: list[tuple[str, str, str, str]] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("utf-8").split(" ", 2)
        except ValueError as exc:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2) from exc
        # surrogateescape: git paths are bytes. Decoding strictly would refuse
        # a legitimately non-UTF-8 path, which is a property of the target's
        # history and not an error on our side.
        entries.append(
            (mode, object_type, object_id, raw_path.decode("utf-8", "surrogateescape"))
        )
    return entries


def _safe_destination_v2(*, subject_root: Path, relative_path: str) -> Path:
    """Reject any entry that would write outside the subject.

    A path from a repository is untrusted input. ``..`` segments, or an
    absolute path, would let materialisation write over the caller's
    filesystem, so containment is checked after resolution rather than
    assumed from the string.
    """
    candidate = (subject_root / relative_path).resolve()
    if not candidate.is_relative_to(subject_root.resolve()):
        raise SubjectMaterialisationError(SUBJECT_PATH_ESCAPES_SUBJECT_REASON_V2)
    return candidate


def _materialise_tree_v2(*, repo_root: Path, sha: str, destination: Path) -> tuple[str, int]:
    """Write a commit's committed bytes into an empty destination."""
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
    destination.mkdir(parents=True, exist_ok=True)

    commit = _resolve_commit_v2(repo_root=repo_root, sha=sha)
    entries = _tree_entries_v2(repo_root=repo_root, commit=commit)

    # Gitlinks name commits in another repository; there are no bytes here to
    # write. They are counted by the scope authority, not materialised.
    blobs = [entry for entry in entries if entry[0] != _GITLINK_MODE_V2]
    if not blobs:
        return commit, 0

    batch_request = "".join(f"{object_id}\n" for _, _, object_id, _ in blobs)
    completed = run_bounded_git_v2(
        ["cat-file", "--batch"], cwd=repo_root, input_bytes=batch_request.encode("utf-8")
    )

    stream = completed.stdout
    offset = 0
    written = 0
    for mode, _object_type, _object_id, relative_path in blobs:
        header_end = stream.find(b"\n", offset)
        if header_end == -1:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        header = stream[offset:header_end].decode("utf-8", "replace").split(" ")
        if len(header) != 3:
            raise SubjectMaterialisationError(SUBJECT_TREE_UNREADABLE_REASON_V2)
        size = int(header[2])
        body_start = header_end + 1
        content = stream[body_start : body_start + size]
        # +1 for the newline `cat-file --batch` writes after each object.
        offset = body_start + size + 1

        target = _safe_destination_v2(subject_root=destination, relative_path=relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == _SYMLINK_MODE_V2:
            # A symlink blob's content is its target path. Recreated as a link
            # so the subject is byte-faithful; the digest hashes link targets
            # as text rather than following them.
            target.symlink_to(content.decode("utf-8", "surrogateescape"))
        else:
            target.write_bytes(content)
            if mode == _EXECUTABLE_MODE_V2:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written += 1

    return commit, written


def materialise_controlled_target_subject_v2(
    *, target_root: Path, head_sha: str, destination: Path
) -> ControlledTargetSubjectV2:
    """Materialise the target's committed bytes at ``head_sha``.

    The result is severed from ``target_root``: deleting or rewriting the
    original checkout afterwards cannot change what the run reviews.
    """
    commit, written = _materialise_tree_v2(
        repo_root=Path(target_root), sha=head_sha, destination=Path(destination)
    )
    return ControlledTargetSubjectV2(
        root=Path(destination), head_sha=commit, file_count=written
    )


def materialise_toolrepo_execution_subject_v2(
    *, toolrepo_root: Path, toolrepo_sha: str, destination: Path
) -> ToolrepoExecutionSubjectV2:
    """Materialise the exact toolrepo bytes an inner epoch will execute.

    The digest is computed *after* writing, from the bytes on disk, so it
    describes what will actually run rather than what was requested.
    """
    commit, _written = _materialise_tree_v2(
        repo_root=Path(toolrepo_root), sha=toolrepo_sha, destination=Path(destination)
    )
    return ToolrepoExecutionSubjectV2(
        root=Path(destination),
        toolrepo_sha=commit,
        subject_digest=compute_subject_digest_v2(Path(destination)),
    )
