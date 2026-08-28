from __future__ import annotations

import json

import pytest

from app.agent_review.final_synthesizer import (
    FinalSynthesizerError,
    load_intake,
    render_final_review_markdown,
    synthesize_final_review,
    validate_chunk_results,
)
from app.agent_review.schemas import (
    ChunkParseFailure,
    ChunkResults,
    ChunkResultsCoverage,
    NormalizedFinding,
    NormalizedRisk,
    RedactionReport,
    RejectedFinding,
    SemanticChunk,
    SemanticChunkPlan,
)


FIXTURE_SECRET = "AGENTESCALA_PHASE4_FIXTURE_SECRET"
EXECUTION_CHUNK_IDS = [
    "chunk-01-primary_backend_logic",
    "chunk-02-api_schema_contract",
]
EXECUTION_GROUPS = ["primary_backend_logic", "api_schema_contract"]
EXECUTION_FILES = ["src/a.py", "src/b.py"]
EXECUTION_MISMATCH_REASONS = {
    "chunk_execution_expected_missing",
    "chunk_execution_foreign_id",
    "chunk_execution_duplicate_id",
    "chunk_execution_state_overlap",
}


def _finding(**overrides: object) -> NormalizedFinding:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "severity": "P2",
        "title": "Schedule validation skips inactive doctor guard",
        "file_path": "backend/services/schedule.py",
        "line_or_hunk": "L42-L48",
        "evidence": "The changed hunk removes the inactive doctor guard before schedule creation.",
        "source_artifact": "artifact:file-diff-context",
        "contract_id": "doctor-schedule-active",
        "impact": "Inactive doctors could be scheduled.",
        "confidence": "high",
        "dedupe_key": "schedule-active-doctor",
    }
    payload.update(overrides)
    return NormalizedFinding.model_validate(payload)


def _risk(**overrides: object) -> NormalizedRisk:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "source": "chunk_risk",
        "title": "Schedule validation needs follow-up",
        "reason": "The chunk did not include enough caller context.",
        "missing_evidence": "caller path",
        "suggested_validation": "Review local code intelligence.",
    }
    payload.update(overrides)
    return NormalizedRisk.model_validate(payload)


def _chunk_results(
    *,
    status: str = "complete",
    findings: list[NormalizedFinding] | None = None,
    risks: list[NormalizedRisk] | None = None,
    limitations: list[str] | None = None,
    model_reported_limitations: list[str] | None = None,
    rejected_findings: list[RejectedFinding] | None = None,
    chunks_parsed: list[str] | None = None,
    chunks_failed: list[ChunkParseFailure] | None = None,
    coverage: ChunkResultsCoverage | None = None,
) -> ChunkResults:
    return ChunkResults(
        target_repo="mglpsw/AgentEscala",
        chunk_plan_ref={"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        chunks_parsed=chunks_parsed if chunks_parsed is not None else ["chunk-01-primary_backend_logic"],
        chunks_failed=chunks_failed if chunks_failed is not None else [],
        confirmed_findings=findings if findings is not None else [],
        risks=risks if risks is not None else [],
        limitations=limitations if limitations is not None else [],
        model_reported_limitations=(
            model_reported_limitations if model_reported_limitations is not None else []
        ),
        rejected_findings=rejected_findings if rejected_findings is not None else [],
        coverage=coverage
        if coverage is not None
        else ChunkResultsCoverage(files_reviewed=["backend/services/schedule.py"]),
        status=status,  # type: ignore[arg-type]
    )


def _execution_plan(
    *,
    chunk_count: int = 1,
    chunk_coverages: list[str] | None = None,
    status: str = "complete",
    limitations: list[str] | None = None,
) -> SemanticChunkPlan:
    coverages = chunk_coverages if chunk_coverages is not None else ["complete"] * chunk_count
    chunks = [
        SemanticChunk(
            chunk_id=EXECUTION_CHUNK_IDS[index],
            semantic_group=EXECUTION_GROUPS[index],  # type: ignore[arg-type]
            order_index=index,
            files=[EXECUTION_FILES[index]],
            artifacts=["artifact:file-diff-context", "artifact:checks"],
            contracts=["target_profile:domain_contracts"],
            coverage=coverages[index],  # type: ignore[arg-type]
            prompt_budget_chars=24_000,
            estimated_chars=512,
            limitations=[],
        )
        for index in range(chunk_count)
    ]
    return SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=chunks,
        files_covered=EXECUTION_FILES[:chunk_count],
        limitations=limitations if limitations is not None else [],
        status=status,  # type: ignore[arg-type]
    )


def _execution_failures(chunk_ids: list[str]) -> list[ChunkParseFailure]:
    failures: list[ChunkParseFailure] = []
    for chunk_id in chunk_ids:
        if chunk_id in EXECUTION_CHUNK_IDS:
            semantic_group = EXECUTION_GROUPS[EXECUTION_CHUNK_IDS.index(chunk_id)]
        else:
            semantic_group = "tests"
        failures.append(
            ChunkParseFailure(
                chunk_id=chunk_id,
                semantic_group=semantic_group,  # type: ignore[arg-type]
                error_class="chunk_response_missing",
                message="chunk response file is missing",
            )
        )
    return failures


def _has_reason(limitations: list[str], reason: str) -> bool:
    return any(
        limitation == reason or limitation.startswith(f"{reason}:")
        for limitation in limitations
    )


@pytest.mark.parametrize(
    (
        "chunk_count",
        "chunks_parsed",
        "failed_ids",
        "expected_reasons",
    ),
    [
        pytest.param(
            1,
            [],
            [],
            ["chunks_parsed_missing", "chunk_execution_expected_missing"],
            id="no-parsed-or-failed",
        ),
        pytest.param(
            1,
            [EXECUTION_CHUNK_IDS[0], "chunk-99-foreign"],
            [],
            ["chunk_execution_foreign_id"],
            id="foreign-parsed",
        ),
        pytest.param(
            1,
            [EXECUTION_CHUNK_IDS[0]],
            ["chunk-99-foreign"],
            ["chunk_execution_foreign_id", "chunks_failed_present"],
            id="foreign-failed",
        ),
        pytest.param(
            1,
            [EXECUTION_CHUNK_IDS[0], EXECUTION_CHUNK_IDS[0]],
            [],
            ["chunk_execution_duplicate_id"],
            id="duplicate-parsed",
        ),
        pytest.param(
            2,
            [EXECUTION_CHUNK_IDS[0]],
            [EXECUTION_CHUNK_IDS[1], EXECUTION_CHUNK_IDS[1]],
            ["chunk_execution_duplicate_id", "chunks_failed_present"],
            id="duplicate-failed",
        ),
        pytest.param(
            1,
            [EXECUTION_CHUNK_IDS[0]],
            [EXECUTION_CHUNK_IDS[0]],
            ["chunk_execution_state_overlap", "chunks_failed_present"],
            id="parsed-and-failed-overlap",
        ),
        pytest.param(
            2,
            [EXECUTION_CHUNK_IDS[0]],
            [],
            ["chunk_execution_expected_missing"],
            id="two-expected-one-accounted",
        ),
    ],
)
def test_u2_synthesis_rejects_chunk_execution_identity_mismatches(
    chunk_count: int,
    chunks_parsed: list[str],
    failed_ids: list[str],
    expected_reasons: list[str],
) -> None:
    """Coverage claims cannot substitute for a coherent execution ledger."""
    plan = _execution_plan(chunk_count=chunk_count)
    results = _chunk_results(
        status="complete",
        chunks_parsed=chunks_parsed,
        chunks_failed=_execution_failures(failed_ids),
        coverage=ChunkResultsCoverage(
            files_reviewed=EXECUTION_FILES[:chunk_count],
        ),
    )

    review = synthesize_final_review(results, chunk_plan=plan)

    for reason in expected_reasons:
        assert _has_reason(review.limitations, reason)
    assert review.status == "degraded"
    assert review.verdict == "manual_review_required"


def test_u2_synthesis_keeps_valid_failed_chunk_cause_without_mismatch_reason() -> None:
    """An expected failed ID is accounted for, but still makes review partial."""
    plan = _execution_plan(chunk_count=2)
    failure = _execution_failures([EXECUTION_CHUNK_IDS[1]])
    results = _chunk_results(
        status="partial",
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        chunks_failed=failure,
        limitations=["chunk_response_missing"],
        coverage=ChunkResultsCoverage(
            files_reviewed=[EXECUTION_FILES[0]],
            files_not_reviewed=[EXECUTION_FILES[1]],
        ),
    )

    review = synthesize_final_review(results, chunk_plan=plan)

    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert "chunk_response_missing" in review.limitations
    assert "chunks_failed_present" in review.limitations
    assert "chunks_parsed_missing" not in review.limitations
    assert not any(
        _has_reason(review.limitations, reason)
        for reason in EXECUTION_MISMATCH_REASONS
    )


def test_u2_synthesis_zero_parsed_without_plan_is_not_positive() -> None:
    results = _chunk_results(
        status="complete",
        chunks_parsed=[],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )

    review = synthesize_final_review(results)

    assert "chunks_parsed_missing" in review.limitations
    assert review.status == "degraded"
    assert review.verdict == "manual_review_required"
    assert not _has_reason(review.limitations, "chunk_execution_expected_missing")


@pytest.mark.parametrize(
    (
        "plan_status",
        "chunk_coverage",
        "plan_limitations",
        "expected_reason",
        "reviewed",
        "partial",
        "not_reviewed",
    ),
    [
        pytest.param(
            "complete",
            "partial",
            [],
            "chunk_plan_status_partial",
            [],
            [EXECUTION_FILES[0]],
            [],
            id="nested-partial",
        ),
        pytest.param(
            "complete",
            "degraded",
            [],
            "chunk_plan_status_degraded",
            [],
            [],
            [EXECUTION_FILES[0]],
            id="nested-degraded",
        ),
        pytest.param(
            "complete",
            "complete",
            ["file_context_fallback_used"],
            "chunk_plan_status_partial",
            [EXECUTION_FILES[0]],
            [],
            [],
            id="fallback-derived-partial",
        ),
        pytest.param(
            "failed",
            "complete",
            [],
            "chunk_plan_status_failed",
            [EXECUTION_FILES[0]],
            [],
            [],
            id="explicit-failed",
        ),
    ],
)
def test_u2_synthesis_uses_nested_and_explicit_plan_degradation(
    plan_status: str,
    chunk_coverage: str,
    plan_limitations: list[str],
    expected_reason: str,
    reviewed: list[str],
    partial: list[str],
    not_reviewed: list[str],
) -> None:
    plan = _execution_plan(
        chunk_coverages=[chunk_coverage],
        status=plan_status,
        limitations=plan_limitations,
    )
    results = _chunk_results(
        status="complete",
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )

    review = synthesize_final_review(results, chunk_plan=plan)

    assert review.limitations.count(expected_reason) == 1
    assert review.coverage.files_reviewed == reviewed
    assert review.coverage.files_partial == partial
    assert review.coverage.files_not_reviewed == not_reviewed
    assert review.status in {"partial", "degraded"}
    assert review.verdict == "manual_review_required"


def test_u2_synthesis_exact_execution_with_informational_note_is_nonblocking() -> None:
    plan = _execution_plan(
        chunk_count=2,
        limitations=["intake_schema_id_missing"],
    )
    results = _chunk_results(
        status="complete",
        chunks_parsed=EXECUTION_CHUNK_IDS.copy(),
        limitations=["intake_schema_id_missing"],
        coverage=ChunkResultsCoverage(files_reviewed=EXECUTION_FILES.copy()),
    )

    review = synthesize_final_review(results, chunk_plan=plan)

    assert review.status == "complete"
    assert review.verdict == "approve_with_minor_notes"
    assert review.coverage.files_reviewed == EXECUTION_FILES
    assert review.limitations.count("intake_schema_id_missing") == 1
    assert "chunks_parsed_missing" not in review.limitations
    assert not any(
        _has_reason(review.limitations, reason)
        for reason in EXECUTION_MISMATCH_REASONS
    )


def test_synthesizer_generates_approved_for_complete_review_without_findings_or_risks() -> None:
    review = synthesize_final_review(_chunk_results())

    assert review.status == "complete"
    assert review.verdict == "approved"
    assert review.coverage.files_reviewed == ["backend/services/schedule.py"]
    assert review.coverage.files_partial == []
    assert review.coverage.files_not_reviewed == []
    assert review.confirmed_findings == []
    assert review.risks == []


def test_reliable_p1_generates_changes_requested_even_when_partial() -> None:
    failure = ChunkParseFailure(
        chunk_id="chunk-02-tests",
        semantic_group="tests",
        error_class="chunk_response_missing",
        message="chunk response file is missing",
    )
    review = synthesize_final_review(
        _chunk_results(
            status="partial",
            findings=[_finding(severity="P1")],
            chunks_failed=[failure],
        )
    )

    assert review.status == "partial"
    assert review.verdict == "changes_requested"
    assert review.confirmed_findings[0].severity == "P1"
    assert "chunk_results_status_partial" in review.limitations
    assert "chunks_failed_present" in review.limitations


def test_untrusted_p1_generates_manual_review_required() -> None:
    review = synthesize_final_review(
        _chunk_results(
            findings=[
                _finding(
                    severity="P1",
                    line_or_hunk=None,
                    source_artifact=None,
                )
            ]
        )
    )

    assert review.verdict == "manual_review_required"
    assert review.confirmed_findings[0].severity == "P1"


def test_p2_generates_required_followup_and_p3_generates_minor_notes() -> None:
    p2_review = synthesize_final_review(_chunk_results(findings=[_finding(severity="P2")]))
    p3_review = synthesize_final_review(_chunk_results(findings=[_finding(severity="P3")]))

    assert p2_review.verdict == "approve_with_required_followup"
    assert p3_review.verdict == "approve_with_minor_notes"


def test_risks_generate_followup_or_manual_review_based_on_status() -> None:
    complete_review = synthesize_final_review(_chunk_results(risks=[_risk()]))
    partial_review = synthesize_final_review(_chunk_results(status="partial", risks=[_risk()]))

    assert complete_review.verdict == "approve_with_required_followup"
    assert partial_review.verdict == "manual_review_required"


@pytest.mark.parametrize(
    ("coverage", "incomplete_field", "expected_path"),
    [
        pytest.param(
            ChunkResultsCoverage(
                files_reviewed=["src/reviewed.py"],
                files_partial=["src/partial.py"],
            ),
            "files_partial",
            "src/partial.py",
            id="partial",
        ),
        pytest.param(
            ChunkResultsCoverage(
                files_reviewed=["src/reviewed.py"],
                files_not_reviewed=["src/not_reviewed.py"],
            ),
            "files_not_reviewed",
            "src/not_reviewed.py",
            id="not-reviewed",
        ),
    ],
)
def test_u2_normalized_incomplete_coverage_overrides_mutated_complete_status(
    coverage: ChunkResultsCoverage,
    incomplete_field: str,
    expected_path: str,
) -> None:
    chunk_results = _chunk_results(status="partial", coverage=coverage)
    chunk_results.status = "complete"

    review = synthesize_final_review(chunk_results)

    assert getattr(review.coverage, incomplete_field) == [expected_path]
    assert review.inputs["chunk_results"]["status"] == "complete"
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert review.confirmed_findings == []
    assert review.risks == []


@pytest.mark.parametrize(
    ("reason", "coverage"),
    [
        pytest.param(
            "coverage_expected_files_missing",
            ChunkResultsCoverage(files_not_reviewed=["src/omitted.py"]),
            id="omitted-expected",
        ),
        pytest.param(
            "coverage_file_in_multiple_states",
            ChunkResultsCoverage(files_partial=["src/overlap.py"]),
            id="normalized-overlap",
        ),
        pytest.param(
            "coverage_file_not_in_chunk:chunk-01-primary_backend_logic",
            ChunkResultsCoverage(files_reviewed=["src/reviewed.py"]),
            id="foreign-path",
        ),
    ],
)
def test_u2_recoverable_coverage_errors_stay_partial_and_manual(
    reason: str,
    coverage: ChunkResultsCoverage,
) -> None:
    review = synthesize_final_review(
        _chunk_results(status="complete", limitations=[reason], coverage=coverage)
    )

    assert reason in review.limitations
    assert review.inputs["chunk_results"]["status"] == "complete"
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"


def test_u2_synthesis_reuses_worst_state_for_direct_overlapping_results() -> None:
    review = synthesize_final_review(
        _chunk_results(
            status="complete",
            coverage=ChunkResultsCoverage(
                files_reviewed=["src/a.py"],
                files_partial=["src/a.py"],
            ),
        )
    )

    assert review.coverage.files_reviewed == []
    assert review.coverage.files_partial == ["src/a.py"]
    assert review.coverage.files_not_reviewed == []
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert review.limitations.count("coverage_file_in_multiple_states") == 1


def test_u2_mutated_complete_status_cannot_hide_self_contained_gap() -> None:
    reviewed = "src/a.py"
    not_reviewed = "src/b.py"
    chunk_results = _chunk_results(
        status="degraded",
        coverage=ChunkResultsCoverage(
            files_reviewed=[reviewed],
            files_not_reviewed=[not_reviewed],
        ),
    )
    chunk_results.status = "complete"

    review = synthesize_final_review(chunk_results)

    assert review.inputs["chunk_results"]["status"] == "complete"
    assert review.inputs["chunk_plan"] == {"provided": False}
    assert review.coverage.files_reviewed == [reviewed]
    assert review.coverage.files_partial == []
    assert review.coverage.files_not_reviewed == [not_reviewed]
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"


def test_u2_forged_reviewed_results_cannot_promote_plan_not_covered_file() -> None:
    reviewed = "src/a.py"
    capped = "src/b.py"
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=[reviewed],
        artifacts=["artifact:file-diff-context", "artifact:checks"],
        contracts=["target_profile:domain_contracts"],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    chunk_plan = SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=[reviewed],
        files_not_covered=[capped],
        status="complete",
    )
    forged_results = _chunk_results(
        status="complete",
        coverage=ChunkResultsCoverage(files_reviewed=[reviewed, capped]),
    )

    review = synthesize_final_review(forged_results, chunk_plan=chunk_plan)

    assert review.coverage.files_reviewed == [reviewed]
    assert review.coverage.files_partial == []
    assert review.coverage.files_not_reviewed == [capped]
    assert review.coverage.expected_files == [reviewed, capped]
    assert review.coverage.missing_expected_files == []
    assert review.coverage.extra_reported_files == []
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert "coverage_file_in_multiple_states" not in review.limitations


def test_u2_forged_results_cannot_review_plan_file_without_a_chunk() -> None:
    reviewed = "src/a.py"
    unassigned = "src/b.py"
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=[reviewed],
        artifacts=["artifact:file-diff-context", "artifact:checks"],
        contracts=["target_profile:domain_contracts"],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    chunk_plan = SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=[reviewed, unassigned],
        status="complete",
    )
    forged_results = _chunk_results(
        status="complete",
        coverage=ChunkResultsCoverage(files_reviewed=[reviewed, unassigned]),
    )

    review = synthesize_final_review(forged_results, chunk_plan=chunk_plan)

    assert review.coverage.files_reviewed == [reviewed]
    assert review.coverage.files_partial == []
    assert review.coverage.files_not_reviewed == [unassigned]
    assert review.coverage.expected_files == [reviewed, unassigned]
    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert "coverage_expected_files_missing" in review.limitations


@pytest.mark.parametrize("blocker_severity", ["P0", "P1"])
def test_u2_reliable_blocker_and_risk_survive_incomplete_coverage(
    blocker_severity: str,
) -> None:
    blocker = _finding(
        severity=blocker_severity,
        title=f"{blocker_severity} reliable blocker",
        dedupe_key=f"reliable-{blocker_severity}",
    )
    followup = _finding(
        severity="P2",
        title="P2 follow-up",
        dedupe_key="p2-follow-up",
    )
    risk = _risk(title="Coverage follow-up risk")
    review = synthesize_final_review(
        _chunk_results(
            status="complete",
            findings=[followup, blocker],
            risks=[risk],
            coverage=ChunkResultsCoverage(
                files_reviewed=["src/a.py"],
                files_not_reviewed=["src/b.py"],
            ),
        )
    )

    assert review.status == "partial"
    assert review.verdict == "changes_requested"
    assert [finding.severity for finding in review.confirmed_findings] == [
        blocker_severity,
        "P2",
    ]
    assert [finding.title for finding in review.confirmed_findings] == [
        f"{blocker_severity} reliable blocker",
        "P2 follow-up",
    ]
    assert len(review.risks) == 1
    assert review.risks[0].title == "Coverage follow-up risk"
    assert review.risks[0].source == "chunk_risk"
    assert review.coverage.files_not_reviewed == ["src/b.py"]


def test_degraded_chunk_results_keeps_explicit_limitation() -> None:
    review = synthesize_final_review(
        _chunk_results(status="degraded", limitations=["chunk_response_json_invalid"])
    )

    assert review.status == "degraded"
    assert review.verdict == "manual_review_required"
    assert "chunk_response_json_invalid" in review.limitations
    assert "chunk_results_status_degraded" in review.limitations


def test_invalid_chunk_results_schema_fails_closed() -> None:
    with pytest.raises(FinalSynthesizerError) as exc_info:
        validate_chunk_results({"schema_id": "wrong", "schema_version": 1})

    assert exc_info.value.error_class == "chunk_results_invalid"


def test_dedupe_by_dedupe_key_aggregates_source_chunks() -> None:
    review = synthesize_final_review(
        _chunk_results(
            findings=[
                _finding(dedupe_key="same-key", chunk_id="chunk-01-primary_backend_logic"),
                _finding(dedupe_key="same-key", chunk_id="chunk-02-api_schema_contract"),
            ],
            chunks_parsed=["chunk-01-primary_backend_logic", "chunk-02-api_schema_contract"],
        )
    )

    assert len(review.confirmed_findings) == 1
    assert review.confirmed_findings[0].dedupe_key == "same-key"
    assert review.confirmed_findings[0].source_chunks == [
        "chunk-01-primary_backend_logic",
        "chunk-02-api_schema_contract",
    ]


def test_structural_dedupe_without_writing_dedupe_key() -> None:
    finding = _finding(dedupe_key=None)
    review = synthesize_final_review(_chunk_results(findings=[finding, finding.model_copy()]))

    assert len(review.confirmed_findings) == 1
    assert review.confirmed_findings[0].dedupe_key is None


def test_synthesizer_dedupes_risks_by_dedupe_key_and_aggregates_source_chunks() -> None:
    review = synthesize_final_review(
        _chunk_results(
            risks=[
                _risk(
                    source="downgraded_finding",
                    chunk_id="chunk-01-primary_backend_logic",
                    semantic_group="primary_backend_logic",
                    reason="missing_required_evidence",
                    missing_evidence="source_artifact",
                    evidence="The first downgraded candidate had incomplete source context.",
                    dedupe_key="same-risk",
                ),
                _risk(
                    source="downgraded_finding",
                    chunk_id="chunk-02-api_schema_contract",
                    semantic_group="api_schema_contract",
                    reason="missing_required_evidence:line_or_hunk",
                    missing_evidence="line_or_hunk",
                    evidence="The second downgraded candidate used different wording.",
                    dedupe_key="same-risk",
                ),
            ],
            chunks_parsed=["chunk-01-primary_backend_logic", "chunk-02-api_schema_contract"],
        )
    )

    assert len(review.risks) == 1
    assert review.risks[0].dedupe_key == "same-risk"
    assert review.risks[0].source_chunks == [
        "chunk-01-primary_backend_logic",
        "chunk-02-api_schema_contract",
    ]
    assert review.risks[0].semantic_groups == ["primary_backend_logic", "api_schema_contract"]


def test_risk_is_not_transformed_into_confirmed_finding() -> None:
    review = synthesize_final_review(_chunk_results(risks=[_risk()]))

    assert review.confirmed_findings == []
    assert len(review.risks) == 1


def test_rejected_findings_are_summary_only_without_evidence_payload() -> None:
    rejected = RejectedFinding(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        reason="missing_required_evidence",
        title="Missing inactive doctor evidence",
        severity="P1",
        file_path="backend/services/schedule.py",
        evidence=f"token={FIXTURE_SECRET} should not appear in summary",
    )
    review = synthesize_final_review(_chunk_results(rejected_findings=[rejected]))
    rendered = review.model_dump_json()

    assert review.rejected_summary.total == 1
    assert review.rejected_summary.by_reason == {"missing_required_evidence": 1}
    assert review.rejected_summary.sample_titles == ["Missing inactive doctor evidence"]
    assert FIXTURE_SECRET not in rendered
    assert "should not appear in summary" not in rendered


def test_markdown_respects_limits_and_outputs_are_sanitized(tmp_path) -> None:  # noqa: ANN001
    absolute_file = tmp_path / "AgentEscala" / "backend" / "services" / "schedule.py"
    findings = [
        _finding(
            severity="P2",
            title=f"Finding {index}",
            file_path=str(absolute_file),
            evidence=f"token={FIXTURE_SECRET} appears in fixture evidence {index}.",
            dedupe_key=f"finding-{index}",
        )
        for index in range(2)
    ]
    risks = [_risk(title=f"Risk {index}", reason=f"Reason {index}") for index in range(2)]
    review = synthesize_final_review(_chunk_results(findings=findings, risks=risks))
    markdown = render_final_review_markdown(review, max_findings=1, max_risks=1)
    rendered = review.model_dump_json() + markdown

    assert "Finding 0" in markdown
    assert "Finding 1" not in markdown
    assert "Mais 1 achado" in markdown
    assert "Risk 0" in markdown
    assert "Risk 1" not in markdown
    assert FIXTURE_SECRET not in rendered
    assert str(tmp_path) not in rendered
    assert "[LOCAL_PATH_REDACTED]" in rendered


def test_optional_chunk_plan_adds_limitation_for_missing_expected_coverage() -> None:
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=["backend/services/schedule.py", "backend/services/doctor.py"],
        artifacts=[],
        contracts=[],
        depends_on=[],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    chunk_plan = SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=chunk.files,
        status="complete",
    )
    review = synthesize_final_review(_chunk_results(), chunk_plan=chunk_plan)

    assert review.status == "partial"
    assert review.verdict == "manual_review_required"
    assert "coverage_expected_files_missing" in review.limitations
    assert review.coverage.missing_expected_files == ["backend/services/doctor.py"]


def test_redaction_report_not_safe_for_llm_degrades_review() -> None:
    report = RedactionReport(output_safe_for_llm=False)
    review = synthesize_final_review(_chunk_results(), redaction_report=report)

    assert review.status == "degraded"
    assert review.verdict == "manual_review_required"
    assert "redaction_report_not_safe_for_llm" in review.limitations


def _minimal_intake_raw(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "target_repo": "mglpsw/aiops-orchestrator",
        "target_profile": {},
        "artifacts": {},
        "artifact_status": [],
        "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
        "status": "complete",
    }
    payload.update(overrides)
    return payload


def test_load_intake_accepts_the_modern_schema_pair(tmp_path) -> None:  # noqa: ANN001
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version=1)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    intake = load_intake(path)

    assert intake.schema_id == "agent-review.intake.v1"
    assert intake.schema_version == 1


def test_load_intake_accepts_the_legacy_schema_pair_during_the_compatibility_window(tmp_path) -> None:  # noqa: ANN001
    raw = _minimal_intake_raw(schema_version="agent-review.intake.v1")
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    intake = load_intake(path)

    assert intake.status == "complete"


def test_load_intake_rejects_an_unsupported_integer_schema_version(tmp_path) -> None:  # noqa: ANN001
    """Issue #146 thread 7 -- schema_version=2 alongside a correct schema_id
    must be rejected. Before the fix, this loader's own weaker inline check
    (``schema_version != INTAKE_SCHEMA and schema_id != INTAKE_SCHEMA``)
    accepted any schema_version once schema_id matched, letting version 2
    through."""

    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version=2)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(FinalSynthesizerError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_an_integer_schema_version_without_a_schema_id(tmp_path) -> None:  # noqa: ANN001
    """Codex review of PR #156 -- schema_version=2 with schema_id absent
    entirely must also be rejected; the schema-less compatibility form only
    tolerates the descriptive-string schema_version, never a bare integer."""

    raw = _minimal_intake_raw(schema_version=2)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(FinalSynthesizerError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_the_hybrid_schema_id_with_descriptive_schema_version(tmp_path) -> None:  # noqa: ANN001
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version="agent-review.intake.v1")
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(FinalSynthesizerError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_an_unknown_schema_id(tmp_path) -> None:  # noqa: ANN001
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v2", schema_version=1)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(FinalSynthesizerError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


# ── AgentEscala#675 / Fix A: provenance at synthesis ─────────────────────────


def test_model_reported_limitations_are_propagated_but_never_authoritative() -> None:
    """The model's self-report must reach the target (so a comment can show
    it) while contributing nothing to status, verdict, or the deterministic
    limitation list."""
    review = synthesize_final_review(
        _chunk_results(
            model_reported_limitations=[
                "contracts_context_not_relevant:The contracts context was not relevant here.",
            ]
        )
    )

    assert review.model_reported_limitations == [
        "contracts_context_not_relevant:The contracts context was not relevant here.",
    ]
    assert review.limitations == []
    # A clean, fully covered review stays clean.
    assert review.status == "complete"
    assert review.verdict == "approved"
    assert review.counts.limitations_total == 0


def test_model_cannot_degrade_the_review_by_naming_a_critical_limitation() -> None:
    """Regression found while auditing #675: `_has_critical_limitation` tests
    membership in `CRITICAL_LIMITATIONS` by exact string match over a flat
    list that used to include model text. A model that emitted the bare code
    `coverage_missing` -- with no detail, so no `:` suffix to tell it apart --
    landed verbatim in that list and drove `status` to `degraded` and the
    verdict to `manual_review_required`, on a review whose coverage was in
    fact complete."""
    review = synthesize_final_review(
        _chunk_results(model_reported_limitations=["coverage_missing"])
    )

    assert review.status == "complete"
    assert review.verdict == "approved"
    assert "coverage_missing" not in review.limitations


def test_markdown_renders_deterministic_and_model_reported_separately() -> None:
    """Acceptance criterion #8: the published comment must distinguish a
    finding from a limitation from an observation. Rendering both namespaces
    into one bullet list made a model sentence look like an engine reason
    code -- and, because the list is truncated at LIMITATION_MD_LIMIT, model
    prose could push deterministic codes out of the comment entirely."""
    review = synthesize_final_review(
        _chunk_results(
            status="partial",
            limitations=["chunk_budget_exceeded:primary_backend_logic"],
            model_reported_limitations=["diff_scope:I only saw one file."],
        )
    )

    markdown = render_final_review_markdown(review)

    assert "chunk_budget_exceeded:primary_backend_logic" in markdown
    assert "diff_scope:I only saw one file." in markdown
    # The two live under distinct headings, and the deterministic one comes first.
    assert "## Limitações" in markdown
    assert "## Observações do modelo" in markdown
    assert markdown.index("## Limitações") < markdown.index("## Observações do modelo")


def test_deterministic_limitations_are_never_crowded_out_by_model_prose() -> None:
    """The markdown limitation list is capped. That cap must apply to each
    namespace on its own, so a chatty model can never evict a deterministic
    reason code from the comment."""
    review = synthesize_final_review(
        _chunk_results(
            status="partial",
            limitations=["chunk_budget_exceeded:primary_backend_logic"],
            model_reported_limitations=[f"noise_{index}:padding" for index in range(30)],
        )
    )

    markdown = render_final_review_markdown(review)

    assert "chunk_budget_exceeded:primary_backend_logic" in markdown
