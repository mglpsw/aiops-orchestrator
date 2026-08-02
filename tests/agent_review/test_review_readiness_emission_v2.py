from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    PullRequestStateV2,
    ReadinessStateV2,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    RunIdentityV2,
    TargetPoliciesV2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.readiness_decision_v2 import compute_readiness_decision_v2
from app.agent_review.review_readiness_emission_v2 import emit_review_readiness_v2
from app.agent_review.run_fragment_coverage_v2 import (
    FragmentCoverageStatusV2,
    RunFragmentCoverageEntryV2,
    RunFragmentCoverageReportMaterialV2,
    RunFragmentCoverageReportV2,
    compute_coverage_report_sha256_v2,
)
from app.agent_review.synthesis_v2 import SynthesisResultV2


# -- fixture helpers ------------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 130,
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


def _fully_reviewed_manifest_and_report(path: str = "app/a.py") -> tuple[ManifestV2, RunFragmentCoverageReportV2]:
    from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, ManifestChunkV2, compute_fragment_id_v2

    diff_sha = hashlib.sha256(path.encode()).hexdigest()
    old_range = LineRangeV2(start=1, end=5)
    new_range = LineRangeV2(start=1, end=5)
    fragment_id = compute_fragment_id_v2(path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha)
    fragment = FragmentV2(
        fragment_id=fragment_id,
        path=path,
        old_range=old_range,
        new_range=new_range,
        hunk_indexes=[0],
        diff_chars=10,
        diff_sha256=diff_sha,
        coverage_required=True,
    )
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
    report_material_kwargs = {
        "schema_id": "agent-review.run-fragment-coverage.v2",
        "schema_version": 2,
        "source": "aiops-review-fragment-coverage",
        "run_id": manifest.run_id,
        "manifest_hash": manifest.identity.manifest_hash,
        "paths": [entry],
    }
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(report_material_kwargs))
    report = RunFragmentCoverageReportV2.model_validate({**report_material_kwargs, "coverage_report_sha256": sha})
    return manifest, report


def _policies(*, coverage_failure_state: str = "blocked_pipeline") -> TargetPoliciesV2:
    return TargetPoliciesV2.model_validate(
        {
            "network_policy": "forbidden",
            "fail_closed": True,
            "redaction_required": True,
            "allow_partial_coverage": False,
            "required_checks": ["pytest"],
            "allowed_semantic_groups": ["primary_backend_logic"],
            "coverage_failure_state": coverage_failure_state,
            "model_uncertainty_state": "manual_required",
        }
    )


def _synthesis(*, manifest: ManifestV2, coverage_report: RunFragmentCoverageReportV2, findings=()) -> SynthesisResultV2:
    return SynthesisResultV2(
        run_id=manifest.run_id,
        evaluated_head_sha=manifest.identity.head_sha,
        coverage_report=coverage_report,
        findings=findings,
        provenance={},
        limitations=(),
    )


def _green_check(head_sha: str) -> RequiredCheckResultV2:
    return RequiredCheckResultV2(
        check_name="pytest", required=True, deterministic=True, conclusion=RequiredCheckConclusionV2.SUCCESS, head_sha=head_sha
    )


def _confirmed_finding(*, finding_id: str, head_sha: str) -> FindingLifecycleRecordV2:
    return FindingLifecycleRecordV2(
        finding_id=finding_id,
        severity=FindingSeverityV2.P0,
        observed_at_head_sha=head_sha,
        disposition=FindingDispositionV2.CONFIRMED,
        actionable=True,
        justification=None,
        decided_by="reviewer-1",
        decided_at_head_sha=head_sha,
        evidence=[],
        superseded_by=None,
    )


# -- emit_review_readiness_v2 ----------------------------------------------------


def test_emits_a_real_ready_artifact() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(manifest.identity.head_sha)],
    )
    assert readiness.state is ReadinessStateV2.READY
    assert readiness.run_id == manifest.run_id


def test_emits_a_real_blocked_code_artifact_with_findings() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-1", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.BLOCKED_CODE

    readiness = emit_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(manifest.identity.head_sha)],
    )
    assert readiness.state is ReadinessStateV2.BLOCKED_CODE
    assert readiness.findings[0].finding_id == "finding-1"


def test_ready_state_with_a_merged_pr_fails_closed_via_the_contracts_own_validator() -> None:
    """Ready requires an open PR -- ReviewReadinessV2.validate_state_invariants
    is the authority, never re-checked here."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    with pytest.raises(ValidationError):
        emit_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.MERGED,
            checks=[_green_check(manifest.identity.head_sha)],
        )


def test_ready_state_without_green_checks_fails_closed() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    with pytest.raises(ValidationError):
        emit_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[],
        )
