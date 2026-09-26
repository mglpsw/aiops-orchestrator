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
    AUTHORIZATION: whether a given (already-identified) commit is reachable
    from ``trusted_ref_sha`` -- a full commit sha the CALLER has already
    verified out-of-band, never a ref name such as ``refs/heads/master`` (see
    "``trusted_ref_sha`` must be an out-of-band anchor" below for why a ref
    name is refused outright rather than merely discouraged). Meaningless
    applied to an unverified sha, and does not imply identity: a commit can
    be a perfectly legitimate, unauthorized feature-branch tip.

A caller that wants an overall accept/refuse decision composes both
results explicitly; this module does not do that composition for it.

## ``trusted_ref_sha`` must be an out-of-band anchor, never a resolved ref (#313)

``authorize_commit_for_execution_v2`` reads both ``commit_sha`` and
``trusted_ref_sha`` through the same hostile-derived trusted object
authority (``#200-G1C2``) that ``verify_executed_source_identity_v2`` uses
for tree/blob content. That authority's OBJECT content is genuinely sound --
every loose object is re-hashed against its own fanout path, every pack is
``verify-pack``'d -- but its REF *values* are copied verbatim from the live,
hostile-scoped checkout (``_copy_refs_fd_v2`` in
``trusted_object_authority_v2.py``). A hostile checkout that points
``refs/heads/master`` at an attacker commit produces an authority whose own
copy of ``refs/heads/master`` names that same attacker commit. Resolving a
ref *name* such as ``"refs/heads/master"`` through that authority as the
trust anchor -- exactly the usage an earlier revision of this docstring
demonstrated -- authorizes whatever the hostile checkout currently claims
that name means, not what a legitimate ``master`` actually is. Reproduced by
external review as issue ``#313``.

There is no verification this module could add to make a ref *value* copied
from a source its own threat model already declares hostile trustworthy --
the fix is not "resolve the ref more carefully", it is that this module
never accepts a ref value as the trust anchor at all. ``trusted_ref_sha``
must already BE a full, immutable commit sha, supplied by the caller from a
source outside this module's own hostile-derived read path (e.g. an
out-of-band-verified release pin obtained before this checkout was ever
touched). ``authorize_commit_for_execution_v2`` refuses, with
``IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2``, any ``trusted_ref_sha`` that is
not exactly 40 lowercase hex characters (sha1 only -- see below for why 64
is deliberately excluded, not merely unimplemented) -- which rejects every
ref-name shape (``refs/heads/master``, ``HEAD``, a bare branch or tag name,
an abbreviated sha) outright, before this module ever opens the trusted
object authority for that value. This does not make the anchor itself
trustworthy -- that remains the caller's own out-of-band responsibility,
exactly as stated above -- it only removes the one mechanism, ref-name
resolution through a hostile-derived store, by which an attacker could
otherwise supply their own answer to "what does the trusted anchor mean"
and have this module accept it as ground truth.

### Why 64-hex (sha256) was dropped, not merely never added (independent review, correction round 2, P0)

An earlier revision of this fix accepted BOTH 40 (sha1) and 64 (sha256) hex
lengths, reasoning that a future sha256-format repository would need the
longer shape. That reasoning was correct about the future and wrong about
the present, and the gap was a real, independently-reproduced P0: the
private trusted object authority this module reads through
(``open_trusted_object_authority_v2``) is hardcoded sha1-format ALWAYS
(``_write_minimal_bare_skeleton_v2`` writes no ``extensions.objectformat``),
so a 64-hex string can never actually be a valid object id there. Git's own
object-vs-ref disambiguation only treats a hex string as a candidate object
id when its length matches the repository's actual hash algorithm; anything
else falls through, silently, to ORDINARY ref-name resolution -- and this
authority's ref values are copied verbatim from the same hostile-scoped
checkout described above. A caller supplying a genuine, public,
out-of-band-verified 64-hex anchor -- the exact case the shape check exists
to protect -- was thereby exposed to an attacker who learns that public
value (it is meant to be publicly verifiable, that is the point of an
out-of-band anchor) and plants an ordinary ref literally named after it,
pointing at their own commit. Reproduced independently three times (two
adversarial review lanes plus direct maintainer reproduction) against the
pre-fix (40, 64) shape. 64 has zero legitimate use today regardless --
``resolve_commit_v2`` in ``git_commit_subject_v2.py`` independently
hard-rejects any resolved value with ``len != 40``, so a genuine sha256
anchor could never work end-to-end even before this module is involved.
Re-adding 64 requires the trusted object authority to gain genuine sha256
support FIRST, not merely widening this shape check again.

### The resolved-equals-supplied invariant (independent review, correction round 2, P0)

Dropping 64 closes the one length that was reproducibly exploitable today,
but not the general class: ANY shape-valid string that ``resolve_commit_v2``
resolves to something other than itself was never actually verified to BE
an object id -- it was only verified to look like one. ``authorize_commit_
for_execution_v2`` now requires ``resolve_commit_v2(ref=trusted_ref_sha) ==
trusted_ref_sha`` exactly, refusing with
``IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2`` otherwise. This is the
structural invariant the module's contract always implied -- "supply an
object id, not something that merely resolves to one" -- made an explicit,
enforced check rather than an assumption the shape check alone was
mistakenly relied upon to guarantee.

### Residual risk the shape check does NOT and cannot close (independent review, #313 follow-up)

The shape check proves ``trusted_ref_sha`` LOOKS like a sha. It cannot prove,
and does not attempt to prove, where that sha actually came from -- Python
has no way to structurally distinguish "a string an out-of-band-verified
release pin produced" from "a string this module family's own
hostile-scoped read path produced" once both are just 40 or 64 hex
characters. Concretely: a caller who wants to satisfy the new shape
requirement could plausibly reach for the already-imported sibling
primitive ``resolve_commit_v2`` (``git_commit_subject_v2.py``) and call it
against ``open_trusted_object_authority_v2(repo_root).trusted_repo_root`` --
i.e. resolve a ref NAME through the exact same hostile-derived authority
this module refuses to do internally, then hand the RESULT (a genuine,
shape-valid 40-hex sha, because it really does name a real commit in that
authority) to ``trusted_ref_sha``. That reconstructs the pre-#313 attack one
call outside this function: if the hostile checkout's branch tip points at
an attacker commit, this "laundering" path resolves to that same attacker
commit, which then passes the shape check trivially and gets accepted as
the trust anchor -- because it IS, genuinely, a real commit sha; it is
simply not an OUT-OF-BAND one. See
``test_laundering_hostile_ref_through_resolve_commit_v2_reproduces_the_original_attack``
in this module's test suite for a checked-in, currently-succeeding
reproduction -- deliberately preserved as a live demonstration of a known,
accepted residual risk, not something this module claims to catch.

This is NOT closable by adding more validation here, for the same reason
tightening the shape check further could not close it: any mechanism built
from string content alone cannot distinguish the two sources, because
by the time the string reaches this function both are indistinguishable
values of the same type. Closing it for real requires a caller-side
provenance/attestation channel that never touches this module family's own
hostile-derived read path at all -- out of scope here because there are no
live callers of ``authorize_commit_for_execution_v2`` today to design that
channel against (the composition layer that would supply ``trusted_ref_sha``
in practice, ``#200-G1B``/``#200-G5``, is not yet implemented). Tracked as a
narrow follow-up, ``#200-G1C2-F3``, scoped for when a real caller exists
rather than designed speculatively now. Until then, the operative control is
what this docstring says: never derive ``trusted_ref_sha`` from
``resolve_commit_v2``, ``open_trusted_object_authority_v2``, or any other
primitive in this module family applied to the checkout under test -- it
must come from somewhere else entirely.

## Composing ``verify_executed_source_identity_v2`` + ``authorize_commit_for_execution_v2``: compare the resolved shas (independent review, correction round 2, P1)

Unlike ``trusted_ref_sha``, ``commit_sha`` (the SUBJECT being identified or
authorized) is never shape-checked in either function -- it may legitimately
be a ref name, ``HEAD``, or an abbreviated sha, because it is not itself a
trust anchor. That is correct for each function independently, but it
creates a split-brain hazard when a caller composes both from the same
input string, because ``verify_executed_source_identity_v2`` and
``authorize_commit_for_execution_v2`` each open their OWN fresh trusted
object authority and resolve ``commit_sha`` separately. If the checkout at
``repo_root`` mutates a ref between the two calls (or is hostile enough to
answer differently depending on timing), a caller who wrote something like::

    identity = verify_executed_source_identity_v2(repo_root=r, commit_sha=x, subject_root=s)
    auth = authorize_commit_for_execution_v2(repo_root=r, commit_sha=x, trusted_ref_sha=pin)

can get IDENTITY proven about one real commit and AUTHORIZATION granted
about a genuinely DIFFERENT real commit, despite supplying the same literal
string ``x`` to both calls -- each function did exactly what it promises,
independently, and neither is wrong on its own. Both result dataclasses
expose the actual resolved sha they each independently confirmed
(``identity.commit_sha`` and ``auth.commit_sha``) precisely so a careful
caller CAN detect this by comparing them -- but nothing before this
correction round said a caller composing the two MUST do that comparison
before treating the pair as describing one commit. A caller composing these
two primitives from a single input string must compare ``identity.commit_sha
== auth.commit_sha`` (both already-resolved, canonical 40-hex values) before
treating IDENTITY and AUTHORIZATION as facts about the same commit; treat a
mismatch as a hard refusal, not a warning. See
``test_composing_identity_and_authorization_from_the_same_input_can_resolve_different_commits``
for a checked-in, currently-succeeding reproduction of the hazard (not a
claimed fix -- there is no live composition caller today to fix it
against, matching the ``trusted_ref_sha``-laundering residual above; if a
structural fix becomes cheap once ``#200-G1B``/``#200-G5`` exist -- e.g.
accepting an already-resolved sha into both calls instead of letting each
resolve independently -- prefer that over asking every future caller to
remember the comparison).

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
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import BoundedGitError, open_bounded_git_subprocess_v2
from app.agent_review.git_commit_subject_v2 import (
    BoundedBlobCarrierV2,
    EXECUTABLE_MODE_V2,
    GITLINK_MODE_V2,
    MAX_EXPANDED_ENTRIES_V2,
    SUBJECT_BLOB_MISSING_REASON_V2,
    SUBJECT_UNREPRESENTABLE_TREE_REASON_V2,
    SYMLINK_MODE_V2,
    SubjectMaterialisationError,
    list_commit_tree_structure_v2,
    read_commit_blobs_v2,
    resolve_commit_v2,
)
from app.agent_review.trusted_object_authority_v2 import (
    AuthorizedGitStorageSetV2,
    TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2,
    TrustedObjectAuthorityError,
    open_trusted_object_authority_v2,
)

__all__ = [
    "IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2",
    "IDENTITY_BLOB_MISSING_REASON_V2",
    "IDENTITY_CONTENT_MISMATCH_REASON_V2",
    "IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2",
    "IDENTITY_GITLINK_PRESENT_REASON_V2",
    "IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2",
    "IDENTITY_EXTRA_UNTRACKED_NODE_REASON_V2",
    "IDENTITY_MISSING_TRACKED_FILE_REASON_V2",
    "IDENTITY_MISSING_TREE_NODE_REASON_V2",
    "IDENTITY_MODE_MISMATCH_REASON_V2",
    "IDENTITY_NODE_TYPE_MISMATCH_REASON_V2",
    "IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2",
    "IDENTITY_TREE_UNREPRESENTABLE_REASON_V2",
    "IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2",
    "IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2",
    "IDENTITY_SYMLINKED_DIRECTORY_REASON_V2",
    "IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2",
    "IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2",
    "IDENTITY_TREE_UNREADABLE_REASON_V2",
    "IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2",
    "IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2",
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
IDENTITY_CONTENT_MISMATCH_REASON_V2 = "identity_content_mismatch"
IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2 = "identity_symlink_target_mismatch"
IDENTITY_MODE_MISMATCH_REASON_V2 = "identity_mode_mismatch"
IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2 = "identity_loaded_code_outside_subject"
IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2 = "identity_subject_root_unreadable"
IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2 = "identity_path_escapes_subject"
IDENTITY_SYMLINKED_DIRECTORY_REASON_V2 = "identity_symlinked_directory_in_subject"

# `#333` -- full Git tree structural equality (contract A / symlink rule A2).
# Additive codes; every pre-existing code keeps its meaning where it applies.
#: A tree node (directory, including an explicit EMPTY tree) the commit
#: declares is absent from the subject.
IDENTITY_MISSING_TREE_NODE_REASON_V2 = "identity_missing_tree_node"
#: A non-regular-file node (directory, special file) exists under the subject
#: where the commit declares nothing. Extra regular files and symlink leaves
#: keep `IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2`.
IDENTITY_EXTRA_UNTRACKED_NODE_REASON_V2 = "identity_extra_untracked_node"
#: The node exists but is not of the kind Git declares (regular file where a
#: symlink is expected, FIFO/special file where a regular file is expected,
#: directory where a leaf is expected...). A symlink sitting where Git declares
#: a TREE keeps the pre-existing `IDENTITY_SYMLINKED_DIRECTORY_REASON_V2`.
IDENTITY_NODE_TYPE_MISMATCH_REASON_V2 = "identity_node_type_mismatch"
#: The commit's own raw tree cannot be represented as a filesystem graph
#: (refused by the C3 structural enumeration: duplicate/aliased names,
#: `.`/`..` tree names, cycles, depth or entry budgets, unrepresentable names).
IDENTITY_TREE_UNREPRESENTABLE_REASON_V2 = "identity_tree_unrepresentable"
#: The OBSERVED subject exceeds the admitted structural budget (nodes or
#: depth) before the comparison could finish; refused typed, never expanded.
IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2 = "identity_subject_structure_budget_exceeded"

#: Structural budgets for the OBSERVED subject. Same numbers as C3's admitted
#: subject (`MAX_EXPANDED_ENTRIES_V2`, child depth <= 100): the verifier walks
#: a graph that was admitted under those budgets, so a subject beyond them is
#: not the materialisation of an admitted commit and is refused before the
#: walk can expand further.
MAX_OBSERVED_NODES_V2: int = MAX_EXPANDED_ENTRIES_V2
MAX_OBSERVED_DEPTH_V2: int = 100
# #200-G1C (issue #303): the graph could not be *completely* walked --
# missing/corrupted parent object, shallow history, or any other reason
# `TrustedObjectAuthorityV2.prove_ancestry` could not finish enumerating the
# trusted ref's full ancestor set. Never collapsed into `authorized=False`:
# an incomplete closure is not a proof of absence.
IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2 = "identity_authorization_undetermined"
# #313 (#200-G1C2-F2): `trusted_ref_sha` is not shaped like a full,
# immutable sha1 commit sha (exactly 40 lowercase hex characters -- 64/
# sha256 deliberately excluded, see `_is_full_commit_sha_shape_v2`'s
# docstring) -- includes every ref-NAME shape (`refs/heads/master`, `HEAD`,
# a bare branch or tag name, an abbreviated sha). Raised BEFORE this module
# ever opens the
# hostile-derived trusted object authority for that value: there is no
# resolution attempt to make safer, the value is refused outright. See the
# module docstring's "`trusted_ref_sha` must be an out-of-band anchor"
# section for why.
IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2 = "identity_trusted_ref_not_a_sha"
# P0 (independent review, correction round 2, `#313` follow-up): the shape
# check alone proved `trusted_ref_sha` LOOKS like a sha, never that
# `resolve_commit_v2` will actually resolve it back to ITSELF as an object
# id rather than falling through to ref-name resolution (the exact
# mechanism the dropped 64-length case exploited, and the general
# structural invariant the old docstring claimed without the code ever
# enforcing it). Raised when the two differ -- `resolve_commit_v2`'s
# return value is git's own canonical resolution of whatever
# `trusted_ref_sha` named, which must be byte-identical to the value
# supplied when that value was already meant to BE an object id.
IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2 = "identity_trusted_ref_sha_mismatch"


class ExecutedSourceIdentityError(ValueError):
    """Identity could not be established. Content-free reason code only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExecutedSourceIdentityV2:
    """IDENTITY only: ``commit_sha``'s raw tree IS the subject's directory graph.

    Full structural equality (`#333`, contract Q = contract A under the
    quiescence precondition documented on ``verify_executed_source_identity_v2``):
    explicit tree nodes including empty ones, leaves, node kinds, bytes,
    symlink target bytes and executable semantics; nothing extra. Not a
    statement that the subject cannot change afterwards, and not the trust
    root of execution (`#301`). Tree equality, not unique provenance -- a different commit sharing the
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

    ``trusted_ref_sha`` is always a full commit sha -- both the value the
    caller supplied (``authorize_commit_for_execution_v2`` refuses anything
    else, see ``#313``) and, redundantly, the value this module independently
    re-resolved against the trusted object authority to confirm it names a
    real, content-verified commit.
    """

    commit_sha: str
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


_DIR_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LEAF_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _acquire_subject_root_fd_v2(subject_root: Path) -> int:
    """Acquire the subject root as a DESCRIPTOR: the locator is discovery input.

    `#333`: the previous verifier resolved the locator and treated the result
    as the compared subject. Here the final component is opened
    `O_DIRECTORY | O_NOFOLLOW`, so a locator whose final component is a
    symlink is refused rather than silently replaced by its target. Every
    later observation is made relative to this descriptor, never by
    re-resolving the locator. Limitation (declared): intermediate locator
    components are resolved by the OS as for any pathname; the producer of the
    locator (C3 workspace / composition) owns that provenance.
    """
    try:
        return os.open(os.fsencode(subject_root), _DIR_OPEN_FLAGS_V2)
    except OSError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_SUBJECT_ROOT_UNREADABLE_REASON_V2) from exc


def _open_directory_relative_v2(root_fd: int, components: tuple[bytes, ...]) -> int:
    """Re-acquire `components` under `root_fd`, one no-follow `openat` per step.

    The logical component path is navigation state only; the returned
    descriptor is the authority. At most two descriptors are live during the
    step-down, whatever the depth (`NonRecursive != ResourceBounded`, C3).
    Always returns a descriptor the caller owns and must close.
    """
    current = os.dup(root_fd)
    try:
        os.set_inheritable(current, False)
        for component in components:
            try:
                next_fd = os.open(component, _DIR_OPEN_FLAGS_V2, dir_fd=current)
            finally:
                os.close(current)
                current = -1
            current = next_fd
        return current
    except OSError as exc:
        if current != -1:
            os.close(current)
        raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc


def _observe_subject_graph_v2(root_fd: int) -> dict[bytes, tuple[str, int]]:
    """Every node beneath `root_fd` as ``raw_relative_path -> (kind, st_mode)``.

    Kinds: ``tree`` (real directory), ``regular``, ``symlink``, ``special``
    (FIFO, socket, device...). Classification comes from
    ``stat(name, dir_fd=..., follow_symlinks=False)`` (`fstatat`
    `AT_SYMLINK_NOFOLLOW`) -- never from ``Path.is_dir()``/``is_file()``,
    which follow symlinks and separate classification from later use
    (`#321`). Directories are entered through a fresh no-follow
    re-acquisition from the root authority. Budgets are enforced on the
    OBSERVED graph as nodes are discovered, so a hostile subject is refused
    typed before it can expand the walk (`MAX_OBSERVED_NODES_V2`,
    `MAX_OBSERVED_DEPTH_V2`). Entries are STREAMED from each directory
    (``os.scandir`` on the descriptor), never listed whole first: one
    oversized directory is refused after ``MAX_OBSERVED_NODES_V2 + 1`` names,
    not after all of them have been read into memory (`#352` F3). Any
    enumeration or classification failure is a typed refusal, never "empty".
    """
    observed: dict[bytes, tuple[str, int]] = {}
    pending: list[tuple[bytes, ...]] = [()]
    count = 0
    while pending:
        components = pending.pop()
        directory_fd = _open_directory_relative_v2(root_fd, components)
        try:
            try:
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        raw_name = os.fsencode(entry.name)
                        st = os.stat(raw_name, dir_fd=directory_fd, follow_symlinks=False)
                        count += 1
                        if count > MAX_OBSERVED_NODES_V2:
                            raise ExecutedSourceIdentityError(IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2)
                        # `#352` R2: the depth budget applies to EVERY node kind,
                        # before kind dispatch -- as C3 checks `child_depth` before
                        # dispatching on object type.
                        if len(components) + 1 > MAX_OBSERVED_DEPTH_V2:
                            raise ExecutedSourceIdentityError(IDENTITY_SUBJECT_STRUCTURE_BUDGET_EXCEEDED_REASON_V2)
                        relative = b"/".join(components + (raw_name,))
                        mode = st.st_mode
                        if stat.S_ISDIR(mode):
                            observed[relative] = ("tree", mode)
                            pending.append(components + (raw_name,))
                        elif stat.S_ISREG(mode):
                            observed[relative] = ("regular", mode)
                        elif stat.S_ISLNK(mode):
                            observed[relative] = ("symlink", mode)
                        else:
                            observed[relative] = ("special", mode)
            except OSError as exc:
                raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
        finally:
            os.close(directory_fd)
    return observed


def _split_relative_v2(relative: bytes) -> tuple[tuple[bytes, ...], bytes]:
    parts = relative.split(b"/")
    return tuple(parts[:-1]), parts[-1]


def _compare_regular_leaf_v2(
    root_fd: int, relative: bytes, carrier: BoundedBlobCarrierV2, path: str, *, executable: bool
) -> None:
    """Open the leaf descriptor-relative and no-follow, establish its type and
    mode on THAT descriptor with ``fstat``, then compare bytes in bounded
    chunks on BOTH sides: the expected blob is streamed from the carrier's
    spool (``iter_chunks``), never held whole (`#352` review). ``O_NONBLOCK``
    makes an ``open`` of a FIFO return instead of block, and the ``S_ISREG``
    check refuses it before any read (`#321`'s defect class). Executable
    semantics are the owner-execute bit of ``st_mode`` (``100755`` <=>
    ``S_IXUSR``), not ``os.access``, which answers a process-permission
    question.
    """
    parents, name = _split_relative_v2(relative)
    parent_fd = _open_directory_relative_v2(root_fd, parents)
    try:
        try:
            leaf_fd = os.open(name, _LEAF_OPEN_FLAGS_V2, dir_fd=parent_fd)
        except OSError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
    finally:
        os.close(parent_fd)
    try:
        st = os.fstat(leaf_fd)
        if not stat.S_ISREG(st.st_mode):
            raise ExecutedSourceIdentityError(IDENTITY_NODE_TYPE_MISMATCH_REASON_V2)
        if bool(st.st_mode & stat.S_IXUSR) != executable:
            raise ExecutedSourceIdentityError(IDENTITY_MODE_MISMATCH_REASON_V2)
        if st.st_size != carrier.size_of(path):
            raise ExecutedSourceIdentityError(IDENTITY_CONTENT_MISMATCH_REASON_V2)
        try:
            for expected_chunk in carrier.iter_chunks(path):
                observed = b""
                while len(observed) < len(expected_chunk):
                    try:
                        piece = os.read(leaf_fd, len(expected_chunk) - len(observed))
                    except OSError as exc:
                        raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
                    if not piece:
                        raise ExecutedSourceIdentityError(IDENTITY_CONTENT_MISMATCH_REASON_V2)
                    observed += piece
                if observed != expected_chunk:
                    raise ExecutedSourceIdentityError(IDENTITY_CONTENT_MISMATCH_REASON_V2)
        except SubjectMaterialisationError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc
        try:
            trailing = os.read(leaf_fd, 1)
        except OSError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
        if trailing:
            raise ExecutedSourceIdentityError(IDENTITY_CONTENT_MISMATCH_REASON_V2)
    finally:
        os.close(leaf_fd)


def _compare_symlink_leaf_v2(root_fd: int, relative: bytes, carrier: BoundedBlobCarrierV2, path: str) -> None:
    """A symlink is data: its raw target bytes are compared, it is never
    followed, and nothing about what the target resolves to is decided here
    (rule A2; the execution boundary belongs to `#301`). The expected target
    is sized from the carrier index first and read only up to the observed
    target's length (bounded by the filesystem), never whole."""
    parents, name = _split_relative_v2(relative)
    parent_fd = _open_directory_relative_v2(root_fd, parents)
    try:
        try:
            target = os.readlink(name, dir_fd=parent_fd)
        except OSError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2) from exc
    finally:
        os.close(parent_fd)
    if carrier.size_of(path) != len(target):
        raise ExecutedSourceIdentityError(IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2)
    try:
        expected_target = carrier.read_bounded(path, len(target))
    except SubjectMaterialisationError as exc:
        raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc
    if target != expected_target:
        raise ExecutedSourceIdentityError(IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2)


_LEGACY_SCAN_MAX_RECORD_V2 = 1 << 20


def _legacy_refusal_reason_v2(trusted_root: Path, commit_sha: str) -> str | None:
    """Refusal path ONLY (C3 already refused the tree as unrepresentable):
    name the two refusals the pre-`#333` verifier reported with their own
    reason codes -- a gitlink, or a ``..``-shaped entry path -- so those codes
    keep their meaning. Streams ``git ls-tree -r -z`` one record at a time
    (at most ``_LEGACY_SCAN_MAX_RECORD_V2`` buffered) and stops at the first
    hit; never on the success path, never materialises the flattened listing.
    Returns ``None`` when neither applies or the scan cannot complete."""
    try:
        proc = open_bounded_git_subprocess_v2(
            ["ls-tree", "-r", "-z", commit_sha], cwd=trusted_root,
            stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except BoundedGitError:
        return None
    try:
        buffer = b""
        while True:
            chunk = proc.stdout.read1(65536) if proc.stdout is not None else b""
            if not chunk:
                return None
            buffer += chunk
            *records, buffer = buffer.split(b"\0")
            for record in records:
                metadata, _, raw_path = record.partition(b"\t")
                if metadata.split(b" ", 1)[0].decode("ascii", "replace") == GITLINK_MODE_V2:
                    return IDENTITY_GITLINK_PRESENT_REASON_V2
                try:
                    _safe_subject_path_v2(subject_root=Path("."), relative_path=os.fsdecode(raw_path))
                except ExecutedSourceIdentityError as exc:
                    return exc.reason_code
            if len(buffer) > _LEGACY_SCAN_MAX_RECORD_V2:
                return None
    except (OSError, ValueError):
        return None
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.kill()
        proc.wait()


def _compare_structure_v2(
    expected: dict[bytes, tuple[str, bool]], observed: dict[bytes, tuple[str, int]]
) -> None:
    """``ExpectedPaths == ObservedPaths`` with node-kind equality at every path.

    Refusal order: every expected node first (missing / wrong kind), in the
    commit's own hierarchical order, then any extra observed node. Reason
    codes: missing tree -> `IDENTITY_MISSING_TREE_NODE`; missing leaf ->
    pre-existing `IDENTITY_MISSING_TRACKED_FILE`; symlink where a tree is
    expected -> pre-existing `IDENTITY_SYMLINKED_DIRECTORY` (still the exact
    fact); other kind mismatch -> `IDENTITY_NODE_TYPE_MISMATCH`; extra regular
    file or symlink -> pre-existing `IDENTITY_EXTRA_UNTRACKED_FILE`; extra
    directory or special file -> `IDENTITY_EXTRA_UNTRACKED_NODE`.
    """
    for relative, (kind, _executable) in expected.items():
        actual = observed.get(relative)
        if actual is None:
            if kind == "tree":
                raise ExecutedSourceIdentityError(IDENTITY_MISSING_TREE_NODE_REASON_V2)
            raise ExecutedSourceIdentityError(IDENTITY_MISSING_TRACKED_FILE_REASON_V2)
        actual_kind = actual[0]
        if kind == actual_kind:
            continue
        if kind == "tree" and actual_kind == "symlink":
            raise ExecutedSourceIdentityError(IDENTITY_SYMLINKED_DIRECTORY_REASON_V2)
        raise ExecutedSourceIdentityError(IDENTITY_NODE_TYPE_MISMATCH_REASON_V2)
    for relative in sorted(observed):
        if relative in expected:
            continue
        if observed[relative][0] in ("regular", "symlink"):
            raise ExecutedSourceIdentityError(IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2)
        raise ExecutedSourceIdentityError(IDENTITY_EXTRA_UNTRACKED_NODE_REASON_V2)


_FULL_COMMIT_SHA_LENGTHS_V2 = (40,)  # sha1 only -- see the P0 note below for why 64 is excluded
_HEX_DIGITS_V2 = frozenset("0123456789abcdef")


def _is_full_commit_sha_shape_v2(value: object) -> bool:
    """True iff ``value`` is an EXACT built-in ``str`` with the exact SHAPE
    of a full, immutable sha1 commit sha -- exactly 40 lowercase hex
    characters, nothing more and nothing less.

    P1 (independent review, correction round 3): the type test is
    ``type(value) is str``, NOT ``isinstance``, because ``isinstance``
    admits SUBCLASSES and a ``str`` subclass gets to define the very
    operators both of this anchor's guards are written in terms of --
    ``__len__`` and ``__iter__`` here, and ``__ne__`` in
    ``authorize_commit_for_execution_v2``'s resolved==supplied invariant.
    The reproduced witness needed a single override: ``__ne__`` returning
    ``False``, on an instance whose real content was an honest, genuinely
    resolvable 40-hex annotated-tag object sha. Because ``!=`` puts the
    caller's object on the RIGHT, and Python gives the reflected operation
    priority when the right operand's type is a proper subclass of the
    left's, the invariant asked the attacker's own object whether it
    mismatched and was told no -- returning ``authorized=True`` for an
    orphan commit that was an ancestor of nothing trusted.

    Note the mechanism precisely, because the obvious guess is wrong and
    would invite the wrong fix: an ``__eq__``-only subclass does NOT defeat
    ``!=``, since ``str`` defines its own ``__ne__`` and the subclass
    inherits it rather than reaching ``object.__ne__``'s
    delegate-and-negate behaviour. Hardening the comparison was therefore
    the wrong lever; excluding subclasses at the type gate is the right
    one, and it is sufficient on its own -- this gate runs BEFORE any
    resolution, so no subclass instance ever reaches the comparison. See
    ``test_only_a_ne_override_defeats_the_invariant_eq_alone_does_not``.

    Deliberately shape-only, and deliberately not the whole story:

    - it does NOT prove ``value`` names a real commit -- that is
      ``resolve_commit_v2``'s job, run afterward against the content-verified
      trusted object authority;
    - it does NOT prove ``value`` is the sha a legitimate caller actually
      intended -- that is the caller's own out-of-band responsibility (see
      ``authorize_commit_for_execution_v2``'s docstring);
    - it does NOT, by itself, prove ``resolve_commit_v2`` will resolve
      ``value`` back to ``value`` -- see ``authorize_commit_for_execution_v2``'s
      own ``resolved != trusted_ref_sha`` check for the invariant that
      actually closes that gap; this function is shape-only on purpose.

    What it DOES do: reject every ref-NAME shape (``refs/heads/master``,
    ``HEAD``, ``main``, an abbreviated sha) outright, so this module never
    even attempts to resolve one of those against the hostile-derived object
    authority as a trust anchor (``#313``). Uppercase hex is deliberately
    also refused rather than case-folded -- git's own tooling always emits
    lowercase, and silently accepting a second spelling is one more shape a
    caller (or an attacker influencing what a caller assembles) could use to
    smuggle something this check did not exactly anticipate. A non-``str``
    value (e.g. a list of single characters that would each individually
    satisfy a naive per-element hex check, or ``None``) is refused via an
    explicit ``isinstance`` gate rather than being allowed to reach ``len()``/
    iteration and either coincidentally pass or raise an untyped ``TypeError``
    (independent-review P2, correction round 2).

    ONLY 40 (sha1) is accepted -- 64 (sha256) was deliberately dropped
    (independent-review P0, correction round 2, ``#313`` follow-up):
    ``_write_minimal_bare_skeleton_v2`` in ``trusted_object_authority_v2.py``
    hardcodes the private trusted object authority as sha1-format ALWAYS (no
    ``extensions.objectformat``), so a 64-hex string can never actually name
    an object in that store. Git's own object-vs-ref disambiguation only
    treats a hex string as a candidate object id when its length matches the
    repository's actual hash algorithm (40 for sha1); a 64-hex string falls
    through, silently, to ORDINARY REF-NAME resolution instead -- and
    `_copy_refs_fd_v2` copies `refs/heads/**`/`refs/tags/**` verbatim from
    the same hostile-scoped checkout this module's own threat model already
    declares adversarial. A caller supplying a genuine, public,
    out-of-band-verified 64-hex anchor (the exact case this shape check
    exists to protect) is thereby exposed to an attacker who learns that
    public value and plants an ordinary ref (`git branch`/`git tag`) LITERALLY
    NAMED after it, pointing at their own commit -- `authorize_commit_for_
    execution_v2` then resolves the caller's own trusted anchor as that ref
    name and returns `authorized=True` for the attacker's commit. Reproduced
    directly, independently, three times (two adversarial review lanes plus
    a human maintainer) against the pre-fix shape (40, 64). Re-adding 64
    without also giving the trusted object authority genuine sha256 support
    (which does not exist anywhere in this module family today --
    ``resolve_commit_v2`` in ``git_commit_subject_v2.py`` independently
    hard-rejects any resolved value with ``len != 40``, so a real sha256
    anchor could never work end-to-end regardless) reopens this exact
    vulnerability with zero corresponding legitimate use.
    """
    if type(value) is not str:
        return False
    return len(value) in _FULL_COMMIT_SHA_LENGTHS_V2 and all(c in _HEX_DIGITS_V2 for c in value)


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
    authorized_storage: AuthorizedGitStorageSetV2 | Sequence[Path | str] | None = None,
    authorized_storage_roots: Sequence[Path | str] | None = None,
) -> ExecutedSourceIdentityV2:
    """Prove ``subject_root``'s directory graph IS ``commit_sha``'s raw tree.

    `#333` contract Q: full Git tree structural equality (contract A, symlink
    rule A2) of the observed materialised subject, UNDER A QUIESCENCE
    PRECONDITION. Let G be ``commit_sha``'s raw Git tree (read through the
    trusted object authority), M the directory graph rooted at the ACQUIRED
    ``subject_root`` descriptor, and I the interval of this call.

    Precondition ``Quiescent(M, I)``: no actor mutates M's node graph, file
    bytes, executable semantics or symlink target data during I (nor the
    trusted object authority's private copy, whose own threat scope is the
    same). This is a contract precondition and a threat-scope boundary, NOT
    an enforced lock: ``QuiescencePrecondition != WriteExclusionMechanism``.
    It is this module's existing boundary made explicit, not a new
    exception -- a writer that can mutate M during I is a
    ``host_arbitrary_code_attacker`` (module docstring, "Threat scope"; the
    same boundary ``trusted_object_authority_v2.py`` declares), out of scope
    here. Resistance to that writer is owned by `#301` (an authenticated,
    immutable closed representation that execution consumes), never by
    observing M again.

    Success postcondition (given the precondition): G == M over I -- the
    same explicit tree nodes (including empty trees) and the same leaves;
    every node of the kind Git declares (tree / regular blob / symlink),
    walked without following symlinks; regular-file bytes, executable
    semantics (``100755`` <=> owner-execute bit) and raw symlink target bytes
    equal; no missing node and no extra node beneath the root.

    Nonclaims: no same-UID arbitrary-writer resistance (a writer that
    violates the precondition can make this return success on a subject that
    differs from G at return -- witnessed in `#352`, R1/R3/N1, and out of
    scope by the boundary above); no immutability after verification; no
    execution provenance and no claim about bytes any process loaded
    (`#301`); not the trust root of `#301`; no Git-anchor provenance
    (`#319`); not unique commit provenance (tree-sharing commits pass alike);
    no filesystem metadata Git does not represent (directory modes,
    ownership, times, xattrs/ACLs); no interpretation of where a symlink
    target resolves; no binding between the returned ``subject_root`` PATH
    and the descriptor that was walked (``PathReturned !=
    DescriptorIdentityVerified``, `#301`'s to bind).

    Resource limitation (declared, `#352` review A): expected and observed
    graphs are keyed by full raw paths, so peak heap is O(total flattened
    path bytes) of the tree -- measured at about 2.9x C3's own
    materialisation peak for the same tree (the flattened ``ls-tree -r``
    listing, which added about 1x more, is off the success path). C3's
    budgets bound entries and depth, not path bytes; a path-bytes budget
    would bound producer and verifier alike and is C3's to add.

    Never trusts a pre-computed digest. Re-derives the commit's tree fresh
    from ``repo_root``'s own git object store on every call, through the
    private trusted object authority, and compares against what is on disk
    at ``subject_root`` NOW.

    Authorities, in order:

    1. ``commit_sha`` resolves to a real commit (never a tree/blob sha).
    2. The tree contains no gitlink, and no ``..``-shaped entry path
       (pre-existing reason codes; consulted through the leaf listing, which
       is NOT the structural authority).
    3. Structure comes from C3's hierarchical raw-tree traversal
       (``list_commit_tree_structure_v2``): ``git ls-tree -r`` drops explicit
       empty trees, so it is exactly the lossy boundary this contract
       removes. A commit C3 cannot represent is refused
       (``IDENTITY_TREE_UNREPRESENTABLE_REASON_V2``).
    4. The subject root is opened ``O_DIRECTORY | O_NOFOLLOW`` and every
       observation is descriptor-relative from that authority: the locator
       is discovery input, a root locator that is itself a symlink is
       refused. Node kinds come from ``fstatat(..., AT_SYMLINK_NOFOLLOW)``;
       directories are entered by no-follow re-acquisition (live
       descriptors of the traversal independent of tree depth -- a property
       of the walk, not a bound on the whole call, whose git-side pipes and
       spool are separate), entries are streamed, and budgets
       ``MAX_OBSERVED_NODES_V2`` / ``MAX_OBSERVED_DEPTH_V2`` are enforced as
       nodes are discovered.
    5. ``ExpectedPaths == ObservedPaths`` with kind equality at every path
       (missing tree node, missing leaf, extra node, extra file, kind
       mismatch, symlink where a tree is declared -- each its own reason).
    6. Only then leaf content: a regular leaf is opened
       ``O_NOFOLLOW | O_NONBLOCK`` and ``fstat`` must say ``S_ISREG`` before
       a byte is read (a FIFO/special file is a kind mismatch, never a
       hang -- `#321`'s class); bytes are compared in bounded chunks; a
       symlink leaf is ``readlink``ed and its raw target bytes compared,
       never followed.
    7. Every path in ``loaded_module_paths`` (defaulting to
       ``loaded_module_files_v2()``) resolves under ``subject_root``. An
       independent second signal, not a substitute for the structure check.
    """
    # `#331-A`: do not pre-resolve `repo_root`. `open_trusted_object_authority_v2`
    # owns component-wise no-follow acquisition of the raw locator, and states
    # the locator contract. Nothing below reads `repo_root` again; every read
    # goes through `authority.trusted_repo_root`. Locator refusals arrive here
    # as one reason code, with the authority's specific code on `__cause__`.
    # `#331-B`: external storage transitions require explicit authorized_storage_roots.
    # `#333` -- the LOCATOR is discovery input; the DESCRIPTOR is the observed
    # subject. `resolved_root` is kept only for the loaded-module containment
    # check (5) and the returned value; no observation is made through it.
    resolved_root = Path(subject_root).resolve()
    root_fd = _acquire_subject_root_fd_v2(Path(subject_root))

    # #200-G1C: every read below goes through a private, remote-less object
    # authority built from whatever is physically present at `repo_root`
    # right now -- never against `repo_root` directly. `repo_root` itself is
    # discovery input only; see `trusted_object_authority_v2.py`.
    # C3 boundary: `read_commit_blobs_v2` returns a carrier that owns an open spool.
    # It is closed here on every exit (success, refusal, any BaseException); the
    # carrier's __del__ is a backstop only and must not be relied on, because a
    # retained traceback keeps this frame (and the carrier) alive.
    expected_content_by_path = None
    try:
        try:
            with open_trusted_object_authority_v2(
                repo_root,
                authorized_storage=authorized_storage,
                authorized_storage_roots=authorized_storage_roots,
            ) as authority:
                trusted_root = authority.trusted_repo_root
                try:
                    resolved_commit = resolve_commit_v2(repo_root=trusted_root, ref=commit_sha)
                except SubjectMaterialisationError as exc:
                    raise ExecutedSourceIdentityError(IDENTITY_UNKNOWN_COMMIT_REASON_V2) from exc

                # Structural authority: C3's hierarchical raw-tree traversal,
                # bounded by C3's own budgets. `git ls-tree -r` drops explicit
                # (empty) tree nodes -- the earliest lossy boundary this
                # contract exists to remove -- and its flattened output is not
                # bounded by those budgets (`#352` review: ~3x the flattened
                # path bytes of a C3-admitted tree), so it is never on the
                # success path. C3 itself refuses gitlinks and `..` names.
                try:
                    structure = list_commit_tree_structure_v2(repo_root=trusted_root, commit_sha=resolved_commit)
                except SubjectMaterialisationError as exc:
                    if exc.reason_code == SUBJECT_UNREPRESENTABLE_TREE_REASON_V2:
                        legacy = _legacy_refusal_reason_v2(trusted_root, resolved_commit)
                        raise ExecutedSourceIdentityError(legacy or IDENTITY_TREE_UNREPRESENTABLE_REASON_V2) from exc
                    raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc

                expected: dict[bytes, tuple[str, bool]] = {}
                for entry in structure:
                    if entry.object_type == "tree":
                        kind = "tree"
                    elif entry.mode == SYMLINK_MODE_V2:
                        kind = "symlink"
                    else:
                        kind = "regular"
                    expected[os.fsencode(entry.path)] = (kind, entry.mode == EXECUTABLE_MODE_V2)
                leaves = [entry for entry in structure if entry.object_type != "tree"]

                try:
                    expected_content_by_path = read_commit_blobs_v2(repo_root=trusted_root, entries=leaves)
                except SubjectMaterialisationError as exc:
                    if exc.reason_code == SUBJECT_BLOB_MISSING_REASON_V2:
                        raise ExecutedSourceIdentityError(IDENTITY_BLOB_MISSING_REASON_V2) from exc
                    raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc
        except TrustedObjectAuthorityError as exc:
            raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc

        # Observation, after all git-side work: the structural graph first
        # (bounded, typed, no content), then every leaf's bytes on a
        # descriptor whose type was established by `fstat` first.
        observed = _observe_subject_graph_v2(root_fd)
        _compare_structure_v2(expected, observed)
        for entry in leaves:
            relative = os.fsencode(entry.path)
            if entry.mode == SYMLINK_MODE_V2:
                _compare_symlink_leaf_v2(root_fd, relative, expected_content_by_path, entry.path)
            else:
                _compare_regular_leaf_v2(
                    root_fd, relative, expected_content_by_path, entry.path,
                    executable=entry.mode == EXECUTABLE_MODE_V2,
                )

        if loaded_module_paths is None:
            loaded_module_paths = loaded_module_files_v2()
        for module_path in loaded_module_paths:
            resolved_module_path = Path(module_path).resolve()
            if not resolved_module_path.is_relative_to(resolved_root):
                raise ExecutedSourceIdentityError(IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2)

        return ExecutedSourceIdentityV2(commit_sha=resolved_commit, subject_root=resolved_root)
    finally:
        # `#352` review: neither close may skip the other, whatever raises.
        try:
            os.close(root_fd)
        finally:
            if expected_content_by_path is not None:
                expected_content_by_path.close()


def authorize_commit_for_execution_v2(
    *,
    repo_root: Path,
    commit_sha: str,
    trusted_ref_sha: str,
    authorized_storage: AuthorizedGitStorageSetV2 | Sequence[Path | str] | None = None,
    authorized_storage_roots: Sequence[Path | str] | None = None,
) -> ExecutedSourceAuthorizationV2:
    """Is ``commit_sha`` reachable from ``trusted_ref_sha``? Distinct from identity.

    ``trusted_ref_sha`` MUST already be a full, immutable sha1 commit sha
    that the caller has verified out-of-band -- never a ref name
    (``refs/heads/master``, ``HEAD``, a branch/tag name) for this function to
    resolve itself. Any value that is not exactly 40 lowercase hex
    characters is refused with ``IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2``
    BEFORE this function ever opens the trusted object authority -- see the
    module docstring's "``trusted_ref_sha`` must be an out-of-band anchor"
    section (``#313``) for why a ref name is refused outright rather than
    merely discouraged: this authority's ref *values* are copied verbatim
    from the same hostile-scoped checkout its object *content* verification
    defends against, so resolving a ref name through it would let whatever
    that hostile checkout currently claims the name means become the trust
    anchor. (64-hex/sha256 was deliberately dropped from the accepted shape,
    not merely left unimplemented -- see ``_is_full_commit_sha_shape_v2``'s
    own docstring for the reproduced P0 this closes: the private trusted
    object authority is hardcoded sha1-format, so a 64-hex string can never
    be a real object id there and instead falls through, silently, to
    ordinary -- and here, hostile-controllable -- ref-name resolution.)

    SHAPE ALONE IS NOT ENOUGH, EVEN AT 40 HEX -- after ``trusted_ref_sha``
    passes the shape check, it is resolved via ``resolve_commit_v2`` like any
    other ref, and the RESULT is required to be byte-identical to the value
    the caller supplied, refused with
    ``IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2`` otherwise. This is the
    structural invariant this function's contract always implied ("supply an
    object id, not a name") but never actually enforced before -- a
    shape-valid string that git's own resolver, for whatever reason
    (git-version/config difference, hash-length collision with the wrong
    algorithm, annotated-tag peeling divergence), resolves to something OTHER
    than itself was never actually verified to BE the object id it looked
    like. Cheap, and it closes the general class of "shape-valid string that
    is not actually its own object id", not merely the one dropped length
    that happened to be reproducibly exploitable today.

    WHAT THE SHAPE CHECK DOES NOT COVER -- do not derive ``trusted_ref_sha``
    by calling ``resolve_commit_v2`` (or any other read primitive in this
    module family) against ``open_trusted_object_authority_v2(repo_root)``'s
    own authority: that reconstructs the exact pre-#313 attack one call
    outside this function, because the *result* of resolving a hostile ref
    through the hostile-derived authority is, genuinely, a real, shape-valid
    commit sha -- just not an out-of-band one. See the module docstring's
    "Residual risk the shape check does NOT and cannot close" section for
    why this cannot be closed by validation alone, and ``#200-G1C2-F3`` for
    the tracked follow-up.

    Both ``commit_sha`` and ``trusted_ref_sha`` are independently re-resolved
    (the latter only to confirm it names a real, content-verified commit --
    never to interpret it as anything other than the exact sha supplied),
    and the ancestry question itself is decided, entirely against a private
    trusted object authority built from ``repo_root`` (#200-G1C) -- never
    against ``repo_root`` directly. This function never evaluates ancestry
    of an unverified string. It says nothing about whether ``commit_sha``'s
    tree matches any particular bytes on disk -- that is
    ``verify_executed_source_identity_v2``'s job, and the two are meant to
    be composed by the caller, never merged here.

    ``AUTHORIZED TRUE`` and ``AUTHORIZED FALSE`` both require a positive,
    completely-enumerated graph proof from the trusted authority (see
    ``TrustedObjectAuthorityV2.prove_ancestry``). An incomplete ancestry
    closure -- shallow history, a missing or corrupted parent object, or
    any other reason the graph could not be fully walked -- raises
    ``IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2`` rather than being
    silently treated as ``False``. This is deliberately never inferred from
    a bare git exit code: see ``trusted_object_authority_v2.py`` for why
    that was the specific mechanism that refuted three successive
    corrections in PR #302's withdrawn S4 attempt.
    """
    if not _is_full_commit_sha_shape_v2(trusted_ref_sha):
        raise ExecutedSourceIdentityError(IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2)

    # `#331-A`: do not pre-resolve `repo_root`. `open_trusted_object_authority_v2`
    # owns component-wise no-follow acquisition of the raw locator, and states
    # the locator contract. Nothing below reads `repo_root` again; every read
    # goes through `authority.trusted_repo_root`. Locator refusals arrive here
    # as one reason code, with the authority's specific code on `__cause__`.
    # `#331-B`: external storage transitions require explicit authorized_storage_roots.
    try:
        with open_trusted_object_authority_v2(
            repo_root,
            authorized_storage=authorized_storage,
            authorized_storage_roots=authorized_storage_roots,
        ) as authority:
            trusted_root = authority.trusted_repo_root
            try:
                resolved_commit = resolve_commit_v2(repo_root=trusted_root, ref=commit_sha)
                resolved_trusted = resolve_commit_v2(repo_root=trusted_root, ref=trusted_ref_sha)
            except SubjectMaterialisationError as exc:
                raise ExecutedSourceIdentityError(IDENTITY_UNKNOWN_COMMIT_REASON_V2) from exc

            # P0 fix (independent review, correction round 2): a
            # shape-valid `trusted_ref_sha` is not necessarily its OWN
            # resolution -- `resolve_commit_v2` resolves whatever git's own
            # rev-parse decides `trusted_ref_sha` names, which is ONLY
            # guaranteed to be the same object id when the resolver actually
            # treated it as one (see `_is_full_commit_sha_shape_v2`'s
            # docstring for the reproduced case where it did not: a 64-hex
            # string falling through to ref-name resolution). This
            # equality check is the structural invariant that actually
            # closes that class, independent of which length or mechanism
            # produces the divergence.
            if resolved_trusted != trusted_ref_sha:
                raise ExecutedSourceIdentityError(IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2)

            authorized = authority.prove_ancestry(
                commit_sha=resolved_commit, trusted_ref_sha=resolved_trusted
            )
    except TrustedObjectAuthorityError as exc:
        if exc.reason_code == TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2:
            raise ExecutedSourceIdentityError(IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2) from exc
        raise ExecutedSourceIdentityError(IDENTITY_TREE_UNREADABLE_REASON_V2) from exc

    return ExecutedSourceAuthorizationV2(
        commit_sha=resolved_commit,
        trusted_ref_sha=resolved_trusted,
        authorized=authorized,
    )
