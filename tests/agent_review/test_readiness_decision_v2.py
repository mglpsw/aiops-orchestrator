from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import (
    CoverageStateV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    ReadinessReasonV2,
    ReadinessStateV2,
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
    READINESS_INVALID_STALE_REASON_CODES_REASON_V2,
    READINESS_SYNTHESIS_MANIFEST_RUN_ID_MISMATCH_REASON_V2,
    ReadinessDecisionError,
    bridge_fragment_coverage_to_chunk_coverage_v2,
    compute_readiness_decision_v2,
)
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
