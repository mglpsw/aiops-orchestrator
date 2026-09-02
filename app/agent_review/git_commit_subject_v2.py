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

from app.agent_review.bounded_git_v2 import (
    BoundedGitError,
    BoundedGitSessionV2,
    open_bounded_git_session_v2,
    run_bounded_git_v2,
)

__all__ = [
    "SUBJECT_BLOB_MISSING_REASON_V2",
    "SUBJECT_DESTINATION_IS_SYMLINK_REASON_V2",
    "SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2",
    "SUBJECT_DUPLICATE_TREE_PATH_REASON_V2",
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
SUBJECT_DESTINATION_IS_SYMLINK_REASON_V2 = "subject_destination_is_symlink"
SUBJECT_DUPLICATE_TREE_PATH_REASON_V2 = "subject_duplicate_tree_path"

GITLINK_MODE_V2 = "160000"
SYMLINK_MODE_V2 = "120000"
EXECUTABLE_MODE_V2 = "100755"
TREE_MODE_V2 = "040000"


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


def resolve_commit_v2(
    *, repo_root: Path, ref: str, session: BoundedGitSessionV2 | None = None
) -> str:
    """Confirm `ref` names a commit in `repo_root`'s own object store.

    `^{commit}` is required rather than accepting any object: a tree or blob
    sha would otherwise resolve to something that is not a revision, and
    nothing downstream should be able to claim an identity that no commit
    history actually contains. The returned value is git's own full sha, not
    an echo of whatever string the caller passed in -- resolving `HEAD`, a
    branch name, or an abbreviated sha all go through this same git call.

    `session`, if given, is forwarded to `run_bounded_git_v2` unchanged --
    see `BoundedGitSessionV2` for why a caller making several git calls as
    part of one logical operation should share one session across all of
    them.
    """
    try:
        completed = run_bounded_git_v2(
            ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=repo_root,
            session=session,
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


def list_commit_tree_entries_v2(
    *, repo_root: Path, commit_sha: str, session: BoundedGitSessionV2 | None = None
) -> list[TreeEntryV2]:
    """List every blob and gitlink in `commit_sha`'s tree, straight from git.

    `session`, if given, is forwarded to `run_bounded_git_v2` unchanged.

    `-z` because paths may contain newlines; the non-`-z` form quotes and
    escapes them, and re-decoding that is an avoidable source of divergence
    between what git recorded and what is written out.

    Refuses a tree containing two entries for the exact same flattened path
    (`#200-G1-PM` finding 3, Codex, PR #284 review of `18dc9e4f`). Proven
    with real `git mktree` plumbing, not a hypothetical: `git commit-tree`
    accepts, and `git ls-tree -r` happily emits, two blob entries sharing
    one literal path in a single tree object -- nothing about the object
    format itself forbids it, only the porcelain commands that normally
    build trees do. Every downstream consumer of this list (materialisation
    in this module, identity verification in
    `commit_derived_execution_identity_v2.py`) eventually keys something by
    `entry.path` -- a dict assignment, a file write -- and a path-keyed
    structure silently prefers whichever duplicate is seen last, which
    means one committed object is dropped without any signal. Detecting the
    ambiguity here, before any caller has a chance to build such a
    structure, and failing closed with a dedicated reason code, closes that
    for every caller at once rather than requiring each one to re-derive
    the same check.

    `-t` is required in addition to `-r` (external Codex review of the
    original finding-3 fix itself, `#200-G1-PM` round 1 on this PR): plain
    `ls-tree -r` never emits a line for an intermediate DIRECTORY at all
    (`git ls-tree -h` documents `-t` as required to "show trees when
    recursing") -- only leaf blob/gitlink lines. A tree with two DIFFERENT
    subtree objects both named e.g. `d`, holding disjoint children (`d/a.py`
    from one, `d/b.py` from the other), has no duplicate *blob* path at
    all -- `d/a.py` and `d/b.py` are two distinct strings -- so the
    blob-only duplicate check above never fires, even though `git fsck`
    itself flags the raw tree as `duplicateEntries`, and materialisation
    silently merges both subtrees' children into what looks like one
    ordinary directory. Reproduced with real `git mktree` plumbing, not a
    hypothetical: two `040000 tree` entries named `d` in one tree object,
    confirmed as `duplicateEntries` by `git fsck --full`, materialise
    losslessly (no error) into a single `d/{a.py,b.py}` directory without
    `-t` in this check. With `-t`, `ls-tree -r` additionally emits a line
    for `d` itself for EACH of the two subtree objects -- both at the exact
    same path `d` -- which the same duplicate-path check below now also
    covers, catching this before the blob level is ever reached. Tree-mode
    entries are collected only for this duplicate-detection pass and are
    deliberately NOT included in the returned list: every existing caller
    (`materialise_commit_subject_v2`'s and `read_commit_blobs_v2`'s content
    handling) assumes every returned entry is a blob or gitlink, and a
    `040000` entry has no batchable blob content to write -- passing one
    through would break that contract rather than extend it.

    NOTE, narrowed by external Codex review of THIS check itself
    (`#200-G1-PM` round 2 on this PR): comparing raw `ls-tree` path
    STRINGS, however many special cases it enumerates (`-t` for tree-level
    names, whatever comes next), catches only tree-OBJECT-level ambiguity
    -- two entries that are literally, syntactically the SAME path, which
    is what `git fsck`'s own `duplicateEntries` means. It structurally
    cannot catch two syntactically DIFFERENT strings that resolve to the
    identical DESTINATION path once written -- e.g. a subtree literally
    named `.` containing `a`, alongside a root-level blob also named `a`,
    which `ls-tree -r -t` reports as `./a` and `a`: two distinct strings by
    this check, yet `_safe_destination_v2` (materialisation's own writer)
    resolves both to the same file. That is a different, destination-aware
    question this destination-agnostic function has no way to answer --
    see `_reject_resolved_destination_collisions_v2` in
    `materialise_commit_subject_v2` (and its sibling in
    `commit_derived_execution_identity_v2.py`'s `verify_executed_source_
    identity_v2`), which detect that class by resolving every entry
    through the SAME function that performs the real write/read, rather
    than maintaining a second, independently-derived string comparison
    here. The two checks are deliberately kept separate, not merged into
    one: this one is a property of the tree OBJECT alone, computable with
    no destination in hand at all; the other is a property of a specific
    destination's path resolution, which this function does not have.
    """
    completed = run_bounded_git_v2(
        ["ls-tree", "-r", "-t", "-z", commit_sha], cwd=repo_root, session=session
    )
    entries: list[TreeEntryV2] = []
    seen_paths: set[str] = set()
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
        path = raw_path.decode("utf-8", "surrogateescape")
        if path in seen_paths:
            raise SubjectMaterialisationError(SUBJECT_DUPLICATE_TREE_PATH_REASON_V2)
        seen_paths.add(path)
        if mode == TREE_MODE_V2:
            # Present only via `-t`, only to make this exact path visible
            # to the duplicate check above; never part of the returned
            # blob/gitlink-only contract (see docstring).
            continue
        entries.append(
            TreeEntryV2(
                mode=mode,
                object_type=object_type,
                object_id=object_id,
                path=path,
            )
        )
    return entries


def read_commit_blobs_v2(
    *, repo_root: Path, entries: list[TreeEntryV2], session: BoundedGitSessionV2 | None = None
) -> dict[str, bytes]:
    """Fetch every blob's raw content in one batched `cat-file` call.

    Keyed by path (not object id) because the caller wants "what is at this
    path in the tree", and a repository can legitimately have two paths
    share a blob (identical file content).

    `session`, if given, is forwarded to `run_bounded_git_v2` unchanged.
    """
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2]
    if not blobs:
        return {}
    batch_request = "".join(f"{entry.object_id}\n" for entry in blobs)
    completed = run_bounded_git_v2(
        ["cat-file", "--batch"],
        cwd=repo_root,
        input_bytes=batch_request.encode("utf-8"),
        session=session,
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


def _reject_symlink_in_destination_ancestry_v2(destination: Path) -> None:
    """Refuse if `destination`, or any EXISTING ancestor directory in its
    path, is a symlink.

    External Codex review of the original finding-2 fix itself
    (`#200-G1-PM` round 1 on this PR): checking only `destination.is_symlink()`
    -- the leaf -- misses a symlink one or more levels ABOVE the leaf, e.g.
    `destination = link/subject` where `link` is a symlink to `real` and
    `subject` does not exist yet. `Path("link/subject").is_symlink()` is
    `False` (the leaf genuinely does not exist and is therefore not a
    symlink itself), so the leaf-only check let this through; every
    subsequent write in `materialise_commit_subject_v2` then landed inside
    `real/subject` while the returned, advertised `root` stayed the
    still-retargetable lexical path `link/subject` -- reproduced here
    exactly as found, not merely asserted: writing through a two-level
    `link/subject` destination did put the materialised bytes under
    `real/subject`, invisible to anything trusting the advertised root.

    Walking every path segment from `destination` up to the filesystem
    root and checking `is_symlink()` on each -- rather than a single
    `resolve()` comparison -- is deliberate: `is_symlink()` on a
    non-existent path returns `False` without raising (unlike `exists()`
    or `stat()`), so this safely covers the common case where most of
    `destination`'s ancestry does not exist yet and will be created fresh
    by `mkdir(parents=True)` below, while still catching a symlink at any
    position that DOES already exist.

    `destination.absolute()` (not `.resolve()`, which would follow any
    symlink and defeat the purpose) anchors a relative `destination` at the
    caller's actual working directory before walking, so a relative path
    is checked against its full real ancestry rather than stopping early
    at `Path(".")` (whose own `.parent` is itself).
    """
    candidate = destination.absolute()
    while True:
        if candidate.is_symlink():
            raise SubjectMaterialisationError(SUBJECT_DESTINATION_IS_SYMLINK_REASON_V2)
        parent = candidate.parent
        if parent == candidate:
            # Reached the filesystem root (`Path("/").parent == Path("/")`).
            break
        candidate = parent


def _reject_resolved_destination_collisions_v2(
    *, subject_root: Path, entries: list[TreeEntryV2]
) -> None:
    """Refuse if two DIFFERENT tree entries resolve to the identical
    destination path, using `_safe_destination_v2` -- the SAME function
    that performs the real writes in `materialise_commit_subject_v2` below
    -- as the single authority for what "the same path" means.

    External Codex review (`#200-G1-PM` round 2 on this PR): the earlier
    fix for duplicate tree paths (in `list_commit_tree_entries_v2`)
    compares `ls-tree`'s raw path STRINGS. That can never be complete,
    because `_safe_destination_v2`'s actual resolution and a
    hand-maintained set of string special-cases are two INDEPENDENT
    implementations of "what path does this entry refer to", and every
    special case closed (`.`-only normalisation, which plain `Path`
    joining already collapses without even needing `.resolve()`, closed
    here) leaves the next one open. Reproduced with real `git mktree`
    plumbing: a subtree literally named `.` containing `a`, plus a
    root-level blob also named `a`, produce the distinct raw strings `./a`
    and `a` (no duplicate by the string check in
    `list_commit_tree_entries_v2`), but `_safe_destination_v2` resolves
    both to the identical destination file; before this fix,
    materialisation silently overwrote the first write with the second,
    reporting `file_count=2` for what was really one surviving file with
    the other's bytes discarded, unsignalled.

    Comparing entries resolved through the SAME function used for the
    actual write, rather than a second, independently-derived string
    comparison, is what closes this structurally: there is exactly one
    authority for "what does this entry's path resolve to", used both to
    detect the collision and to perform the write, so the two cannot
    disagree by construction -- no future path-alias form (`..` combined
    with other segments, mixed separators, whatever comes next) can reopen
    this specific gap, because there is no second implementation left to
    fall behind.

    Called BEFORE any write (and before fetching blob content at all, in
    the caller) so a colliding tree is refused before any bytes are
    written or discarded.
    """
    seen_targets: set[Path] = set()
    for entry in entries:
        target = _safe_destination_v2(subject_root=subject_root, relative_path=entry.path)
        if target in seen_targets:
            raise SubjectMaterialisationError(SUBJECT_DUPLICATE_TREE_PATH_REASON_V2)
        seen_targets.add(target)


def materialise_commit_subject_v2(
    *, repo_root: Path, ref: str, destination: Path
) -> MaterialisedCommitSubjectV2:
    """Write `ref`'s resolved commit's committed bytes into an empty directory.

    The result is severed from `repo_root`: deleting or rewriting the
    original checkout afterwards cannot change what was materialised.

    Opens its own `BoundedGitSessionV2` (`#200-G1-PM` round 3 on this PR)
    and threads it through every git call this function makes, so that if
    an EARLIER call in this materialisation triggers an unexpected object
    -store write and is rejected, every LATER call in this same
    materialisation still sees that write as "new relative to session
    start" -- see `BoundedGitSessionV2` for why a fresh per-call baseline
    cannot do that.
    """
    destination = Path(destination)
    _reject_symlink_in_destination_ancestry_v2(destination)
    if destination.exists() and any(destination.iterdir()):
        raise SubjectMaterialisationError(SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2)
    destination.mkdir(parents=True, exist_ok=True)

    session = open_bounded_git_session_v2(cwd=repo_root)
    commit_sha = resolve_commit_v2(repo_root=repo_root, ref=ref, session=session)
    entries = list_commit_tree_entries_v2(
        repo_root=repo_root, commit_sha=commit_sha, session=session
    )
    blobs = [entry for entry in entries if entry.mode != GITLINK_MODE_V2]

    try:
        _reject_resolved_destination_collisions_v2(subject_root=destination, entries=blobs)
    except SubjectMaterialisationError:
        # Refused before any blob content was even fetched -- destination
        # is still empty (checked above), safe to discard unconditionally.
        shutil.rmtree(destination, ignore_errors=True)
        raise

    content_by_path = read_commit_blobs_v2(repo_root=repo_root, entries=blobs, session=session)

    written = 0
    # `#200-G1-PM` round 3 on this PR (external Codex review of the round-2
    # `_reject_resolved_destination_collisions_v2` preflight itself): that
    # preflight resolves every entry ONCE, before any writes -- a TOCTOU
    # window, because the write loop below mutates the filesystem as it
    # goes (writing a symlink entry changes what a LATER entry's `..`
    # -bearing path resolves through). Reproduced with real `git mktree`
    # plumbing: symlink `a -> e` plus blobs at `e/file` and `z/../a/file`.
    # At preflight time `a` does not exist, so the two blob entries resolve
    # to different destinations and no collision is seen; once the write
    # loop actually creates `a` as a symlink, the later entry's FRESH
    # resolution (recomputed per entry below, always against current
    # on-disk state) now traverses through it, landing on the same file
    # the earlier entry already wrote -- silently discarding it, unless
    # THIS collision, not just the preflight one, is also checked.
    # `written_targets`, populated as each entry is actually about to be
    # written (not from the static preflight pass), catches this
    # regardless of which of the two colliding entries git happens to
    # order first: whichever one resolves to an already-written path,
    # written earlier in this very loop, is refused before overwriting it.
    written_targets: set[Path] = set()
    try:
        for entry in blobs:
            content = content_by_path[entry.path]
            target = _safe_destination_v2(subject_root=destination, relative_path=entry.path)
            if target in written_targets:
                raise SubjectMaterialisationError(SUBJECT_DUPLICATE_TREE_PATH_REASON_V2)
            written_targets.add(target)
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
    # `#200-G1-PM` finding 6 (Codex, PR #284 review of `18dc9e4f`):
    # `relative` can contain lone surrogate characters -- `Path.rglob`,
    # like the rest of Python's filesystem layer on POSIX, decodes raw
    # path bytes with `surrogateescape` by default, exactly the same
    # encoding `list_commit_tree_entries_v2` and `materialise_commit_
    # subject_v2` use for a legitimately non-UTF-8 git path. A plain
    # `.encode("utf-8")` (strict errors) raises `UnicodeEncodeError` on
    # those surrogates, so this function could not digest a subject that
    # the tree-reading and materialisation APIs right next to it explicitly
    # support -- encoding with the matching `surrogateescape` strategy
    # round-trips the original raw path bytes instead.
    return hashlib.sha256("\n".join(entries).encode("utf-8", "surrogateescape")).hexdigest()
