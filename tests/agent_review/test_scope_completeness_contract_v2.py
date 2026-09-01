"""`#200-G3` -- contract-level and end-to-end tests for the additive
``ScopeCompletenessV2`` representation: ``ReviewReadinessV2.scope``, the
``ReadinessReasonV2.SCOPE_INCOMPLETE`` reason, the readiness precedence
wiring in ``readiness_decision_v2``, and the terminal three-way predicate
``fragment_coverage_scope_and_checks_are_ready_v2``.

The central RED reproduction here (`test_the_false_ready_path_stays_closed_
end_to_end`) is the FULL-PIPELINE version of `#277`'s exact defect: fragment
coverage over the reviewable file is complete, but the diff also carries a
`src/pages/[id].tsx`-shaped path -- must never reach `ready`.
"""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import (
    READY_REQUIRES_SCOPE_COMPLETE_REASON_V2,
    ChunkCoverageV2,
    CoverageStateV2,
    FindingLifecycleRecordV2,
    PipelineAssessmentV2,
    PipelineDegradationCauseV2,
    PullRequestStateV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    ReviewReadinessV2,
    RunIdentityV2,
    ScopeCompletenessV2,
    TargetPoliciesV2,
    compute_run_id,
    evaluate_ready_preconditions_v2,
)
from app.agent_review.diff_acquisition_v2 import ParsedFileDiffV2, ParsedHunkV2
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.operational_scope_v2 import assess_changed_scope_v2
from app.agent_review.readiness_decision_v2 import (
    compute_readiness_decision_v2,
    fragment_coverage_scope_and_checks_are_ready_v2,
)
from app.agent_review.review_readiness_emission_v2 import _assemble_review_readiness_v2
from app.agent_review.run_fragment_coverage_v2 import (
    FragmentCoverageStatusV2,
    RunFragmentCoverageEntryV2,
    RunFragmentCoverageReportMaterialV2,
    RunFragmentCoverageReportV2,
    compute_coverage_report_sha256_v2,
)
from app.agent_review.synthesis_v2 import SynthesisResultV2
from tests.agent_review.test_operational_scope_v2 import _profile as _scope_profile


# -- fixture helpers -------------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 281,
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


def _synthesis(*, manifest: ManifestV2, coverage_report: RunFragmentCoverageReportV2) -> SynthesisResultV2:
    return SynthesisResultV2(
        run_id=manifest.run_id,
        evaluated_head_sha=manifest.identity.head_sha,
        coverage_report=coverage_report,
        findings=(),
        provenance={},
        limitations=(),
    )


def _green_check(head_sha: str) -> RequiredCheckResultV2:
    return RequiredCheckResultV2(
        check_name="pytest", required=True, deterministic=True, conclusion=RequiredCheckConclusionV2.SUCCESS, head_sha=head_sha
    )


def _hunk(seed: str) -> ParsedHunkV2:
    return ParsedHunkV2(
        old_start=1, old_lines=1, new_start=1, new_lines=1, diff_sha256=hashlib.sha256(seed.encode()).hexdigest(), diff_chars=8
    )


def _complete_scope(*paths: str) -> ScopeCompletenessV2:
    return ScopeCompletenessV2(
        complete=True,
        changed_paths=tuple(sorted(paths)),
        reviewable_paths=tuple(sorted(paths)),
        metadata_only_paths=(),
        unsupported_paths=(),
        must_review_blocked_paths=(),
    )


def _incomplete_scope(*, reviewable: tuple[str, ...], unsupported: tuple[str, ...]) -> ScopeCompletenessV2:
    return ScopeCompletenessV2(
        complete=False,
        changed_paths=tuple(sorted({*reviewable, *unsupported})),
        reviewable_paths=reviewable,
        metadata_only_paths=(),
        unsupported_paths=unsupported,
        must_review_blocked_paths=(),
    )


# -- ScopeCompletenessV2 own invariants -------------------------------------------


class TestScopeCompletenessV2Contract:
    def test_fully_complete_constructs(self) -> None:
        scope = _complete_scope("app/a.py")
        assert scope.complete is True

    def test_complete_flag_must_match_absence_of_unsupported(self) -> None:
        with pytest.raises(ValidationError):
            ScopeCompletenessV2(
                complete=True,
                changed_paths=("app/a.py",),
                reviewable_paths=(),
                metadata_only_paths=(),
                unsupported_paths=("app/a.py",),
                must_review_blocked_paths=(),
            )

    def test_partitions_must_be_disjoint(self) -> None:
        with pytest.raises(ValidationError):
            ScopeCompletenessV2(
                complete=True,
                changed_paths=("app/a.py",),
                reviewable_paths=("app/a.py",),
                metadata_only_paths=("app/a.py",),
                unsupported_paths=(),
                must_review_blocked_paths=(),
            )

    def test_partitions_must_exactly_cover_changed_paths(self) -> None:
        with pytest.raises(ValidationError):
            ScopeCompletenessV2(
                complete=True,
                changed_paths=("app/a.py", "app/b.py"),
                reviewable_paths=("app/a.py",),
                metadata_only_paths=(),
                unsupported_paths=(),
                must_review_blocked_paths=(),
            )

    def test_must_review_blocked_must_be_unreviewable(self) -> None:
        with pytest.raises(ValidationError):
            ScopeCompletenessV2(
                complete=True,
                changed_paths=("app/a.py",),
                reviewable_paths=("app/a.py",),
                metadata_only_paths=(),
                unsupported_paths=(),
                must_review_blocked_paths=("app/a.py",),
            )

    def test_pipeline_degradation_cause_now_accepts_scope_incomplete(self) -> None:
        PipelineDegradationCauseV2(
            reason_code=ReadinessReasonV2.SCOPE_INCOMPLETE,
            component="scope_completeness",
            detail="one path unrepresentable",
        )


# -- evaluate_ready_preconditions_v2: scope gating --------------------------------


def _ready_kwargs(**overrides):
    coverage = ChunkCoverageV2(
        status=CoverageStateV2.COMPLETE,
        expected_files=("app/a.py",),
        reviewed_files=("app/a.py",),
        partially_reviewed_files=(),
        missing_files=(),
        must_review_files=(),
        missing_must_review_files=(),
        degradation_causes=(),
    )
    kwargs = dict(
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check("2" * 40)],
        coverage=coverage,
        pipeline=PipelineAssessmentV2(degraded=False, causes=[]),
        reason_codes=[],
        blockers=[],
        findings=[],
        scope=None,
    )
    kwargs.update(overrides)
    return kwargs


class TestEvaluateReadyPreconditionsScopeGating:
    def test_scope_none_does_not_block_ready(self) -> None:
        assert evaluate_ready_preconditions_v2(**_ready_kwargs()) is None

    def test_scope_complete_does_not_block_ready(self) -> None:
        assert evaluate_ready_preconditions_v2(**_ready_kwargs(scope=_complete_scope("app/a.py"))) is None

    def test_scope_incomplete_blocks_ready(self) -> None:
        scope = _incomplete_scope(reviewable=("app/a.py",), unsupported=("assets/logo.png",))
        unmet = evaluate_ready_preconditions_v2(**_ready_kwargs(scope=scope))
        assert unmet == READY_REQUIRES_SCOPE_COMPLETE_REASON_V2

    def test_scope_complete_but_must_review_blocked_still_blocks_ready(self) -> None:
        scope = ScopeCompletenessV2(
            complete=True,
            changed_paths=("app/a.py", "app/renamed.py"),
            reviewable_paths=("app/a.py",),
            metadata_only_paths=("app/renamed.py",),
            unsupported_paths=(),
            must_review_blocked_paths=("app/renamed.py",),
        )
        unmet = evaluate_ready_preconditions_v2(**_ready_kwargs(scope=scope))
        assert unmet == READY_REQUIRES_SCOPE_COMPLETE_REASON_V2


# -- fragment_coverage_scope_and_checks_are_ready_v2 ------------------------------


class TestTerminalThreeWayPredicate:
    def _coverage(self, *, complete: bool = True) -> ChunkCoverageV2:
        return ChunkCoverageV2(
            status=CoverageStateV2.COMPLETE if complete else CoverageStateV2.PARTIAL,
            expected_files=("app/a.py",),
            reviewed_files=("app/a.py",) if complete else (),
            partially_reviewed_files=() if complete else ("app/a.py",),
            missing_files=(),
            must_review_files=(),
            missing_must_review_files=(),
            degradation_causes=(),
        )

    def test_all_three_axes_green_is_ready(self) -> None:
        assert fragment_coverage_scope_and_checks_are_ready_v2(
            coverage=self._coverage(),
            scope=_complete_scope("app/a.py"),
            checks=[_green_check("2" * 40)],
        ) is True

    def test_coverage_incomplete_alone_blocks(self) -> None:
        assert fragment_coverage_scope_and_checks_are_ready_v2(
            coverage=self._coverage(complete=False),
            scope=_complete_scope("app/a.py"),
            checks=[_green_check("2" * 40)],
        ) is False

    def test_scope_incomplete_alone_blocks_even_with_perfect_coverage_and_checks(self) -> None:
        """The exact relationship this primitive exists to make explicit:
        fragment coverage being complete is NOT sufficient on its own."""
        scope = _incomplete_scope(reviewable=("app/a.py",), unsupported=("assets/logo.png",))
        assert fragment_coverage_scope_and_checks_are_ready_v2(
            coverage=self._coverage(),
            scope=scope,
            checks=[_green_check("2" * 40)],
        ) is False

    def test_checks_not_green_alone_blocks(self) -> None:
        red_check = RequiredCheckResultV2(
            check_name="pytest", required=True, deterministic=True, conclusion=RequiredCheckConclusionV2.FAILURE, head_sha="2" * 40
        )
        assert fragment_coverage_scope_and_checks_are_ready_v2(
            coverage=self._coverage(),
            scope=_complete_scope("app/a.py"),
            checks=[red_check],
        ) is False

    def test_no_checks_at_all_blocks(self) -> None:
        assert fragment_coverage_scope_and_checks_are_ready_v2(
            coverage=self._coverage(),
            scope=_complete_scope("app/a.py"),
            checks=[],
        ) is False


# -- compute_readiness_decision_v2: scope wiring end to end -----------------------


class TestComputeReadinessDecisionScopeWiring:
    def test_scope_none_preserves_pre_existing_behavior(self) -> None:
        manifest, report = _fully_reviewed_manifest_and_report()
        synthesis = _synthesis(manifest=manifest, coverage_report=report)
        decision = compute_readiness_decision_v2(synthesis=synthesis, manifest=manifest, policies=_policies())
        assert decision.state is ReadinessStateV2.READY
        assert decision.scope is None
        assert ReadinessReasonV2.SCOPE_INCOMPLETE not in decision.reason_codes

    def test_scope_complete_stays_ready(self) -> None:
        manifest, report = _fully_reviewed_manifest_and_report()
        synthesis = _synthesis(manifest=manifest, coverage_report=report)
        scope = _complete_scope("app/a.py")
        decision = compute_readiness_decision_v2(
            synthesis=synthesis, manifest=manifest, policies=_policies(), scope=scope
        )
        assert decision.state is ReadinessStateV2.READY
        assert decision.scope is scope

    def test_scope_incomplete_moves_off_ready_per_policy(self) -> None:
        manifest, report = _fully_reviewed_manifest_and_report()
        synthesis = _synthesis(manifest=manifest, coverage_report=report)
        scope = _incomplete_scope(reviewable=("app/a.py",), unsupported=("assets/logo.png",))
        decision = compute_readiness_decision_v2(
            synthesis=synthesis,
            manifest=manifest,
            policies=_policies(coverage_failure_state="blocked_pipeline"),
            scope=scope,
        )
        assert decision.state is ReadinessStateV2.BLOCKED_PIPELINE
        assert ReadinessReasonV2.SCOPE_INCOMPLETE in decision.reason_codes
        assert any(c.reason_code is ReadinessReasonV2.SCOPE_INCOMPLETE for c in decision.pipeline.causes)
        assert decision.pipeline.degraded is True

    def test_scope_incomplete_with_manual_required_policy(self) -> None:
        manifest, report = _fully_reviewed_manifest_and_report()
        synthesis = _synthesis(manifest=manifest, coverage_report=report)
        scope = _incomplete_scope(reviewable=("app/a.py",), unsupported=("assets/logo.png",))
        decision = compute_readiness_decision_v2(
            synthesis=synthesis,
            manifest=manifest,
            policies=_policies(coverage_failure_state="manual_required"),
            scope=scope,
        )
        assert decision.state is ReadinessStateV2.MANUAL_REQUIRED
        assert ReadinessReasonV2.SCOPE_INCOMPLETE in decision.reason_codes

    def test_must_review_blocked_scope_moves_off_ready_even_though_scope_complete(self) -> None:
        manifest, report = _fully_reviewed_manifest_and_report()
        synthesis = _synthesis(manifest=manifest, coverage_report=report)
        scope = ScopeCompletenessV2(
            complete=True,
            changed_paths=("app/a.py", "app/renamed.py"),
            reviewable_paths=("app/a.py",),
            metadata_only_paths=("app/renamed.py",),
            unsupported_paths=(),
            must_review_blocked_paths=("app/renamed.py",),
        )
        decision = compute_readiness_decision_v2(
            synthesis=synthesis, manifest=manifest, policies=_policies(), scope=scope
        )
        assert decision.state is not ReadinessStateV2.READY
        assert ReadinessReasonV2.SCOPE_INCOMPLETE in decision.reason_codes


# -- full pipeline RED reproduction: the exact #277 false-READY shape ------------


def test_the_false_ready_path_stays_closed_end_to_end() -> None:
    """`#277`'s round-1 defect, reproduced through the REAL production
    pipeline this slice ships: `compute_readiness_decision_v2` ->
    `_assemble_review_readiness_v2` -> a real `ReviewReadinessV2`.

    `app/a.py` is fully reviewed (fragment coverage complete). The SAME
    diff also touched `src/pages/[id].tsx` -- a path this product cannot
    represent for review at all. Before `#200-G3`, `ChunkCoverageV2` (built
    only over `expected_files`, which never included the unrepresentable
    path) would report `complete`, and nothing would stop `ready`. With the
    scope authority wired in, the artifact must land on a non-`ready`
    state carrying `SCOPE_INCOMPLETE`, and constructing a `ready`
    `ReviewReadinessV2` from this exact material must be IMPOSSIBLE -- not
    merely "the composer chose not to", but rejected by the frozen
    contract's own validator if anything upstream tried.
    """

    manifest, report = _fully_reviewed_manifest_and_report(path="app/a.py")
    synthesis = _synthesis(manifest=manifest, coverage_report=report)

    file_diffs = [
        ParsedFileDiffV2(
            old_path="app/a.py",
            new_path="app/a.py",
            change_type="modified",
            is_binary=False,
            is_submodule=False,
            similarity_index=None,
            old_no_newline_at_eof=False,
            new_no_newline_at_eof=False,
            hunks=(_hunk("a"),),
            truncated=False,
        ),
        ParsedFileDiffV2(
            old_path="src/pages/[id].tsx",
            new_path="src/pages/[id].tsx",
            change_type="modified",
            is_binary=False,
            is_submodule=False,
            similarity_index=None,
            old_no_newline_at_eof=False,
            new_no_newline_at_eof=False,
            hunks=(_hunk("witness"),),
            truncated=False,
        ),
    ]
    scope_assessment = assess_changed_scope_v2(file_diffs=file_diffs, profile=_scope_profile())
    assert scope_assessment.reviewable_paths == ("app/a.py",)
    assert scope_assessment.unsupported_paths == ("src/pages/[id].tsx",)
    scope = scope_assessment.to_scope_completeness_v2()
    assert scope.complete is False

    decision = compute_readiness_decision_v2(
        synthesis=synthesis, manifest=manifest, policies=_policies(coverage_failure_state="blocked_pipeline"), scope=scope
    )
    # Coverage over the fragments it knows about IS complete -- proving the
    # defect is real: without scope wired in, nothing else would stop READY.
    assert decision.coverage.status is CoverageStateV2.COMPLETE
    assert decision.state is not ReadinessStateV2.READY
    assert decision.state is ReadinessStateV2.BLOCKED_PIPELINE
    assert ReadinessReasonV2.SCOPE_INCOMPLETE in decision.reason_codes

    readiness = _assemble_review_readiness_v2(
        decision=decision,
        findings=synthesis.findings,
        identity=manifest.identity,
        evaluated_identity=manifest.identity,
        pr_state=PullRequestStateV2.OPEN,
        checks=[_green_check(manifest.identity.head_sha)],
    )
    assert readiness.state is not ReadinessStateV2.READY
    assert readiness.scope is scope
    assert readiness.scope.complete is False

    # And: the frozen contract itself refuses to construct a `ready`
    # artifact from this exact scope, independent of the composer's
    # choice -- the strongest form of "impossible", not merely "avoided".
    with pytest.raises(ValidationError):
        ReviewReadinessV2(
            schema_id="agent-review.review-readiness.v2",
            schema_version=2,
            source="aiops-review-quality-gate",
            run_id=readiness.run_id,
            identity=readiness.identity,
            evaluated_run_id=readiness.evaluated_run_id,
            evaluated_identity=readiness.evaluated_identity,
            head_sha=readiness.head_sha,
            evaluated_head_sha=readiness.evaluated_head_sha,
            pr_state=PullRequestStateV2.OPEN,
            checks=readiness.checks,
            coverage=readiness.coverage,
            scope=scope,
            pipeline=PipelineAssessmentV2(degraded=False, causes=[]),
            state=ReadinessStateV2.READY,
            reason_codes=[],
            blockers=[],
            findings=[],
        )
