from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_review.chunk_result_parser import (
    _normalize_plan_coverage_partition,
    parse_chunk_results,
)
from app.agent_review.final_synthesizer import synthesize_final_review
from app.agent_review.quality_gate import (
    FinalReviewDocument,
    QualityGateError,
    evaluate_review_quality_gate,
    load_intake,
    validate_final_review_document,
)
from app.agent_review.schemas import (
    ChunkParseFailure,
    ChunkResults,
    ChunkResultsCoverage,
    ReviewIntake,
    ReviewQualityGate,
    SemanticChunk,
    SemanticChunkPlan,
)


FIXTURE_SECRET = "AGENTESCALA_PHASE5A_GATE_SECRET"
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


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "severity": "P1",
        "title": "Schedule validation skips inactive doctor guard",
        "file_path": "backend/services/schedule.py",
        "line_or_hunk": "L42-L48",
        "evidence": "The changed hunk removes the inactive doctor guard before schedule creation.",
        "source_artifact": "artifact:file-diff-context",
        "contract_id": "doctor-schedule-active",
        "impact": "Inactive doctors could be scheduled.",
        "confidence": "high",
        "dedupe_key": "schedule-active-doctor",
        "source_chunks": ["chunk-01-primary_backend_logic"],
        "semantic_groups": ["primary_backend_logic"],
    }
    payload.update(overrides)
    return payload


def _risk(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "source": "chunk_risk",
        "title": "Schedule validation needs follow-up",
        "reason": "Caller context was not available.",
        "source_chunks": ["chunk-01-primary_backend_logic"],
        "semantic_groups": ["primary_backend_logic"],
    }
    payload.update(overrides)
    return payload


def _final_review(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.final-review.v1",
        "source": "aiops-review-synthesize",
        "target_repo": "mglpsw/AgentEscala",
        "status": "complete",
        "verdict": "approved",
        "summary": "Synthetic final review fixture.",
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "rejected_summary": {"total": 0, "by_reason": {}, "sample_titles": []},
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
            "expected_files": [],
            "missing_expected_files": [],
            "extra_reported_files": [],
            "comparison_available": False,
        },
        "counts": {
            "confirmed_findings_total": 0,
            "findings_by_severity": {},
            "risks_total": 0,
            "risks_by_source": {},
            "rejected_findings_total": 0,
            "rejected_findings_by_reason": {},
            "limitations_total": 0,
            "chunks_parsed": 1,
            "chunks_failed": 0,
        },
        "inputs": {},
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _chunk_results(
    *,
    status: str = "complete",
    chunks_parsed: list[str] | None = None,
    chunks_failed: list[ChunkParseFailure] | None = None,
    limitations: list[str] | None = None,
    coverage: ChunkResultsCoverage | None = None,
) -> ChunkResults:
    return ChunkResults(
        target_repo="mglpsw/AgentEscala",
        chunk_plan_ref={"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        chunks_parsed=chunks_parsed if chunks_parsed is not None else ["chunk-01-primary_backend_logic"],
        chunks_failed=chunks_failed if chunks_failed is not None else [],
        confirmed_findings=[],
        risks=[],
        limitations=limitations if limitations is not None else [],
        rejected_findings=[],
        coverage=coverage
        if coverage is not None
        else ChunkResultsCoverage(files_reviewed=["backend/services/schedule.py"]),
        status=status,  # type: ignore[arg-type]
        created_at="2026-06-02T00:00:00Z",
    )


def _gate(final_review: dict[str, object], chunk_results: ChunkResults | None = None, **kwargs: object):
    return evaluate_review_quality_gate(
        validate_final_review_document(final_review),
        chunk_results if chunk_results is not None else _chunk_results(),
        **kwargs,
    )


def _coverage(
    *,
    reviewed: list[str] | None = None,
    partial: list[str] | None = None,
    not_reviewed: list[str] | None = None,
) -> dict[str, object]:
    reviewed = reviewed if reviewed is not None else []
    partial = partial if partial is not None else []
    not_reviewed = not_reviewed if not_reviewed is not None else []
    return {
        "files_reviewed": reviewed,
        "files_partial": partial,
        "files_not_reviewed": not_reviewed,
        "expected_files": [*reviewed, *partial, *not_reviewed],
        "missing_expected_files": [],
        "extra_reported_files": [],
        "comparison_available": True,
    }


def _intake_with_must_review(file_path: str) -> ReviewIntake:
    return ReviewIntake(
        target_repo="mglpsw/AgentEscala",
        target_profile={},
        artifacts={
            "file-diff-context": {
                "content": {
                    "coverage_requirements": {
                        "must_review_files": [file_path],
                    }
                }
            }
        },
        artifact_status=[],
        redaction_summary={"schema_version": "agent-review.redaction-report.v1"},
        status="complete",
    )


def _chunk_plan_for_gate(
    *,
    reviewed_file: str,
    status: str = "complete",
    files_not_covered: list[str] | None = None,
) -> SemanticChunkPlan:
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=[reviewed_file],
        artifacts=["artifact:file-diff-context", "artifact:checks"],
        contracts=["target_profile:domain_contracts"],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    return SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=[reviewed_file],
        files_not_covered=files_not_covered if files_not_covered is not None else [],
        status=status,  # type: ignore[arg-type]
    )


def _chunk_plan_with_partition(
    *,
    chunk_files: list[str],
    files_covered: list[str],
    files_partially_covered: list[str] | None = None,
    files_not_covered: list[str] | None = None,
) -> SemanticChunkPlan:
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=chunk_files,
        artifacts=["artifact:file-diff-context", "artifact:checks"],
        contracts=["target_profile:domain_contracts"],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    return SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=files_covered,
        files_partially_covered=(
            files_partially_covered if files_partially_covered is not None else []
        ),
        files_not_covered=files_not_covered if files_not_covered is not None else [],
        status="complete",
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


def _write_round_trip_response(
    responses_dir: Path,
    *,
    plan: SemanticChunkPlan,
    partial: bool,
) -> None:
    chunk = plan.chunks[0]
    coverage_notes = (
        {
            "files_reviewed": [chunk.files[0]],
            "files_partial": [chunk.files[1]],
        }
        if partial
        else {"files_reviewed": chunk.files}
    )
    payload = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "coverage_notes": coverage_notes,
    }
    (responses_dir / f"{chunk.chunk_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
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
def test_u2_gate_rejects_chunk_execution_identity_mismatches(
    chunk_count: int,
    chunks_parsed: list[str],
    failed_ids: list[str],
    expected_reasons: list[str],
    critical_pr: bool,
) -> None:
    plan = _execution_plan(chunk_count=chunk_count)
    results = _chunk_results(
        status="complete",
        chunks_parsed=chunks_parsed,
        chunks_failed=_execution_failures(failed_ids),
        coverage=ChunkResultsCoverage(
            files_reviewed=EXECUTION_FILES[:chunk_count],
        ),
    )

    gate = _gate(
        _final_review(
            coverage=_coverage(reviewed=EXECUTION_FILES[:chunk_count]),
        ),
        results,
        chunk_plan=plan,
        critical_pr=critical_pr,
    )

    for reason in expected_reasons:
        assert _has_reason(gate.limitations, reason)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_gate_keeps_valid_failed_chunk_cause_without_mismatch_reason(
    critical_pr: bool,
) -> None:
    plan = _execution_plan(chunk_count=2)
    results = _chunk_results(
        status="partial",
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        chunks_failed=_execution_failures([EXECUTION_CHUNK_IDS[1]]),
        limitations=["chunk_response_missing"],
        coverage=ChunkResultsCoverage(
            files_reviewed=[EXECUTION_FILES[0]],
            files_not_reviewed=[EXECUTION_FILES[1]],
        ),
    )

    gate = _gate(
        _final_review(
            status="partial",
            verdict="manual_review_required",
            limitations=["chunk_response_missing", "chunks_failed_present"],
            coverage=_coverage(
                reviewed=[EXECUTION_FILES[0]],
                not_reviewed=[EXECUTION_FILES[1]],
            ),
        ),
        results,
        chunk_plan=plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "chunk_response_missing" in gate.limitations
    assert "chunks_failed_present" in gate.limitations
    assert f"chunk_failed:{EXECUTION_CHUNK_IDS[1]}:chunk_response_missing" in gate.warnings
    assert "chunks_parsed_missing" not in gate.limitations
    assert not any(
        _has_reason(gate.limitations, reason)
        for reason in EXECUTION_MISMATCH_REASONS
    )


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_gate_zero_parsed_without_plan_is_not_positive(critical_pr: bool) -> None:
    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            status="complete",
            chunks_parsed=[],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        critical_pr=critical_pr,
    )

    assert "chunks_parsed_missing" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert not _has_reason(gate.limitations, "chunk_execution_expected_missing")


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
@pytest.mark.parametrize(
    "invalid_chunk_id",
    [
        "",
        " chunk-01-primary_backend_logic",
        "../chunk-01-primary_backend_logic",
        "ghp_abcdefghijk_sensitive",
    ],
    ids=["empty", "leading-space", "path-like", "secret-like"],
)
def test_u2_gate_invalid_parsed_id_without_plan_is_not_positive(
    invalid_chunk_id: str,
    critical_pr: bool,
) -> None:
    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            status="complete",
            chunks_parsed=[invalid_chunk_id],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        critical_pr=critical_pr,
    )

    assert "chunks_parsed_missing" in gate.limitations
    assert "chunk_execution_foreign_id" in gate.limitations
    if invalid_chunk_id:
        assert invalid_chunk_id not in "\n".join([*gate.limitations, *gate.warnings])
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
@pytest.mark.parametrize(
    "invalid_chunk_id",
    ["", "../chunk-01-primary_backend_logic", "ghp_abcdefghijk_sensitive"],
    ids=["empty", "path-like", "secret-like"],
)
def test_u2_gate_matching_invalid_plan_and_result_ids_are_not_execution_backed(
    invalid_chunk_id: str,
    critical_pr: bool,
) -> None:
    plan = _execution_plan()
    plan.chunks[0].chunk_id = invalid_chunk_id
    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            status="complete",
            chunks_parsed=[invalid_chunk_id],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=plan,
        critical_pr=critical_pr,
    )

    assert "chunks_parsed_missing" in gate.limitations
    assert "chunk_execution_foreign_id" in gate.limitations
    if invalid_chunk_id:
        assert invalid_chunk_id not in "\n".join([*gate.limitations, *gate.warnings])
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_mutated_unhashable_parsed_id_with_p1_fails_closed() -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunks_parsed[0] = []  # type: ignore[assignment]

    gate = _gate(
        _final_review(
            confirmed_findings=[_finding()],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        results,
    )

    assert "chunks_parsed_missing" in gate.limitations
    assert "chunk_execution_foreign_id" in gate.limitations
    assert any("source_chunk_not_parsed" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_invalid_parsed_id_cannot_authorize_p1_finding() -> None:
    invalid_chunk_id = "../chunk-01-primary_backend_logic"
    gate = _gate(
        _final_review(
            confirmed_findings=[
                _finding(
                    chunk_id=invalid_chunk_id,
                    source_chunks=[invalid_chunk_id],
                )
            ],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[invalid_chunk_id],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "chunks_parsed_missing" in gate.limitations
    assert "chunk_execution_foreign_id" in gate.limitations
    assert any("source_chunk_not_parsed" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_does_not_echo_invalid_failed_chunk_id() -> None:
    invalid_chunk_id = "../chunk-02-api_schema_contract"
    failure = _execution_failures([EXECUTION_CHUNK_IDS[1]])[0]
    failure.chunk_id = invalid_chunk_id

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            chunks_failed=[failure],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "chunk_execution_foreign_id" in gate.limitations
    assert "chunks_failed_present" in gate.limitations
    assert invalid_chunk_id not in "\n".join([*gate.limitations, *gate.warnings])
    assert gate.status == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_mutated_malformed_failed_carrier_fails_closed() -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        chunks_failed=_execution_failures([EXECUTION_CHUNK_IDS[1]]),
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunks_failed[0] = None  # type: ignore[assignment]

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
    )

    assert "chunk_execution_foreign_id" in gate.limitations
    assert "chunks_failed_present" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_rejects_mismatched_plan_target_repo() -> None:
    plan = _execution_plan()
    plan.target_repo = "mglpsw/OtherRepo"
    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=plan,
    )

    assert "target_repo_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_rejects_observable_chunk_plan_ref_mismatch() -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref["status"] = "partial"

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
        chunk_plan=plan,
    )

    assert "chunk_plan_ref_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_rejects_final_review_target_repo_mismatch() -> None:
    gate = _gate(
        _final_review(
            target_repo="mglpsw/OtherRepo",
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "target_repo_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "plan_ref_status",
    ["partial", "degraded", "failed"],
    ids=["partial", "degraded", "failed"],
)
def test_u2_gate_propagates_chunk_plan_ref_status_without_plan(
    plan_ref_status: str,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref["status"] = plan_ref_status

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
    )

    assert f"chunk_plan_status_{plan_ref_status}" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("declared_count", "expected_reason"),
    [
        pytest.param(2, "chunk_execution_expected_missing", id="declares-more"),
        pytest.param(0, "chunk_execution_foreign_id", id="declares-none"),
    ],
)
def test_u2_gate_checks_chunk_plan_ref_count_without_plan(
    declared_count: int,
    expected_reason: str,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref["chunk_count"] = declared_count

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
    )

    assert expected_reason in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "mutated_status",
    ["", "mystery", None, [], {}],
    ids=["empty", "unknown", "none", "list", "dict"],
)
def test_u2_gate_mutated_invalid_chunk_results_status_fails_closed(
    mutated_status: object,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.status = mutated_status  # type: ignore[assignment]

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
    )

    assert "chunk_results_status_invalid" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_snapshots_validated_final_coverage_against_later_mutation() -> None:
    raw = _final_review(
        status="partial",
        verdict="manual_review_required",
        coverage=_coverage(partial=[EXECUTION_FILES[0]]),
    )
    document = validate_final_review_document(raw)
    raw["status"] = "complete"
    raw["verdict"] = "approved"
    raw["coverage"] = _coverage(reviewed=[EXECUTION_FILES[0]])

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_rejects_mutation_of_validated_document_snapshot() -> None:
    document = validate_final_review_document(
        _final_review(
            status="partial",
            verdict="manual_review_required",
            coverage=_coverage(partial=[EXECUTION_FILES[0]]),
        )
    )
    document.raw["status"] = "complete"
    document.raw["verdict"] = "approved"
    document.raw["coverage"] = _coverage(reviewed=[EXECUTION_FILES[0]])

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_mutated_after_validation" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_revalidates_direct_final_review_document() -> None:
    raw = _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    raw["schema_id"] = "agent-review.not-final-review.v1"
    document = FinalReviewDocument(raw=raw, verdict_unknown=False)

    with pytest.raises(QualityGateError) as exc:
        evaluate_review_quality_gate(
            document,
            _chunk_results(
                chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
                coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
            ),
        )

    assert exc.value.error_class == "final_review_invalid"


def test_u2_gate_target_mismatch_cannot_authorize_p1_finding() -> None:
    gate = _gate(
        _final_review(
            target_repo="mglpsw/OtherRepo",
            confirmed_findings=[_finding()],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "target_repo_mismatch" in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "chunk_results_ref",
    [
        pytest.param({"status": "failed"}, id="status"),
        pytest.param({"created_at": "2000-01-01T00:00:00Z"}, id="created-at"),
        pytest.param({"schema_version": 99}, id="schema-version"),
    ],
)
def test_u2_gate_rejects_observable_final_chunk_results_ref_mismatch(
    chunk_results_ref: dict[str, object],
) -> None:
    gate = _gate(
        _final_review(
            inputs={"chunk_results": chunk_results_ref},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_input_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "chunk_plan_ref",
    [
        pytest.param({"provided": False}, id="provided-false"),
        pytest.param({"provided": True, "status": "partial"}, id="status"),
    ],
)
def test_u2_gate_rejects_observable_final_chunk_plan_ref_mismatch(
    chunk_plan_ref: dict[str, object],
) -> None:
    plan = _execution_plan()
    gate = _gate(
        _final_review(
            inputs={"chunk_plan": chunk_plan_ref},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=plan,
    )

    assert "final_review_input_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_final_input_mismatch_cannot_authorize_p1_finding() -> None:
    gate = _gate(
        _final_review(
            confirmed_findings=[_finding()],
            inputs={"chunk_results": {"status": "failed"}},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_input_mismatch" in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_matching_generated_input_refs_remain_a_positive_control() -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    review = synthesize_final_review(results, chunk_plan=plan)

    gate = _gate(review.model_dump(mode="json"), results, chunk_plan=plan)

    assert "final_review_input_mismatch" not in gate.limitations
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False


@pytest.mark.parametrize(
    ("authority", "carrier", "malformed", "expected_reason"),
    [
        pytest.param(
            "results",
            "chunks_parsed",
            {EXECUTION_CHUNK_IDS[0]: []},
            "chunk_results_structure_invalid",
            id="result-parsed-dict",
        ),
        pytest.param(
            "results",
            "files_reviewed",
            {EXECUTION_FILES[0]: []},
            "chunk_results_structure_invalid",
            id="result-reviewed-dict",
        ),
        pytest.param(
            "results",
            "target_repo",
            [],
            "chunk_results_structure_invalid",
            id="result-target-list",
        ),
        pytest.param(
            "plan",
            "chunks",
            None,
            "chunk_plan_structure_invalid",
            id="plan-chunks-none",
        ),
        pytest.param(
            "plan",
            "chunk_files",
            {EXECUTION_FILES[0]: []},
            "chunk_plan_structure_invalid",
            id="plan-files-dict",
        ),
    ],
)
def test_u2_gate_rejects_malformed_coverage_authority_containers(
    authority: str,
    carrier: str,
    malformed: object,
    expected_reason: str,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    if authority == "results":
        if carrier == "files_reviewed":
            results.coverage.files_reviewed = malformed  # type: ignore[assignment]
        else:
            setattr(results, carrier, malformed)
    elif carrier == "chunk_files":
        plan.chunks[0].files = malformed  # type: ignore[assignment]
    else:
        setattr(plan, carrier, malformed)

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
        chunk_plan=plan,
    )

    assert expected_reason in gate.limitations
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("authority", "field", "mutated", "expected_reason"),
    [
        pytest.param(
            "results",
            "schema_id",
            "agent-review.not-chunk-results.v1",
            "chunk_results_schema_mismatch",
            id="result-schema-id",
        ),
        pytest.param(
            "results",
            "schema_version",
            True,
            "chunk_results_schema_mismatch",
            id="result-schema-bool",
        ),
        pytest.param(
            "plan",
            "schema_id",
            "agent-review.not-plan.v1",
            "chunk_plan_schema_mismatch",
            id="plan-schema-id",
        ),
        pytest.param(
            "plan",
            "schema_version",
            1.0,
            "chunk_plan_schema_mismatch",
            id="plan-schema-float",
        ),
    ],
)
def test_u2_gate_revalidates_direct_coverage_authority_envelopes(
    authority: str,
    field: str,
    mutated: object,
    expected_reason: str,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref = {}
    setattr(results if authority == "results" else plan, field, mutated)

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
        chunk_plan=plan,
    )

    assert expected_reason in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("input_name", "field", "mutated"),
    [
        pytest.param("chunk_results", "schema_version", True, id="result-schema-bool"),
        pytest.param("chunk_results", "schema_version", 1.0, id="result-schema-float"),
        pytest.param("chunk_plan", "schema_version", True, id="plan-schema-bool"),
        pytest.param("chunk_plan", "chunk_count", 1.0, id="plan-count-float"),
    ],
)
def test_u2_gate_rejects_final_input_integer_type_confusion(
    input_name: str,
    field: str,
    mutated: object,
) -> None:
    plan = _execution_plan()
    gate = _gate(
        _final_review(
            inputs={input_name: {"provided": True, field: mutated}},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=plan,
    )

    assert "final_review_input_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_plan_foreign_p1_cannot_authorize_confirmed_blocker() -> None:
    foreign_id = "chunk-99-primary_backend_logic"
    gate = _gate(
        _final_review(
            confirmed_findings=[
                _finding(chunk_id=foreign_id, source_chunks=[foreign_id])
            ],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        _chunk_results(
            chunks_parsed=[foreign_id],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=_execution_plan(),
    )

    assert "chunk_execution_foreign_id" in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "mismatch",
    ["plan-ref", "result-status"],
    ids=["plan-ref", "result-status"],
)
def test_u2_gate_result_identity_mismatch_cannot_authorize_p1(
    mismatch: str,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    if mismatch == "plan-ref":
        results.chunk_plan_ref["schema_version"] = 99
        expected_reason = "chunk_plan_ref_mismatch"
    else:
        results.status = "unknown"  # type: ignore[assignment]
        expected_reason = "chunk_results_status_invalid"

    gate = _gate(
        _final_review(
            confirmed_findings=[_finding()],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        results,
    )

    assert expected_reason in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_post_validation_mutation_cannot_authorize_p1() -> None:
    document = validate_final_review_document(
        _final_review(
            confirmed_findings=[_finding()],
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        )
    )
    document.raw["summary"] = "Mutated after validation."

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_mutated_after_validation" in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_detects_boolean_schema_mutation_after_validation() -> None:
    document = validate_final_review_document(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    )
    document.raw["schema_version"] = True

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_mutated_after_validation" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


def test_u2_gate_matching_explicit_integer_and_target_refs_remain_positive() -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    review = synthesize_final_review(results, chunk_plan=plan).model_dump(mode="json")
    review["inputs"]["chunk_results"].update(  # type: ignore[index,union-attr]
        {"target_repo": results.target_repo, "schema_version": 1}
    )
    review["inputs"]["chunk_plan"].update(  # type: ignore[index,union-attr]
        {
            "target_repo": plan.target_repo,
            "schema_version": 1,
            "chunk_count": 1,
        }
    )

    gate = _gate(review, results, chunk_plan=plan)

    assert "final_review_input_mismatch" not in gate.limitations
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
@pytest.mark.parametrize(
    (
        "plan_status",
        "chunk_coverage",
        "plan_limitations",
        "expected_reason",
    ),
    [
        pytest.param(
            "complete",
            "partial",
            [],
            "chunk_plan_status_partial",
            id="nested-partial",
        ),
        pytest.param(
            "complete",
            "degraded",
            [],
            "chunk_plan_status_degraded",
            id="nested-degraded",
        ),
        pytest.param(
            "complete",
            "complete",
            ["file_context_fallback_used"],
            "chunk_plan_status_partial",
            id="fallback-derived-partial",
        ),
        pytest.param(
            "failed",
            "complete",
            [],
            "chunk_plan_status_failed",
            id="explicit-failed",
        ),
    ],
)
def test_u2_gate_uses_nested_and_explicit_plan_degradation(
    plan_status: str,
    chunk_coverage: str,
    plan_limitations: list[str],
    expected_reason: str,
    critical_pr: bool,
) -> None:
    plan = _execution_plan(
        chunk_coverages=[chunk_coverage],
        status=plan_status,
        limitations=plan_limitations,
    )

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        _chunk_results(
            status="complete",
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
        chunk_plan=plan,
        critical_pr=critical_pr,
    )

    assert gate.limitations.count(expected_reason) == 1
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_gate_exact_execution_with_informational_reason_is_nonblocking(
    critical_pr: bool,
) -> None:
    plan = _execution_plan(
        chunk_count=2,
        limitations=["intake_schema_id_missing"],
    )

    gate = _gate(
        _final_review(
            verdict="approve_with_minor_notes",
            limitations=["intake_schema_id_missing"],
            coverage=_coverage(reviewed=EXECUTION_FILES.copy()),
        ),
        _chunk_results(
            status="complete",
            chunks_parsed=EXECUTION_CHUNK_IDS.copy(),
            limitations=["intake_schema_id_missing"],
            coverage=ChunkResultsCoverage(files_reviewed=EXECUTION_FILES.copy()),
        ),
        chunk_plan=plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "passed"
    assert gate.normalized_verdict == "approve_with_minor_notes"
    assert gate.manual_review_required is False
    assert gate.limitations.count("intake_schema_id_missing") == 1
    assert "chunks_parsed_missing" not in gate.limitations
    assert not any(
        _has_reason(gate.limitations, reason)
        for reason in EXECUTION_MISMATCH_REASONS
    )


@pytest.mark.parametrize(
    ("partial", "expected_parser_status", "expected_review_status", "expected_gate_status"),
    [
        pytest.param(False, "complete", "complete", "passed", id="all-reviewed"),
        pytest.param(
            True,
            "partial",
            "partial",
            "manual_review_required",
            id="partial",
        ),
    ],
)
def test_u2_serialized_parser_synthesizer_gate_round_trip_preserves_truth(
    tmp_path: Path,
    partial: bool,
    expected_parser_status: str,
    expected_review_status: str,
    expected_gate_status: str,
) -> None:
    plan = _execution_plan()
    plan.chunks[0].files = EXECUTION_FILES.copy()
    plan.files_covered = EXECUTION_FILES.copy()
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    _write_round_trip_response(
        responses_dir,
        plan=plan,
        partial=partial,
    )

    parsed = parse_chunk_results(plan, responses_dir=responses_dir)
    serialized_results = parsed.model_dump_json()
    loaded_results = ChunkResults.model_validate_json(serialized_results)
    review = synthesize_final_review(loaded_results, chunk_plan=plan)
    serialized_review = review.model_dump_json()
    final_document = validate_final_review_document(json.loads(serialized_review))
    gate = evaluate_review_quality_gate(
        final_document,
        ChunkResults.model_validate_json(serialized_results),
        chunk_plan=plan,
        critical_pr=False,
    )

    assert parsed.status == expected_parser_status
    assert loaded_results.status == expected_parser_status
    assert review.status == expected_review_status
    assert gate.status == expected_gate_status
    assert gate.manual_review_required is partial
    if partial:
        assert review.coverage.files_partial == [EXECUTION_FILES[1]]
        assert review.verdict == "manual_review_required"
        assert gate.normalized_verdict == "manual_review_required"
    else:
        assert review.coverage.files_reviewed == EXECUTION_FILES
        assert review.verdict == "approved"
        assert gate.normalized_verdict == "approved"
    assert "chunks_parsed_missing" not in review.limitations
    assert "chunks_parsed_missing" not in gate.limitations
    assert not any(
        _has_reason(review.limitations, reason)
        or _has_reason(gate.limitations, reason)
        for reason in EXECUTION_MISMATCH_REASONS
    )


def test_unknown_final_verdict_generates_failed_gate_not_validation_error() -> None:
    gate = _gate(_final_review(verdict="surprising_verdict"))

    assert gate.status == "failed"
    assert gate.normalized_verdict == "review_unavailable"
    assert gate.manual_review_required is True
    assert "final_review_verdict_unknown" in gate.limitations


def test_structurally_invalid_final_review_fails_before_gate() -> None:
    with pytest.raises(QualityGateError) as exc_info:
        validate_final_review_document({"schema_id": "wrong", "schema_version": 1})

    assert exc_info.value.error_class == "final_review_invalid"


def test_no_minimum_material_is_review_unavailable() -> None:
    final_review = _final_review(
        coverage={
            "files_reviewed": [],
            "files_partial": [],
            "files_not_reviewed": [],
            "expected_files": [],
            "missing_expected_files": [],
            "extra_reported_files": [],
            "comparison_available": False,
        },
    )
    chunk_results = _chunk_results(chunks_parsed=[], coverage=ChunkResultsCoverage())

    gate = _gate(final_review, chunk_results)

    assert gate.status == "failed"
    assert gate.normalized_verdict == "review_unavailable"
    assert gate.manual_review_required is True
    assert "review_material_missing" in gate.limitations


def test_reliable_p1_normalizes_to_changes_requested() -> None:
    gate = _gate(_final_review(verdict="approved", confirmed_findings=[_finding()]))

    assert gate.normalized_verdict == "changes_requested"
    assert gate.status == "passed"
    assert gate.manual_review_required is False
    assert "approved_with_confirmed_blocker" in gate.blocked_reasons


def test_p1_empty_or_redacted_evidence_does_not_confirm_blocker() -> None:
    empty = _gate(_final_review(verdict="changes_requested", confirmed_findings=[_finding(evidence="   ")]))
    redacted = _gate(_final_review(verdict="changes_requested", confirmed_findings=[_finding(evidence="[REDACTED]")]))

    assert empty.normalized_verdict == "manual_review_required"
    assert redacted.normalized_verdict == "manual_review_required"
    assert "changes_requested_without_confirmed_blocker" in empty.blocked_reasons
    assert any("missing_evidence" in warning for warning in empty.warnings)
    assert any("redacted_or_placeholder_only_evidence" in warning for warning in redacted.warnings)


def test_source_chunks_must_be_parsed_but_chunk_id_can_be_fallback() -> None:
    unparsed = _gate(
        _final_review(confirmed_findings=[_finding(source_chunks=["chunk-99-missing"])]),
        _chunk_results(chunks_parsed=["chunk-01-primary_backend_logic"]),
    )
    fallback = _gate(
        _final_review(confirmed_findings=[_finding(source_chunks=[])]),
        _chunk_results(chunks_parsed=["chunk-01-primary_backend_logic"]),
    )

    assert unparsed.normalized_verdict == "manual_review_required"
    assert any("source_chunk_not_parsed" in warning for warning in unparsed.warnings)
    assert fallback.normalized_verdict == "changes_requested"


def test_degraded_chunk_results_without_blocker_requires_manual_review() -> None:
    gate = _gate(_final_review(), _chunk_results(status="degraded", limitations=["chunk_response_json_invalid"]))

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert "chunk_results_status_degraded" in gate.limitations


def test_chunk_failure_adds_warning_and_limitation() -> None:
    failure = ChunkParseFailure(
        chunk_id="chunk-02-tests",
        semantic_group="tests",
        error_class="chunk_response_missing",
        message="chunk response file is missing",
    )
    gate = _gate(_final_review(status="partial"), _chunk_results(status="partial", chunks_failed=[failure]))

    assert gate.status == "manual_review_required"
    assert "chunks_failed_present" in gate.limitations
    assert "chunk_failed:chunk-02-tests:chunk_response_missing" in gate.warnings


def test_critical_coverage_gap_requires_manual_review() -> None:
    final_review = _final_review(
        coverage={
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
            "expected_files": ["backend/services/schedule.py", "backend/services/doctor.py"],
            "missing_expected_files": ["backend/services/doctor.py"],
            "extra_reported_files": [],
            "comparison_available": True,
        }
    )

    gate = _gate(final_review, critical_pr=True)

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert "critical_expected_files_missing" in gate.limitations


@pytest.mark.parametrize(
    ("coverage_payload", "critical_pr", "must_review"),
    [
        pytest.param(
            _coverage(not_reviewed=["src/a.py", "src/b.py"]),
            False,
            None,
            id="all-not-reviewed-noncritical",
        ),
        pytest.param(
            _coverage(reviewed=["src/a.py"], partial=["src/b.py"]),
            False,
            None,
            id="partial-noncritical",
        ),
        pytest.param(
            _coverage(reviewed=["src/a.py"], partial=["src/b.py"]),
            True,
            "src/b.py",
            id="partial-critical-must-review",
        ),
    ],
)
def test_u2_incomplete_coverage_cannot_bypass_gate_with_mutated_complete_claims(
    coverage_payload: dict[str, object],
    critical_pr: bool,
    must_review: str | None,
) -> None:
    final_review = validate_final_review_document(
        _final_review(
            status="partial",
            verdict="manual_review_required",
            coverage=coverage_payload,
        )
    )
    final_review.raw["status"] = "complete"
    final_review.raw["verdict"] = "approved"
    chunk_coverage = ChunkResultsCoverage(
        files_reviewed=list(coverage_payload["files_reviewed"]),
        files_partial=list(coverage_payload["files_partial"]),
        files_not_reviewed=list(coverage_payload["files_not_reviewed"]),
    )
    chunk_results = _chunk_results(status="partial", coverage=chunk_coverage)
    chunk_results.status = "complete"
    intake = _intake_with_must_review(must_review) if must_review is not None else None

    gate = evaluate_review_quality_gate(
        final_review,
        chunk_results,
        intake=intake,
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.inputs["critical_pr"] is critical_pr
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert gate.blocked_reasons == []
    assert gate.warnings == []
    assert "review_material_missing" not in gate.limitations
    if must_review is not None:
        assert "critical_must_review_files_not_covered" in gate.limitations
    else:
        assert not any(reason.startswith("critical_") for reason in gate.limitations)


@pytest.mark.parametrize(
    "incomplete_carrier",
    ["final_review", "chunk_results"],
)
def test_u2_each_incomplete_coverage_carrier_independently_blocks_gate(
    incomplete_carrier: str,
) -> None:
    complete = _coverage(reviewed=["src/a.py", "src/b.py"])
    incomplete = _coverage(reviewed=["src/a.py"], partial=["src/b.py"])
    final_coverage = incomplete if incomplete_carrier == "final_review" else complete
    chunk_coverage = incomplete if incomplete_carrier == "chunk_results" else complete

    gate = _gate(
        _final_review(coverage=final_coverage),
        _chunk_results(
            coverage=ChunkResultsCoverage(
                files_reviewed=list(chunk_coverage["files_reviewed"]),
                files_partial=list(chunk_coverage["files_partial"]),
                files_not_reviewed=list(chunk_coverage["files_not_reviewed"]),
            )
        ),
        critical_pr=False,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_supplied_plan_rejects_foreign_complete_coverage(critical_pr: bool) -> None:
    expected = "src/expected.py"
    foreign = "src/foreign.py"
    chunk = SemanticChunk(
        chunk_id="chunk-01-primary_backend_logic",
        semantic_group="primary_backend_logic",
        order_index=0,
        files=[expected],
        artifacts=[],
        contracts=[],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )
    chunk_plan = SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=[chunk],
        files_covered=[expected],
        status="complete",
    )

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[foreign])),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[foreign])),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_reported_files_not_in_plan" in gate.limitations


@pytest.mark.parametrize("blocker_severity", ["P0", "P1"])
def test_u2_incomplete_coverage_does_not_erase_reliable_blocker(
    blocker_severity: str,
) -> None:
    reviewed = "src/a.py"
    not_reviewed = "src/b.py"
    coverage_payload = _coverage(
        reviewed=[reviewed],
        not_reviewed=[not_reviewed],
    )
    finding = _finding(
        severity=blocker_severity,
        file_path=reviewed,
        title=f"{blocker_severity} reliable blocker",
        dedupe_key=f"reliable-{blocker_severity}",
    )

    gate = _gate(
        _final_review(
            status="partial",
            verdict="changes_requested",
            confirmed_findings=[finding],
            risks=[_risk(title="Coverage follow-up risk")],
            coverage=coverage_payload,
        ),
        _chunk_results(
            status="degraded",
            coverage=ChunkResultsCoverage(
                files_reviewed=[reviewed],
                files_not_reviewed=[not_reviewed],
            ),
        ),
        critical_pr=False,
    )

    assert gate.inputs["final_review"]["status"] == "partial"
    assert gate.inputs["chunk_results"]["status"] == "degraded"
    assert gate.status == "degraded"
    assert gate.normalized_verdict == "changes_requested"
    assert gate.manual_review_required is False
    assert gate.blocked_reasons == [
        f"confirmed_blocker:{blocker_severity}:{reviewed}"
    ]
    assert gate.warnings == []
    assert "review_material_missing" not in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_partial_chunk_plan_blocks_otherwise_complete_gate(critical_pr: bool) -> None:
    reviewed = "src/reviewed.py"
    chunk_plan = _chunk_plan_for_gate(reviewed_file=reviewed, status="partial")

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed])),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[reviewed])),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.inputs["chunk_plan"]["status"] == "partial"
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "chunk_plan_status_partial" in gate.limitations
    assert "review_material_missing" not in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_plan_files_not_covered_blocks_otherwise_complete_gate(critical_pr: bool) -> None:
    reviewed = "src/reviewed.py"
    not_covered = "src/not_covered.py"
    chunk_plan = _chunk_plan_for_gate(
        reviewed_file=reviewed,
        files_not_covered=[not_covered],
    )

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed])),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[reviewed])),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert chunk_plan.chunks[0].artifacts == ["artifact:file-diff-context", "artifact:checks"]
    assert chunk_plan.files_not_covered == [not_covered]
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_expected_files_missing" in gate.limitations
    if critical_pr:
        assert "critical_chunk_plan_files_not_covered" in gate.limitations
    else:
        assert not any(reason.startswith("critical_") for reason in gate.limitations)


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_mutated_expected_files_cannot_hide_omission_without_plan(critical_pr: bool) -> None:
    reviewed = "src/reviewed.py"
    omitted = "src/omitted.py"
    final_review = validate_final_review_document(
        _final_review(coverage=_coverage(reviewed=[reviewed]))
    )
    raw_coverage = final_review.raw["coverage"]
    assert isinstance(raw_coverage, dict)
    expected_files = raw_coverage["expected_files"]
    assert isinstance(expected_files, list)
    expected_files.append(omitted)
    assert raw_coverage["files_reviewed"] == [reviewed]
    assert raw_coverage["expected_files"] == [reviewed, omitted]
    assert raw_coverage["missing_expected_files"] == []

    gate = evaluate_review_quality_gate(
        final_review,
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[reviewed])),
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.inputs["chunk_plan"] == {"provided": False}
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_expected_files_missing" in gate.limitations
    assert "review_material_missing" not in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_complete_matching_plan_remains_a_positive_gate_control(critical_pr: bool) -> None:
    reviewed = "src/reviewed.py"
    chunk_plan = _chunk_plan_for_gate(reviewed_file=reviewed)

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed])),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[reviewed])),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.inputs["chunk_plan"]["status"] == "complete"
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False
    assert "chunk_plan_status_partial" not in gate.limitations
    assert "coverage_expected_files_missing" not in gate.limitations
    assert "critical_chunk_plan_files_not_covered" not in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_exactly_reviewed_coverage_remains_a_positive_gate_control(critical_pr: bool) -> None:
    coverage_payload = _coverage(reviewed=["src/a.py", "src/b.py"])
    chunk_results = _chunk_results(
        coverage=ChunkResultsCoverage(files_reviewed=["src/a.py", "src/b.py"])
    )
    intake = _intake_with_must_review("src/b.py") if critical_pr else None

    gate = _gate(
        _final_review(coverage=coverage_payload),
        chunk_results,
        intake=intake,
        critical_pr=critical_pr,
    )

    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False
    assert not any(reason.startswith("critical_") for reason in gate.limitations)


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_plan_not_reviewed_caps_reviewed_carriers(critical_pr: bool) -> None:
    reviewed = "src/a.py"
    capped = "src/b.py"
    chunk_plan = _chunk_plan_with_partition(
        chunk_files=[reviewed],
        files_covered=[reviewed],
        files_not_covered=[capped],
    )
    normalized = _normalize_plan_coverage_partition(chunk_plan)
    assert normalized.as_chunk_results_coverage() == ChunkResultsCoverage(
        files_reviewed=[reviewed],
        files_not_reviewed=[capped],
    )
    assert normalized.limitations == ()
    assert normalized.foreign_files == ()

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed, capped])),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=[reviewed, capped])
        ),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.inputs["chunk_plan"]["status"] == "complete"
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_file_in_multiple_states" not in gate.limitations
    if critical_pr:
        assert "critical_chunk_plan_files_not_covered" in gate.limitations


@pytest.mark.parametrize(
    "plan_shape",
    ["covered-outside-chunks", "covered-without-chunks"],
)
@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_plan_covered_file_without_chunk_cannot_authorize_reviewed_carriers(
    plan_shape: str,
    critical_pr: bool,
) -> None:
    reviewed = "src/a.py"
    unassigned = "src/b.py"
    if plan_shape == "covered-outside-chunks":
        chunk_plan = _chunk_plan_with_partition(
            chunk_files=[reviewed],
            files_covered=[reviewed, unassigned],
        )
        carrier_files = [reviewed, unassigned]
    else:
        chunk_plan = SemanticChunkPlan(
            target_repo="mglpsw/AgentEscala",
            max_parallel_blocks=6,
            chunks=[],
            files_covered=[unassigned],
            status="complete",
        )
        carrier_files = [unassigned]

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=carrier_files)),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=carrier_files)
        ),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_expected_files_missing" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_plan_partial_caps_reviewed_carriers(critical_pr: bool) -> None:
    reviewed = "src/a.py"
    capped = "src/b.py"
    chunk_plan = _chunk_plan_with_partition(
        chunk_files=[reviewed, capped],
        files_covered=[reviewed],
        files_partially_covered=[capped],
    )
    normalized = _normalize_plan_coverage_partition(chunk_plan)
    assert normalized.as_chunk_results_coverage() == ChunkResultsCoverage(
        files_reviewed=[reviewed],
        files_partial=[capped],
    )
    assert normalized.limitations == ()
    assert normalized.foreign_files == ()

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed, capped])),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=[reviewed, capped])
        ),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_file_in_multiple_states" not in gate.limitations


@pytest.mark.parametrize(
    ("files_covered", "files_partial", "files_not_reviewed", "expected"),
    [
        pytest.param(
            ["src/b.py"],
            ["src/b.py"],
            [],
            ChunkResultsCoverage(files_partial=["src/b.py"]),
            id="reviewed-plus-partial",
        ),
        pytest.param(
            ["src/b.py"],
            [],
            ["src/b.py"],
            ChunkResultsCoverage(files_not_reviewed=["src/b.py"]),
            id="reviewed-plus-not-reviewed",
        ),
        pytest.param(
            [],
            ["src/b.py"],
            ["src/b.py"],
            ChunkResultsCoverage(files_not_reviewed=["src/b.py"]),
            id="partial-plus-not-reviewed",
        ),
    ],
)
def test_u2_intraplan_overlap_uses_worst_precedence(
    files_covered: list[str],
    files_partial: list[str],
    files_not_reviewed: list[str],
    expected: ChunkResultsCoverage,
) -> None:
    path = "src/b.py"
    chunk_plan = _chunk_plan_with_partition(
        chunk_files=[path],
        files_covered=files_covered,
        files_partially_covered=files_partial,
        files_not_covered=files_not_reviewed,
    )
    normalized = _normalize_plan_coverage_partition(chunk_plan)
    assert normalized.as_chunk_results_coverage() == expected
    assert normalized.limitations == ("coverage_file_in_multiple_states",)
    assert normalized.foreign_files == ()

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[path])),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=[path])),
        chunk_plan=chunk_plan,
        critical_pr=False,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_file_in_multiple_states" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_no_plan_uses_final_expected_universe_for_both_carriers(
    critical_pr: bool,
) -> None:
    reviewed = ["src/a.py", "src/b.py"]

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=reviewed)),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=[reviewed[0]])
        ),
        critical_pr=critical_pr,
    )

    assert gate.inputs["chunk_plan"] == {"provided": False}
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_expected_files_missing" in gate.limitations


@pytest.mark.parametrize("with_plan", [False, True], ids=["no-plan", "with-plan"])
@pytest.mark.parametrize(
    "foreign_carrier",
    ["final_review", "chunk_results"],
)
@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_mutated_expected_files_cannot_shrink_away_reviewed_paths(
    with_plan: bool,
    foreign_carrier: str,
    critical_pr: bool,
) -> None:
    expected = "src/a.py"
    hidden = "src/b.py"
    final_reviewed = [expected, hidden] if foreign_carrier == "final_review" else [expected]
    chunk_reviewed = [expected, hidden] if foreign_carrier == "chunk_results" else [expected]
    final_coverage = _coverage(reviewed=final_reviewed)
    final_coverage["expected_files"] = [expected]
    chunk_plan = _chunk_plan_for_gate(reviewed_file=expected) if with_plan else None

    gate = _gate(
        _final_review(coverage=final_coverage),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=chunk_reviewed)
        ),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_reported_files_not_in_plan" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_empty_mutated_expected_files_cannot_authorize_nonempty_carriers(
    critical_pr: bool,
) -> None:
    reviewed = "src/a.py"
    final_coverage = _coverage(reviewed=[reviewed])
    final_coverage["expected_files"] = []

    gate = _gate(
        _final_review(coverage=final_coverage),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=[reviewed])
        ),
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_reported_files_not_in_plan" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_disjoint_complete_carriers_are_rejected(critical_pr: bool) -> None:
    final_path = "src/a.py"
    chunk_path = "src/b.py"

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[final_path])),
        _chunk_results(
            coverage=ChunkResultsCoverage(files_reviewed=[chunk_path])
        ),
        critical_pr=critical_pr,
    )

    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_expected_files_missing" in gate.limitations
    assert "coverage_reported_files_not_in_plan" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_erased_final_coverage_is_rejected_with_complete_chunks(
    critical_pr: bool,
) -> None:
    reviewed = "src/a.py"
    final_review = validate_final_review_document(
        _final_review(coverage=_coverage(reviewed=[reviewed]))
    )
    erased = final_review.raw.pop("coverage")
    assert isinstance(erased, dict)
    assert "coverage" not in final_review.raw
    chunk_results = _chunk_results(
        coverage=ChunkResultsCoverage(files_reviewed=[reviewed])
    )
    assert chunk_results.coverage.files_reviewed == [reviewed]

    gate = evaluate_review_quality_gate(
        final_review,
        chunk_results,
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_missing" in gate.limitations


@pytest.mark.parametrize("critical_pr", [False, True], ids=["noncritical", "critical"])
def test_u2_emptied_chunk_coverage_is_rejected_with_complete_final(
    critical_pr: bool,
) -> None:
    reviewed = "src/a.py"
    chunk_results = _chunk_results(
        coverage=ChunkResultsCoverage(files_reviewed=[reviewed])
    )
    chunk_results.coverage.files_reviewed.clear()
    assert chunk_results.coverage == ChunkResultsCoverage()

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=[reviewed])),
        chunk_results,
        critical_pr=critical_pr,
    )

    assert gate.inputs["final_review"]["status"] == "complete"
    assert gate.inputs["chunk_results"]["status"] == "complete"
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True
    assert "coverage_missing" in gate.limitations


@pytest.mark.parametrize(
    ("with_plan", "critical_pr"),
    [
        pytest.param(False, False, id="no-plan-noncritical"),
        pytest.param(False, True, id="no-plan-critical"),
        pytest.param(True, False, id="matching-plan-noncritical"),
        pytest.param(True, True, id="matching-plan-critical"),
    ],
)
def test_u2_matching_carriers_remain_positive_controls(
    with_plan: bool,
    critical_pr: bool,
) -> None:
    reviewed = ["src/a.py", "src/b.py"]
    chunk_plan = (
        _chunk_plan_with_partition(
            chunk_files=reviewed,
            files_covered=reviewed,
        )
        if with_plan
        else None
    )

    gate = _gate(
        _final_review(coverage=_coverage(reviewed=reviewed)),
        _chunk_results(coverage=ChunkResultsCoverage(files_reviewed=reviewed)),
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )

    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False
    assert "coverage_expected_files_missing" not in gate.limitations
    assert "coverage_reported_files_not_in_plan" not in gate.limitations
    assert "coverage_missing" not in gate.limitations
    assert "coverage_file_in_multiple_states" not in gate.limitations


def test_must_review_files_are_best_effort_from_intake() -> None:
    intake = ReviewIntake(
        target_repo="mglpsw/AgentEscala",
        target_profile={},
        artifacts={
            "file-diff-context": {
                "content": {
                    "coverage_requirements": {
                        "must_review_files": ["backend/services/doctor.py"],
                    }
                }
            }
        },
        artifact_status=[],
        redaction_summary={"schema_version": "agent-review.redaction-report.v1"},
        status="complete",
    )

    gate = _gate(_final_review(), intake=intake, critical_pr=True)

    assert gate.normalized_verdict == "manual_review_required"
    assert "critical_must_review_files_not_covered" in gate.limitations


def test_red34_non_canonical_must_review_declaration_is_not_a_false_gap() -> None:
    """RED-34 (P2 follow-through): must_review_files declared in a
    non-canonical form ("./backend/a.py") must match reviewed coverage
    reported in its canonical form ("backend/a.py") -- the two are the same
    file (RED-28/RED-31 established this same identity for semantic_
    chunker's own must_review handling), so this must not raise a false
    critical_must_review_files_not_covered gap.
    """
    intake = ReviewIntake(
        target_repo="mglpsw/AgentEscala",
        target_profile={},
        artifacts={
            "file-diff-context": {
                "content": {
                    "coverage_requirements": {
                        "must_review_files": ["./backend/a.py"],
                    }
                }
            }
        },
        artifact_status=[],
        redaction_summary={"schema_version": "agent-review.redaction-report.v1"},
        status="complete",
    )

    gate = _gate(
        _final_review(coverage={**_final_review()["coverage"], "files_reviewed": ["backend/a.py"]}),
        intake=intake,
        critical_pr=True,
    )

    assert "critical_must_review_files_not_covered" not in gate.limitations


def test_red34_genuinely_absent_canonical_required_file_still_produces_gap() -> None:
    """A genuinely uncovered must_review file (same canonical identity on
    both sides, still absent from coverage) must still produce the
    critical gap -- the RED-34 fix narrows false positives, it does not
    weaken real coverage enforcement.
    """
    intake = ReviewIntake(
        target_repo="mglpsw/AgentEscala",
        target_profile={},
        artifacts={
            "file-diff-context": {
                "content": {
                    "coverage_requirements": {
                        "must_review_files": ["./backend/a.py", "backend/b.py"],
                    }
                }
            }
        },
        artifact_status=[],
        redaction_summary={"schema_version": "agent-review.redaction-report.v1"},
        status="complete",
    )

    gate = _gate(
        _final_review(coverage={**_final_review()["coverage"], "files_reviewed": ["backend/a.py"]}),
        intake=intake,
        critical_pr=True,
    )

    assert "critical_must_review_files_not_covered" in gate.limitations


def test_approved_with_p2_risks_or_limitations_is_not_clean_approved() -> None:
    p2 = _gate(_final_review(confirmed_findings=[_finding(severity="P2")]))
    risk = _gate(_final_review(risks=[_risk()]))
    limitation = _gate(_final_review(limitations=["coverage_reported_files_not_in_plan"]))

    assert p2.normalized_verdict == "approve_with_required_followup"
    assert risk.normalized_verdict == "approve_with_required_followup"
    assert limitation.normalized_verdict == "approve_with_minor_notes"


def test_operational_claim_without_explicit_operational_evidence_is_not_blocker() -> None:
    gate = _gate(
        _final_review(
            verdict="changes_requested",
            confirmed_findings=[
                _finding(
                    title="CT102 deploy may be affected",
                    evidence="Docs mention the CT102 prohibition as a guardrail.",
                    impact="Production runtime may be affected.",
                    source_artifact="artifact:file-diff-context",
                )
            ],
        )
    )

    assert gate.normalized_verdict == "manual_review_required"
    assert any("operational_claim_requires_explicit_evidence" in warning for warning in gate.warnings)


def test_product_text_does_not_trigger_operational_claim_detection() -> None:
    gate = _gate(
        _final_review(
            verdict="approved",
            confirmed_findings=[
                _finding(
                    title="Product flow breaks schedule validation",
                    evidence="The changed product flow skips the inactive doctor guard before schedule creation.",
                    impact="The product flow can schedule inactive doctors.",
                    source_artifact="artifact:file-diff-context",
                )
            ],
        )
    )

    assert gate.normalized_verdict == "changes_requested"
    assert not any("operational_claim_requires_explicit_evidence" in warning for warning in gate.warnings)


def test_ct102_deploy_still_triggers_operational_claim_detection() -> None:
    gate = _gate(
        _final_review(
            verdict="changes_requested",
            confirmed_findings=[
                _finding(
                    title="CT102 deploy guard is missing",
                    evidence="Docs mention the CT102 deploy prohibition as a guardrail.",
                    impact="Production runtime may be affected.",
                    source_artifact="artifact:file-diff-context",
                )
            ],
        )
    )

    assert gate.normalized_verdict == "manual_review_required"
    assert any("operational_claim_requires_explicit_evidence" in warning for warning in gate.warnings)


def test_test_failure_requires_supported_source_artifact() -> None:
    unsupported = _gate(
        _final_review(
            verdict="changes_requested",
            confirmed_findings=[
                _finding(
                    title="Pytest failure blocks merge",
                    evidence="pytest failed in the changed test module.",
                    impact="The test suite is failing.",
                    source_artifact="artifact:file-diff-context",
                )
            ],
        )
    )
    supported = _gate(
        _final_review(
            verdict="approved",
            confirmed_findings=[
                _finding(
                    title="Pytest failure blocks merge",
                    evidence="pytest failed in the changed test module.",
                    impact="The test suite is failing.",
                    source_artifact="artifact:checks",
                )
            ],
        )
    )

    assert unsupported.normalized_verdict == "manual_review_required"
    assert supported.normalized_verdict == "changes_requested"


def test_output_is_deterministic_and_sanitized(tmp_path: Path) -> None:
    final_review = _final_review(
        confirmed_findings=[
            _finding(
                file_path=str(tmp_path / "AgentEscala" / "backend" / "services" / "schedule.py"),
                title="Absolute path blocker",
                evidence="The changed hunk removes the inactive doctor guard before schedule creation.",
                dedupe_key="absolute-path-blocker",
            ),
            _finding(
                title=f"token={FIXTURE_SECRET} should be redacted",
                evidence="[REDACTED]",
                dedupe_key="secret-warning",
            )
        ]
    )

    first = _gate(final_review)
    second = _gate(final_review)
    first_rendered = json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    second_rendered = json.dumps(second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    assert first_rendered == second_rendered
    assert FIXTURE_SECRET not in first_rendered
    assert str(tmp_path) not in first_rendered
    assert "[LOCAL_PATH_REDACTED]" in first_rendered


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


def test_load_intake_accepts_the_modern_schema_pair(tmp_path: Path) -> None:
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version=1)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    intake = load_intake(path)

    assert intake.schema_id == "agent-review.intake.v1"
    assert intake.schema_version == 1


def test_load_intake_accepts_the_legacy_schema_pair_during_the_compatibility_window(tmp_path: Path) -> None:
    raw = _minimal_intake_raw(schema_version="agent-review.intake.v1")
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    intake = load_intake(path)

    assert intake.status == "complete"


def test_load_intake_rejects_an_unsupported_integer_schema_version(tmp_path: Path) -> None:
    """Issue #146 thread 7 -- schema_version=2 alongside a correct schema_id
    was accepted before this fix, because this loader's own inline check only
    verified schema_id OR schema_version matched the schema, never that an
    integer schema_version equals exactly 1."""

    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version=2)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(QualityGateError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_an_integer_schema_version_without_a_schema_id(tmp_path: Path) -> None:
    """Codex review of PR #156 -- schema_version=2 with schema_id absent
    entirely must also be rejected; the schema-less compatibility form only
    tolerates the descriptive-string schema_version, never a bare integer."""

    raw = _minimal_intake_raw(schema_version=2)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(QualityGateError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_the_hybrid_schema_id_with_descriptive_schema_version(tmp_path: Path) -> None:
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v1", schema_version="agent-review.intake.v1")
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(QualityGateError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


def test_load_intake_rejects_an_unknown_schema_id(tmp_path: Path) -> None:
    raw = _minimal_intake_raw(schema_id="agent-review.intake.v2", schema_version=1)
    path = tmp_path / "intake.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(QualityGateError) as exc_info:
        load_intake(path)

    assert exc_info.value.error_class == "intake_invalid"


# ── AgentEscala#675 / Fix A: the gate never decides on model text ────────────


def test_quality_gate_does_not_carry_a_model_reported_limitations_field() -> None:
    """The model's self-report is preserved on `ChunkResults` and rendered
    from `FinalReview` (`final_synthesizer.render_final_review_markdown`'s
    "## Observações do modelo" section, published verbatim as
    `final-review.md` -- the artifact `consume-aiops-quality-gate.py`
    actually embeds in the comment). No consumer, upstream or target-side,
    reads a copy of it off `ReviewQualityGate`: adding one here would be a
    field no code ever looks at. So the gate does not carry one at all
    (#675 corrective audit, finding 4)."""
    assert "model_reported_limitations" not in ReviewQualityGate.model_fields


def test_gate_ignores_model_reported_limitations_on_the_final_review_input() -> None:
    """`FinalReview.model_reported_limitations` is a real field on the input
    this function reads (`raw`), so proving decision-neutrality here means
    proving the gate is unaffected by its presence -- not that it echoes a
    copy without deciding on it: not status, not verdict, not score, not
    manual_review_required, not blocked_reasons."""
    clean = _gate(_final_review())
    with_model_text = _gate(
        _final_review(
            model_reported_limitations=[
                "contracts_context_not_relevant:The contracts context was not relevant here.",
            ]
        )
    )

    assert clean.limitations == with_model_text.limitations
    assert clean.status == with_model_text.status
    assert clean.normalized_verdict == with_model_text.normalized_verdict
    assert clean.quality_score == with_model_text.quality_score
    assert clean.manual_review_required == with_model_text.manual_review_required
    assert clean.blocked_reasons == with_model_text.blocked_reasons


def test_model_text_cannot_spend_the_gate_quality_score_budget() -> None:
    """`_quality_score` charges 0.02 per limitation. While model prose shared
    the deterministic list, a verbose model measurably lowered the score of a
    review it had not actually found anything wrong with."""
    clean = _gate(_final_review())
    chatty = _gate(
        _final_review(
            model_reported_limitations=[f"noise_{index}:padding" for index in range(20)]
        )
    )

    assert chatty.quality_score == clean.quality_score


def test_schema_transport_and_coverage_failures_stay_distinct_causes() -> None:
    """Acceptance criterion #2: schema mismatch, HTTP 5xx and coverage
    missing are never aggregated into one failure. They arrive through
    different producers and must survive as separate reason codes."""
    gate = _gate(
        _final_review(
            status="degraded",
            verdict="manual_review_required",
            limitations=["coverage_missing"],
        ),
        _chunk_results(
            status="degraded",
            limitations=[
                "agent_router_call_failed:schema_mismatch",
                "agent_router_call_failed:http_5xx",
            ],
            coverage=ChunkResultsCoverage(),
        ),
    )

    assert "agent_router_call_failed:schema_mismatch" in gate.limitations
    assert "agent_router_call_failed:http_5xx" in gate.limitations
    assert "coverage_missing" in gate.limitations
    assert len({
        "agent_router_call_failed:schema_mismatch",
        "agent_router_call_failed:http_5xx",
        "coverage_missing",
    } & set(gate.limitations)) == 3


@pytest.mark.parametrize(
    ("carrier", "malformed", "extra_reason"),
    [
        pytest.param("chunks_parsed", {EXECUTION_CHUNK_IDS[0]: []}, None, id="parsed-dict"),
        pytest.param("chunks_parsed", (EXECUTION_CHUNK_IDS[0],), None, id="parsed-tuple"),
        pytest.param("chunks_parsed", {EXECUTION_CHUNK_IDS[0]}, None, id="parsed-set"),
        pytest.param("chunks_parsed", 1, None, id="parsed-scalar"),
        pytest.param("chunks_failed", {}, None, id="failed-empty-dict"),
        pytest.param("files_reviewed", {EXECUTION_FILES[0]: []}, None, id="reviewed-dict"),
        pytest.param("files_reviewed", EXECUTION_FILES[0], None, id="reviewed-scalar"),
        pytest.param("files_partial", (EXECUTION_FILES[0],), None, id="partial-tuple"),
        pytest.param("files_not_reviewed", {EXECUTION_FILES[0]}, None, id="not-reviewed-set"),
        pytest.param("target_repo", ["mglpsw/AgentEscala"], "target_repo_mismatch", id="target-list"),
    ],
)
def test_u2_gate_rejects_post_validation_malformed_chunk_results_shape(
    carrier: str,
    malformed: object,
    extra_reason: str | None,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    if carrier in {"chunks_parsed", "chunks_failed", "target_repo"}:
        setattr(results, carrier, malformed)
    else:
        setattr(results.coverage, carrier, malformed)
    raw = _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    raw.pop("inputs")

    gate = _gate(raw, results)

    assert "chunk_results_structure_invalid" in gate.limitations
    if extra_reason is not None:
        assert extra_reason in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("carrier", "malformed"),
    [
        pytest.param("chunks", {"chunk": EXECUTION_CHUNK_IDS[0]}, id="chunks-dict"),
        pytest.param("files_covered", (EXECUTION_FILES[0],), id="covered-tuple"),
        pytest.param("files_partially_covered", {EXECUTION_FILES[0]}, id="partial-set"),
        pytest.param("files_not_covered", EXECUTION_FILES[0], id="not-covered-scalar"),
        pytest.param("chunk_files", {EXECUTION_FILES[0]: []}, id="chunk-files-dict"),
        pytest.param("chunk_files", (EXECUTION_FILES[0],), id="chunk-files-tuple"),
        pytest.param("chunk_files", {EXECUTION_FILES[0]}, id="chunk-files-set"),
        pytest.param("chunk_files", EXECUTION_FILES[0], id="chunk-files-scalar"),
    ],
)
def test_u2_gate_rejects_post_validation_malformed_chunk_plan_shape(
    carrier: str,
    malformed: object,
) -> None:
    plan = _execution_plan()
    if carrier == "chunk_files":
        plan.chunks[0].files = malformed  # type: ignore[assignment]
    else:
        setattr(plan, carrier, malformed)
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    raw = _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan)

    assert "chunk_plan_structure_invalid" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("authority", "field", "mutated", "reason"),
    [
        pytest.param("results", "schema_id", "agent-review.not-chunk-results.v1", "chunk_results_schema_mismatch", id="result-schema-id"),
        pytest.param("results", "schema_version", 2, "chunk_results_schema_mismatch", id="result-schema-version-two"),
        pytest.param("results", "schema_version", True, "chunk_results_schema_mismatch", id="result-schema-version-bool"),
        pytest.param("plan", "schema_id", "agent-review.not-plan.v1", "chunk_plan_schema_mismatch", id="plan-schema-id"),
        pytest.param("plan", "schema_version", 2, "chunk_plan_schema_mismatch", id="plan-schema-version-two"),
        pytest.param("plan", "schema_version", True, "chunk_plan_schema_mismatch", id="plan-schema-version-bool"),
    ],
)
def test_u2_gate_rejects_mutated_authority_envelope(
    authority: str,
    field: str,
    mutated: object,
    reason: str,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    setattr(results if authority == "results" else plan, field, mutated)
    raw = _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan)

    assert reason in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("field", "mutated", "supply_plan"),
    [
        pytest.param("schema_version", True, True, id="schema-version-bool"),
        pytest.param("chunk_count", True, True, id="chunk-count-bool"),
        pytest.param("chunk_count", 1.0, True, id="chunk-count-float"),
        pytest.param("target_repo", "mglpsw/OtherRepo", False, id="target-without-plan"),
        pytest.param("target_repo", "mglpsw/OtherRepo", True, id="target-with-plan"),
    ],
)
def test_u2_gate_rejects_exact_type_or_target_plan_ref_confusion(
    field: str,
    mutated: object,
    supply_plan: bool,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref.update(
        {
            "source": plan.source,
            "status": plan.status,
            "created_at": plan.created_at,
            "chunk_count": len(plan.chunks),
            "target_repo": plan.target_repo,
        }
    )
    results.chunk_plan_ref[field] = mutated
    raw = _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan if supply_plan else None)

    assert "chunk_plan_ref_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    "inputs",
    [
        pytest.param(
            {"chunk_results": {"schema_version": True}},
            id="chunk-results-schema-bool",
        ),
        pytest.param(
            {"chunk_plan": {"chunk_count": 1.0}},
            id="chunk-plan-count-float",
        ),
    ],
)
def test_u2_gate_rejects_exact_type_confusion_in_final_input_ref(
    inputs: dict[str, object],
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )

    gate = _gate(
        _final_review(inputs=inputs, coverage=_coverage(reviewed=[EXECUTION_FILES[0]])),
        results,
        chunk_plan=plan,
    )

    assert "final_review_input_mismatch" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.manual_review_required is True


@pytest.mark.parametrize("include_target_repo", [False, True], ids=["target-omitted", "target-exact"])
def test_u2_gate_exact_list_and_reference_types_remain_positive(
    include_target_repo: bool,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref.update(
        {
            "source": plan.source,
            "status": plan.status,
            "created_at": plan.created_at,
            "chunk_count": len(plan.chunks),
        }
    )
    chunk_results_ref: dict[str, object] = {
        "schema_id": results.schema_id,
        "schema_version": results.schema_version,
        "source": results.source,
        "status": results.status,
        "created_at": results.created_at,
    }
    chunk_plan_ref: dict[str, object] = {
        "schema_id": plan.schema_id,
        "schema_version": plan.schema_version,
        "source": plan.source,
        "status": plan.status,
        "created_at": plan.created_at,
        "chunk_count": len(plan.chunks),
    }
    if include_target_repo:
        results.chunk_plan_ref["target_repo"] = results.target_repo
        chunk_results_ref["target_repo"] = results.target_repo
        chunk_plan_ref["target_repo"] = plan.target_repo

    gate = _gate(
        _final_review(
            inputs={"chunk_results": chunk_results_ref, "chunk_plan": chunk_plan_ref},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        results,
        chunk_plan=plan,
    )

    assert type(chunk_results_ref["schema_version"]) is int
    assert type(chunk_plan_ref["schema_version"]) is int
    assert type(chunk_plan_ref["chunk_count"]) is int
    assert "chunk_results_structure_invalid" not in gate.limitations
    assert "chunk_plan_structure_invalid" not in gate.limitations
    assert "final_review_input_mismatch" not in gate.limitations
    assert "chunk_plan_ref_mismatch" not in gate.limitations
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False


@pytest.mark.parametrize(
    ("reported_count", "expect_mismatch"),
    [
        pytest.param(1, False, id="matching-retained-ref"),
        pytest.param(2, True, id="contradictory-retained-ref"),
    ],
)
def test_u2_gate_compares_final_plan_ref_to_retained_result_ref_without_plan(
    reported_count: int,
    expect_mismatch: bool,
) -> None:
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    results.chunk_plan_ref.update(
        {
            "source": "aiops-semantic-chunk-planner",
            "status": "complete",
            "chunk_count": 1,
            "target_repo": results.target_repo,
        }
    )
    final_plan_ref = {"provided": True, **results.chunk_plan_ref}
    final_plan_ref["chunk_count"] = reported_count

    gate = _gate(
        _final_review(
            inputs={"chunk_plan": final_plan_ref},
            coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
        ),
        results,
    )

    assert ("final_review_input_mismatch" in gate.limitations) is expect_mismatch
    if expect_mismatch:
        assert gate.status == "manual_review_required"
        assert gate.normalized_verdict == "manual_review_required"
        assert gate.manual_review_required is True
    else:
        assert gate.status == "passed"
        assert gate.normalized_verdict == "approved"
        assert gate.manual_review_required is False


def test_u2_gate_rejects_boolean_schema_version_mutated_after_final_validation() -> None:
    document = validate_final_review_document(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    )
    document.raw["schema_version"] = True

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert "final_review_mutated_after_validation" in gate.limitations
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.normalized_verdict != "approved"
    assert gate.manual_review_required is True


def test_u2_gate_exact_integer_final_schema_version_remains_positive() -> None:
    document = validate_final_review_document(
        _final_review(coverage=_coverage(reviewed=[EXECUTION_FILES[0]]))
    )

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
        ),
    )

    assert type(document.raw["schema_version"]) is int
    assert "final_review_mutated_after_validation" not in gate.limitations
    assert gate.status == "passed"
    assert gate.normalized_verdict == "approved"
    assert gate.manual_review_required is False


def test_u2_gate_snapshot_mutation_revokes_matching_p1_causal_trust() -> None:
    raw = _final_review(
        verdict="changes_requested",
        confirmed_findings=[
            _finding(file_path=EXECUTION_FILES[0], source_chunks=[EXECUTION_CHUNK_IDS[0]])
        ],
        coverage=_coverage(reviewed=EXECUTION_FILES.copy()),
    )
    raw.pop("inputs")
    document = validate_final_review_document(raw)
    document.raw["coverage"]["files_reviewed"].reverse()  # type: ignore[index,union-attr]
    document._snapshot["coverage"]["files_reviewed"].reverse()  # type: ignore[index,union-attr]

    gate = evaluate_review_quality_gate(
        document,
        _chunk_results(
            chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
            coverage=ChunkResultsCoverage(files_reviewed=EXECUTION_FILES.copy()),
        ),
    )

    assert "final_review_mutated_after_validation" in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.normalized_verdict != "changes_requested"
    assert gate.manual_review_required is True


@pytest.mark.parametrize(
    ("case", "source_chunk", "semantic_group", "reason"),
    [
        pytest.param(
            "foreign",
            "chunk-99-tests",
            "tests",
            "source_chunk_not_in_plan",
            id="foreign-source-chunk",
        ),
        pytest.param(
            "wrong-file",
            EXECUTION_CHUNK_IDS[1],
            EXECUTION_GROUPS[1],
            "source_chunk_not_assigned_to_file",
            id="source-chunk-assigned-elsewhere",
        ),
    ],
)
def test_u2_gate_plan_source_must_authorize_p1_file(
    case: str,
    source_chunk: str,
    semantic_group: str,
    reason: str,
) -> None:
    plan = _execution_plan(chunk_count=1 if case == "foreign" else 2)
    parsed = (
        [EXECUTION_CHUNK_IDS[0], source_chunk]
        if case == "foreign"
        else EXECUTION_CHUNK_IDS.copy()
    )
    results = _chunk_results(
        chunks_parsed=parsed,
        coverage=ChunkResultsCoverage(files_reviewed=EXECUTION_FILES[: len(plan.chunks)]),
    )
    raw = _final_review(
        verdict="changes_requested",
        confirmed_findings=[
            _finding(
                chunk_id=source_chunk,
                semantic_group=semantic_group,
                file_path=EXECUTION_FILES[0],
                source_chunks=[source_chunk],
            )
        ],
        coverage=_coverage(reviewed=EXECUTION_FILES[: len(plan.chunks)]),
    )
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan)

    assert any(reason in warning for warning in gate.warnings)
    assert not any(blocked.startswith("confirmed_blocker:") for blocked in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.normalized_verdict != "changes_requested"
    assert gate.manual_review_required is True


def test_u2_gate_plan_assigned_p1_remains_a_confirmed_blocker() -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    raw = _final_review(
        verdict="changes_requested",
        confirmed_findings=[
            _finding(
                chunk_id=EXECUTION_CHUNK_IDS[0],
                semantic_group=EXECUTION_GROUPS[0],
                file_path=EXECUTION_FILES[0],
                source_chunks=[EXECUTION_CHUNK_IDS[0]],
            )
        ],
        coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
    )
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan)

    assert not any("source_chunk_not_" in warning for warning in gate.warnings)
    assert any(reason.startswith("confirmed_blocker:P1:") for reason in gate.blocked_reasons)
    assert gate.normalized_verdict == "changes_requested"
    assert gate.manual_review_required is False


@pytest.mark.parametrize(
    ("case", "trigger_reason"),
    [
        pytest.param("plan-ref", "chunk_plan_ref_mismatch", id="plan-ref-mismatch"),
        pytest.param("result-status", "chunk_results_status_invalid", id="result-status-invalid"),
    ],
)
def test_u2_gate_integrity_failure_revokes_matching_p1_causal_trust(
    case: str,
    trigger_reason: str,
) -> None:
    plan = _execution_plan()
    results = _chunk_results(
        chunks_parsed=[EXECUTION_CHUNK_IDS[0]],
        coverage=ChunkResultsCoverage(files_reviewed=[EXECUTION_FILES[0]]),
    )
    if case == "plan-ref":
        results.chunk_plan_ref["status"] = "partial"
    else:
        results.status = "mystery"  # type: ignore[assignment]
    raw = _final_review(
        verdict="changes_requested",
        confirmed_findings=[
            _finding(
                file_path=EXECUTION_FILES[0],
                source_chunks=[EXECUTION_CHUNK_IDS[0]],
            )
        ],
        coverage=_coverage(reviewed=[EXECUTION_FILES[0]]),
    )
    raw.pop("inputs")

    gate = _gate(raw, results, chunk_plan=plan)

    assert trigger_reason in gate.limitations
    assert any("input_binding_mismatch" in warning for warning in gate.warnings)
    assert not any(reason.startswith("confirmed_blocker:") for reason in gate.blocked_reasons)
    assert gate.status == "manual_review_required"
    assert gate.normalized_verdict == "manual_review_required"
    assert gate.normalized_verdict != "changes_requested"
    assert gate.manual_review_required is True
