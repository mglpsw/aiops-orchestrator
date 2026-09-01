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

import os
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import BoundedGitError, run_bounded_git_v2
from app.agent_review.git_commit_subject_v2 import (
    EXECUTABLE_MODE_V2,
    GITLINK_MODE_V2,
    SUBJECT_BLOB_MISSING_REASON_V2,
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
    "IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2",
    "IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2",
    "IDENTITY_SYMLINKED_DIRECTORY_REASON_V2",
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
IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2 = "identity_path_escapes_subject"
IDENTITY_SYMLINKED_DIRECTORY_REASON_V2 = "identity_symlinked_directory_in_subject"


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

    def __bool__(self) -> bool:
        # Independent-review finding (correction round): a frozen dataclass
        # is truthy by default regardless of its fields. Without this, a
        # future caller writing `if authorize_commit_for_execution_v2(...):`
        # instead of `.authorized` would always take the "authorized"
        # branch, silently. Not exercised by any call site today, but a
        # footgun worth closing before one exists.
        return self.authorized


def _safe_subject_path_v2(*, subject_root: Path, relative_path: str) -> Path:
    """Reject any tree entry whose path would land outside ``subject_root``.

    A path from a commit's tree is untrusted input, exactly as it is for
    ``git_commit_subject_v2._safe_destination_v2`` during materialisation --
    this function exists because verification must apply the identical
    containment discipline, not because it can borrow that one unchanged
    (that helper resolves a destination being *written*, where the leaf
    typically does not exist yet; this one is checked against a subject
    whose files already exist, some of which may themselves be symlinks).

    Proven necessary, not merely theoretical: ``git mktree`` accepts a
    subtree literally named ``..`` (git only refuses a path *segment*
    containing a literal ``/``, not the two-character name ``..`` on its
    own), and ``git ls-tree -r`` on such a tree emits a flattened entry path
    like ``../evil.py``. ``Path(subject_root) / "../evil.py"`` is not
    rejected by the ``/`` operator (only a truly absolute right-hand side
    would override the left), but the OS resolves the ``..`` on open/stat,
    so an unchecked ``actual_path`` would read from *outside*
    ``subject_root``.

    Containment is decided *lexically*, on ``relative_path`` itself via
    ``posixpath.normpath`` -- deliberately not via ``Path.resolve()`` against
    the filesystem. ``resolve()`` would dereference a symlink sitting at
    ``relative_path`` (a legitimate, already-materialised tracked entry) and
    judge containment by where that symlink's *target* points, which is a
    different question this function must not answer: a symlink tampered to
    point at ``/etc/passwd`` must be caught by the symlink-target-text
    comparison in the caller, tagged with its own reason code, not folded
    into this containment check.
    """
    normalised = posixpath.normpath(relative_path)
    if normalised == ".." or normalised.startswith("../") or posixpath.isabs(normalised):
        raise ExecutedSourceIdentityError(IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2)
    return subject_root / relative_path


def _reachable_leaf_paths_v2(subject_root: Path) -> frozenset[str]:
    """Enumerate every leaf path under ``subject_root`` with ONE traversal
    policy, and refuse outright if any symlinked directory is found.

    Independent-review finding (correction round after the first review
    pair): the original code used two different traversal policies that
    disagreed about what is "under" ``subject_root``. The per-tracked-path
    comparison joined paths with plain ``/`` (which the OS resolves by
    transparently following a symlink in an intermediate component), while
    the "no extra file" scan used ``Path.rglob("*")`` (which does NOT
    descend into a symlinked directory -- it reports the symlink entry
    itself and stops). Replacing a materialised tracked directory with a
    symlink to an attacker directory containing a byte-identical file
    (satisfying the tracked-file comparison) plus an extra untracked file
    made the two checks disagree: the completeness scan never saw the extra
    file, while it was fully reachable by anything that actually opens
    files under ``subject_root`` (e.g. Python's import machinery).

    ``materialise_commit_subject_v2`` never creates a symlinked directory
    itself -- tree structure is always real directories via
    ``mkdir(parents=True)``, and symlinks are only ever created as leaf blob
    entries. A symlinked directory anywhere under ``subject_root`` is
    therefore never something this primitive's own materialisation would
    produce, and is refused unconditionally rather than given a traversal
    policy to disagree about. This also means the per-entry comparison
    loop's plain path joins are safe from this specific class once this
    function has run: nothing left under ``subject_root`` can transparently
    redirect an intermediate path component elsewhere.

    ``os.walk(..., followlinks=False)`` is used rather than
    ``Path.rglob`` for the enumeration itself precisely because it reports
    (without descending into) any symlinked directory in ``dirnames``,
    which is exactly the signal this function needs to refuse on.
    """
    leaf_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(subject_root, followlinks=False):
        current_dir = Path(dirpath)
        for dirname in dirnames:
            if (current_dir / dirname).is_symlink():
                raise ExecutedSourceIdentityError(IDENTITY_SYMLINKED_DIRECTORY_REASON_V2)
        for filename in filenames:
            leaf_paths.append((current_dir / filename).relative_to(subject_root).as_posix())
    return frozenset(leaf_paths)


def loaded_module_files_v2(*, package_prefix: str = "app.agent_review") -> tuple[Path, ...]:
    """Every file currently loaded from the given package prefix.

    Asked of ``sys.modules`` rather than of the filesystem because the
    question is "which code did this interpreter actually import", and only
    the interpreter can answer that. Overridable in tests only to describe a
    synthetic fixture -- production always takes the default, which reads
    real interpreter state.
    """
    discovered: list[Path] = []
    for module_name, module in list(sys.modules.items()):
        if not module_name.startswith(package_prefix):
            continue
        module_file = getattr(module, "__file__", None)
        if module_file is not None:
            discovered.append(Path(module_file))
    return tuple(discovered)


def verify_executed_source_identity_v2(
    *,
    repo_root: Path,
    commit_sha: str,
    subject_root: Path,
    loaded_module_paths: tuple[Path, ...] | None = None,
) -> ExecutedSourceIdentityV2:
    """Prove ``subject_root``'s bytes are exactly ``commit_sha``'s tree.

    Never trusts a pre-computed digest. Re-derives the commit's tree fresh
    from ``repo_root``'s own git object store on every call
    (``list_commit_tree_entries_v2`` + ``read_commit_blobs_v2``, i.e. a
    fresh ``git ls-tree`` + ``git cat-file --batch``) and compares it
    byte-for-byte against what is actually on disk at ``subject_root``. No
    digest, sha, or any other value supplied by a caller as a description of
    ``subject_root``'s content is ever accepted as ground truth -- only
    ``commit_sha`` is used, and only as an input to ``resolve_commit_v2``,
    which re-verifies it names a real commit in ``repo_root``'s own history
    rather than trusting its shape.

    Checks, in order:

    1. ``commit_sha`` resolves to a real commit in ``repo_root`` (never a
       tree or blob sha, and never a value that merely looks like a sha).
    2. ``subject_root`` contains no symlinked directory anywhere --
       ``_reachable_leaf_paths_v2`` walks it with one consistent traversal
       policy and refuses outright if it finds one, closing a mismatch
       between that policy and plain path joining that independent review
       showed could otherwise hide an untracked file behind a symlinked
       directory. Its result is also what check 4 below compares against,
       so both "is everything expected present" and "is nothing unexpected
       present" share the same view of ``subject_root``.
    3. The commit's tree contains no gitlink -- a submodule reference names
       a commit in another repository, which this primitive has no bytes
       for and therefore cannot verify; refused rather than silently
       skipped.
    4. Every tracked path in the commit's tree exists under ``subject_root``
       with byte-identical content (and, for symlinks, byte-identical
       target text) and the mode implied by git (executable bit set iff the
       tree entry is the executable blob mode). This alone makes a
       "narrowed" subject -- one that omits part of the real tree -- an
       *incomplete* subject, refused directly, not merely a subject with a
       digest a checker forgot to look at closely enough. Each tree path is
       resolved through ``_safe_subject_path_v2`` first: a hostile tree can
       contain a subtree literally named ``..`` (git only rejects a literal
       ``/`` inside one path segment, not the two-character name ``..``),
       which ``ls-tree -r`` then flattens into an entry path like
       ``../evil.py`` -- an unchecked join would read from outside
       ``subject_root`` when the OS resolves it. Safe against symlinked-
       directory redirection specifically because check 2 has already
       proven none exist under ``subject_root`` by this point.
    5. ``subject_root`` contains no file absent from the commit's tree --
       an untracked file planted directly into the subject (whether before
       or after materialisation) is refused rather than silently ignored.
       Compares against the SAME leaf-path set check 2 already computed,
       not a second, independently-traversed view of the filesystem.
    6. Every path in ``loaded_module_paths`` (defaulting to
       ``loaded_module_files_v2()``, i.e. real interpreter state) resolves
       under ``subject_root``. Kept as an independent second signal on top
       of (4): "every tracked path is present" and "every loaded module
       lives under the root" are different properties, and neither check is
       asked to cover for the other.
    """
    repo_root = Path(repo_root).resolve()
    subject_root = Path(subject_root).resolve()

    if not subject_root.is_dir():
        raise ExecutedSourceIdentityError(IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2)

    # Runs before anything else touches subject_root's contents: once this
    # has not raised, nothing under subject_root can redirect a plain path
    # join elsewhere, so every later check's use of ordinary path joining is
    # safe against symlinked-directory substitution.
    reachable_leaf_paths = _reachable_leaf_paths_v2(subject_root)

    try:
        resolved_commit = resolve_commit_v2(repo_root=repo_root, ref=commit_sha)
    except SubjectMaterialisationError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_UNKNOWN_COMMIT_REASON_V2) from exc

    try:
        entries = list_commit_tree_entries_v2(repo_root=repo_root, commit_sha=resolved_commit)
    except SubjectMaterialisationError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc

    for entry in entries:
        if entry.mode == GITLINK_MODE_V2:
            raise ExecutedSourceIdentityError(IDENTITY_GITLINK_PRESENT_REASON_V2)

    try:
        expected_content_by_path = read_commit_blobs_v2(repo_root=repo_root, entries=entries)
    except SubjectMaterialisationError as exc:
        if exc.reason_code == SUBJECT_BLOB_MISSING_REASON_V2:
            raise ExecutedSourceIdentityError(IDENTITY_BLOB_MISSING_REASON_V2) from exc
        raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc

    expected_paths = {entry.path: entry for entry in entries}

    for entry in entries:
        if entry.mode == GITLINK_MODE_V2:
            # Defensive only: the early loop above already refuses any
            # commit whose tree contains a gitlink, so this is never reached
            # in practice. Kept so that a future change to (or mutation of)
            # that early check fails closed with a typed refusal here
            # instead of an uncaught KeyError against
            # `expected_content_by_path`, which never has gitlink entries.
            continue
        actual_path = _safe_subject_path_v2(subject_root=subject_root, relative_path=entry.path)
        expected_bytes = expected_content_by_path[entry.path]

        if entry.mode == SYMLINK_MODE_V2:
            if not actual_path.is_symlink():
                raise ExecutedSourceIdentityError(IDENTITY_MISSING_TRACKED_FILE_REASON_V2)
            expected_target = expected_bytes.decode("utf-8", "surrogateescape")
            if os.readlink(actual_path) != expected_target:
                raise ExecutedSourceIdentityError(IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2)
            continue

        if actual_path.is_symlink() or not actual_path.is_file():
            raise ExecutedSourceIdentityError(IDENTITY_MISSING_TRACKED_FILE_REASON_V2)
        if actual_path.read_bytes() != expected_bytes:
            raise ExecutedSourceIdentityError(IDENTITY_CONTENT_MISMATCH_REASON_V2)

        should_be_executable = entry.mode == EXECUTABLE_MODE_V2
        is_executable = os.access(actual_path, os.X_OK)
        if should_be_executable != is_executable:
            raise ExecutedSourceIdentityError(IDENTITY_MODE_MISMATCH_REASON_V2)

    for relative in sorted(reachable_leaf_paths):
        if relative not in expected_paths:
            raise ExecutedSourceIdentityError(IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2)

    if loaded_module_paths is None:
        loaded_module_paths = loaded_module_files_v2()
    for module_path in loaded_module_paths:
        resolved_module_path = Path(module_path).resolve()
        if not resolved_module_path.is_relative_to(subject_root):
            raise ExecutedSourceIdentityError(IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2)

    return ExecutedSourceIdentityV2(commit_sha=resolved_commit, subject_root=subject_root)


def authorize_commit_for_execution_v2(
    *, repo_root: Path, commit_sha: str, trusted_ref: str
) -> ExecutedSourceAuthorizationV2:
    """Is ``commit_sha`` reachable from ``trusted_ref``? Distinct from identity.

    Both ``commit_sha`` and ``trusted_ref`` are independently re-resolved
    against ``repo_root``'s own object store before the ancestry check, so
    this function never evaluates ancestry of an unverified string. It says
    nothing about whether ``commit_sha``'s tree matches any particular bytes
    on disk -- that is ``verify_executed_source_identity_v2``'s job, and the
    two are meant to be composed by the caller, never merged here.
    """
    repo_root = Path(repo_root).resolve()
    try:
        resolved_commit = resolve_commit_v2(repo_root=repo_root, ref=commit_sha)
        resolved_trusted = resolve_commit_v2(repo_root=repo_root, ref=trusted_ref)
    except SubjectMaterialisationError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_UNKNOWN_COMMIT_REASON_V2) from exc

    try:
        run_bounded_git_v2(
            ["merge-base", "--is-ancestor", resolved_commit, resolved_trusted],
            cwd=repo_root,
        )
        authorized = True
    except BoundedGitError as exc:
        if exc.reason_code == "bounded_git_command_failed":
            authorized = False
        else:
            raise

    return ExecutedSourceAuthorizationV2(
        commit_sha=resolved_commit,
        trusted_ref=trusted_ref,
        trusted_ref_sha=resolved_trusted,
        authorized=authorized,
    )
