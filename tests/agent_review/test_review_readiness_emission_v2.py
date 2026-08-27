from __future__ import annotations

import dataclasses
import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    READY_REQUIRES_GREEN_CHECKS_REASON_V2,
    READY_REQUIRES_OPEN_PR_REASON_V2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    PullRequestStateV2,
    ReadinessStateV2,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    ReviewReadinessV2,
    RunIdentityV2,
    TargetPoliciesV2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.readiness_decision_v2 import (
    _apply_required_check_assessment_v2,
    compute_readiness_decision_v2,
)
from app.agent_review.required_check_readiness_v2 import _assess_required_checks_v2
from app.agent_review.review_readiness_emission_v2 import (
    READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2,
    ReadinessEmissionError,
    _assemble_review_readiness_v2,
)
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


def _fully_reviewed_manifest_and_report(
    path: str = "app/a.py", **identity_overrides: object
) -> tuple[ManifestV2, RunFragmentCoverageReportV2]:
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
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash, **identity_overrides))
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


# -- _assemble_review_readiness_v2 -- pure assembly + replay protection, unaffected
# by #201-C's required-check authority wiring. Called directly (not through
# produce_review_readiness_v2), with _green_check(...) standing in for "some
# already-legitimated set" -- exactly like _fully_reviewed_manifest_and_report
# stands in for a real manifest, never a claim about required-check authority.
# ---------------------------------------------------------------------------


def test_emits_a_real_ready_artifact() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    readiness = _assemble_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(manifest.identity.head_sha)],
    )
    assert readiness.state is ReadinessStateV2.READY
    assert readiness.run_id == manifest.run_id


def test_submission_order_never_changes_the_serialized_artifact_bytes() -> None:
    """Post-merge review finding on PR #220
    (`#220 (comment) discussion_r3773499142`), confirmed and fixed: the
    same legitimated checks submitted in a different sequence produced a
    different `ReviewReadinessV2.checks` order, and therefore different
    serialized artifact bytes, for semantically identical runs.

    Proved here at the Class-B composition layer -- the full chain
    `_assess_required_checks_v2` -> `_apply_required_check_assessment_v2`
    -> `_assemble_review_readiness_v2`, all real, none patched. Class B is
    the correct layer for this: the property under test is canonical
    SERIALIZATION, which is independent of authority, and the C0 boundary
    refuses every non-empty submission in production today, so this
    invariance is not observable end-to-end (see the `#201-C` plan's own
    Class A/B/C split). No `produce_review_readiness_v2` /
    `run_synthetic_review_v2` call is made and nothing is monkeypatched,
    so `test_required_check_readiness_arch_v2.py`'s assert-7 guard is
    unaffected."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())

    head_sha = manifest.identity.head_sha
    checks_in_order = (
        RequiredCheckResultV2(
            check_name="pytest",
            required=True,
            deterministic=True,
            conclusion=RequiredCheckConclusionV2.SUCCESS,
            head_sha=head_sha,
        ),
        RequiredCheckResultV2(
            check_name="mypy",
            required=True,
            deterministic=True,
            conclusion=RequiredCheckConclusionV2.SUCCESS,
            head_sha=head_sha,
        ),
    )

    def _artifact_for(submitted: tuple[RequiredCheckResultV2, ...]) -> ReviewReadinessV2:
        assessment = _assess_required_checks_v2(
            verified_checks=submitted, required_check_names=("mypy", "pytest")
        )
        folded = _apply_required_check_assessment_v2(decision=decision, assessment=assessment)
        return _assemble_review_readiness_v2(
            decision=folded,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=assessment.checks,
        )

    forward = _artifact_for(checks_in_order)
    reversed_ = _artifact_for(tuple(reversed(checks_in_order)))

    # Same checks, in the same canonical order.
    assert [check.check_name for check in forward.checks] == ["mypy", "pytest"]
    assert forward.checks == reversed_.checks

    # And therefore byte-identical serialized artifacts.
    assert forward.model_dump_json() == reversed_.model_dump_json()
    assert forward.model_dump(mode="json") == reversed_.model_dump(mode="json")


def test_emits_a_real_blocked_code_artifact_with_findings() -> None:
    manifest, report = _fully_reviewed_manifest_and_report()
    finding = _confirmed_finding(finding_id="finding-1", head_sha=manifest.identity.head_sha)
    synthesis = _synthesis(manifest=manifest, coverage_report=report, findings=(finding,))
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.BLOCKED_CODE

    readiness = _assemble_review_readiness_v2(
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
    is the authority, never re-checked here.

    `#200-D` two-epoch model: the refusal is unchanged, its TYPE and PRECISION
    are not. `ready` preconditions are caller-visible, so they are established
    before the artifact is built, and the reason NAMES the unmet rule --
    recovering the discrimination a single `..._contract_invalid` code had
    destroyed. The rules live once in `contracts_v2` and are consulted by both
    this path and the artifact's own validator.
    """

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.MERGED,
            checks=[_green_check(manifest.identity.head_sha)],
        )
    assert excinfo.value.reason_code == READY_REQUIRES_OPEN_PR_REASON_V2


def test_ready_state_without_green_checks_fails_closed() -> None:
    """Same evaluated type change as the test above; same proposition."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    with pytest.raises(ReadinessEmissionError) as excinfo:
        _assemble_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[],
        )
    assert excinfo.value.reason_code == READY_REQUIRES_GREEN_CHECKS_REASON_V2


def test_emit_review_readiness_rejects_a_decision_replayed_from_a_different_run() -> None:
    """Issue #145 thread 2 -- a `ready` decision computed for one run must not
    be combinable with an unrelated run's identity/findings/checks at emission
    time. Before C1's provenance fields existed, nothing in `emit_review_
    readiness_v2` could detect this replay."""

    manifest_a, report_a = _fully_reviewed_manifest_and_report("app/a.py")
    synthesis_a = _synthesis(manifest=manifest_a, coverage_report=report_a)
    decision_from_run_a = compute_readiness_decision_v2(synthesis=synthesis_a, manifest=manifest_a, policies=_policies())
    assert decision_from_run_a.state is ReadinessStateV2.READY

    manifest_b, report_b = _fully_reviewed_manifest_and_report("app/b.py")
    synthesis_b = _synthesis(manifest=manifest_b, coverage_report=report_b)

    with pytest.raises(ReadinessEmissionError) as exc_info:
        _assemble_review_readiness_v2(
            decision=decision_from_run_a,
            findings=synthesis_b.findings,
            identity=manifest_b.identity,
            evaluated_identity=manifest_b.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[_green_check(manifest_b.identity.head_sha)],
        )

    assert exc_info.value.reason_code == READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2


def test_emit_review_readiness_rejects_a_decision_with_matching_run_id_but_divergent_manifest_hash() -> None:
    """Same run_id is not enough on its own -- manifest_hash must also match,
    otherwise a decision computed against a stale/tampered manifest could be
    replayed against a differently-shaped one that happens to share a run_id
    collision."""

    manifest, report = _fully_reviewed_manifest_and_report()
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    tampered_decision = dataclasses.replace(decision, manifest_hash="f" * 64)

    with pytest.raises(ReadinessEmissionError) as exc_info:
        _assemble_review_readiness_v2(
            decision=tampered_decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[_green_check(manifest.identity.head_sha)],
        )

    assert exc_info.value.reason_code == READINESS_EMISSION_DECISION_PROVENANCE_MISMATCH_REASON_V2


# -- `produce_review_readiness_v2` -- the real production path, Class A -----
#
# Every test below goes through the REAL, unpatched #201-C0 verifier. None
# reaches `ready` or `blocked_pipeline` -- see the module docstring and
# test_required_check_readiness_arch_v2.py's assert 7 for why no fixture in
# this codebase is permitted to make that happen today.


def _profile_bound_identity(tmp_path, *, required_checks: list[str]):
    from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import _write_target_profile

    profile_root = _write_target_profile(tmp_path, required_checks=required_checks)
    profile = load_target_profile_v2(profile_root)
    return profile_root, compute_profile_hash_v2(profile)


def test_produce_review_readiness_emits_manual_required_when_authority_is_not_established(tmp_path) -> None:
    import json

    from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
    from app.agent_review.contracts_v2 import ReadinessReasonV2, RunOriginV2
    from app.agent_review.review_readiness_emission_v2 import produce_review_readiness_v2
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import TOOLCHAIN_DIGEST, _snapshot_dict

    profile_root, profile_hash = _profile_bound_identity(tmp_path, required_checks=["pytest"])
    manifest, report = _fully_reviewed_manifest_and_report(profile_hash=profile_hash)
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
    assert decision.state is ReadinessStateV2.READY

    origin = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))

    readiness = produce_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[],
        provenance=[],
        origin=origin,
        snapshot=empty_snapshot,
        toolchain_digest=TOOLCHAIN_DIGEST,
        target_profile_root=str(profile_root),
    )

    assert readiness.state is ReadinessStateV2.MANUAL_REQUIRED
    assert ReadinessReasonV2.POLICY_FAILURE in readiness.reason_codes
    assert readiness.checks == []


def test_produce_review_readiness_propagates_a_forged_submission_uncaught(tmp_path) -> None:
    import json

    from app.agent_review.authoritative_check_policy_v2 import load_authoritative_check_policy_v2
    from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
    from app.agent_review.authoritative_producer_evidence_v2 import (
        INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2,
    )
    from app.agent_review.contracts_v2 import RunOriginV2
    from app.agent_review.required_check_provenance_v2 import (
        RequiredCheckProvenanceErrorV2,
        RequiredCheckProvenanceV2,
    )
    from app.agent_review.review_readiness_emission_v2 import produce_review_readiness_v2
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import (
        TOOLCHAIN_DIGEST,
        _hand_built_ci_pair,
        _snapshot_dict,
    )

    profile_root, profile_hash = _profile_bound_identity(tmp_path, required_checks=["pytest"])
    manifest, report = _fully_reviewed_manifest_and_report(profile_hash=profile_hash)
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())

    origin = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")
    loaded_policy = load_authoritative_check_policy_v2(profile_root)
    snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict(["pytest"])))
    result, provenance_dict = _hand_built_ci_pair(
        check_name="pytest", snapshot=snapshot, loaded_policy=loaded_policy,
        identity=manifest.identity, origin=origin, toolchain_digest=TOOLCHAIN_DIGEST,
    )
    provenance = RequiredCheckProvenanceV2.model_validate(provenance_dict)

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc_info:
        produce_review_readiness_v2(
            decision=decision,
            findings=synthesis.findings,
            identity=manifest.identity,
            evaluated_identity=manifest.identity,
            pr_state=PullRequestStateV2.OPEN,
            checks=[result],
            provenance=[provenance],
            origin=origin,
            snapshot=snapshot,
            toolchain_digest=TOOLCHAIN_DIGEST,
            target_profile_root=str(profile_root),
        )

    assert exc_info.value.reason_code == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2


def test_produce_review_readiness_emits_stale_even_when_the_trusted_profile_has_drifted_since(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed. `produce_review_
    readiness_v2` used to call `_verify_and_assess_required_checks_v2`
    UNCONDITIONALLY, before ever looking at `decision.state` -- so even an
    EMPTY submission alongside a genuinely `STALE` decision could be
    refused if `--target-profile` (a live, base/default checkout) had
    moved since `evaluated_identity` was computed, contradicting `_apply_
    required_check_assessment_v2`'s own documented guarantee that "STALE
    is sovereign... never consulted". Reproduced before the fix: an empty
    submission against a STALE decision, with the trusted profile rewritten
    to a DIFFERENT required-check set after `evaluated_identity.profile_
    hash` was computed, raised `RequiredCheckReadinessErrorV2` instead of
    emitting the STALE artifact. Fixed by moving the STALE short-circuit
    into `produce_review_readiness_v2` itself, before the `#201-C0` call --
    a STALE decision no longer touches the boundary at all."""

    import json

    from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
    from app.agent_review.contracts_v2 import ReadinessReasonV2, RunOriginV2
    from app.agent_review.review_readiness_emission_v2 import produce_review_readiness_v2
    from tests.agent_review.test_aiops_review_quality_gate_v2_cli import TOOLCHAIN_DIGEST, _snapshot_dict

    profile_root, profile_hash = _profile_bound_identity(tmp_path, required_checks=["pytest"])
    manifest, report = _fully_reviewed_manifest_and_report(profile_hash=profile_hash)
    synthesis = _synthesis(manifest=manifest, coverage_report=report)
    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(),
        stale_reason_codes=frozenset({ReadinessReasonV2.HEAD_MISMATCH}),
    )
    assert decision.state is ReadinessStateV2.STALE

    # The trusted profile "moves" after evaluated_identity.profile_hash was
    # computed -- rewrite it with a different required-check set.
    profile_file = profile_root / ".aiops" / "target-profile.v2.yaml"
    profile_file.write_text(
        profile_file.read_text().replace("    - pytest\n", "    - pytest\n    - mypy\n", 1), encoding="utf-8"
    )
    policy_file = profile_root / ".aiops" / "authoritative-checks.v2.yaml"
    policy_file.write_text(
        policy_file.read_text()
        + """  - check_name: mypy
    workflow_path: .github/workflows/authoritative-checks.yml
    job_name: authoritative mypy
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: mglpsw/aiops-orchestrator
      path: .github/workflows/authoritative-checks.yml
      sha: "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
    producer_workflow_ref: refs/heads/master
    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
""",
        encoding="utf-8",
    )

    origin = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))
    current_identity = manifest.identity.model_copy(update={"head_sha": "9" * 40})

    readiness = produce_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=current_identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[],
        provenance=[],
        origin=origin,
        snapshot=empty_snapshot,
        toolchain_digest=TOOLCHAIN_DIGEST,
        target_profile_root=str(profile_root),
    )

    assert readiness.state == ReadinessStateV2.STALE.value
    assert readiness.checks == []
