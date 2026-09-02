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

## What this module does NOT prove

The chain this module proves stops at MATERIALIZED BYTES: "the bytes
currently sitting at ``subject_root`` are exactly ``commit_sha``'s tree,
re-derived fresh from git's own object store at the moment this function is
called." It does **not** extend that chain to EXECUTED BYTES -- it says
nothing about whether an *already-running* interpreter previously loaded
those bytes, or loaded something else before this check ran, or will still
be running the same bytes by the time a caller acts on the result. A caller
that needs "what a fresh process loads matches what was verified" composes
this primitive with a fresh-process launch that verifies before importing
anything; a caller that needs "what an *already-running* interpreter has
already executed matches some commit" is asking a question this module was
never designed to answer, and no wording change here can make it answer it.
That second question -- execution provenance for a process that may already
be running -- is tracked separately as `#301` (`#200-G1B`); it is a
different layer, not a stricter version of what this module proves, and is
not conflated with it here.

## IDENTITY is not AUTHORIZATION

This module deliberately keeps two questions apart and never collapses them
into one boolean:

``ExecutedSourceIdentityV2`` / ``verify_executed_source_identity_v2``
    IDENTITY: that ``commit_sha``'s tree matches, byte-for-byte, the bytes
    currently materialized at ``subject_root``, as of the moment this
    function is called. This is TREE EQUALITY, not unique provenance: if
    another commit happens to share the exact same tree (e.g. an empty
    commit, or identical content committed twice under different messages
    or on different branches), that other commit's sha would pass this same
    check against the same on-disk bytes just as validly -- this function
    proves a match against the specific ``commit_sha`` the caller supplied,
    never that ``commit_sha`` is the only commit that could explain what is
    on disk. A fact derivable entirely from the toolrepo's own git object
    store plus what is actually on disk. Says nothing about whether that
    commit was *supposed* to run, and says nothing about what any
    interpreter -- already running or not -- has actually loaded into
    memory (see "What this module does NOT prove" above).

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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import run_bounded_git_v2
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
    "IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2",
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
    "IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2",
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
# S2 (#200-G1-S / issue #305): distinct from every other reason above --
# raised when the completeness traversal itself could not enumerate a
# directory (e.g. a permission error), which is NOT the same fact as "that
# directory is empty". Never silently folded into a clean pass.
IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2 = "identity_traversal_unreadable"
# S4 (#200-G1-S / issue #305): distinct from `authorized=False`. Raised when
# `authorize_commit_for_execution_v2` cannot obtain a definitive ancestry
# answer -- e.g. a shallow or otherwise incomplete history -- rather than
# silently reporting a clean negative for a question it could not actually
# answer. `PROVEN_NOT_ANCESTOR != COULD_NOT_PROVE_ANCESTRY`.
IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2 = "identity_authorization_undetermined"
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
    """IDENTITY only: ``commit_sha``'s tree matches the bytes now on disk.

    Tree equality, not unique provenance -- a different commit sharing the
    exact same tree would pass this same check against the same bytes. Never
    carries an opinion about whether that commit was permitted to run -- see
    ``ExecutedSourceAuthorizationV2`` for that separate question.
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
    policy to disagree about.

    Called by ``verify_executed_source_identity_v2`` LAST, with a fresh
    walk, deliberately not before the per-entry comparison loop (round-2
    independent review: calling it only once, early, left the rest of the
    function -- including subprocess calls to git -- as an open window in
    which a concurrent writer could add a file a start-of-call snapshot
    would never see; see that function's docstring, check 4). This function
    does not make any claim about what happens *during* the per-entry loop
    that runs before it; it only guarantees that whatever is actually under
    ``subject_root`` at the moment IT runs -- including anything introduced
    partway through the call -- is what gets compared against the commit's
    tree for completeness.

    A manual, recursive ``os.scandir``-based walk is used rather than
    ``os.walk`` or ``Path.rglob`` for the enumeration itself -- see the S2
    note below for why ``os.walk`` alone is not enough to fail closed here.

    S2 (``#200-G1-S``, issue #305, salvaged from forensic PR #302's finding
    #4, hardened further after independent review of this fix itself found
    a second, narrower gap in the first attempt): "cannot enumerate a
    directory" is not the same fact as "directory is empty" -- an unreadable
    subtree must contribute a typed refusal, never zero leaf paths as if it
    had been checked and found empty. `os.walk`'s default behaviour on a
    directory it cannot enumerate (e.g. a permission error during
    ``scandir``) is to silently skip it; passing an ``onerror`` callback
    closes that gap. But CPython's ``os.walk`` ALSO separately catches an
    ``OSError`` from classifying an already-enumerated entry (its internal
    ``entry.is_dir()`` call, used to sort each name into `dirnames` or
    `filenames`) and silently treats that entry as a non-directory --
    `onerror` is never invoked for that failure, only for the ``scandir``
    call itself. A tracked directory whose classification fails at exactly
    that moment (e.g. a race, a stale NFS handle, a mid-walk permission
    change) would be added to this function's leaf-path set under its OWN
    name (not descended into), which is invisible unless that bare name
    happens to coincide with an actual tracked leaf path -- silently
    skipping the subtree's real completeness check either way. Reimplemented
    as an explicit recursive walk over ``os.scandir`` so BOTH failure points
    -- the initial ``scandir`` call and each entry's own
    ``is_symlink``/``is_dir`` classification -- are wrapped and raise the
    same typed refusal, with no CPython-internal fallback path left that
    this module does not control.
    """

    leaf_paths: list[str] = []

    def _walk(directory: Path) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
        for entry in entries:
            try:
                # `is_dir()` follows symlinks by default, matching what
                # `os.walk` itself classifies as a directory entry (a
                # symlink-to-directory is still sorted into `dirnames`,
                # just not recursed into when `followlinks=False`) --
                # `is_symlink()` is checked separately so a symlinked
                # directory is refused outright rather than given a
                # traversal policy to disagree about (see this function's
                # docstring above).
                is_symlink = entry.is_symlink()
                is_dir = entry.is_dir()
            except OSError as exc:
                raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
            entry_path = Path(entry.path)
            if is_dir:
                if is_symlink:
                    raise ExecutedSourceIdentityError(IDENTITY_SYMLINKED_DIRECTORY_REASON_V2)
                _walk(entry_path)
            else:
                leaf_paths.append(entry_path.relative_to(subject_root).as_posix())

    _walk(subject_root)
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
    2. The commit's tree contains no gitlink -- a submodule reference names
       a commit in another repository, which this primitive has no bytes
       for and therefore cannot verify; refused rather than silently
       skipped.
    3. Every tracked path in the commit's tree exists under ``subject_root``
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
       ``subject_root`` when the OS resolves it.
    4. ``subject_root`` contains no symlinked directory anywhere, and no
       file absent from the commit's tree. Both checked together by
       ``_reachable_leaf_paths_v2``, deliberately called LAST -- as close to
       return as this function's structure allows -- with a FRESH walk, not
       one taken at call start. Independent review (round 1) found that
       checking this once, early, let a symlinked directory's transparent
       following by check 3's plain path joins disagree with an
       early-computed "what's present" view. Independent review (round 2)
       found a narrower but real follow-on: even after that fix, checking
       completeness once at call start left everything after it (commit
       resolution, tree listing, blob reads, all of check 3) as an open
       window in which a concurrent writer with access to ``subject_root``
       could add a file that a start-of-call snapshot would never see.
       Running this check last, against the filesystem as it is at that
       moment, does not eliminate every conceivable race (no check-then-use
       pattern can, without a filesystem-level lock this primitive does not
       take), but it collapses the window from "this function's entire
       duration, including subprocess calls to git" to "the checks between
       here and return", and a symlinked directory introduced earlier in
       the call to redirect an earlier comparison is still caught here, as
       long as it has not ALSO been removed again by the time this runs.
    5. Every path in ``loaded_module_paths`` (defaulting to
       ``loaded_module_files_v2()``, i.e. real interpreter state) resolves
       under ``subject_root``. Kept as an independent second signal on top
       of (3): "every tracked path is present" and "every loaded module
       lives under the root" are different properties, and neither check is
       asked to cover for the other.
    """
    repo_root = Path(repo_root).resolve()
    subject_root = Path(subject_root).resolve()

    if not subject_root.is_dir():
        raise ExecutedSourceIdentityError(IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2)

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

    # Deliberately called here, last, with a fresh walk -- not at call
    # start. See check 4 in the docstring above for why: this is what
    # closes the round-2 TOCTOU gap on top of round 1's static fix. A
    # symlinked directory introduced at ANY point before this line, and
    # still present when this line runs, is caught here regardless of
    # whether it existed for the whole call or was introduced moments ago.
    reachable_leaf_paths = _reachable_leaf_paths_v2(subject_root)
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


def _history_is_shallow_v2(*, repo_root: Path) -> bool:
    """Is ``repo_root`` a shallow (history-truncated) checkout at all?

    ``git rev-parse --is-shallow-repository`` prints exactly ``true`` or
    ``false``. A shallow repository can hold a commit object whose own
    parent field genuinely names another commit that is ALSO separately
    present locally (e.g. fetched as its own independent shallow tip via a
    different ref) without the edge between them being traversable: the
    shallow boundary makes git treat the boundary commit as having no
    parents for graph-walking purposes, full stop, regardless of what is
    separately present in the object store. That is what makes a shallow
    history's negative ``merge-base --is-ancestor`` answers unsafe to trust
    without this check.

    Independent-review finding (P2) on this PR's own external review (PR
    #306, on top of `#200-G1-S` / issue #305): this probe is itself a git
    invocation and can itself fail (an older git lacking the flag, a
    repository that becomes unreadable between the two commands, ...).
    ``check=False`` was used to read the exit code without an exception, but
    nothing inspected it -- a failed probe's empty/garbage stdout does not
    equal the literal string ``"true"``, so it silently evaluated as "not
    shallow", defeating S4's whole distinction for exactly the failure mode
    S4 exists to catch. Any non-zero exit or any output other than the two
    tokens git actually documents is now refused outright, the same as the
    ancestry check's own undetermined path -- never treated as a confident
    "not shallow".
    """
    completed = run_bounded_git_v2(
        ["rev-parse", "--is-shallow-repository"], cwd=repo_root, check=False
    )
    output = completed.stdout.decode("utf-8", "surrogateescape").strip()
    if completed.returncode != 0 or output not in ("true", "false"):
        raise _AncestryUndeterminedV2()
    return output == "true"


class _AncestryUndeterminedV2(Exception):
    """Internal signal only: some check inside ``_ancestry_negative_is_
    trustworthy_v2`` could not affirmatively confirm completeness. Never
    escapes ``authorize_commit_for_execution_v2`` -- it is always converted
    to ``ExecutedSourceIdentityError(IDENTITY_AUTHORIZATION_UNDETERMINED_
    REASON_V2)`` at that single call site, so there is exactly one place in
    this module that raises the public, typed refusal for this property.
    """


def _ancestry_negative_is_trustworthy_v2(
    *, repo_root: Path, merge_base_result: subprocess.CompletedProcess
) -> bool:
    """Positively establish whether a ``merge-base --is-ancestor`` exit-1
    result may be trusted as a clean ``authorized=False``.

    Deliberately shaped as ONE gate that must affirmatively return ``True``
    -- completeness POSITIVELY confirmed -- rather than a growing list of
    individually-named bad conditions that defaults to "trust it" for
    anything not yet on the list. Independent review of an earlier version
    of this fix (this PR's own external review, PR #306) found a second
    real gap after the first correction: enumerating "shallow" as the one
    known cause of an untrustworthy negative missed a corrupt-but-not-
    shallow object store (a real, reachable parent commit object deleted)
    as a second, independent cause. Enumerating causes one at a time is
    exactly the antipattern this module's own history (`#303`/`#304`'s
    STOP disposition on a structurally similar problem) warns against: the
    next unenumerated cause would silently fall through to "trusted" again.

    This function's default is refusal, not trust: every branch below ends
    in ``return False`` unless every check it knows how to run affirmatively
    succeeded. A future git behaviour this function does not yet have a
    name for does not need a new branch added here to fail safely -- it
    already does, because nothing affirmatively confirmed it, and the
    caller (``authorize_commit_for_execution_v2``) never inverts ``False``
    from this function into anything but "undetermined".

    Two independent checks currently compose the affirmative confirmation,
    both empirically grounded, not assumed:

    1. The ancestor check's own diagnostic output. A clean negative is
       always silent (verified with a real corrupted object store: a
       reachable-but-deleted parent commit object makes ``merge-base
       --is-ancestor`` exit 1 WITH ``error: Could not read <sha>`` on
       stderr; a genuine non-ancestor in a complete history exits 1 with
       EMPTY stderr). Any stderr at all means the graph was not actually
       fully walked -- not confirmed, regardless of shallow status.
    2. Shallow-history confirmation (``_history_is_shallow_v2``). A shallow
       clone's truncation is a normal boundary condition, not an error --
       git exits 1 SILENTLY, indistinguishable from a genuine non-ancestor
       by stderr alone, so it needs its own independent probe. If that
       probe itself cannot give a definitive answer (``_AncestryUndetermined
       V2``, e.g. an older git or an unreadable repository), that is ALSO a
       failure to confirm, not a reason to assume "not shallow".
    """
    if merge_base_result.stderr:
        return False
    try:
        is_shallow = _history_is_shallow_v2(repo_root=repo_root)
    except _AncestryUndeterminedV2:
        return False
    return not is_shallow


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

    S4 (``#200-G1-S``, issue #305, salvaged from forensic PR #302's finding
    #7, ported here as the FINAL qualified form after two prior intermediate
    attempts were each found to have their own false negative -- first a
    shallow-history gap, then a corrupt-but-not-shallow gap in the fix for
    THAT): ``git merge-base --is-ancestor`` exits 1 both when ``commit_sha``
    genuinely is not an ancestor of ``trusted_ref`` in a complete history,
    AND when the history available to this call is too incomplete to
    determine that at all -- git's exit code alone does not distinguish
    "proven not an ancestor" from "could not prove ancestry". Collapsing
    both into ``authorized=False`` would silently treat "I don't know" as a
    confident negative.

    An exit-1 answer is trusted as a clean ``authorized=False`` ONLY when
    ``_ancestry_negative_is_trustworthy_v2`` affirmatively confirms it --
    see that function's own docstring for why this is deliberately ONE gate
    that must positively succeed, not a list of individually-enumerated bad
    conditions defaulting to "trust it" for anything unnamed. Any exit code
    other than 0 or 1 (e.g. a usage error) is treated as undetermined the
    same way -- an operational failure, never a clean negative.

    ``PROVEN_NOT_ANCESTOR != COULD_NOT_PROVE_ANCESTRY``.
    """
    repo_root = Path(repo_root).resolve()
    try:
        resolved_commit = resolve_commit_v2(repo_root=repo_root, ref=commit_sha)
        resolved_trusted = resolve_commit_v2(repo_root=repo_root, ref=trusted_ref)
    except SubjectMaterialisationError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_UNKNOWN_COMMIT_REASON_V2) from exc

    completed = run_bounded_git_v2(
        ["merge-base", "--is-ancestor", resolved_commit, resolved_trusted],
        cwd=repo_root,
        check=False,
    )
    if completed.returncode == 0:
        authorized = True
    elif completed.returncode == 1:
        if not _ancestry_negative_is_trustworthy_v2(
            repo_root=repo_root, merge_base_result=completed
        ):
            raise ExecutedSourceIdentityError(IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2)
        authorized = False
    else:
        # Any other exit code (invalid object, corrupt store, usage error)
        # is a genuine operational failure, not a clean negative -- refused
        # the same way an unconfirmed negative is, rather than silently
        # becoming `authorized=False`.
        raise ExecutedSourceIdentityError(IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2)

    return ExecutedSourceAuthorizationV2(
        commit_sha=resolved_commit,
        trusted_ref=trusted_ref,
        trusted_ref_sha=resolved_trusted,
        authorized=authorized,
    )
