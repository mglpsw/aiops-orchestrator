from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    CoverageStateV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    PipelineAssessmentV2,
    PipelineDegradationCauseV2,
    PullRequestStateV2,
    ReadinessBlockerV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    RunIdentityV2,
    TargetPoliciesV2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestDegradationV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.readiness_decision_v2 import (
    COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2,
    COVERAGE_BRIDGE_UNKNOWN_DEGRADATION_REASON_V2,
    DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2,
    READINESS_INVALID_STALE_REASON_CODES_REASON_V2,
    READINESS_SYNTHESIS_MANIFEST_RUN_ID_MISMATCH_REASON_V2,
    REQUIRED_CHECKS_PIPELINE_COMPONENT_V2,
    ReadinessDecisionError,
    ReadinessDecisionV2,
    _apply_required_check_assessment_v2,
    _joined_with_budget_v2,
    bridge_fragment_coverage_to_chunk_coverage_v2,
    compute_readiness_decision_v2,
)
from app.agent_review.required_check_readiness_v2 import RequiredCheckStatusV2, _assess_required_checks_v2
from app.agent_review.review_readiness_emission_v2 import _assemble_review_readiness_v2
from app.agent_review.run_fragment_coverage_v2 import (
    FragmentCoverageReasonV2,
    FragmentCoverageStatusV2,
    RunFragmentCoverageEntryV2,
    RunFragmentCoverageReportMaterialV2,
    RunFragmentCoverageReportV2,
    compute_coverage_report_sha256_v2,
)
from app.agent_review.synthesis_v2 import SynthesisResultV2


# -- fixture helpers -----------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 127,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _fragment(*, path: str, seed: bytes, start: int) -> FragmentV2:
    diff_sha = hashlib.sha256(seed).hexdigest()
    old_range = LineRangeV2(start=start, end=start + 4)
    new_range = LineRangeV2(start=start, end=start + 4)
    fragment_id = compute_fragment_id_v2(path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha)
    return FragmentV2(
        fragment_id=fragment_id,
        path=path,
        old_range=old_range,
        new_range=new_range,
        hunk_indexes=[0],
        diff_chars=10,
        diff_sha256=diff_sha,
        coverage_required=True,
    )


def _policies(
    *,
    coverage_failure_state: str = "blocked_pipeline",
    allowed_semantic_groups: list[str] = ("primary_backend_logic",),
) -> TargetPoliciesV2:
    return TargetPoliciesV2.model_validate(
        {
            "network_policy": "forbidden",
            "fail_closed": True,
            "redaction_required": True,
            "allow_partial_coverage": False,
            "required_checks": ["pytest"],
            "allowed_semantic_groups": list(allowed_semantic_groups),
            "coverage_failure_state": coverage_failure_state,
            "model_uncertainty_state": "manual_required",
        }
    )


def _fully_reviewed_manifest_and_report(*, path: str = "app/a.py") -> tuple[ManifestV2, RunFragmentCoverageReportV2]:
    """One path, one fragment, one chunk, fully reviewed -- the baseline
    READY-eligible shape."""

    fragment = _fragment(path=path, seed=b"c1-ready", start=1)
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": [path],
        "must_review_files": [path],
        "fragments": [fragment],
        "chunks": [
            ManifestChunkV2(
                chunk_id="chunk-0",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment.fragment_id],
                payload_sha256=None,
            )
        ],
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    entry = RunFragmentCoverageEntryV2(
        path=path,
        expected_fragment_ids=[fragment.fragment_id],
        assigned_fragment_ids=[fragment.fragment_id],
        reviewed_fragment_ids=[fragment.fragment_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0"],
        status=FragmentCoverageStatusV2.REVIEWED,
        reason_codes=[],
    )
    report = _report(manifest=manifest, entries=[entry])
    return manifest, report


def _report(*, manifest: ManifestV2, entries: list[RunFragmentCoverageEntryV2]) -> RunFragmentCoverageReportV2:
    material_kwargs = {
        "schema_id": "agent-review.run-fragment-coverage.v2",
        "schema_version": 2,
        "source": "aiops-review-fragment-coverage",
        "run_id": manifest.run_id,
        "manifest_hash": manifest.identity.manifest_hash,
        "paths": entries,
    }
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material_kwargs))
    return RunFragmentCoverageReportV2.model_validate({**material_kwargs, "coverage_report_sha256": sha})


def _split_and_degraded_manifest_and_report() -> tuple[ManifestV2, RunFragmentCoverageReportV2]:
    """The #116 combined fixture: one path, three fragments -- two real
    ones genuinely split across two different chunks, plus a third that is
    explicitly degraded (accounted for by a real ManifestDegradationV2
    cause). Proves structural_split and fragment_degraded coexisting on a
    single entry, with deterministic DEGRADED precedence at the bridge."""

    path = "app/a.py"
    fragment_chunk_0 = _fragment(path=path, seed=b"combined-1", start=1)
    fragment_chunk_1 = _fragment(path=path, seed=b"combined-2", start=10)
    fragment_degraded = _fragment(path=path, seed=b"combined-3", start=19)

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": [path],
        "must_review_files": [path],
        "fragments": [fragment_chunk_0, fragment_chunk_1, fragment_degraded],
        "chunks": [
            ManifestChunkV2(
                chunk_id="chunk-0",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_chunk_0.fragment_id],
                payload_sha256=None,
            ),
            ManifestChunkV2(
                chunk_id="chunk-1",
                order_index=1,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_chunk_1.fragment_id],
                payload_sha256=None,
            ),
        ],
        "max_chunks": 10,
        "degradation_causes": [
            ManifestDegradationV2(
                reason_code="budget_exhausted",
                affected_fragment_ids=[fragment_degraded.fragment_id],
                detail="deliberately degraded for the #116 combined split+degradation regression",
            )
        ],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    entry = RunFragmentCoverageEntryV2(
        path=path,
        expected_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id, fragment_degraded.fragment_id],
        assigned_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id],
        reviewed_fragment_ids=[],
        partially_reviewed_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id],
        missing_fragment_ids=[fragment_degraded.fragment_id],
        affected_chunk_ids=["chunk-0", "chunk-1"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT, FragmentCoverageReasonV2.FRAGMENT_DEGRADED],
    )
    report = _report(manifest=manifest, entries=[entry])
    return manifest, report


def _plain_split_manifest_and_report() -> tuple[ManifestV2, RunFragmentCoverageReportV2]:
    """A structurally split path with NO manifest-level degradation cause
    at all -- the plain "path fragmentado" acceptance-criterion case."""

    path = "app/a.py"
    fragment_chunk_0 = _fragment(path=path, seed=b"split-only-1", start=1)
    fragment_chunk_1 = _fragment(path=path, seed=b"split-only-2", start=10)

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": [path],
        "must_review_files": [path],
        "fragments": [fragment_chunk_0, fragment_chunk_1],
        "chunks": [
            ManifestChunkV2(
                chunk_id="chunk-0",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_chunk_0.fragment_id],
                payload_sha256=None,
            ),
            ManifestChunkV2(
                chunk_id="chunk-1",
                order_index=1,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_chunk_1.fragment_id],
                payload_sha256=None,
            ),
        ],
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    entry = RunFragmentCoverageEntryV2(
        path=path,
        expected_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id],
        assigned_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id],
        reviewed_fragment_ids=[],
        partially_reviewed_fragment_ids=[fragment_chunk_0.fragment_id, fragment_chunk_1.fragment_id],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0", "chunk-1"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT],
    )
    report = _report(manifest=manifest, entries=[entry])
    return manifest, report


def _synthesis(
    *,
    manifest: ManifestV2,
    coverage_report: RunFragmentCoverageReportV2,
    findings: tuple[FindingLifecycleRecordV2, ...] = (),
    limitations: tuple[str, ...] = (),
) -> SynthesisResultV2:
    return SynthesisResultV2(
        run_id=manifest.run_id,
        evaluated_head_sha=manifest.identity.head_sha,
        coverage_report=coverage_report,
        findings=findings,
        provenance={},
        limitations=limitations,
    )


def _new_finding(*, finding_id: str, head_sha: str, severity: FindingSeverityV2 = FindingSeverityV2.P1) -> FindingLifecycleRecordV2:
    return FindingLifecycleRecordV2(
        finding_id=finding_id,
        severity=severity,
        observed_at_head_sha=head_sha,
        disposition=FindingDispositionV2.NEW,
        actionable=True,
        justification=None,
        decided_by=None,
        decided_at_head_sha=None,
        evidence=[],
        superseded_by=None,
    )


def _confirmed_finding(
    *, finding_id: str, head_sha: str, severity: FindingSeverityV2 = FindingSeverityV2.P0
) -> FindingLifecycleRecordV2:
    return FindingLifecycleRecordV2(
        finding_id=finding_id,
        severity=severity,
        observed_at_head_sha=head_sha,
        disposition=FindingDispositionV2.CONFIRMED,
        actionable=True,
        justification=None,
        decided_by="reviewer-1",
        decided_at_head_sha=head_sha,
        evidence=[],
        superseded_by=None,
    )


# -- bridge_fragment_coverage_to_chunk_coverage_v2 -----------------------------


def test_bridge_produces_complete_coverage_when_everything_is_reviewed() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    coverage = bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=manifest)
    assert coverage.status is CoverageStateV2.COMPLETE
    assert coverage.reviewed_files == ("app/a.py",)
    assert coverage.missing_must_review_files == ()
    assert coverage.degradation_causes == ()


def test_bridge_produces_partial_coverage_for_a_plain_structural_split() -> None:
    manifest, report = _plain_split_manifest_and_report()
    coverage = bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=manifest)
    assert coverage.status is CoverageStateV2.PARTIAL
    assert coverage.partially_reviewed_files == ("app/a.py",)
    assert coverage.degradation_causes == ()


def test_bridge_produces_degraded_coverage_for_the_split_plus_degraded_fixture() -> None:
    """The #116 combined fixture: structural_split and fragment_degraded
    coexist on the same fragment-level entry; the bridge deterministically
    resolves the path as DEGRADED (backed by the real manifest cause), not
    merely PARTIAL."""

    manifest, report = _split_and_degraded_manifest_and_report()
    coverage = bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=manifest)
    assert coverage.status is CoverageStateV2.DEGRADED
    assert coverage.missing_files == ("app/a.py",) or coverage.partially_reviewed_files == ("app/a.py",)
    assert len(coverage.degradation_causes) == 1
    assert coverage.degradation_causes[0].reason_code.value == "budget_exhausted"
    assert coverage.degradation_causes[0].affected_files == ("app/a.py",)


def test_bridge_rejects_a_coverage_report_that_does_not_match_the_manifest() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    other_manifest, _ = _fully_reviewed_manifest_and_report(path="app/b.py")
    with pytest.raises(Exception):
        bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=other_manifest)


# -- compute_readiness_decision_v2: one case per state -------------------------


def test_decision_is_ready_when_everything_is_clean() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY
    assert decision.reason_codes == ()
    assert decision.blockers == ()
    assert decision.pipeline.degraded is False


def test_decision_is_blocked_code_when_a_finding_is_confirmed() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-1", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.CONFIRMED_CODE_FINDING in decision.reason_codes
    assert any(b.finding_id == "finding-1" for b in decision.blockers)


def test_decision_is_blocked_pipeline_for_a_plain_coverage_failure_when_policy_says_so() -> None:
    manifest, report = _plain_split_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state="blocked_pipeline")
    )
    assert decision.state is ReadinessStateV2.BLOCKED_PIPELINE
    assert decision.reason_codes == (ReadinessReasonV2.COVERAGE_FAILURE,)


def test_decision_is_exactly_the_policy_configured_coverage_failure_state() -> None:
    """Acceptance criterion, verbatim: 'path fragmentado produz exatamente
    policies.coverage_failure_state'."""

    manifest, report = _plain_split_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)

    for configured in ("blocked_pipeline", "manual_required"):
        decision = compute_readiness_decision_v2(
            synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state=configured)
        )
        assert decision.state.value == configured
        assert ReadinessReasonV2.COVERAGE_FAILURE in decision.reason_codes


def test_decision_is_manual_required_when_a_chunk_reports_model_uncertainty() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report, limitations=("model_uncertainty",))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in decision.reason_codes


def test_decision_is_manual_required_for_a_new_finding_pending_confirmation() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _new_finding(finding_id="finding-2", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert decision.reason_codes == (ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED,)
    assert any(b.finding_id == "finding-2" for b in decision.blockers)


def test_decision_is_stale_when_the_caller_signals_a_head_mismatch() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis,
        manifest=manifest,
        policies=_policies(),
        stale_reason_codes=frozenset({ReadinessReasonV2.HEAD_MISMATCH}),
    )
    assert decision.state is ReadinessStateV2.STALE
    assert decision.reason_codes == (ReadinessReasonV2.HEAD_MISMATCH,)
    assert decision.blockers == ()


# -- precedence: confirmed finding wins over everything else -------------------


def test_confirmed_finding_wins_over_a_coexisting_coverage_failure() -> None:
    manifest, report = _plain_split_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-3", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.CONFIRMED_CODE_FINDING in decision.reason_codes
    assert ReadinessReasonV2.COVERAGE_FAILURE in decision.reason_codes


def test_model_uncertainty_wins_over_a_coexisting_coverage_failure_regardless_of_policy() -> None:
    """model_uncertainty can never appear in BLOCKED_PIPELINE's allowed
    reason set, so its presence always forces MANUAL_REQUIRED even when the
    target's own policy says coverage failures should block_pipeline."""

    manifest, report = _plain_split_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report, limitations=("model_uncertainty",))
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state="blocked_pipeline")
    )
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in decision.reason_codes
    assert ReadinessReasonV2.COVERAGE_FAILURE in decision.reason_codes


# -- error paths ----------------------------------------------------------------


def test_decision_rejects_a_synthesis_manifest_run_id_mismatch() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    other_manifest, _ = _fully_reviewed_manifest_and_report(path="app/b.py")
    with pytest.raises(ReadinessDecisionError) as excinfo:
        compute_readiness_decision_v2(synthesis=synthesis, manifest=other_manifest, policies=_policies())
    assert excinfo.value.reason_code == READINESS_SYNTHESIS_MANIFEST_RUN_ID_MISMATCH_REASON_V2


def test_decision_rejects_invalid_stale_reason_codes() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    with pytest.raises(ReadinessDecisionError) as excinfo:
        compute_readiness_decision_v2(
            synthesis=synthesis,
            manifest=manifest,
            policies=_policies(),
            stale_reason_codes=frozenset({ReadinessReasonV2.COVERAGE_FAILURE}),
        )
    assert excinfo.value.reason_code == READINESS_INVALID_STALE_REASON_CODES_REASON_V2


def test_bridge_fails_closed_on_a_run_mixing_a_plain_split_with_an_unrelated_degradation() -> None:
    """Two different paths: one purely structurally split (no manifest
    cause), one genuinely degraded (real manifest cause) -- ChunkCoverageV2's
    frozen contract cannot represent both under a single status value.
    Must fail closed, not silently misrepresent either."""

    split_manifest, split_report = _plain_split_manifest_and_report()
    degraded_fragment = _fragment(path="app/b.py", seed=b"mixed-degraded", start=1)

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py", "app/b.py"],
        "must_review_files": ["app/a.py", "app/b.py"],
        "fragments": [*split_manifest.fragments, degraded_fragment],
        "chunks": [
            *split_manifest.chunks,
        ],
        "max_chunks": 10,
        "degradation_causes": [
            ManifestDegradationV2(
                reason_code="artifact_missing",
                affected_fragment_ids=[degraded_fragment.fragment_id],
                detail="mixed-scenario degradation on a second, unrelated path",
            )
        ],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)

    split_entry = split_report.paths[0]
    degraded_entry = RunFragmentCoverageEntryV2(
        path="app/b.py",
        expected_fragment_ids=[degraded_fragment.fragment_id],
        assigned_fragment_ids=[],
        reviewed_fragment_ids=[],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[degraded_fragment.fragment_id],
        affected_chunk_ids=[],
        status=FragmentCoverageStatusV2.MISSING,
        reason_codes=[FragmentCoverageReasonV2.FRAGMENT_DEGRADED],
    )
    report = _report(manifest=manifest, entries=[split_entry, degraded_entry])

    with pytest.raises(ReadinessDecisionError) as excinfo:
        bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=manifest)
    assert excinfo.value.reason_code == COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2


def test_bridge_fails_closed_on_an_unmapped_manifest_degradation_reason() -> None:
    """Found by independent review: the manifest-to-coverage degradation
    reason mapping is a bare dict lookup with no fallback. It is exhaustive
    over manifest_v2.DegradationReasonValueV2's current 7 literal values
    today (so this is unreachable through any manifest a real ManifestV2
    constructor accepts), but must still fail closed with a named reason
    code -- not a bare KeyError -- if that literal is ever extended without
    updating the mapping here. ManifestDegradationV2.model_construct
    bypasses its own Literal validation to simulate exactly that future
    case without waiting for it to actually happen."""

    path = "app/a.py"
    fragment = _fragment(path=path, seed=b"unmapped-reason", start=1)
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": [path],
        "must_review_files": [path],
        "fragments": [fragment],
        "chunks": [],
        "max_chunks": 10,
        "degradation_causes": [
            ManifestDegradationV2.model_construct(
                reason_code="a_future_reason_not_yet_mapped",
                affected_fragment_ids=[fragment.fragment_id],
                detail="simulates a manifest_v2 literal value added without updating this module",
            )
        ],
    }
    material = ManifestMaterialV2.model_construct(**material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2.model_construct(
        **material_kwargs, run_id=compute_run_id(identity), identity=identity
    )

    entry = RunFragmentCoverageEntryV2(
        path=path,
        expected_fragment_ids=[fragment.fragment_id],
        assigned_fragment_ids=[],
        reviewed_fragment_ids=[],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[fragment.fragment_id],
        affected_chunk_ids=[],
        status=FragmentCoverageStatusV2.MISSING,
        reason_codes=[FragmentCoverageReasonV2.FRAGMENT_DEGRADED],
    )
    report = _report(manifest=manifest, entries=[entry])

    with pytest.raises(ReadinessDecisionError) as excinfo:
        bridge_fragment_coverage_to_chunk_coverage_v2(coverage_report=report, manifest=manifest)
    assert excinfo.value.reason_code == COVERAGE_BRIDGE_UNKNOWN_DEGRADATION_REASON_V2


# -- `#201-C`: `_apply_required_check_assessment_v2` precedence (plan §5.1) --

_SATISFIED = _assess_required_checks_v2(
    verified_checks=(
        RequiredCheckResultV2(
            check_name="pytest", required=True, deterministic=True,
            conclusion=RequiredCheckConclusionV2.SUCCESS, head_sha="2" * 40,
        ),
    ),
    required_check_names=("pytest",),
)
_FAILED = _assess_required_checks_v2(
    verified_checks=(
        RequiredCheckResultV2(
            check_name="pytest", required=True, deterministic=True,
            conclusion=RequiredCheckConclusionV2.FAILURE, head_sha="2" * 40,
        ),
    ),
    required_check_names=("pytest",),
)
_NOT_ESTABLISHED = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))


def _ready_decision():
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    return compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())


def _blocked_code_decision():
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-1", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    return compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())


def _blocked_pipeline_decision():
    manifest, report = _plain_split_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    return compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state="blocked_pipeline")
    )


def _manual_required_model_uncertainty_decision():
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report, limitations=("model_uncertainty",))
    return compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())


def _manual_required_new_finding_decision():
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _new_finding(finding_id="finding-2", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    return compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())


def _stale_decision():
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    return compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(),
        stale_reason_codes=frozenset({ReadinessReasonV2.HEAD_MISMATCH}),
    )


def test_satisfied_never_changes_a_ready_decision() -> None:
    decision = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_SATISFIED)
    assert decision.state is ReadinessStateV2.READY
    assert decision.reason_codes == ()
    assert decision.blockers == ()
    assert decision.pipeline.degraded is False


def test_satisfied_never_changes_any_other_decision() -> None:
    for build in (
        _blocked_code_decision, _blocked_pipeline_decision,
        _manual_required_model_uncertainty_decision, _manual_required_new_finding_decision, _stale_decision,
    ):
        before = build()
        after = _apply_required_check_assessment_v2(decision=before, assessment=_SATISFIED)
        assert after == before


def test_failed_turns_ready_into_manual_required_with_policy_failure() -> None:
    decision = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_FAILED)
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert decision.reason_codes == (ReadinessReasonV2.POLICY_FAILURE,)
    assert len(decision.blockers) == 1
    assert decision.blockers[0].reason_code is ReadinessReasonV2.POLICY_FAILURE
    assert decision.blockers[0].active is True
    assert decision.blockers[0].finding_id is None
    assert decision.pipeline.degraded is True
    assert len(decision.pipeline.causes) == 1
    assert decision.pipeline.causes[0].component == REQUIRED_CHECKS_PIPELINE_COMPONENT_V2
    assert "pytest" in decision.pipeline.causes[0].detail


def test_authority_not_established_turns_ready_into_manual_required() -> None:
    decision = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_NOT_ESTABLISHED)
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert decision.reason_codes == (ReadinessReasonV2.POLICY_FAILURE,)


def test_check_failure_is_never_confirmed_code_finding() -> None:
    """`CHECK FAILURE != CONFIRMED CODE FINDING`. A required-check problem on
    an otherwise-READY decision must never itself produce `BLOCKED_CODE` --
    only a genuinely confirmed finding may."""

    decision = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_FAILED)
    assert decision.state is not ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.CONFIRMED_CODE_FINDING not in decision.reason_codes


@pytest.mark.parametrize("assessment", [_FAILED, _NOT_ESTABLISHED])
def test_a_confirmed_finding_keeps_blocked_code_and_gains_policy_failure(assessment) -> None:
    before = _blocked_code_decision()
    after = _apply_required_check_assessment_v2(decision=before, assessment=assessment)

    assert after.state is ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.CONFIRMED_CODE_FINDING in after.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in after.reason_codes
    # The original confirmed-finding blocker survives untouched, alongside
    # the new required-checks blocker.
    assert any(b.reason_code is ReadinessReasonV2.CONFIRMED_CODE_FINDING for b in after.blockers)
    assert any(b.reason_code is ReadinessReasonV2.POLICY_FAILURE for b in after.blockers)


@pytest.mark.parametrize("assessment", [_FAILED, _NOT_ESTABLISHED])
def test_blocked_pipeline_downgrades_to_manual_required(assessment) -> None:
    """Ratified, non-monotonic precedence (plan §5.2): a required-check
    problem always forces `MANUAL_REQUIRED`, even though `BLOCKED_PIPELINE`
    would itself tolerate `POLICY_FAILURE` -- both `coverage_failure` and
    `policy_failure` remain visible in `reason_codes`."""

    before = _blocked_pipeline_decision()
    after = _apply_required_check_assessment_v2(decision=before, assessment=assessment)

    assert after.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.COVERAGE_FAILURE in after.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in after.reason_codes


@pytest.mark.parametrize("assessment", [_FAILED, _NOT_ESTABLISHED])
def test_manual_required_model_uncertainty_gains_policy_failure(assessment) -> None:
    before = _manual_required_model_uncertainty_decision()
    after = _apply_required_check_assessment_v2(decision=before, assessment=assessment)

    assert after.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in after.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in after.reason_codes


@pytest.mark.parametrize("assessment", [_FAILED, _NOT_ESTABLISHED])
def test_manual_required_new_finding_gains_policy_failure(assessment) -> None:
    before = _manual_required_new_finding_decision()
    after = _apply_required_check_assessment_v2(decision=before, assessment=assessment)

    assert after.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED in after.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in after.reason_codes
    # The original pending-finding blocker survives untouched.
    assert any(b.reason_code is ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED for b in after.blockers)


@pytest.mark.parametrize("assessment", [_SATISFIED, _FAILED, _NOT_ESTABLISHED])
def test_stale_is_sovereign_and_never_gains_a_check_reason(assessment) -> None:
    before = _stale_decision()
    after = _apply_required_check_assessment_v2(decision=before, assessment=assessment)
    assert after == before


def test_failed_and_not_established_produce_distinguishable_detail() -> None:
    """The two non-satisfied statuses map to the same `POLICY_FAILURE`
    reason (there is no frozen reason code for "authority not established"
    specifically), but the human-readable `detail` still distinguishes them
    -- no information is silently collapsed."""

    failed = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_FAILED)
    not_established = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=_NOT_ESTABLISHED)

    assert "failed" in failed.pipeline.causes[0].detail
    assert "authority not established" in not_established.pipeline.causes[0].detail
    assert failed.pipeline.causes[0].detail != not_established.pipeline.causes[0].detail


def test_a_large_required_check_set_never_exceeds_the_detail_contracts_own_bound() -> None:
    """Adversarial review finding, confirmed and fixed. `TargetPoliciesV2.
    required_checks` has no maximum count, and each `SafeText` name may be
    up to 512 characters on its own -- `TargetPoliciesV2.required_checks:
    list[SafeText]` (contracts_v2.py) carries no upper bound at all. A
    realistic large required-check set (a monorepo's CI matrix) joined
    without a cap would exceed `PipelineDegradationCauseV2.detail`'s own
    `SafeText` bound (max_length=512) and raise `pydantic.ValidationError`
    -- propagating UNCAUGHT out of `_apply_required_check_assessment_v2`
    (a pure function with no `try`/`except`), crashing `produce_review_
    readiness_v2` instead of producing exactly the `manual_required`
    artifact this module exists to make representable. Reproduced before
    the fix: 30 realistically-named required checks produced a 2189-
    character `detail`, and `PipelineDegradationCauseV2(...)` raised
    `string_too_long`."""

    names = tuple(f"required-check-name-number-{i:03d}-with-some-extra-padding-to-be-realistic" for i in range(30))
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=names)

    decision = _apply_required_check_assessment_v2(decision=_ready_decision(), assessment=assessment)

    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    detail = decision.pipeline.causes[0].detail
    assert len(detail) <= 512
    # Still informative -- not silently emptied.
    assert "required-check-name-number-000" in detail or "30" in detail


def test_blocked_pipeline_with_schema_or_transport_failure_is_refused_cleanly_not_crashed() -> None:
    """Adversarial review finding, confirmed and fixed. `ReadinessDecisionV2`
    is freely constructible (no validation at construction) -- a
    hand-crafted or foreign `--decision` file could carry `BLOCKED_PIPELINE`
    with `SCHEMA_FAILURE`/`TRANSPORT_FAILURE`, which the frozen contract's
    `BLOCKED_PIPELINE` branch allows but `MANUAL_REQUIRED`'s branch does
    not. `compute_readiness_decision_v2` itself never produces either
    reason, so this was unreachable through the real producer, but nothing
    enforced that. Before the fix, downgrading such a decision produced an
    unrepresentable `ReadinessDecisionV2` that only failed several calls
    later, as an opaque `pydantic.ValidationError` from inside
    `ReviewReadinessV2.__init__` -- reproducing, for this combination,
    exactly the crash-instead-of-a-state defect class `#201-C` exists to
    eliminate. Now refused immediately, by name, before any transformation
    is attempted."""

    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE, expected_files=(), reviewed_files=(), partially_reviewed_files=(),
        missing_files=(), must_review_files=(), missing_must_review_files=(), degradation_causes=(),
    )
    cause = PipelineDegradationCauseV2(
        reason_code=ReadinessReasonV2.TRANSPORT_FAILURE, component="transport", detail="synthetic"
    )
    identity = RunIdentityV2.model_validate(_identity())
    decision = ReadinessDecisionV2(
        state=ReadinessStateV2.BLOCKED_PIPELINE,
        reason_codes=(ReadinessReasonV2.TRANSPORT_FAILURE,),
        blockers=(),
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=True, causes=[cause]),
        run_id=compute_run_id(identity),
        manifest_hash=identity.manifest_hash,
    )
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    with pytest.raises(ReadinessDecisionError) as exc_info:
        _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    assert exc_info.value.reason_code == DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2


def test_blocked_code_tolerates_schema_or_transport_failure_unaffected() -> None:
    """The companion positive case: `BLOCKED_CODE`'s own allowed pipeline-
    reason set already includes `SCHEMA_FAILURE`/`TRANSPORT_FAILURE`
    (contracts_v2.py), so the guard above must never fire for it -- a
    confirmed finding coexisting with a pipeline failure and a required-
    check problem remains fully representable."""

    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE, expected_files=(), reviewed_files=(), partially_reviewed_files=(),
        missing_files=(), must_review_files=(), missing_must_review_files=(), degradation_causes=(),
    )
    cause = PipelineDegradationCauseV2(
        reason_code=ReadinessReasonV2.TRANSPORT_FAILURE, component="transport", detail="synthetic"
    )
    identity = RunIdentityV2.model_validate(_identity())
    blocker = ReadinessBlockerV2(
        blocker_id="confirmed-finding-1", reason_code=ReadinessReasonV2.CONFIRMED_CODE_FINDING,
        active=True, finding_id="finding-1",
    )
    decision = ReadinessDecisionV2(
        state=ReadinessStateV2.BLOCKED_CODE,
        reason_codes=(ReadinessReasonV2.CONFIRMED_CODE_FINDING, ReadinessReasonV2.TRANSPORT_FAILURE),
        blockers=(blocker,),
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=True, causes=[cause]),
        run_id=compute_run_id(identity),
        manifest_hash=identity.manifest_hash,
    )
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    result = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    assert result.state is ReadinessStateV2.BLOCKED_CODE
    assert ReadinessReasonV2.TRANSPORT_FAILURE in result.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in result.reason_codes


def test_blocked_code_with_transport_failure_survives_full_review_readiness_construction() -> None:
    """Adversarial review finding (round 8, raised independently by
    multiple reviewers): `_BLOCKED_CODE_SAFE_EXISTING_REASONS_V2`/
    `_MANUAL_REQUIRED_SAFE_EXISTING_REASONS_V2` are hand-copies of
    `contracts_v2.py`'s own allowed-reason sets -- `contracts_v2.py`
    cannot be touched by this slice (plan rev.2.1, C-3: frozen contract),
    so there is no shared, single source of truth to import from instead.
    The companion test right above this one
    (`test_blocked_code_tolerates_schema_or_transport_failure_unaffected`)
    only proves `_apply_required_check_assessment_v2`'s OWN guard accepts
    `BLOCKED_CODE`+`TRANSPORT_FAILURE` -- it never proves the real, frozen
    `ReviewReadinessV2` contract accepts it too. If the hand-copy and the
    contract were ever to disagree, this is the only kind of test that
    would catch it: run the combination all the way through the real
    contract, not just through this module's own guard. See also the
    `MANUAL_REQUIRED` siblings of this proof further down (`test_
    blocked_pipeline_downgrade_survives_full_review_readiness_
    construction`, `test_model_uncertainty_downgrade_survives_full_
    review_readiness_construction`, `test_finding_confirmation_required_
    downgrade_survives_full_review_readiness_construction`) -- this test
    closes the `BLOCKED_CODE` gap those three left uncovered."""

    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-1", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.BLOCKED_CODE

    transport_cause = PipelineDegradationCauseV2(
        reason_code=ReadinessReasonV2.TRANSPORT_FAILURE, component="transport", detail="synthetic"
    )
    transport_blocker = ReadinessBlockerV2(
        blocker_id="transport-failure", reason_code=ReadinessReasonV2.TRANSPORT_FAILURE, active=True, finding_id=None,
    )
    decision_with_transport = ReadinessDecisionV2(
        state=decision.state,
        reason_codes=(*decision.reason_codes, ReadinessReasonV2.TRANSPORT_FAILURE),
        blockers=(*decision.blockers, transport_blocker),
        coverage=decision.coverage,
        pipeline=PipelineAssessmentV2(degraded=True, causes=[*decision.pipeline.causes, transport_cause]),
        run_id=decision.run_id,
        manifest_hash=decision.manifest_hash,
    )

    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))
    adjusted = _apply_required_check_assessment_v2(decision=decision_with_transport, assessment=assessment)
    assert adjusted.state is ReadinessStateV2.BLOCKED_CODE

    readiness = _assemble_review_readiness_v2(
        decision=adjusted, findings=synthesis.findings, identity=manifest.identity,
        evaluated_identity=manifest.identity, pr_state=PullRequestStateV2.OPEN, checks=[],
    )

    assert readiness.state.value == "blocked_code"
    assert ReadinessReasonV2.TRANSPORT_FAILURE in readiness.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes


def test_blocked_code_with_finding_confirmation_required_is_refused_cleanly_not_crashed() -> None:
    """Adversarial review finding, confirmed and fixed (round 5). The
    round-1 guard (see `test_blocked_pipeline_with_schema_or_transport_
    failure_is_refused_cleanly_not_crashed` above) only checked the
    `MANUAL_REQUIRED` branch, on the mistaken assumption that `BLOCKED_
    CODE`'s allowed reason set is a strict superset of `MANUAL_REQUIRED`'s.
    It is not: `BLOCKED_CODE` disallows `FINDING_CONFIRMATION_REQUIRED`
    exactly like `MANUAL_REQUIRED` disallows `SCHEMA_FAILURE`/`TRANSPORT_
    FAILURE`. A hand-crafted `--decision` file with `state=BLOCKED_CODE,
    reason_codes=(CONFIRMED_CODE_FINDING, FINDING_CONFIRMATION_REQUIRED)`
    -- unreachable through `compute_readiness_decision_v2`, which never
    adds `FINDING_CONFIRMATION_REQUIRED` to a `BLOCKED_CODE` decision, but
    not enforced against a hand-built file -- reproduced, before this fix,
    the identical opaque `pydantic.ValidationError` several calls later
    inside `ReviewReadinessV2.__init__` that the round-1 fix was supposed
    to eliminate for every branch, not just `MANUAL_REQUIRED`."""

    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE, expected_files=(), reviewed_files=(), partially_reviewed_files=(),
        missing_files=(), must_review_files=(), missing_must_review_files=(), degradation_causes=(),
    )
    identity = RunIdentityV2.model_validate(_identity())
    blocker = ReadinessBlockerV2(
        blocker_id="confirmed-finding-1", reason_code=ReadinessReasonV2.CONFIRMED_CODE_FINDING,
        active=True, finding_id="finding-1",
    )
    decision = ReadinessDecisionV2(
        state=ReadinessStateV2.BLOCKED_CODE,
        reason_codes=(ReadinessReasonV2.CONFIRMED_CODE_FINDING, ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED),
        blockers=(blocker,),
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=False, causes=[]),
        run_id=compute_run_id(identity),
        manifest_hash=identity.manifest_hash,
    )
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    with pytest.raises(ReadinessDecisionError) as exc_info:
        _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    assert exc_info.value.reason_code == DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2


def test_a_preexisting_required_checks_pipeline_cause_is_refused_cleanly_not_crashed() -> None:
    """Adversarial review finding, confirmed and fixed (round 6). Reason-code
    membership alone (the round-1/round-5 guards) is not sufficient:
    `PipelineAssessmentV2.validate_degradation` (`contracts_v2.py`) also
    requires every `(cause.reason_code, cause.component)` pair to be
    unique. A hand-crafted decision already carrying a `POLICY_FAILURE`
    cause with `component="required_checks"` passes the reason-code guard
    (`POLICY_FAILURE` is always safe) but collides with the cause this
    function appends, reproducing the same opaque `pydantic.ValidationError`
    several calls later that the earlier rounds' guards were meant to
    eliminate everywhere."""

    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE, expected_files=(), reviewed_files=(), partially_reviewed_files=(),
        missing_files=(), must_review_files=(), missing_must_review_files=(), degradation_causes=(),
    )
    identity = RunIdentityV2.model_validate(_identity())
    existing_cause = PipelineDegradationCauseV2(
        reason_code=ReadinessReasonV2.POLICY_FAILURE, component=REQUIRED_CHECKS_PIPELINE_COMPONENT_V2,
        detail="pre-existing",
    )
    decision = ReadinessDecisionV2(
        state=ReadinessStateV2.MANUAL_REQUIRED,
        reason_codes=(ReadinessReasonV2.POLICY_FAILURE,),
        blockers=(),
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=True, causes=[existing_cause]),
        run_id=compute_run_id(identity),
        manifest_hash=identity.manifest_hash,
    )
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    with pytest.raises(ReadinessDecisionError) as exc_info:
        _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    assert exc_info.value.reason_code == DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2


def test_a_preexisting_required_checks_blocker_id_is_refused_cleanly_not_crashed() -> None:
    """Companion to the cause-collision test above: `ReviewReadinessV2.
    validate_state_invariants` separately requires every `blocker.
    blocker_id` to be unique. A hand-crafted decision already carrying a
    blocker with `blocker_id="required-checks"` -- this function's own
    fixed blocker id -- passes the reason-code guard and has no colliding
    pipeline cause, but collides on blocker_id alone."""

    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE, expected_files=(), reviewed_files=(), partially_reviewed_files=(),
        missing_files=(), must_review_files=(), missing_must_review_files=(), degradation_causes=(),
    )
    identity = RunIdentityV2.model_validate(_identity())
    existing_blocker = ReadinessBlockerV2(
        blocker_id="required-checks", reason_code=ReadinessReasonV2.POLICY_FAILURE, active=True, finding_id=None,
    )
    decision = ReadinessDecisionV2(
        state=ReadinessStateV2.MANUAL_REQUIRED,
        reason_codes=(ReadinessReasonV2.POLICY_FAILURE,),
        blockers=(existing_blocker,),
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=False, causes=[]),
        run_id=compute_run_id(identity),
        manifest_hash=identity.manifest_hash,
    )
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    with pytest.raises(ReadinessDecisionError) as exc_info:
        _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    assert exc_info.value.reason_code == DECISION_UNREPRESENTABLE_WITH_REQUIRED_CHECK_ASSESSMENT_REASON_V2


def test_a_red_non_required_check_survives_full_review_readiness_construction_as_manual_required() -> None:
    """Adversarial review finding (round 8), full end-to-end proof.
    Companion to `test_a_red_non_required_check_never_produces_satisfied`
    in `test_required_check_readiness_v2.py`: every required check
    ("pytest") is green, one legitimately-verified NON-required check
    ("lint") is red. Before the fix, this combination reported `SATISFIED`,
    left a `READY` decision unchanged, and crashed `ReviewReadinessV2.
    __init__` several calls later with an uncaught `pydantic.
    ValidationError` -- because the frozen contract's READY branch holds
    EVERY entry of `self.checks` to green, not only the required-named
    ones. The fix makes `status` account for every verified check's
    conclusion, so this combination now downgrades to a representable
    `MANUAL_REQUIRED` artifact instead of crashing."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    checks = (
        RequiredCheckResultV2(
            check_name="pytest", required=True, deterministic=True,
            conclusion=RequiredCheckConclusionV2.SUCCESS, head_sha=manifest.identity.head_sha,
        ),
        RequiredCheckResultV2(
            check_name="lint", required=True, deterministic=True,
            conclusion=RequiredCheckConclusionV2.FAILURE, head_sha=manifest.identity.head_sha,
        ),
    )
    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))
    assert assessment.status is RequiredCheckStatusV2.FAILED

    adjusted = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)
    assert adjusted.state is ReadinessStateV2.MANUAL_REQUIRED

    readiness = _assemble_review_readiness_v2(
        decision=adjusted, findings=synthesis.findings, identity=manifest.identity,
        evaluated_identity=manifest.identity, pr_state=PullRequestStateV2.OPEN, checks=assessment.checks,
    )

    assert readiness.state.value == "manual_required"
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes
    assert any(c.check_name == "lint" and c.conclusion.value == "failure" for c in readiness.checks)


def test_joined_with_budget_never_produces_a_truncated_suffix() -> None:
    """Adversarial review finding, confirmed and fixed. The previous
    implementation packed names against `budget` with no headroom reserved
    for the ` (+N more)` suffix appended afterward, so the final hard
    slice routinely chopped the suffix itself. Reproduced before the fix:
    two 198-char names at budget=200 produced a string ending in a bare
    `' ('`, with no digit or closing paren. Every result must now be
    well-formed: it fits the budget, and if it was truncated at all, it
    ends with a complete `(+N more)`."""

    result = _joined_with_budget_v2(("a" * 198, "b" * 198), budget=200)
    assert len(result) <= 200
    assert result.endswith(")")
    assert "more)" in result

    many_short_names = tuple(f"check-{i}" for i in range(40))
    result_many = _joined_with_budget_v2(many_short_names, budget=200)
    assert len(result_many) <= 200
    assert result_many.endswith(")")

    tiny_budget = _joined_with_budget_v2(("a" * 300,), budget=10)
    assert len(tiny_budget) <= 10


def test_joined_with_budget_never_drops_all_content_for_a_single_overlong_name() -> None:
    """Adversarial review finding, confirmed and fixed (round 7). When even
    the single FIRST name alone (plus its own suffix) exceeds `budget`, the
    previous fallback dropped it entirely, returning a content-free
    `"(+1 more)"` -- conveying nothing about which check was missing/failed
    -- even though `budget` had plenty of room to show a meaningfully
    truncated prefix of that one name. `SafeText` check names may be up to
    512 characters (`contracts_v2.py`) against this module's own 200-
    character per-segment budget, so a single overlong name is realistically
    reachable, not just a synthetic edge case. The result must now retain a
    real prefix of the name instead of discarding it."""

    single_overlong = _joined_with_budget_v2(("a" * 300,), budget=200)
    assert len(single_overlong) <= 200
    assert single_overlong == "a" * 200
    assert single_overlong != "(+1 more)"

    with_a_short_companion = _joined_with_budget_v2(("short", "b" * 300), budget=200)
    assert len(with_a_short_companion) <= 200
    assert with_a_short_companion.startswith("short")
    assert "(+1 more)" in with_a_short_companion


def test_joined_with_budget_returns_the_full_join_when_it_fits() -> None:
    assert _joined_with_budget_v2(("a", "b", "c"), budget=200) == "a, b, c"


def test_a_required_check_present_and_green_never_masks_a_different_missing_one() -> None:
    """The literal #145 regression shape, with a SUCCESS conclusion
    specifically -- a Codex review of #145 found that a target requiring
    both pytest and mypy was satisfied by a submission containing only a
    green pytest. The only existing coverage of "one present, one missing"
    used a FAILURE conclusion for the present check; add the SUCCESS case
    by name so a future change to the missing/failed precedence cannot
    reintroduce this exact shape unnoticed."""

    checks = (
        RequiredCheckResultV2(
            check_name="pytest", required=True, deterministic=True,
            conclusion=RequiredCheckConclusionV2.SUCCESS, head_sha="2" * 40,
        ),
    )

    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest", "mypy"))

    assert assessment.status is RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    assert assessment.missing_check_names == ("mypy",)
    assert assessment.failed_check_names == ()


def test_blocked_pipeline_downgrade_survives_full_review_readiness_construction() -> None:
    """Closes the exact class of gap the adversarial review's confirmed
    finding exploited: `_apply_required_check_assessment_v2` alone
    validating is not the same fact as the frozen `ReviewReadinessV2`
    contract accepting the result. `COVERAGE_FAILURE` (the one reason
    `compute_readiness_decision_v2` can actually attach to a real
    `BLOCKED_PIPELINE` decision) combined with `POLICY_FAILURE` must
    survive all the way to a real, fully-constructed artifact -- not just
    the intermediate `ReadinessDecisionV2`."""

    manifest, report = _plain_split_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state="blocked_pipeline")
    )
    assert decision.state is ReadinessStateV2.BLOCKED_PIPELINE

    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))
    adjusted = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)
    assert adjusted.state is ReadinessStateV2.MANUAL_REQUIRED

    readiness = _assemble_review_readiness_v2(
        decision=adjusted,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[],
    )

    assert readiness.state.value == "manual_required"
    assert ReadinessReasonV2.COVERAGE_FAILURE in readiness.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes


def test_model_uncertainty_downgrade_survives_full_review_readiness_construction() -> None:
    """Same class of proof as `test_blocked_pipeline_downgrade_survives_
    full_review_readiness_construction`, for the other reachable member of
    `_MANUAL_REQUIRED_SAFE_EXISTING_REASONS_V2`: `MODEL_UNCERTAINTY` +
    `POLICY_FAILURE` together must survive a real, fully-constructed
    `ReviewReadinessV2`, not just the intermediate `ReadinessDecisionV2`."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report, limitations=("model_uncertainty",))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in decision.reason_codes

    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))
    adjusted = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    readiness = _assemble_review_readiness_v2(
        decision=adjusted, findings=synthesis.findings, identity=manifest.identity,
        evaluated_identity=manifest.identity, pr_state=PullRequestStateV2.OPEN, checks=[],
    )

    assert readiness.state.value == "manual_required"
    assert ReadinessReasonV2.MODEL_UNCERTAINTY in readiness.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes


def test_finding_confirmation_required_downgrade_survives_full_review_readiness_construction() -> None:
    """Same class of proof, for `FINDING_CONFIRMATION_REQUIRED` -- the one
    member of the safe set whose own contract invariant additionally
    requires a matching blocker naming a real, pending, actionable new
    finding. Proves the required-check wiring does not disturb that
    finding-confirmation machinery."""

    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _new_finding(finding_id="finding-3", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED in decision.reason_codes

    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))
    adjusted = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)

    readiness = _assemble_review_readiness_v2(
        decision=adjusted, findings=synthesis.findings, identity=manifest.identity,
        evaluated_identity=manifest.identity, pr_state=PullRequestStateV2.OPEN, checks=[],
    )

    assert readiness.state.value == "manual_required"
    assert ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED in readiness.reason_codes
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes
