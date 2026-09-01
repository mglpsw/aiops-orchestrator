"""`#200-G1` -- bind executed source identity to git commit authority.

## Predecessor and why it was refuted

`operational_inner_control_v2.py` (`#277`, frozen forensic, DO NOT resurrect
by that name or reuse its verification logic as authority) modelled identity
as ``bytes + caller-supplied document -> claimed commit``: a caller (the
outer half of a two-process design) declared a ``subject_root``, a
``declared_toolrepo_sha`` and a ``subject_digest``, and the inner process
checked that (a) its own loaded code lived under the declared root and (b)
hashing the declared root's on-disk bytes reproduced the declared digest.

Round 1 refuted (a) alone: the declared root was narrowed to a subdirectory
containing only the entry script, so the check passed while the digest
covered none of the real semantic package.

Round 2, after (a) was fixed to require every loaded module under the root,
refuted the combination anyway, by fabrication: ``declared_toolrepo_sha`` was
never checked against anything beyond a 40-hex-character shape. An attacker
tampers a module inside a *correctly declared* root, recomputes the digest
honestly with this codebase's own public digest helper over the *tampered*
tree, and declares the real, honestly-committed HEAD sha. Every check in the
predecessor passes. The artifact then claims an identity (the real sha) that
the executed bytes do not have.

The common cause: identity was established by comparing two values the same
untrusted party could both supply (a claimed digest, checked only against
itself) rather than by asking git what a commit's bytes actually are.

## This module's design

```
AUTHORIZED COMMIT -> GIT OBJECTS OF THAT COMMIT -> MATERIALIZED BYTES
                   -> EXECUTED BYTES -> ARTIFACT IDENTITY
```

Direction is commit -> bytes, never bytes + document -> claimed commit.
``verify_executed_source_identity_v2`` never accepts a pre-computed digest as
ground truth. Given a commit sha and the toolrepo's own git repository, it
independently re-derives -- fresh, on every call, straight from
``git ls-tree`` / ``git cat-file`` against that repository's own object store
-- what that commit's tree actually contains, and compares it byte-for-byte
against what is actually sitting on disk at ``subject_root``. There is no
digest field an attacker can fabricate, because there is no declared digest
in the trust path at all: the comparison is always against freshly-read git
object content.

## IDENTITY is not AUTHORIZATION

This module deliberately keeps two questions apart and never collapses them
into one boolean:

``ExecutedSourceIdentityV2`` / ``verify_executed_source_identity_v2``
    IDENTITY: which commit produced the bytes that are executing right now.
    A fact derivable entirely from the toolrepo's own git object store plus
    what is actually on disk. Says nothing about whether that commit was
    *supposed* to run.

``ExecutedSourceAuthorizationV2`` / ``authorize_commit_for_execution_v2``
    AUTHORIZATION: whether a given (already-identified) commit is permitted
    for this invocation -- e.g. reachable from a trusted ref such as
    ``refs/heads/master``. Meaningless applied to an unverified sha, and does
    not imply identity: a commit can be a perfectly legitimate, unauthorized
    feature-branch tip.

A caller that wants an overall accept/refuse decision composes both
results explicitly; this module does not do that composition for it.

## Threat scope

In scope: ``hostile_target_checkout`` (git-level tricks against the
repository being read), ``hostile_environment`` (ambient env/PATH
poisoning), ``ordinary_caller_forgery`` (a caller declares an sha/root/digest
that does not match reality). ``mutable_dev_checkout`` must never define
executed identity -- identity comes from git objects, never from worktree
state.

Out of scope: ``host_arbitrary_code_attacker``. Someone who can already run
arbitrary code with this process's privileges does not need to forge an
identity check to do so; that is not a boundary this module claims to hold.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.git_commit_subject_v2 import (
    EXECUTABLE_MODE_V2,
    GITLINK_MODE_V2,
    SYMLINK_MODE_V2,
    SubjectMaterialisationError,
    list_commit_tree_entries_v2,
    read_commit_blobs_v2,
    resolve_commit_v2,
)

__all__ = [
    "IDENTITY_BLOB_MISSING_REASON_V2",
    "IDENTITY_CONTENT_MISMATCH_REASON_V2",
    "IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2",
    "IDENTITY_GITLINK_PRESENT_REASON_V2",
    "IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2",
    "IDENTITY_MISSING_TRACKED_FILE_REASON_V2",
    "IDENTITY_MODE_MISMATCH_REASON_V2",
    "IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2",
    "IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2",
    "IDENTITY_TREE_UNREADABLE_REASON_V2",
    "IDENTITY_UNKNOWN_COMMIT_REASON_V2",
    "ExecutedSourceAuthorizationV2",
    "ExecutedSourceIdentityError",
    "ExecutedSourceIdentityV2",
    "authorize_commit_for_execution_v2",
    "loaded_module_files_v2",
    "verify_executed_source_identity_v2",
]


IDENTITY_UNKNOWN_COMMIT_REASON_V2 = "identity_unknown_commit"
IDENTITY_BLOB_MISSING_REASON_V2 = "identity_blob_missing"
IDENTITY_TREE_UNREADABLE_REASON_V2 = "identity_tree_unreadable"
IDENTITY_GITLINK_PRESENT_REASON_V2 = "identity_gitlink_present"
IDENTITY_MISSING_TRACKED_FILE_REASON_V2 = "identity_missing_tracked_file"
IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2 = "identity_extra_untracked_file"
IDENTITY_CONTENT_MISMATCH_REASON_V2 = "identity_content_mismatch"
IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2 = "identity_symlink_target_mismatch"
IDENTITY_MODE_MISMATCH_REASON_V2 = "identity_mode_mismatch"
IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2 = "identity_loaded_code_outside_subject"
IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2 = "identity_subject_root_unreadable"


class ExecutedSourceIdentityError(ValueError):
    """Identity could not be established. Content-free reason code only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExecutedSourceIdentityV2:
    """IDENTITY only: which commit produced the bytes now on disk.

    Never carries an opinion about whether that commit was permitted to run
    -- see ``ExecutedSourceAuthorizationV2`` for that separate question.
    """

    commit_sha: str
    subject_root: Path


@dataclass(frozen=True)
class ExecutedSourceAuthorizationV2:
    """AUTHORIZATION only: is this (already-identified) commit permitted.

    Never establishes identity by itself -- checking ancestry of an
    unverified sha proves nothing about what actually executed.
    """

    commit_sha: str
    trusted_ref: str
    trusted_ref_sha: str
    authorized: bool


def loaded_module_files_v2(*, package_prefix: str = "app.agent_review") -> tuple[Path, ...]:
    """Every file currently loaded from the given package prefix.

    Asked of ``sys.modules`` rather than of the filesystem because the
    question is "which code did this interpreter actually import", and only
    the interpreter can answer that. Overridable in tests only to describe a
    synthetic fixture -- production always takes the default, which reads
    real interpreter state.
    """
    raise NotImplementedError("stub: RED phase, not yet implemented")


def verify_executed_source_identity_v2(
    *,
    repo_root: Path,
    commit_sha: str,
    subject_root: Path,
    loaded_module_paths: tuple[Path, ...] | None = None,
) -> ExecutedSourceIdentityV2:
    """Prove ``subject_root``'s bytes are exactly ``commit_sha``'s tree.

    Never trusts a pre-computed digest. Re-derives the commit's tree fresh
    from ``repo_root``'s own git object store on every call and compares it
    byte-for-byte against what is actually on disk at ``subject_root``.
    """
    raise NotImplementedError("stub: RED phase, not yet implemented")


def authorize_commit_for_execution_v2(
    *, repo_root: Path, commit_sha: str, trusted_ref: str
) -> ExecutedSourceAuthorizationV2:
    """Is ``commit_sha`` reachable from ``trusted_ref``? Distinct from identity."""
    raise NotImplementedError("stub: RED phase, not yet implemented")
