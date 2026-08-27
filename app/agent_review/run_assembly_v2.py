"""E2 -- assemble a real RunIdentityV2 + ManifestV2 from a real diff (issue
#129, child of tracker #109).

Closes the gap that, until now, ``ManifestV2`` was only ever constructed in
tests: this module is the first production adapter turning
``acquire_authoritative_diff_v2``'s output (#103) into a real, identity-bound
manifest, using a real ``TargetProfileV2`` (#85) and a real
``SemanticGroupingPolicyV2`` (#106) -- never a caller-supplied
``semantic_group`` string, never a branch on repository name (#86's own
ban).

Deliberately ends BEFORE any payload exists: assembles ``RunIdentityV2`` and
``ManifestV2``, nothing more. Payload content (F1/#131), ``PayloadSetV2``
emission (F2/#132), readiness (C1/C2), and any CLI are all out of scope.

## Two things this module does NOT compute itself

- **``evidence_hash``** is caller-supplied. Computing it for real requires a
  real ``EvidenceBundleV2`` (#128), which in turn requires reading real
  artifact/contract content from a target checkout and sanitizing it
  (``redaction.sanitize_artifact_value``) -- that is F1/#131's job, not this
  one's. This module only consumes the already-computed hash.
- **``max_lines_per_chunk``** is caller-supplied, not derived from
  ``TargetBudgetsV2.max_chars_per_chunk``. ``TargetBudgetsV2`` is
  char-denominated; ``planner_v2.plan_lossless_chunks_v2`` is
  line-denominated. Reconciling those units is a real, pre-existing gap in
  this codebase (present before this issue, not introduced by it) and
  inventing a chars-per-line conversion policy here would be a speculative,
  hard-to-reverse guess this issue never asked for. Only
  ``TargetBudgetsV2.max_chunks`` -- the one budget field this issue's
  acceptance criteria explicitly names -- is consumed, as the GLOBAL cap
  across every semantic group.

## The multi-group orchestrator

``plan_lossless_chunks_v2`` plans exactly one semantic group per call.
Hunks are first grouped by ``classify_semantic_group_v2`` (#106), then each
group is planned in a SEPARATE call, processed in sorted semantic-group-name
order (deterministic, reproducible regardless of input ordering) against
the REMAINING global chunk budget after previously-processed groups. A group
that cannot fit within its remaining share blocks the WHOLE assembly. This
is a conservative, deterministic, order-stable allocation, not a search for
a globally optimal cross-group packing -- a different processing order
might occasionally find a feasible split this one does not; global-optimal
cross-group bin-packing is out of scope for this foundational slice.

## Paths that never produce a fragment at all

``ManifestDegradationV2`` requires ``affected_fragment_ids`` -- it can only
describe a REAL fragment's omission, and has no way to represent a path that
never produced any fragment in the first place (binary, submodule, hunkless,
truncated, or simply absent from the diff despite being expected). Rather
than force an invented fragment_id or misuse an unrelated reason code, this
module keeps that decision OUTSIDE ``ManifestV2`` entirely:

- if such a path is must-review, the WHOLE assembly is refused
  (``ManifestAssemblyOutcomeV2(state="blocked_pipeline", ...)``) with this
  module's own ``AssemblyBlockedReasonV2`` -- never a fabricated
  ``ManifestDegradationV2``;
- otherwise it is silently excluded from ``expected_files`` (mirroring the
  planner's own precedent for dropped auxiliary fragments) and surfaced,
  for audit, in the outcome's own ``excluded_paths``.

A group whose REQUIRED fragments genuinely cannot be packed (budget
exhausted mid-assembly) still goes through the EXISTING, already-tested
``plan_lossless_chunks_v2`` ``blocked_pipeline``/``ManifestDegradationV2``
path -- that case always has real fragment_ids, so no new representation is
needed there.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal, Sequence

from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, TargetProfileV2, compute_run_id
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2, validate_diff_completeness_v2
from app.agent_review.manifest_v2 import (
    ManifestChunkV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2
from app.agent_review.semantic_grouping_policy_v2 import (
    SemanticGroupingError,
    SemanticGroupingPolicyV2,
    bind_semantic_grouping_policy_to_target_profile_v2,
    classify_semantic_group_v2,
    compute_effective_policy_hash_v2,
)

# `#200-D` predecessor: this authority CONSTRUCTS `RunIdentityV2`/`ManifestV2`
# and drives the planner, so contract violations and caller-supplied budget
# violations surfaced as raw pydantic/builtin errors. A consumer cannot name
# those; this owner can.
RUN_ASSEMBLY_CONTRACT_INVALID_REASON_V2 = "run_assembly_contract_invalid"
RUN_ASSEMBLY_BUDGET_INVALID_REASON_V2 = "run_assembly_budget_invalid"

RUN_ASSEMBLY_UNKNOWN_MUST_REVIEW_ARTIFACT_REASON_V2 = "run_assembly_unknown_must_review_artifact"
RUN_ASSEMBLY_REQUIRED_PATH_MISSING_REASON_V2 = "run_assembly_required_path_missing"
RUN_ASSEMBLY_REQUIRED_PATH_TRUNCATED_REASON_V2 = "run_assembly_required_path_truncated"
RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2 = "run_assembly_required_path_unrepresentable"
RUN_ASSEMBLY_GLOBAL_BUDGET_EXHAUSTED_REASON_V2 = "run_assembly_global_budget_exhausted"
RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2 = "run_assembly_group_packing_infeasible"

_MANIFEST_SCHEMA_ID_V2 = "agent-review.manifest.v2"
_MANIFEST_SOURCE_V2 = "aiops-review-plan-chunks-v2"


class RunAssemblyError(ValueError):
    """Raised for a run-assembly failure that is a configuration/input
    defect, not a legitimate blocked-pipeline outcome. Carries a stable
    ``reason_code`` only -- never manifest, diff, or profile content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class AssemblyBlockedReasonV2:
    """Plain, freely constructible data value describing why the WHOLE
    assembly was refused -- distinct from ``ManifestDegradationV2``, which
    requires real fragment_ids this module's blocking cases do not have
    (see the module docstring)."""

    reason_code: str
    affected_paths: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class ManifestAssemblyOutcomeV2:
    """Plain, freely constructible data value -- like ``PlanningOutcomeV2``
    and ``SynthesisResultV2``, not a wire contract with its own schema/hash."""

    state: Literal["assembled", "blocked_pipeline"]
    manifest: ManifestV2 | None
    excluded_paths: tuple[str, ...]
    blocked_reason: AssemblyBlockedReasonV2 | None


def _resolve_must_review_paths(profile: TargetProfileV2) -> frozenset[str]:
    """``TargetMustReviewV2.artifact_ids`` references configured artifacts
    by id; resolving it to a path means looking up each id's
    ``TargetArtifactV2.path`` -- no new semantics invented, just following
    an existing field reference.

    ``TargetProfileV2.validate_profile_references`` (``contracts_v2.py``)
    already guarantees ``must_review.artifact_ids <= {artifact.artifact_id
    for artifact in artifacts}`` at construction time, so the ``None``
    branch below is unreachable through any validly-constructed
    ``TargetProfileV2`` today. Kept as defense in depth, not because a
    dangling reference is believed reachable through this module's own
    inputs -- mirrors ``readiness_decision_v2.py``'s identical
    defense-in-depth precedent for a dangling ``fragment_id``."""

    explicit_paths = set(profile.must_review.paths)
    artifacts_by_id = {artifact.artifact_id: artifact for artifact in profile.artifacts}
    for artifact_id in profile.must_review.artifact_ids:
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            raise RunAssemblyError(RUN_ASSEMBLY_UNKNOWN_MUST_REVIEW_ARTIFACT_REASON_V2)
        explicit_paths.add(artifact.path)
    return frozenset(explicit_paths)


def _is_must_review_path(path: str, *, explicit_paths: frozenset[str], patterns: Sequence[str]) -> bool:
    return path in explicit_paths or any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def assemble_manifest_from_diff_v2(
    file_diffs: Sequence[ParsedFileDiffV2],
    *,
    profile: TargetProfileV2,
    grouping_policy: SemanticGroupingPolicyV2,
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    tested_merge_sha: str,
    toolrepo_sha: str,
    evidence_hash: str,
    max_lines_per_chunk: int,
    expected_paths: frozenset[str] | None = None,
) -> ManifestAssemblyOutcomeV2:
    """Public boundary: every EXPECTED assembly failure is a
    ``RunAssemblyError`` carrying a stable reason code.

    `#200-D` predecessor (model B). This authority builds `RunIdentityV2` and
    `ManifestV2` and drives the planner, so before closure a caller had to
    catch pydantic's ``ValidationError`` and a bare ``ValueError`` to find out
    that assembly refused -- knowledge of HOW this module is implemented, not
    THAT it refused.

    A caller-supplied budget that cannot describe a chunk is validated here,
    where it is this authority's own parameter. The planner's OTHER
    ``ValueError``s (duplicate fragment_id, a hunk with no real lines) report
    inconsistent acquisition output, not operator input, so they are
    deliberately NOT converted: they are defects and must stay raw.
    """

    # Only `max_lines_per_chunk` needs this: it is a bare `int` parameter of
    # this function. `profile.budgets.max_chunks` is a `PositiveInt` on the
    # profile contract, so an equivalent guard for it would be unreachable --
    # and unreachable code that looks like a guard is worse than none.
    if not isinstance(max_lines_per_chunk, int) or max_lines_per_chunk < 1:
        raise RunAssemblyError(RUN_ASSEMBLY_BUDGET_INVALID_REASON_V2)

    try:
        return _assemble_manifest_from_diff_v2(
            file_diffs,
            profile=profile,
            grouping_policy=grouping_policy,
            repo=repo,
            pr_number=pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            tested_merge_sha=tested_merge_sha,
            toolrepo_sha=toolrepo_sha,
            evidence_hash=evidence_hash,
            max_lines_per_chunk=max_lines_per_chunk,
            expected_paths=expected_paths,
        )
    except RunAssemblyError:
        raise
    except ValidationError as exc:
        # identity/manifest/fragment contract violation built from caller
        # material -- never the pydantic message, which carries content.
        raise RunAssemblyError(RUN_ASSEMBLY_CONTRACT_INVALID_REASON_V2) from exc


def _assemble_manifest_from_diff_v2(
    file_diffs: Sequence[ParsedFileDiffV2],
    *,
    profile: TargetProfileV2,
    grouping_policy: SemanticGroupingPolicyV2,
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    tested_merge_sha: str,
    toolrepo_sha: str,
    evidence_hash: str,
    max_lines_per_chunk: int,
    expected_paths: frozenset[str] | None = None,
) -> ManifestAssemblyOutcomeV2:
    """Assemble a real ``RunIdentityV2`` + ``ManifestV2`` from an already-
    acquired diff (``acquire_authoritative_diff_v2``'s own output, #103 --
    this module never calls it itself, keeping git-subprocess acquisition
    and manifest assembly independently testable).

    ``expected_paths``, when supplied, is an independent source of "files
    this run is expected to touch" (e.g. a GitHub changed-files API list)
    to cross-check against the diff's own paths -- this is what makes
    ``validate_diff_completeness_v2``'s ``missing_paths`` meaningful.
    Without it (the default), expected paths are derived from the diff
    itself, so ``missing_paths`` is trivially always empty -- there is
    nothing external to compare against.
    """

    try:
        bind_semantic_grouping_policy_to_target_profile_v2(grouping_policy, profile)
    except SemanticGroupingError as exc:
        raise RunAssemblyError(exc.reason_code) from exc

    must_review_explicit = _resolve_must_review_paths(profile)
    must_review_patterns = tuple(profile.must_review.patterns)

    diff_paths = frozenset(file_diff.path for file_diff in file_diffs if file_diff.path)
    resolved_expected_paths = expected_paths if expected_paths is not None else diff_paths

    completeness = validate_diff_completeness_v2(tuple(file_diffs), expected_paths=resolved_expected_paths)
    problem_paths = (
        frozenset(completeness.missing_paths)
        | frozenset(completeness.unrepresentable_paths)
        | frozenset(completeness.truncated_paths)
    )

    required_problem_paths = sorted(
        path
        for path in problem_paths
        if _is_must_review_path(path, explicit_paths=must_review_explicit, patterns=must_review_patterns)
    )
    if required_problem_paths:
        first = required_problem_paths[0]
        if first in completeness.missing_paths:
            reason_code = RUN_ASSEMBLY_REQUIRED_PATH_MISSING_REASON_V2
            detail = "a must-review path is expected but absent from the acquired diff"
        elif first in completeness.truncated_paths:
            reason_code = RUN_ASSEMBLY_REQUIRED_PATH_TRUNCATED_REASON_V2
            detail = "a must-review path's patch content is incomplete (truncated)"
        else:
            reason_code = RUN_ASSEMBLY_REQUIRED_PATH_UNREPRESENTABLE_REASON_V2
            detail = "a must-review path is binary, a submodule, hunkless, or violates the path contract"
        return ManifestAssemblyOutcomeV2(
            state="blocked_pipeline",
            manifest=None,
            excluded_paths=(),
            blocked_reason=AssemblyBlockedReasonV2(
                reason_code=reason_code, affected_paths=tuple(required_problem_paths), detail=detail
            ),
        )

    excluded_paths = problem_paths  # none of these are must-review, verified above

    hunks_by_group: dict[str, list[HunkInputV2]] = {}
    for file_diff in file_diffs:
        path = file_diff.path
        if not path or path in excluded_paths:
            continue
        try:
            semantic_group = classify_semantic_group_v2(grouping_policy, path=path)
        except SemanticGroupingError as exc:
            raise RunAssemblyError(exc.reason_code) from exc
        is_required = _is_must_review_path(path, explicit_paths=must_review_explicit, patterns=must_review_patterns)
        for hunk_index, hunk in enumerate(file_diff.hunks):
            hunks_by_group.setdefault(semantic_group.value, []).append(
                HunkInputV2(
                    path=path,
                    hunk_index=hunk_index,
                    old_start=hunk.old_start,
                    old_end=hunk.old_start + hunk.old_lines - 1,
                    new_start=hunk.new_start,
                    new_end=hunk.new_start + hunk.new_lines - 1,
                    diff_sha256=hunk.diff_sha256,
                    diff_chars=hunk.diff_chars,
                    must_review=is_required,
                )
            )

    remaining_budget = profile.budgets.max_chunks
    all_fragments = []
    all_chunks: list[ManifestChunkV2] = []
    order_index = 0

    for group in sorted(hunks_by_group):
        group_hunks = hunks_by_group[group]
        required_hunks = [hunk for hunk in group_hunks if hunk.must_review]

        if remaining_budget < 1:
            return ManifestAssemblyOutcomeV2(
                state="blocked_pipeline",
                manifest=None,
                excluded_paths=(),
                blocked_reason=AssemblyBlockedReasonV2(
                    reason_code=RUN_ASSEMBLY_GLOBAL_BUDGET_EXHAUSTED_REASON_V2,
                    affected_paths=tuple(sorted({hunk.path for hunk in group_hunks})),
                    detail=(
                        f"global max_chunks={profile.budgets.max_chunks} exhausted before "
                        f"semantic group {group!r} could be planned"
                    ),
                ),
            )

        if not required_hunks:
            # No coverage obligation in this group at all: behaves exactly
            # as before this fix (full access to the remaining budget,
            # first-come-first-served in sorted-group order). This is the
            # ALREADY-documented "not globally optimal" cross-group
            # allocation trade-off -- a purely auxiliary group could, in
            # principle, still leave less budget for a later group's
            # required content, same as any other group ordering choice.
            # What this fix closes is the MORE surprising case just below:
            # a group's OWN auxiliary content outgrowing its OWN required
            # content's real need.
            outcome = plan_lossless_chunks_v2(
                group_hunks,
                semantic_group=group,
                max_lines_per_chunk=max_lines_per_chunk,
                max_chunks=remaining_budget,
                chunk_id_prefix=f"chunk-{group}",
            )
            if outcome.state == "blocked_pipeline":
                return ManifestAssemblyOutcomeV2(
                    state="blocked_pipeline",
                    manifest=None,
                    excluded_paths=(),
                    blocked_reason=AssemblyBlockedReasonV2(
                        reason_code=RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2,
                        affected_paths=tuple(sorted({hunk.path for hunk in group_hunks})),
                        detail=(
                            f"semantic group {group!r} could not be packed within its remaining "
                            f"chunk budget of {remaining_budget}"
                        ),
                    ),
                )
        else:
            # Required-only pass: determine the true minimum bin count this
            # group's must-review content needs, ignoring auxiliary
            # entirely.
            required_outcome = plan_lossless_chunks_v2(
                required_hunks,
                semantic_group=group,
                max_lines_per_chunk=max_lines_per_chunk,
                max_chunks=remaining_budget,
                chunk_id_prefix=f"chunk-{group}",
            )
            if required_outcome.state == "blocked_pipeline":
                return ManifestAssemblyOutcomeV2(
                    state="blocked_pipeline",
                    manifest=None,
                    excluded_paths=(),
                    blocked_reason=AssemblyBlockedReasonV2(
                        reason_code=RUN_ASSEMBLY_GROUP_PACKING_INFEASIBLE_REASON_V2,
                        affected_paths=tuple(sorted({hunk.path for hunk in group_hunks})),
                        detail=(
                            f"semantic group {group!r} could not be packed within its remaining "
                            f"chunk budget of {remaining_budget}"
                        ),
                    ),
                )
            required_bins = len(required_outcome.chunks)

            # Full pass: required + auxiliary hunks together, but capped to
            # EXACTLY required_bins -- auxiliary can only use leftover room
            # inside bins required content already claimed, never an extra
            # bin (plan_lossless_chunks_v2 only grows bin count for
            # auxiliary while `len(bins) < max_chunks`, which is now
            # already false). Found by independent review: without this
            # cap, a group's own optional auxiliary content could consume
            # bins far beyond what its own required content needed,
            # starving a LATER group's required content of shared global
            # budget -- an avoidable blocked_pipeline. Guaranteed to still
            # succeed: the required subset and max_chunks are identical to
            # the required-only pass above, so the same deterministic exact
            # packer proves the same feasibility again.
            outcome = plan_lossless_chunks_v2(
                group_hunks,
                semantic_group=group,
                max_lines_per_chunk=max_lines_per_chunk,
                max_chunks=required_bins,
                chunk_id_prefix=f"chunk-{group}",
            )
            if outcome.state == "blocked_pipeline":
                raise RunAssemblyError("run_assembly_internal_required_repack_inconsistency")

        all_fragments.extend(outcome.fragments)
        for chunk in outcome.chunks:
            all_chunks.append(
                ManifestChunkV2(
                    chunk_id=chunk.chunk_id,
                    order_index=order_index,
                    semantic_group=chunk.semantic_group,
                    fragment_ids=chunk.fragment_ids,
                    payload_sha256=None,
                )
            )
            order_index += 1
        remaining_budget -= len(outcome.chunks)

    expected_files = sorted(diff_paths - excluded_paths)
    must_review_files = sorted(
        path
        for path in expected_files
        if _is_must_review_path(path, explicit_paths=must_review_explicit, patterns=must_review_patterns)
    )

    material_kwargs = {
        "schema_id": _MANIFEST_SCHEMA_ID_V2,
        "schema_version": 2,
        "source": _MANIFEST_SOURCE_V2,
        "expected_files": expected_files,
        "must_review_files": must_review_files,
        "fragments": all_fragments,
        "chunks": all_chunks,
        "max_chunks": profile.budgets.max_chunks,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    policy_hash = compute_effective_policy_hash_v2(profile.policies, grouping_policy)
    profile_hash = compute_profile_hash_v2(profile)

    identity = RunIdentityV2(
        repo=repo,
        pr_number=pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        tested_merge_sha=tested_merge_sha,
        toolrepo_sha=toolrepo_sha,
        profile_hash=profile_hash,
        policy_hash=policy_hash,
        manifest_hash=manifest_hash,
        evidence_hash=evidence_hash,
    )
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)
    return ManifestAssemblyOutcomeV2(
        state="assembled",
        manifest=manifest,
        excluded_paths=tuple(sorted(excluded_paths)),
        blocked_reason=None,
    )
