"""`#200-G3` -- changed scope is not fragment coverage (successor to `#200-F`).

## The confusion this module ends

``ChunkCoverageV2`` answers *"were the reviewable fragments reviewed?"*. It
is computed over ``expected_files``, which is the set of paths that
produced reviewable fragments. A changed path that yields no fragment -- a
pure rename, a chmod, a binary, an empty file -- never enters
``expected_files`` at all, so coverage reports ``complete`` while the path
is simply invisible. The contract cannot notice on its own.

``#276`` saw the hazard and reached for::

    if assembly.excluded_paths:
        raise OperationalRunError(...)

which denies review outright for renames, chmod-only changes, binaries,
lockfiles, images and empty-file additions -- and did so under a reason
code (``operational_run_scope_silently_narrowed``) that misdescribed the
event, since nothing was narrowed silently; the composer refused. The fix
sat at the wrong level of abstraction: it closed a vector that could not
have produced an emitted ``ready`` artifact, and broke ordinary reviews to
do it. Withdrawn by `#200-F`'s ADR, not revisited here.

## The distinction, kept explicit

``DiffCoverage``
    coverage of the actual reviewable fragments. Unchanged, still owned by
    ``run_fragment_coverage_v2``.

``ScopeCompleteness``
    the disposition of *every* changed path. Owned here.

Every changed path lands in exactly one structural disposition
(:class:`PathDispositionV2`), so nothing is dropped and nothing needs a
blanket refusal. ``must_review_blocked`` is a SEPARATE, orthogonal axis
(:attr:`ScopeAssessmentV2.blocked`/``must_review_blocked_paths``) layered on
top of the structural disposition, not a twelfth structural value returned
by :func:`classify_changed_path_v2` -- a path is blocked because it is
*both* required *and* unreviewable, which is a fact about the combination
of profile policy and disposition, not a disposition in its own right.

## Why "metadata only" (rename / chmod / empty-file / submodule) does not
## make scope incomplete

A pure rename with no content change has no reviewable material. Neither
does a chmod, an empty-file add, or a gitlink pointer move. Calling those
"incomplete" would mean no review of an ordinary refactor could ever be
complete, which is how `#276` ended up denying them. The rule is *"all
changed material representable -> scope complete"*: these paths carry no
material, so they are vacuously representable once they are **accounted
for**. Accounting is the product; refusal was not.

``binary_unsupported``/``truncated``/``unrepresentable``/``type_change``
are the opposite case and are treated as the opposite: each carries
material this product cannot represent for review. That is a real
capability gap, it makes total scope incomplete, and ``ready`` becomes
impossible. A genuine git *type change* (a regular file becoming a
symlink, or the reverse) is git's own delete-plus-add rendering of ONE
coherent change to the SAME path -- recognized by
:func:`_is_type_change_pair_v2` -- and is dispositioned ``type_change``,
not two independent changes and not a duplicate-path error.

A must-review path that is unreviewable fails closed regardless -- the
target declared it as material that may not go unexamined.

## The disagreement detector (anti-recurrence, not merely defense-in-depth)

`#200-F`'s round-1 defect was two independent reimplementations of "is this
path representable" silently disagreeing: a path
(``src/pages/[id].tsx``-shaped) that ``run_assembly_v2`` correctly excluded
was, at the time, incorrectly certified ``reviewable``/``scope_complete``
by a scope authority that had reimplemented three of the assembly's four
representability conditions. Sharing
``diff_acquisition_v2.path_violates_relative_path_contract_v2`` (this
module never restates it) closes the CURRENT instance of that defect. It
does not close the CLASS of defect: a future change to either module in
isolation could silently reintroduce a divergence. The structural
anti-recurrence mechanism is
:func:`assert_scope_authority_agrees_with_assembly_v2` -- a composer-level
refusal whenever the scope authority's own ``reviewable_paths`` and an
independently-computed assembly ``expected_files`` set diverge in EITHER
direction, checked as a runtime invariant every run, not merely proved once
by a fuzz corpus.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence

from app.agent_review.contracts_v2 import TargetProfileV2
from app.agent_review.diff_acquisition_v2 import (
    ParsedFileDiffV2,
    path_violates_relative_path_contract_v2,
)

# Single source of truth for must-review matching. Reusing `run_assembly_v2`'s
# private helpers rather than reimplementing fnmatch semantics here: two
# matchers that agree today would drift, and a scope authority that disagreed
# with the assembly about what is required would be worse than no authority.
from app.agent_review.run_assembly_v2 import (
    _is_must_review_path,
    _resolve_must_review_paths,
)

__all__ = [
    "SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2",
    "SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2",
    "PathDispositionV2",
    "ScopeAssessmentV2",
    "ScopeAssessmentError",
    "assess_changed_scope_v2",
    "assert_scope_authority_agrees_with_assembly_v2",
    "classify_changed_path_v2",
]


SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2 = "scope_assessment_duplicate_changed_path"
SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2 = "scope_authority_assembly_disagreement"


class ScopeAssessmentError(ValueError):
    """Raised when the changed scope itself is incoherent, or when this
    authority and an independent assembly disagree about it. Carries a
    stable ``reason_code`` only -- never diff or path content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class PathDispositionV2(str, enum.Enum):
    """What the product did, or could not do, with one changed path.

    Exhaustive by construction: :func:`classify_changed_path_v2` and
    :func:`assess_changed_scope_v2` together return a member for every
    input path, so "unclassified" is not a reachable state and no path can
    be dropped on the floor. ``must_review_blocked`` is deliberately NOT a
    member here -- see the module docstring.
    """

    #: Produced reviewable fragments and entered the ordinary review path.
    REVIEWABLE = "reviewable"

    #: Changed, no reviewable material, and not one of the more specific
    #: categories below (e.g. a pure copy with no content change).
    METADATA_ONLY = "metadata_only"

    #: A pure rename: same content, no hunks.
    RENAME = "rename"

    #: A pure permission-bit change: same content, same kind of object, no
    #: hunks. Distinct from ``type_change`` -- see that member.
    CHMOD_ONLY = "chmod_only"

    #: A git *type* change: the object at this path became a different
    #: KIND of object (regular file <-> symlink), rendered by git as a
    #: delete block plus an add block for the same path. Real material
    #: changed and this product cannot render the transition for review,
    #: so -- unlike ``chmod_only`` -- this makes total scope incomplete.
    TYPE_CHANGE = "type_change"

    #: An empty file was added or deleted: no hunks are possible either way.
    EMPTY_FILE_TRANSITION = "empty_file_transition"

    #: A gitlink/submodule pointer move. A 40-byte pointer, not content.
    SUBMODULE_GITLINK = "submodule_gitlink"

    #: Binary content this product cannot render as textual hunks. A real
    #: capability gap.
    BINARY_UNSUPPORTED = "binary_unsupported"

    #: The patch content itself is incomplete (a hunk header declared more
    #: old/new lines than its body actually supplies).
    TRUNCATED = "truncated"

    #: A path git accepts but that fails ``contracts_v2.RelativePath`` -- a
    #: glob metacharacter (``[id].tsx``, an everyday Next.js/SvelteKit
    #: route), an overlong name, a decoded control character.
    UNREPRESENTABLE = "unrepresentable"


#: Dispositions whose paths carry no material this product could ever have
#: reviewed -- accounted for, vacuously representable, do not make scope
#: incomplete.
_VACUOUSLY_REPRESENTABLE_DISPOSITIONS_V2 = frozenset(
    {
        PathDispositionV2.METADATA_ONLY,
        PathDispositionV2.RENAME,
        PathDispositionV2.CHMOD_ONLY,
        PathDispositionV2.EMPTY_FILE_TRANSITION,
        PathDispositionV2.SUBMODULE_GITLINK,
    }
)

#: Dispositions that represent a genuine capability gap: material changed
#: and this product could not represent it for review. Each of these makes
#: total scope incomplete.
_UNSUPPORTED_DISPOSITIONS_V2 = frozenset(
    {
        PathDispositionV2.TYPE_CHANGE,
        PathDispositionV2.BINARY_UNSUPPORTED,
        PathDispositionV2.TRUNCATED,
        PathDispositionV2.UNREPRESENTABLE,
    }
)

assert (
    _VACUOUSLY_REPRESENTABLE_DISPOSITIONS_V2 | _UNSUPPORTED_DISPOSITIONS_V2 | {PathDispositionV2.REVIEWABLE}
    == frozenset(PathDispositionV2)
), "every PathDispositionV2 member must be classified representable or unsupported"
assert not (_VACUOUSLY_REPRESENTABLE_DISPOSITIONS_V2 & _UNSUPPORTED_DISPOSITIONS_V2)


def _is_type_change_pair_v2(blocks: Sequence[ParsedFileDiffV2]) -> bool:
    """Are these two blocks git's rendering of one type change?

    Exactly one delete and one add for the same path. Anything else sharing
    a path is a genuine inconsistency and still refuses, so this does not
    become a way for two real changes to the same file to be silently
    merged.
    """
    if len(blocks) != 2:
        return False
    return {block.change_type for block in blocks} == {"deleted", "added"}


def classify_changed_path_v2(file_diff: ParsedFileDiffV2) -> PathDispositionV2:
    """Give one changed path (one ``ParsedFileDiffV2`` block) exactly one
    structural disposition.

    Order matters and is deliberate:

    ``truncated`` first
        A truncated patch may be truncated *anywhere*, including before a
        binary marker or a mode-change header, so nothing later in this
        function can be trusted about it. Treating it as unsupported is the
        fail-safe reading.

    ``submodule`` before ``binary``
        A gitlink is a 40-byte pointer, not content. Reporting it as an
        unsupported binary would invent a capability gap that does not
        exist and would make ``ready`` unreachable for any submodule bump.

    ``binary`` before the path-contract and hunk tests
        Binary diffs legitimately carry no textual hunks; without this
        order every binary would be misfiled as metadata-only and would
        stop counting against scope completeness.

    unrepresentable path before the hunk test
        The predicate is **imported** from ``diff_acquisition_v2``, which
        is the module ``run_assembly_v2``'s assembly consults (via
        ``validate_diff_completeness_v2``), so the two cannot disagree
        about what is representable. This is the exact defect class
        `#200-F` found: reimplementing this condition, rather than sharing
        it, let a path be certified ``reviewable`` here while the assembly
        silently excluded it.

    ``chmod_only`` before the hunk test
        The unified-diff parser (``diff_acquisition_v2``) only ever reports
        ``change_type == "type_changed"`` for a SINGLE block that has both
        an old and a new mode, no hunks, and is not binary -- which is
        exactly git's rendering of a pure permission-bit change. A genuine
        cross-kind type change (regular <-> symlink) arrives as a
        DIFFERENT shape entirely: two blocks (delete + add) for the same
        path, handled by :func:`_is_type_change_pair_v2` in
        :func:`assess_changed_scope_v2`, never here.

    absence of hunks last
        What remains with no hunks is a pure rename or an empty-file add or
        delete; a pure copy or anything else hunkless falls back to the
        generic ``metadata_only``.
    """
    if file_diff.truncated:
        return PathDispositionV2.TRUNCATED
    if file_diff.is_submodule:
        return PathDispositionV2.SUBMODULE_GITLINK
    if file_diff.is_binary:
        return PathDispositionV2.BINARY_UNSUPPORTED
    if path_violates_relative_path_contract_v2(file_diff.path):
        return PathDispositionV2.UNREPRESENTABLE
    if file_diff.change_type == "type_changed":
        return PathDispositionV2.CHMOD_ONLY
    if not file_diff.hunks:
        if file_diff.change_type == "renamed":
            return PathDispositionV2.RENAME
        if file_diff.change_type in ("added", "deleted"):
            return PathDispositionV2.EMPTY_FILE_TRANSITION
        return PathDispositionV2.METADATA_ONLY
    return PathDispositionV2.REVIEWABLE


@dataclass(frozen=True)
class ScopeAssessmentV2:
    """The disposition of every changed path in one run.

    A **private** authority: not a published schema on its own. The
    published, additive representation is
    :meth:`to_scope_completeness_v2`, which folds this rich internal
    classification into the coarse two-bucket
    ``contracts_v2.ScopeCompletenessV2`` shape (``metadata_only_paths`` /
    ``unsupported_paths``) that `#200-F`'s ADR already proposed and this
    slice ships.
    """

    path_dispositions: tuple[tuple[str, PathDispositionV2], ...]
    must_review_paths: frozenset[str]

    def __post_init__(self) -> None:
        paths = [path for path, _ in self.path_dispositions]
        if len(paths) != len(set(paths)):
            raise ScopeAssessmentError(SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2)

    def _paths_with(self, *dispositions: PathDispositionV2) -> tuple[str, ...]:
        wanted = frozenset(dispositions)
        return tuple(sorted(path for path, disposition in self.path_dispositions if disposition in wanted))

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted(path for path, _ in self.path_dispositions))

    @property
    def reviewable_paths(self) -> tuple[str, ...]:
        return self._paths_with(PathDispositionV2.REVIEWABLE)

    @property
    def metadata_only_paths(self) -> tuple[str, ...]:
        """Every path whose disposition is vacuously representable."""
        return self._paths_with(*_VACUOUSLY_REPRESENTABLE_DISPOSITIONS_V2)

    @property
    def unsupported_paths(self) -> tuple[str, ...]:
        """Every path whose disposition is a genuine capability gap."""
        return self._paths_with(*_UNSUPPORTED_DISPOSITIONS_V2)

    @property
    def must_review_blocked_paths(self) -> tuple[str, ...]:
        """A must-review path that produced nothing reviewable -- blocked
        whether the cause was an absence of material (``metadata_only``-
        shaped) or a gap in our ability to represent it
        (``unsupported``-shaped). The target asked for the path to be
        examined; "there was nothing to look at" is a conclusion only a
        human is entitled to draw."""
        return tuple(
            sorted(
                path
                for path, disposition in self.path_dispositions
                if disposition is not PathDispositionV2.REVIEWABLE and path in self.must_review_paths
            )
        )

    @property
    def scope_complete(self) -> bool:
        """True when no changed path carries material we could not
        represent. Metadata-only-shaped paths do not count against
        completeness -- they carry no material. Unsupported-shaped paths
        do."""
        return not self.unsupported_paths

    @property
    def blocked(self) -> bool:
        """True when a path the target declared must-review is
        unreviewable. Fail-closed and strictly stronger than
        ``not scope_complete``: an ordinary binary makes scope incomplete,
        but a binary the profile *required* to be reviewed blocks the
        run."""
        return bool(self.must_review_blocked_paths)

    @property
    def accounted_paths(self) -> tuple[str, ...]:
        """Every path that received a disposition. Equal to
        ``changed_paths`` for any value this module builds -- exposed so
        the invariant can be asserted from outside rather than trusted."""
        return self.changed_paths

    def disposition_of(self, path: str) -> PathDispositionV2 | None:
        for candidate, disposition in self.path_dispositions:
            if candidate == path:
                return disposition
        return None

    def to_scope_completeness_v2(self):
        """Fold this internal assessment into the published, additive
        ``contracts_v2.ScopeCompletenessV2``. Imported lazily to avoid a
        module-level import cycle (``contracts_v2`` does not, and must
        not, import this module)."""

        from app.agent_review.contracts_v2 import ScopeCompletenessV2

        return ScopeCompletenessV2(
            complete=self.scope_complete,
            changed_paths=self.changed_paths,
            reviewable_paths=self.reviewable_paths,
            metadata_only_paths=self.metadata_only_paths,
            unsupported_paths=self.unsupported_paths,
            must_review_blocked_paths=self.must_review_blocked_paths,
        )


def assess_changed_scope_v2(
    *,
    file_diffs: Sequence[ParsedFileDiffV2],
    profile: TargetProfileV2,
) -> ScopeAssessmentV2:
    """Assess every changed path against the target's must-review policy.

    Raises rather than silently coalescing when two file diffs claim the
    same canonical path and are NOT a recognized type-change pair: that
    would make one of them disappear, which is the exact class of silent
    loss this authority exists to prevent.
    """
    explicit_must_review = _resolve_must_review_paths(profile)
    must_review_patterns = tuple(profile.must_review.patterns)

    # Group by path BEFORE dispositioning. Git renders a single genuine type
    # change -- a regular file becoming a symlink, or the reverse -- as a
    # delete block *plus* an add block for the same path.
    by_path: dict[str, list[ParsedFileDiffV2]] = {}
    for file_diff in file_diffs:
        by_path.setdefault(file_diff.path, []).append(file_diff)

    must_review_paths: set[str] = set()
    for path in by_path:
        if _is_must_review_path(path, explicit_paths=explicit_must_review, patterns=must_review_patterns):
            must_review_paths.add(path)

    dispositions: list[tuple[str, PathDispositionV2]] = []
    for path, blocks in sorted(by_path.items()):
        if len(blocks) == 1:
            disposition = classify_changed_path_v2(blocks[0])
        elif _is_type_change_pair_v2(blocks):
            disposition = PathDispositionV2.TYPE_CHANGE
        else:
            raise ScopeAssessmentError(SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2)
        dispositions.append((path, disposition))

    return ScopeAssessmentV2(
        path_dispositions=tuple(dispositions),
        must_review_paths=frozenset(must_review_paths),
    )


def assert_scope_authority_agrees_with_assembly_v2(
    assessment: ScopeAssessmentV2,
    *,
    assembly_expected_files: Sequence[str],
) -> None:
    """Composer-level anti-recurrence check: refuse the WHOLE run rather
    than emit anything when this scope authority's own ``reviewable_paths``
    and an independently-computed assembly's ``expected_files`` diverge in
    EITHER direction.

    This is deliberately a RUNTIME invariant, checked every run, not merely
    a fact proved once by a fuzz corpus and then trusted forever: a future
    change to either this module or ``run_assembly_v2`` in isolation could
    silently reintroduce the exact `#200-F` round-1 divergence (a scope
    authority certifying a path as reviewable that the assembly actually
    excluded, or the reverse). See the module docstring.
    """

    scope_reviewable = frozenset(assessment.reviewable_paths)
    assembly_expected = frozenset(assembly_expected_files)
    if scope_reviewable != assembly_expected:
        raise ScopeAssessmentError(SCOPE_AUTHORITY_ASSEMBLY_DISAGREEMENT_REASON_V2)
