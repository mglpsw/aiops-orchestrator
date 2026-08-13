"""C1 -- readiness decision library (issue #127, child of tracker #108).

Computes the readiness decision (state, reason codes, blockers, bridged
coverage, pipeline assessment) as a PURE FUNCTION from ``SynthesisResultV2``
(#107, already merged) + ``ManifestV2`` (#84) + ``TargetPoliciesV2`` (#85).
Deliberately does not emit a ``ReviewReadinessV2`` artifact, does not touch
``pr_state`` or GitHub ``checks``, and does not own a CLI -- all three are
issue #130/C2's job, layered on top of this decision once it acquires
``pr_state``/``checks`` from the forge and this run's real identity.

``ReviewReadinessV2`` (``contracts_v2.py``) is an already-published, frozen
contract with its own dense ``validate_state_invariants``. This module never
constructs one; it only produces values (``state``, ``reason_codes``,
``blockers``, ``coverage``, ``pipeline``) that C2 can drop straight into that
constructor once identity/``pr_state``/``checks`` are added, without needing
to re-derive any of this module's logic. If bridging fragment-granular
coverage into ``ChunkCoverageV2``'s frozen file-granular shape ever required
altering ``ReviewReadinessV2`` itself, that would be a breaking-contract
change, not an implementation task -- this module fails closed with its own
reason code instead (see ``COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2``
below), rather than silently misrepresenting a case the frozen contract
cannot express.

## Two steps, kept distinct

1. ``bridge_fragment_coverage_to_chunk_coverage_v2`` -- bridges the
   fragment-granular ``RunFragmentCoverageReportV2`` (#104/#115) into the
   file-granular ``ChunkCoverageV2`` (frozen in #81) that
   ``ReviewReadinessV2.coverage`` requires. Revalidates the coverage report
   against the manifest first (``bind_coverage_report_to_manifest_v2``,
   #115) -- a ``SynthesisResultV2`` is, like ``ParsedChunkResultV2``, "a
   plain data value, freely constructible" with no seal proving its
   ``coverage_report`` actually matches this exact ``manifest``, so this
   module treats it as untrusted input and revalidates it, the same
   discipline ``synthesis_v2``/``lifecycle_v2`` already apply to
   ``ParsedChunkResultV2``.
2. ``compute_readiness_decision_v2`` -- the actual precedence chain over
   confirmed/new blocking findings, bridged coverage, and
   ``synthesis.limitations``-derived model uncertainty, deciding one of the
   5 ``ReadinessStateV2`` values plus the reason codes/blockers/pipeline
   assessment that must accompany it, per ``ReviewReadinessV2``'s own
   invariants (verified against that validator's logic, never against a
   copy of it).

## Deterministic precedence (total -- every input combination reaches
exactly one outcome, documented once here, not re-derived at each call site)

```text
1. caller-supplied stale_reason_codes non-empty  -> STALE
2. >=1 CONFIRMED blocking finding                -> BLOCKED_CODE
3. else synthesis.limitations non-empty          -> MANUAL_REQUIRED
   (model_uncertainty can never appear in BLOCKED_PIPELINE's allowed
   reason set, so its presence always forces MANUAL_REQUIRED, regardless
   of policies.coverage_failure_state)
4. else coverage needs attention                 -> policies.coverage_failure_state
   (exactly, per the issue's own acceptance criterion -- "path fragmentado
   produz exatamente policies.coverage_failure_state")
5. else >=1 NEW blocking finding pending          -> MANUAL_REQUIRED
6. else                                          -> READY
```

Known, documented scope limitation: when step 4 resolves to
``BLOCKED_PIPELINE`` (the target's policy choice) and there is ALSO a NEW
blocking finding pending confirmation, that finding is not independently
representable in this decision's ``reason_codes`` -- ``FINDING_CONFIRMATION_REQUIRED``
is not in ``BLOCKED_PIPELINE``'s allowed reason set (only
``policies.coverage_failure_state == "manual_required"`` can carry both).
This is a real, accepted gap, not silently papered over: the finding still
exists in ``synthesis.findings`` and surfaces once the pipeline degradation
clears or the target's policy is ``manual_required``.

``synthesis.limitations`` is free-form ``SafeIdentifier`` text (grep across
this repo's own fixtures shows values from ``"model_uncertainty"`` to
``"chunk_budget_exceeded:api_schema_contract"`` to
``"optional_artifact_missing:checks"`` -- no closed enum). Inventing a
bespoke per-string mapping to ``ReadinessReasonV2`` would be fragile and
permanently incomplete. Any non-empty ``limitations`` therefore folds into a
single ``MODEL_UNCERTAINTY`` pipeline cause -- the correct semantic bucket
for "the review process itself self-reported it could not fully complete or
trust its work", distinct from ``COVERAGE_FAILURE`` (handled separately by
the coverage bridge). Every raw limitation identifier survives verbatim,
sorted, in that cause's own ``detail`` field -- no information is dropped.

Deliberately out of scope, per the issue: emitting ``ReviewReadinessV2``;
acquiring or interpreting ``pr_state``; acquiring or interpreting GitHub
``checks``; any CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    CoverageDegradationReasonV2,
    CoverageDegradationV2,
    CoverageStateV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    PipelineAssessmentV2,
    PipelineDegradationCauseV2,
    ReadinessBlockerV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    TargetPoliciesV2,
)
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.required_check_readiness_v2 import (
    RequiredCheckReadinessAssessmentV2,
    RequiredCheckStatusV2,
)
from app.agent_review.run_fragment_coverage_v2 import (
    FragmentCoverageBindingError,
    FragmentCoverageStatusV2,
    RunFragmentCoverageReportV2,
    bind_coverage_report_to_manifest_v2,
)
from app.agent_review.synthesis_v2 import SynthesisResultV2

READINESS_SYNTHESIS_MANIFEST_RUN_ID_MISMATCH_REASON_V2 = "readiness_synthesis_manifest_run_id_mismatch"
READINESS_INVALID_STALE_REASON_CODES_REASON_V2 = "readiness_invalid_stale_reason_codes"
COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2 = "readiness_coverage_bridge_mixed_degradation_and_plain_partial"
COVERAGE_BRIDGE_UNKNOWN_DEGRADATION_REASON_V2 = "readiness_coverage_bridge_unknown_manifest_degradation_reason"
DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2 = (
    "readiness_decision_unrepresentable_with_required_check_assessment"
)

_ALLOWED_STALE_REASON_CODES_V2 = frozenset({ReadinessReasonV2.HEAD_MISMATCH, ReadinessReasonV2.IDENTITY_MISMATCH})
# MANUAL_REQUIRED's own allowed reason set, verbatim (contracts_v2.py's
# `validate_state_invariants`, MANUAL_REQUIRED branch) -- what an existing
# decision's reason_codes must already be a subset of for the #201-C
# downgrade to that state to be representable at all. `POLICY_FAILURE` is
# always safe (it is the one #201-C itself is about to add).
_MANUAL_REQUIRED_SAFE_EXISTING_REASONS_V2 = frozenset(
    {
        ReadinessReasonV2.COVERAGE_FAILURE,
        ReadinessReasonV2.MODEL_UNCERTAINTY,
        ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED,
        ReadinessReasonV2.POLICY_FAILURE,
    }
)
# BLOCKED_CODE's own allowed PIPELINE reason set, verbatim (contracts_v2.py,
# BLOCKED_CODE branch: `allowed_pipeline_reasons`) -- `CONFIRMED_CODE_FINDING`
# itself is checked separately by the contract (it is mandatory, not part of
# this "pipeline reasons" bucket) and is never added or removed here, so it
# is included for completeness but never actually exercised as the subject
# of this guard.
_BLOCKED_CODE_SAFE_EXISTING_REASONS_V2 = frozenset(
    {
        ReadinessReasonV2.CONFIRMED_CODE_FINDING,
        ReadinessReasonV2.SCHEMA_FAILURE,
        ReadinessReasonV2.TRANSPORT_FAILURE,
        ReadinessReasonV2.COVERAGE_FAILURE,
        ReadinessReasonV2.MODEL_UNCERTAINTY,
        ReadinessReasonV2.POLICY_FAILURE,
    }
)

_BLOCKING_DISPOSITIONS_V2 = frozenset({FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED})
_BLOCKING_SEVERITIES_V2 = frozenset({FindingSeverityV2.P0, FindingSeverityV2.P1, FindingSeverityV2.P2})

# Manifest-level degradation (7 reasons, manifest_v2.DegradationReasonValueV2)
# folded into the coarser ChunkCoverageV2/CoverageDegradationReasonV2 space
# (5 reasons). packing_search_exhausted and planner_limit_exceeded are both
# budget-shaped ("could not fit within the packer's constraints"), so both
# fold into BUDGET_EXHAUSTED -- documented here, not silently coerced.
_MANIFEST_TO_COVERAGE_DEGRADATION_REASON_V2: Mapping[str, CoverageDegradationReasonV2] = {
    "artifact_missing": CoverageDegradationReasonV2.ARTIFACT_MISSING,
    "budget_exhausted": CoverageDegradationReasonV2.BUDGET_EXHAUSTED,
    "transport_failure": CoverageDegradationReasonV2.TRANSPORT_FAILURE,
    "schema_failure": CoverageDegradationReasonV2.SCHEMA_FAILURE,
    "model_uncertainty": CoverageDegradationReasonV2.MODEL_UNCERTAINTY,
    "packing_search_exhausted": CoverageDegradationReasonV2.BUDGET_EXHAUSTED,
    "planner_limit_exceeded": CoverageDegradationReasonV2.BUDGET_EXHAUSTED,
}


class ReadinessDecisionError(ValueError):
    """Raised for a readiness-decision failure. Carries a stable
    ``reason_code`` only -- never manifest, synthesis, or policy content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ReadinessDecisionV2:
    """Plain, freely constructible data value -- like ``SynthesisResultV2``,
    not a wire contract with its own schema/hash. Meant for C2 to fold
    directly into a ``ReviewReadinessV2`` artifact alongside identity,
    ``pr_state``, and ``checks``.

    ``run_id``/``manifest_hash`` are this decision's own run provenance --
    copied directly from the ``ManifestV2`` this decision was actually
    computed against, per a Codex review of #145 that found the decision
    file carried no run identity at all: a `ready` decision computed for
    run A could otherwise be combined with an unrelated run B's identity/
    findings/checks at emission time, with no invariant able to detect the
    replay. A caller (C2's own CLI) MUST verify these against the identity
    it is about to emit against before trusting the decision -- this
    dataclass only carries the provenance, it does not enforce the check
    itself (that is `review_readiness_emission_v2._assemble_review_
    readiness_v2`'s job, since only it has both the decision and the
    identity in hand)."""

    state: ReadinessStateV2
    reason_codes: tuple[ReadinessReasonV2, ...]
    blockers: tuple[ReadinessBlockerV2, ...]
    coverage: ChunkCoverageV2
    pipeline: PipelineAssessmentV2
    run_id: str
    manifest_hash: str


def bridge_fragment_coverage_to_chunk_coverage_v2(
    *, coverage_report: RunFragmentCoverageReportV2, manifest: ManifestV2
) -> ChunkCoverageV2:
    """Bridge fragment-granular coverage (#104/#115) into the file-granular
    ``ChunkCoverageV2`` (frozen in #81) ``ReviewReadinessV2.coverage``
    requires.

    A path is classified against a REAL ``ManifestDegradationV2`` cause
    (never merely because it is structurally split across chunks): if any
    non-complete (partial or missing) path is covered by a genuine
    manifest-level degradation cause, ``DEGRADED`` status takes precedence
    over the weaker "just partial" classification for that path -- this is
    the deterministic precedence the #116 combined fixture (one path,
    structurally split AND carrying a genuinely degraded fragment) proves:
    ``structural_split`` and ``fragment_degraded`` coexist on that single
    fragment-level entry, and the file-granular bridge correctly resolves
    the path as ``DEGRADED``, backed by the real ``ManifestDegradationV2``
    cause, not merely ``PARTIAL``.

    ``ChunkCoverageV2.status`` is a single value governing every affected
    file in the whole run. If some non-complete paths ARE covered by a real
    degradation cause while others are NOT (a run mixing a purely
    structural split with an unrelated genuine degradation on a different
    path), no single ``ChunkCoverageV2.status`` value can represent both
    faithfully (``PARTIAL`` forbids any ``degradation_causes``; ``DEGRADED``
    requires every affected file to be covered by one) -- this is a real
    limitation of the already-frozen v1 contract (#81), not something this
    bridge can paper over. Fails closed with
    ``COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2`` rather than silently
    misrepresenting either case.
    """

    bind_coverage_report_to_manifest_v2(coverage_report, manifest)

    status_by_path = {entry.path: entry.status for entry in coverage_report.paths}
    expected_files = list(manifest.expected_files)

    reviewed_files = [p for p in expected_files if status_by_path.get(p) is FragmentCoverageStatusV2.REVIEWED]
    partially_reviewed_files = [
        p for p in expected_files if status_by_path.get(p) is FragmentCoverageStatusV2.PARTIAL
    ]
    missing_files = [p for p in expected_files if status_by_path.get(p) is FragmentCoverageStatusV2.MISSING]

    must_review_files = list(manifest.must_review_files)
    non_complete = set(partially_reviewed_files) | set(missing_files)
    missing_must_review_files = [p for p in must_review_files if p in non_complete]

    fragment_path_by_id = {fragment.fragment_id: fragment.path for fragment in manifest.fragments}
    # The `.get(...) -> None -> skip` pattern here and below is defensive
    # but, verified by independent review, provably unreachable today:
    # ManifestMaterialV2.validate_material (manifest_v2.py, already frozen)
    # rejects any manifest whose degradation_causes reference a
    # fragment_id outside manifest.fragments before a ManifestV2 can even
    # be constructed. Kept as defense in depth, not because a dangling
    # reference is believed reachable through this module's own inputs.
    caused_paths: set[str] = set()
    for cause in manifest.degradation_causes:
        for fragment_id in cause.affected_fragment_ids:
            path = fragment_path_by_id.get(fragment_id)
            if path is not None:
                caused_paths.add(path)

    if not caused_paths:
        status = CoverageStateV2.PARTIAL if non_complete else CoverageStateV2.COMPLETE
        degradation_causes: list[CoverageDegradationV2] = []
    else:
        if caused_paths != non_complete:
            raise ReadinessDecisionError(COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2)
        status = CoverageStateV2.DEGRADED
        files_by_reason: dict[CoverageDegradationReasonV2, set[str]] = {}
        details_by_reason: dict[CoverageDegradationReasonV2, set[str]] = {}
        for cause in manifest.degradation_causes:
            mapped_reason = _MANIFEST_TO_COVERAGE_DEGRADATION_REASON_V2.get(cause.reason_code)
            if mapped_reason is None:
                # Fail closed with a named reason code rather than a bare
                # KeyError: _MANIFEST_TO_COVERAGE_DEGRADATION_REASON_V2 is
                # exhaustive over manifest_v2.DegradationReasonValueV2's
                # current 7 literal values today, but this module must not
                # silently crash uninformatively if that literal is ever
                # extended without updating the mapping here.
                raise ReadinessDecisionError(COVERAGE_BRIDGE_UNKNOWN_DEGRADATION_REASON_V2)
            cause_paths = {
                fragment_path_by_id[fragment_id]
                for fragment_id in cause.affected_fragment_ids
                if fragment_id in fragment_path_by_id
            }
            if not cause_paths:
                continue
            files_by_reason.setdefault(mapped_reason, set()).update(cause_paths)
            details_by_reason.setdefault(mapped_reason, set()).add(cause.detail)
        degradation_causes = [
            CoverageDegradationV2(
                reason_code=reason,
                affected_files=tuple(sorted(files)),
                detail="; ".join(sorted(details_by_reason[reason])),
            )
            for reason, files in sorted(files_by_reason.items(), key=lambda item: item[0].value)
        ]

    return ChunkCoverageV2(
        status=status,
        expected_files=tuple(expected_files),
        reviewed_files=tuple(reviewed_files),
        partially_reviewed_files=tuple(partially_reviewed_files),
        missing_files=tuple(missing_files),
        must_review_files=tuple(must_review_files),
        missing_must_review_files=tuple(missing_must_review_files),
        degradation_causes=tuple(degradation_causes),
    )


def compute_readiness_decision_v2(
    *,
    synthesis: SynthesisResultV2,
    manifest: ManifestV2,
    policies: TargetPoliciesV2,
    stale_reason_codes: frozenset[ReadinessReasonV2] = frozenset(),
) -> ReadinessDecisionV2:
    """The readiness decision: state, reason codes, blockers, bridged
    coverage, and pipeline assessment -- see the module docstring for the
    full deterministic precedence.

    ``stale_reason_codes`` is the one input this function cannot derive
    itself: detecting that ``manifest``'s evaluated identity/HEAD no longer
    matches the CURRENT identity/HEAD requires comparing against something
    outside a single synthesis result, which is C2's responsibility (it
    holds the live run/identity state this library never touches). When the
    caller has already made that determination, passing the resulting
    reason codes here short-circuits straight to ``STALE`` -- matching
    ``ReviewReadinessV2.validate_state_invariants``'s own stale branch,
    which ignores coverage/pipeline/findings entirely once stale.
    """

    if synthesis.run_id != manifest.run_id:
        raise ReadinessDecisionError(READINESS_SYNTHESIS_MANIFEST_RUN_ID_MISMATCH_REASON_V2)

    try:
        coverage = bridge_fragment_coverage_to_chunk_coverage_v2(
            coverage_report=synthesis.coverage_report, manifest=manifest
        )
    except FragmentCoverageBindingError as exc:
        raise ReadinessDecisionError(exc.reason_code) from exc

    if stale_reason_codes:
        if not stale_reason_codes <= _ALLOWED_STALE_REASON_CODES_V2:
            raise ReadinessDecisionError(READINESS_INVALID_STALE_REASON_CODES_REASON_V2)
        return ReadinessDecisionV2(
            state=ReadinessStateV2.STALE,
            reason_codes=tuple(sorted(stale_reason_codes, key=lambda code: code.value)),
            blockers=(),
            coverage=coverage,
            pipeline=PipelineAssessmentV2(degraded=False, causes=[]),
            run_id=manifest.run_id,
            manifest_hash=manifest.identity.manifest_hash,
        )

    blocking_findings = [
        finding
        for finding in synthesis.findings
        if finding.actionable
        and finding.disposition in _BLOCKING_DISPOSITIONS_V2
        and finding.severity in _BLOCKING_SEVERITIES_V2
    ]
    confirmed_findings = sorted(
        (f for f in blocking_findings if f.disposition is FindingDispositionV2.CONFIRMED),
        key=lambda f: f.finding_id,
    )
    new_findings = sorted(
        (f for f in blocking_findings if f.disposition is FindingDispositionV2.NEW),
        key=lambda f: f.finding_id,
    )

    coverage_needs_attention = (
        coverage.status is not CoverageStateV2.COMPLETE or bool(coverage.missing_must_review_files)
    )
    model_uncertainty_present = bool(synthesis.limitations)

    reasons: set[ReadinessReasonV2] = set()
    blockers: list[ReadinessBlockerV2] = []
    causes: list[PipelineDegradationCauseV2] = []

    if coverage_needs_attention:
        reasons.add(ReadinessReasonV2.COVERAGE_FAILURE)
        blockers.append(
            ReadinessBlockerV2(
                blocker_id="coverage-failure",
                reason_code=ReadinessReasonV2.COVERAGE_FAILURE,
                active=True,
                finding_id=None,
            )
        )
        causes.append(
            PipelineDegradationCauseV2(
                reason_code=ReadinessReasonV2.COVERAGE_FAILURE,
                component="fragment_coverage",
                detail="one or more expected files are not completely reviewed",
            )
        )
    if model_uncertainty_present:
        reasons.add(ReadinessReasonV2.MODEL_UNCERTAINTY)
        blockers.append(
            ReadinessBlockerV2(
                blocker_id="model-uncertainty",
                reason_code=ReadinessReasonV2.MODEL_UNCERTAINTY,
                active=True,
                finding_id=None,
            )
        )
        causes.append(
            PipelineDegradationCauseV2(
                reason_code=ReadinessReasonV2.MODEL_UNCERTAINTY,
                component="chunk_review",
                detail="; ".join(sorted(synthesis.limitations)),
            )
        )

    def _confirmation_blockers(findings: list[FindingLifecycleRecordV2]) -> list[ReadinessBlockerV2]:
        return [
            ReadinessBlockerV2(
                blocker_id=f"confirm-{finding.finding_id}",
                reason_code=ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED,
                active=True,
                finding_id=finding.finding_id,
            )
            for finding in findings
        ]

    if confirmed_findings:
        state = ReadinessStateV2.BLOCKED_CODE
        reasons.add(ReadinessReasonV2.CONFIRMED_CODE_FINDING)
        blockers.extend(
            ReadinessBlockerV2(
                blocker_id=f"confirmed-{finding.finding_id}",
                reason_code=ReadinessReasonV2.CONFIRMED_CODE_FINDING,
                active=True,
                finding_id=finding.finding_id,
            )
            for finding in confirmed_findings
        )
    elif model_uncertainty_present:
        state = ReadinessStateV2.MANUAL_REQUIRED
        if new_findings:
            reasons.add(ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED)
            blockers.extend(_confirmation_blockers(new_findings))
    elif coverage_needs_attention:
        state = ReadinessStateV2(policies.coverage_failure_state)
        if state is ReadinessStateV2.MANUAL_REQUIRED and new_findings:
            reasons.add(ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED)
            blockers.extend(_confirmation_blockers(new_findings))
    elif new_findings:
        state = ReadinessStateV2.MANUAL_REQUIRED
        reasons.add(ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED)
        blockers.extend(_confirmation_blockers(new_findings))
    else:
        state = ReadinessStateV2.READY

    pipeline = PipelineAssessmentV2(degraded=bool(causes), causes=causes)

    return ReadinessDecisionV2(
        state=state,
        reason_codes=tuple(sorted(reasons, key=lambda code: code.value)),
        blockers=tuple(blockers),
        coverage=coverage,
        pipeline=pipeline,
        run_id=manifest.run_id,
        manifest_hash=manifest.identity.manifest_hash,
    )


REQUIRED_CHECKS_PIPELINE_COMPONENT_V2 = "required_checks"
_REQUIRED_CHECKS_BLOCKER_ID_V2 = "required-checks"
# `PipelineDegradationCauseV2.detail` is `SafeText` (contracts_v2.py):
# `max_length=512`. Each segment gets a fixed budget well under that bound
# so the two segments plus fixed prose can never together exceed it.
_DETAIL_SEGMENT_BUDGET_V2 = 200


def _joined_with_budget_v2(names: tuple[str, ...], *, budget: int) -> str:
    """Join `names` up to `budget` characters, appending a deterministic
    `(+N more)` suffix if the full list does not fit. Never returns a
    string longer than `budget`.

    `TargetPoliciesV2.required_checks` (contracts_v2.py) has no maximum
    count, and each `SafeText` name may be up to 512 characters on its
    own -- an adversarial review of this PR found that joining an
    unbounded number/length of names here could exceed `PipelineDegradation
    CauseV2.detail`'s own `SafeText` bound, raising `pydantic.
    ValidationError` from a pure function with no `try`/`except`, crashing
    `produce_review_readiness_v2` instead of producing exactly the
    `manual_required` artifact this module exists to make representable.
    Truncating deterministically -- rather than trusting the joined string
    to happen to fit -- is the fix, not a defensive afterthought: the full
    name lists remain on `RequiredCheckReadinessAssessmentV2` itself, so
    nothing this string omits is lost to a caller that needs it exactly.
    """

    joined = ", ".join(names)
    if len(joined) <= budget:
        return joined
    # Adversarial review finding, confirmed and fixed: the previous version
    # packed `kept` against `budget` with no headroom reserved for the
    # suffix appended afterward, so the trailing hard slice `result[:budget]`
    # was not a rarely-hit backstop -- it routinely fired and could chop the
    # suffix mid-word (e.g. "... (+22 mo" instead of "... (+22 more)"), or
    # even chop a kept name. Reproduced: two 198-char names at budget=200
    # produced a string ending in a bare " (" with no digit or closing
    # paren at all. Fixed by shrinking the candidate until it -- WITH its
    # own suffix already attached -- fits, so no post-hoc slice is ever
    # needed and the result is provably well-formed.
    for keep_count in range(len(names), 0, -1):
        candidate = ", ".join(names[:keep_count])
        suffix = f" (+{len(names) - keep_count} more)"
        if len(candidate) + len(suffix) <= budget:
            return candidate + suffix
    # budget too small even for "(+N more)" alone with zero names kept --
    # still never exceed it.
    return f"(+{len(names)} more)"[:budget]


def _required_check_detail_v2(assessment: RequiredCheckReadinessAssessmentV2) -> str:
    parts: list[str] = []
    if assessment.missing_check_names:
        parts.append(
            "authority not established for: "
            + _joined_with_budget_v2(assessment.missing_check_names, budget=_DETAIL_SEGMENT_BUDGET_V2)
        )
    if assessment.failed_check_names:
        parts.append(
            "failed: " + _joined_with_budget_v2(assessment.failed_check_names, budget=_DETAIL_SEGMENT_BUDGET_V2)
        )
    detail = "; ".join(parts)
    # Defensive final cap, verified never actually exercised by the two
    # segment budgets above (fixed prose + separators stay well under
    # 512), but asserted structurally rather than trusted by arithmetic.
    return detail[:512] if len(detail) > 512 else detail


def _apply_required_check_assessment_v2(
    *, decision: ReadinessDecisionV2, assessment: RequiredCheckReadinessAssessmentV2
) -> ReadinessDecisionV2:
    """`#201-C` -- fold a required-check assessment into a content decision,
    per the ratified precedence:

    1. `STALE` is sovereign. A required-check assessment is never consulted
       when `decision.state is STALE`: identity/HEAD divergence already
       makes any evidence non-current, and `ReviewReadinessV2.validate_
       state_invariants`'s own STALE branch forbids any reason code beyond
       `head_mismatch`/`identity_mismatch` -- adding a check reason here
       would not degrade gracefully, it would make the artifact
       unconstructible.
    2. `assessment.status is SATISFIED` -- the decision passes through
       completely unchanged. This is the only case that can still reach
       `READY`, and only if `decision` itself was already `READY`.
    3. Otherwise (`FAILED` or `AUTHORITY_NOT_ESTABLISHED`), `POLICY_FAILURE`
       is added as an additional reason/blocker/structured pipeline cause.
       If `decision.state` was already `BLOCKED_CODE` (a CONFIRMED code
       finding), it stays `BLOCKED_CODE` -- the required-check failure
       coexists as an additional cause, never itself the reason the state
       is `BLOCKED_CODE`, per `CHECK FAILURE != CONFIRMED CODE FINDING`.
       Every other content state (`READY`, `MANUAL_REQUIRED`,
       `BLOCKED_PIPELINE`) becomes `MANUAL_REQUIRED`.

       Downgrading `BLOCKED_PIPELINE` to `MANUAL_REQUIRED` here is a
       RATIFIED PRECEDENCE DECISION, not a technical necessity:
       `BLOCKED_PIPELINE`'s own frozen invariant would in fact tolerate
       `POLICY_FAILURE` coexisting inside it. The decision is that a
       required-check problem is judged more expressive of "a human
       decision is the right next step" than the target's own
       coverage-failure policy choice, so it always wins. This makes the
       precedence non-monotonic in one specific combination (a target
       whose `coverage_failure_state` is `blocked_pipeline`, combined with
       a required-check failure, ends up LESS restrictive in STATE than
       `BLOCKED_PIPELINE` alone would be -- though never less informative:
       both `coverage_failure` and `policy_failure` remain visible in
       `reason_codes`) -- recorded here deliberately, not discovered later.

    Adversarial review finding, confirmed and fixed (round 1): `ReadinessDecisionV2`
    is "freely constructible" (C1's own design -- no validation at
    construction, unlike `ReviewReadinessV2`), so a `--decision` JSON file
    could carry `BLOCKED_PIPELINE` together with `SCHEMA_FAILURE`/
    `TRANSPORT_FAILURE` -- reasons the frozen contract's own `BLOCKED_
    PIPELINE` branch allows, but which `MANUAL_REQUIRED`'s branch does
    NOT. `compute_readiness_decision_v2` itself never produces either
    reason (it only ever adds `COVERAGE_FAILURE`/`MODEL_UNCERTAINTY`), so
    this was unreachable through the real producer -- but nothing enforces
    that a `--decision` file came from it. Downgrading such a decision to
    `MANUAL_REQUIRED` would silently construct an UNREPRESENTABLE
    combination, surfacing many calls later as an opaque `pydantic.
    ValidationError` from deep inside `ReviewReadinessV2.__init__` --
    reproducing, for THIS specific reason-code combination, the exact
    "crash instead of a representable state" defect class `#201-C`'s own
    module docstring says it eliminates. Refused here instead, immediately
    and by name, before any transformation is attempted -- fail-closed,
    per the plan's own stop condition ("readiness incapaz de representar
    ausência de autoridade sem mentir").

    Adversarial review finding, confirmed and fixed (round 5): the round-1
    fix guarded only the `MANUAL_REQUIRED` branch, on the mistaken premise
    that `BLOCKED_CODE`'s own allowed reason set is a strict superset and
    therefore can never be the unrepresentable branch. It is not a superset:
    `BLOCKED_CODE` allows `SCHEMA_FAILURE`/`TRANSPORT_FAILURE` but -- like
    `MANUAL_REQUIRED` not allowing them -- disallows `FINDING_CONFIRMATION_
    REQUIRED`. A hand-crafted `--decision` with `state=BLOCKED_CODE,
    reason_codes=(CONFIRMED_CODE_FINDING, FINDING_CONFIRMATION_REQUIRED)`
    (unreachable through `compute_readiness_decision_v2`, which never adds
    `FINDING_CONFIRMATION_REQUIRED` to a `BLOCKED_CODE` decision, but not
    enforced against a hand-built file) reproduced the identical "opaque
    `pydantic.ValidationError` several calls later" defect the round-1 fix
    was supposed to eliminate everywhere. The guard below now checks
    whichever branch `state` actually resolves to, against that branch's
    own safe-existing-reasons set, symmetrically.

    A required check the assessment reports `FAILED` for is never dropped:
    `assessment.checks` -- the exact tuple `#201-C0`'s verifier accepted,
    including every red result -- becomes `ReviewReadinessV2.checks`
    unchanged, one layer up in `review_readiness_emission_v2.
    produce_review_readiness_v2`. This function only ever WIDENS the set of
    reasons/blockers/causes; it never removes anything `compute_readiness_
    decision_v2` already produced, and the frozen contract's own validator
    remains the sole authority on whether the result is well-formed -- this
    function does not re-implement any of its checks.
    """

    if decision.state is ReadinessStateV2.STALE:
        return decision
    if assessment.status is RequiredCheckStatusV2.SATISFIED:
        return decision

    state = (
        ReadinessStateV2.BLOCKED_CODE
        if decision.state is ReadinessStateV2.BLOCKED_CODE
        else ReadinessStateV2.MANUAL_REQUIRED
    )
    safe_existing_reasons = (
        _BLOCKED_CODE_SAFE_EXISTING_REASONS_V2
        if state is ReadinessStateV2.BLOCKED_CODE
        else _MANUAL_REQUIRED_SAFE_EXISTING_REASONS_V2
    )
    if not set(decision.reason_codes) <= safe_existing_reasons:
        # See the docstring above (round 5): checked symmetrically for
        # whichever branch `state` resolves to -- neither branch's safe set
        # is a superset of the other's.
        raise ReadinessDecisionError(DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2)

    cause = PipelineDegradationCauseV2(
        reason_code=ReadinessReasonV2.POLICY_FAILURE,
        component=REQUIRED_CHECKS_PIPELINE_COMPONENT_V2,
        detail=_required_check_detail_v2(assessment),
    )
    blocker = ReadinessBlockerV2(
        blocker_id=_REQUIRED_CHECKS_BLOCKER_ID_V2,
        reason_code=ReadinessReasonV2.POLICY_FAILURE,
        active=True,
        finding_id=None,
    )

    return ReadinessDecisionV2(
        state=state,
        reason_codes=tuple(
            sorted({*decision.reason_codes, ReadinessReasonV2.POLICY_FAILURE}, key=lambda code: code.value)
        ),
        blockers=(*decision.blockers, blocker),
        coverage=decision.coverage,
        pipeline=PipelineAssessmentV2(degraded=True, causes=[*decision.pipeline.causes, cause]),
        run_id=decision.run_id,
        manifest_hash=decision.manifest_hash,
    )
