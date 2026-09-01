"""`#200-F` authority C -- changed scope is not fragment coverage.

## The confusion this module ends

`ChunkCoverageV2` answers *"were the reviewable fragments reviewed?"*. It is
computed over ``expected_files``, which is the set of paths that produced
reviewable fragments. A changed path that yields no fragment -- a pure rename,
a chmod, a binary, an empty file -- never enters ``expected_files`` at all, so
coverage reports ``complete`` while the path is simply invisible. The contract
cannot notice: this is demonstrated, not asserted, in
``test_operational_scope_contract_spike_v2.py``.

`#276` saw the hazard and reached for::

    if assembly.excluded_paths:
        raise OperationalRunError(...)

which denies review outright for renames, chmod-only changes, binaries,
lockfiles, images and empty-file additions -- and did so under a reason code
(``operational_run_scope_silently_narrowed``) that misdescribed the event,
since nothing was narrowed silently; the composer refused. The fix sat at the
wrong level of abstraction: it closed a vector that could not have produced an
emitted ``ready`` artifact, and broke ordinary reviews to do it.

## The distinction, kept explicit

``DiffCoverage``
    coverage of the actual reviewable fragments. Unchanged, still owned by
    ``run_fragment_coverage_v2``.

``ScopeCompleteness``
    the disposition of *every* changed path. Owned here.

Every changed path lands in exactly one disposition, so nothing is dropped and
nothing needs a blanket refusal.

## Why "metadata only" does not make scope incomplete

A pure rename with no content change has no reviewable material. Neither does
a chmod, an empty-file add, or a gitlink pointer move. Calling those
"incomplete" would mean no review of an ordinary refactor could ever be
complete, which is how the predecessor ended up denying them. The grant's rule
is *"all changed material representable -> scope complete"*: these paths carry
no material, so they are vacuously representable once they are **accounted
for**. Accounting is the product; refusal was not.

``unsupported`` is the opposite case and is treated as the opposite: a binary
or a truncated patch *does* carry material this product cannot represent. That
is a real capability gap, it makes total scope incomplete, and ``ready``
becomes impossible.

A must-review path that is unreviewable fails closed regardless -- the target
declared it as material that may not go unexamined.
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
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

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
    "PathDispositionV2",
    "ScopeAssessmentV2",
    "ScopeAssessmentError",
    "assess_changed_scope_v2",
    "classify_changed_path_v2",
]


SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2 = "scope_assessment_duplicate_changed_path"


class ScopeAssessmentError(ExpectedOperationalRefusalV2, ValueError):
    """Raised when the changed scope itself is incoherent."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class PathDispositionV2(str, enum.Enum):
    """What the product did, or could not do, with one changed path.

    Exhaustive by construction: :func:`classify_changed_path_v2` returns a
    member for every input, so "unclassified" is not a reachable state and no
    path can be dropped on the floor.
    """

    #: Produced reviewable fragments and entered the ordinary review path.
    REVIEWABLE = "reviewable"

    #: Changed, but carries no reviewable material at all: a pure rename, a
    #: mode-only change, an empty-file add or delete, a submodule pointer.
    METADATA_ONLY = "metadata_only"

    #: Carries material this product cannot represent for review: binaries,
    #: lockfiles and images stored as binary, truncated or unrepresentable
    #: patches. A capability gap, not an absence of content.
    UNSUPPORTED = "unsupported"


def classify_changed_path_v2(file_diff: ParsedFileDiffV2) -> PathDispositionV2:
    """Give one changed path exactly one disposition.

    Order matters and is deliberate:

    ``truncated`` first
        A truncated patch may be truncated *anywhere*, including before the
        binary marker, so nothing later in this function can be trusted about
        it. Treating it as unsupported is the fail-safe reading.

    ``submodule`` before ``binary``
        A gitlink is a 40-byte pointer, not content. Reporting it as an
        unsupported binary would invent a capability gap that does not exist
        and would make ``ready`` unreachable for any submodule bump.

    ``binary`` before the hunk test
        Binary diffs legitimately carry no textual hunks; without this order
        every binary would be misfiled as metadata-only and would stop
        counting against scope completeness.

    unrepresentable path before the hunk test
        A path git accepts can still be unrepresentable under
        ``contracts_v2.RelativePath`` -- a glob metacharacter (``[id].tsx`` is
        an everyday Next.js/SvelteKit route), an overlong name, a decoded
        control character. The predicate is **imported** from
        ``diff_acquisition_v2``, which is the module the assembly consults,
        so the two cannot disagree about what is representable.

        This condition was missing. Its absence let the scope authority
        certify such a path as ``reviewable`` and ``scope_complete`` while the
        assembly silently excluded it, producing a run that emitted ``ready``
        having never reviewed a changed file. Reimplementing three of the
        assembly's four conditions was the entire defect, which is why this
        now shares the predicate rather than restating it.

    absence of hunks last
        What remains with no hunks is a pure rename, a mode-only change, or an
        empty-file add or delete.
    """
    if file_diff.truncated:
        return PathDispositionV2.UNSUPPORTED
    if file_diff.is_submodule:
        return PathDispositionV2.METADATA_ONLY
    if file_diff.is_binary:
        return PathDispositionV2.UNSUPPORTED
    if path_violates_relative_path_contract_v2(file_diff.path):
        return PathDispositionV2.UNSUPPORTED
    if not file_diff.hunks:
        return PathDispositionV2.METADATA_ONLY
    return PathDispositionV2.REVIEWABLE


@dataclass(frozen=True)
class ScopeAssessmentV2:
    """The disposition of every changed path in one run.

    A **private** authority: it is not a published schema, and nothing here is
    emitted directly. `#200-F` §8 establishes that the published
    ``agent-review.review-readiness.v2`` contract has no channel able to carry
    ``scope_incomplete`` without misdescribing it, so this value informs the
    composer's decision and is recorded in evidence rather than serialised
    into an artifact whose vocabulary cannot express it.
    """

    changed_paths: tuple[str, ...]
    reviewable_paths: tuple[str, ...]
    metadata_only_paths: tuple[str, ...]
    unsupported_paths: tuple[str, ...]
    must_review_blocked_paths: tuple[str, ...]

    @property
    def scope_complete(self) -> bool:
        """True when no changed path carries material we could not represent.

        Metadata-only paths do not count against completeness -- they carry no
        material. Unsupported paths do.
        """
        return not self.unsupported_paths

    @property
    def blocked(self) -> bool:
        """True when a path the target declared must-review is unreviewable.

        Fail-closed and strictly stronger than ``not scope_complete``: an
        ordinary binary makes scope incomplete, but a binary the profile
        *required* to be reviewed blocks the run.
        """
        return bool(self.must_review_blocked_paths)

    @property
    def accounted_paths(self) -> tuple[str, ...]:
        """Every path that received a disposition.

        Equal to ``changed_paths`` for any value this module builds. Exposed
        so the invariant can be asserted from outside rather than trusted.
        """
        return tuple(
            sorted(
                {*self.reviewable_paths, *self.metadata_only_paths, *self.unsupported_paths}
            )
        )


def assess_changed_scope_v2(
    *,
    file_diffs: Sequence[ParsedFileDiffV2],
    profile: TargetProfileV2,
) -> ScopeAssessmentV2:
    """Assess every changed path against the target's must-review policy.

    Raises rather than silently coalescing when two file diffs claim the same
    canonical path: that would make one of them disappear, which is the exact
    class of silent loss this authority exists to prevent.
    """
    explicit_must_review = _resolve_must_review_paths(profile)
    must_review_patterns = tuple(profile.must_review.patterns)

    reviewable: list[str] = []
    metadata_only: list[str] = []
    unsupported: list[str] = []
    must_review_blocked: list[str] = []
    seen: set[str] = set()

    for file_diff in file_diffs:
        path = file_diff.path
        if path in seen:
            raise ScopeAssessmentError(SCOPE_ASSESSMENT_DUPLICATE_PATH_REASON_V2)
        seen.add(path)

        disposition = classify_changed_path_v2(file_diff)
        if disposition is PathDispositionV2.REVIEWABLE:
            reviewable.append(path)
            continue

        if disposition is PathDispositionV2.METADATA_ONLY:
            metadata_only.append(path)
        else:
            unsupported.append(path)

        # A must-review path that produced nothing reviewable is blocked
        # whether the cause was an absence of material or a gap in our
        # ability to represent it. The target asked for the path to be
        # examined; "there was nothing to look at" is a conclusion only a
        # human is entitled to draw.
        if _is_must_review_path(
            path, explicit_paths=explicit_must_review, patterns=must_review_patterns
        ):
            must_review_blocked.append(path)

    return ScopeAssessmentV2(
        changed_paths=tuple(sorted(seen)),
        reviewable_paths=tuple(sorted(reviewable)),
        metadata_only_paths=tuple(sorted(metadata_only)),
        unsupported_paths=tuple(sorted(unsupported)),
        must_review_blocked_paths=tuple(sorted(must_review_blocked)),
    )
