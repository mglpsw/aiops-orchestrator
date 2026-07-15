from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from app.agent_review.schemas import FinalReviewVerdict, ReviewQualityGate
from app.agent_review.telemetry import build_review_telemetry, load_final_review, load_optional_artifact, load_quality_gate


FIXTURE_SECRET = "AGENTESCALA_PHASE3_TELEMETRY_SECRET"


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "severity": "P1",
        "title": "Schedule validation skips inactive doctor guard",
        "file_path": "backend/services/schedule.py",
        "line_or_hunk": "L42-L48",
        "evidence": "The changed hunk removes the inactive doctor guard.",
        "source_artifact": "artifact:file-diff-context",
        "impact": "Inactive doctors could be scheduled.",
        "confidence": "high",
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
        "confirmed_findings": [_finding()],
        "risks": [
            {
                "chunk_id": "chunk-02-tests",
                "semantic_group": "tests",
                "source": "downgraded_finding",
                "title": "Pytest claim needs checks source",
                "reason": "unsupported_test_failure_source",
                "source_chunks": ["chunk-02-tests"],
                "semantic_groups": ["tests"],
            }
        ],
        "limitations": ["coverage_reported_files_not_in_plan"],
        "rejected_summary": {"total": 2, "by_reason": {"missing_file_path": 2}, "sample_titles": []},
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": ["tests/test_schedule.py"],
            "files_not_reviewed": ["docs/release.md"],
            "expected_files": ["backend/services/schedule.py", "tests/test_schedule.py", "docs/release.md"],
            "missing_expected_files": [],
            "extra_reported_files": [],
            "comparison_available": True,
        },
        "counts": {
            "confirmed_findings_total": 1,
            "findings_by_severity": {"P1": 1},
            "risks_total": 1,
            "risks_by_source": {"downgraded_finding": 1},
            "rejected_findings_total": 2,
            "rejected_findings_by_reason": {"missing_file_path": 2},
            "limitations_total": 1,
            "chunks_parsed": 2,
            "chunks_failed": 1,
        },
        "inputs": {"review_mode": "offline", "contract_pack": "phase05"},
        "bundle_chars": 1234,
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _quality_gate(**overrides: object) -> ReviewQualityGate:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.quality-gate.v1",
        "source": "aiops-review-quality-gate",
        "status": "passed",
        "normalized_verdict": "changes_requested",
        "quality_score": 0.98,
        "manual_review_required": False,
        "second_opinion_requested": False,
        "second_opinion_status": "not_required",
        "blocked_reasons": ["approved_with_confirmed_blocker"],
        "warnings": ["chunk_failed:chunk-02-tests:chunk_response_missing"],
        "limitations": ["chunks_failed_present"],
        "inputs": {"critical_pr": True},
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return ReviewQualityGate.model_validate(payload)


def _chunk_results() -> dict[str, object]:
    return {
        "schema_version": 1,
        "schema_id": "agent-review.chunk-results.v1",
        "source": "aiops-review-parse-chunks",
        "target_repo": "mglpsw/AgentEscala",
        "chunk_plan_ref": {"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        "chunks_parsed": ["chunk-01-primary_backend_logic", "chunk-02-tests"],
        "chunks_failed": [
            {
                "chunk_id": "chunk-03-docs_changelog",
                "semantic_group": "docs_changelog",
                "error_class": "chunk_response_missing",
                "message": "chunk response file is missing",
            }
        ],
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "rejected_findings": [{"reason": "missing_file_path"}],
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": ["tests/test_schedule.py"],
            "files_not_reviewed": ["docs/release.md"],
        },
        "status": "partial",
        "created_at": "2026-06-02T00:00:00Z",
    }


def _chunk_plan() -> dict[str, object]:
    return {
        "schema_version": 1,
        "schema_id": "agent-review.semantic-chunk-plan.v1",
        "source": "aiops-semantic-chunk-planner",
        "target_repo": "mglpsw/AgentEscala",
        "max_parallel_blocks": 3,
        "chunks": [
            {"chunk_id": "chunk-01-primary_backend_logic", "coverage": "complete"},
            {"chunk_id": "chunk-02-tests", "coverage": "partial"},
            {"chunk_id": "chunk-03-docs_changelog", "coverage": "degraded"},
        ],
        "files_covered": ["backend/services/schedule.py"],
        "files_partially_covered": ["tests/test_schedule.py"],
        "files_not_covered": ["docs/release.md"],
        "limitations": [],
        "status": "partial",
    }


def test_review_telemetry_collects_authoritative_gate_and_metrics() -> None:
    telemetry = build_review_telemetry(
        final_review=_final_review(),
        quality_gate=_quality_gate(),
        chunk_results=_chunk_results(),
        chunk_plan=_chunk_plan(),
        redaction_report={
            "schema_version": "agent-review.redaction-report.v1",
            "source": "aiops-review-intake",
            "output_safe_for_llm": True,
            "secret_like_values_found": 3,
            "redacted_lines_present": True,
        },
        pr_number=61,
        commit_sha="abc123",
    )

    assert telemetry.schema_id == "agent-review.telemetry.v1"
    assert telemetry.source == "aiops-review-telemetry"
    assert telemetry.target == {
        "commit_sha": "abc123",
        "critical_pr": True,
        "pr_number": 61,
        "repository": "mglpsw/AgentEscala",
    }
    assert telemetry.pipeline["chunk_count"] == 3
    assert telemetry.pipeline["chunks_reviewed"] == 2
    assert telemetry.pipeline["chunks_failed"] == 1
    assert telemetry.pipeline["chunks_degraded"] == 1
    assert telemetry.coverage["files_covered"] == 1
    assert telemetry.coverage["files_partial"] == 1
    assert telemetry.coverage["files_not_covered"] == 1
    assert telemetry.findings["by_severity"] == {"P0": 0, "P1": 1, "P2": 0, "P3": 0}
    assert telemetry.findings["downgraded_findings_count"] == 1
    assert telemetry.findings["rejected_findings_count"] == 2
    assert telemetry.review["verdict"] == "approved"
    assert telemetry.review["review_mode"] == "offline"
    assert telemetry.quality_gate["normalized_verdict"] == "changes_requested"
    assert telemetry.quality_gate["manual_review_required"] is False
    assert telemetry.redaction["status"] == "safe"
    assert telemetry.performance["bundle_chars"] == 1234


def test_review_telemetry_warns_on_artifact_divergence_without_recalculating_gate() -> None:
    final_review = _final_review(
        verdict="approved",
        counts={**_final_review()["counts"], "confirmed_findings_total": 0},
    )
    telemetry = build_review_telemetry(
        final_review=final_review,
        quality_gate=_quality_gate(
            status="manual_review_required",
            normalized_verdict="manual_review_required",
            manual_review_required=True,
        ),
        chunk_results={
            **_chunk_results(),
            "chunks_parsed": ["chunk-01-primary_backend_logic"],
            "chunks_failed": [],
        },
        chunk_plan=_chunk_plan(),
    )

    assert telemetry.quality_gate["status"] == "manual_review_required"
    assert telemetry.quality_gate["normalized_verdict"] == "manual_review_required"
    assert telemetry.quality_gate["manual_review_required"] is True
    assert telemetry.findings["confirmed_count"] == 0
    assert "artifact_divergence:final_review_verdict_vs_quality_gate_normalized_verdict" in telemetry.warnings
    assert "artifact_divergence:chunk_plan_vs_chunk_results" in telemetry.warnings
    assert "artifact_divergence:final_review_confirmed_findings_count" in telemetry.warnings


def test_review_telemetry_treats_empty_final_review_coverage_lists_as_authoritative() -> None:
    telemetry = build_review_telemetry(
        final_review=_final_review(
            coverage={
                "files_reviewed": [],
                "files_partial": [],
                "files_not_reviewed": [],
                "expected_files": [],
            }
        ),
        quality_gate=_quality_gate(),
        chunk_results={
            **_chunk_results(),
            "coverage": {
                "files_reviewed": ["backend/a.py"],
                "files_partial": ["tests/test_a.py"],
                "files_not_reviewed": ["docs/a.md"],
            },
        },
        chunk_plan={
            **_chunk_plan(),
            "files_covered": ["backend/a.py"],
            "files_partially_covered": ["tests/test_a.py"],
            "files_not_covered": ["docs/a.md"],
        },
    )

    assert telemetry.coverage["expected_files"] == 0
    assert telemetry.coverage["files_covered"] == 0
    assert telemetry.coverage["files_partial"] == 0
    assert telemetry.coverage["files_not_covered"] == 0


def test_review_telemetry_coverage_fields_missing_from_final_review_use_fallbacks() -> None:
    telemetry = build_review_telemetry(
        final_review=_final_review(coverage={}),
        quality_gate=_quality_gate(),
        chunk_results=_chunk_results(),
        chunk_plan=_chunk_plan(),
    )

    assert telemetry.coverage["expected_files"] == 3
    assert telemetry.coverage["files_covered"] == 1
    assert telemetry.coverage["files_partial"] == 1
    assert telemetry.coverage["files_not_covered"] == 1


def test_review_telemetry_invalid_final_review_coverage_fields_use_fallbacks() -> None:
    telemetry = build_review_telemetry(
        final_review=_final_review(
            coverage={
                "files_reviewed": "backend/a.py",
                "files_partial": {"path": "tests/test_a.py"},
                "files_not_reviewed": 1,
                "expected_files": "backend/a.py",
            }
        ),
        quality_gate=_quality_gate(),
        chunk_results=_chunk_results(),
        chunk_plan=_chunk_plan(),
    )

    assert telemetry.coverage["expected_files"] == 3
    assert telemetry.coverage["files_covered"] == 1
    assert telemetry.coverage["files_partial"] == 1
    assert telemetry.coverage["files_not_covered"] == 1


def test_review_telemetry_non_empty_final_review_coverage_lists_are_authoritative() -> None:
    telemetry = build_review_telemetry(
        final_review=_final_review(
            coverage={
                "files_reviewed": ["backend/final.py"],
                "files_partial": ["tests/test_final.py"],
                "files_not_reviewed": ["docs/final.md"],
                "expected_files": ["backend/final.py", "tests/test_final.py", "docs/final.md"],
            }
        ),
        quality_gate=_quality_gate(),
        chunk_results={
            **_chunk_results(),
            "coverage": {
                "files_reviewed": ["backend/chunk.py", "backend/chunk_extra.py"],
                "files_partial": [],
                "files_not_reviewed": [],
            },
        },
        chunk_plan={
            **_chunk_plan(),
            "files_covered": ["backend/chunk.py", "backend/chunk_extra.py"],
            "files_partially_covered": [],
            "files_not_covered": [],
        },
    )

    assert telemetry.coverage["expected_files"] == 3
    assert telemetry.coverage["files_covered"] == 1
    assert telemetry.coverage["files_partial"] == 1
    assert telemetry.coverage["files_not_covered"] == 1


def test_review_telemetry_measured_zero_coverage_remains_zero_and_deterministic() -> None:
    final_review = _final_review(
        coverage={
            "files_reviewed": [],
            "files_partial": [],
            "files_not_reviewed": [],
            "expected_files": [],
        }
    )
    chunk_results = {
        **_chunk_results(),
        "coverage": {
            "files_reviewed": ["backend/a.py"],
            "files_partial": ["tests/test_a.py"],
            "files_not_reviewed": ["docs/a.md"],
        },
    }
    first = build_review_telemetry(final_review=final_review, quality_gate=_quality_gate(), chunk_results=chunk_results)
    second = build_review_telemetry(final_review=final_review, quality_gate=_quality_gate(), chunk_results=chunk_results)
    rendered_first = json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    rendered_second = json.dumps(second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    assert first.coverage == {
        "expected_files": 0,
        "files_covered": 0,
        "files_not_covered": 0,
        "files_partial": 0,
        "status": "complete",
    }
    assert rendered_first == rendered_second


def test_review_telemetry_is_deterministic_and_sanitized(tmp_path: Path) -> None:
    final_review = _final_review(
        confirmed_findings=[
            _finding(
                title=f"token={FIXTURE_SECRET} should be redacted",
                file_path=str(tmp_path / "AgentEscala" / "backend" / "services" / "schedule.py"),
            )
        ],
        target_repo=str(tmp_path / "AgentEscala"),
    )

    first = build_review_telemetry(final_review=final_review, quality_gate=_quality_gate())
    second = build_review_telemetry(final_review=final_review, quality_gate=_quality_gate())
    rendered_first = json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    rendered_second = json.dumps(second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)

    assert rendered_first == rendered_second
    assert FIXTURE_SECRET not in rendered_first
    assert str(tmp_path) not in rendered_first
    assert "[LOCAL_PATH_REDACTED]" in rendered_first


def test_required_artifact_loaders_validate_schema(tmp_path: Path) -> None:
    final_review_path = tmp_path / "final-review.json"
    quality_gate_path = tmp_path / "review-quality-gate.json"
    final_review_path.write_text(json.dumps(_final_review(), sort_keys=True), encoding="utf-8")
    quality_gate_path.write_text(json.dumps(_quality_gate().model_dump(mode="json"), sort_keys=True), encoding="utf-8")

    assert load_final_review(final_review_path)["schema_id"] == "agent-review.final-review.v1"
    assert load_quality_gate(quality_gate_path).schema_id == "agent-review.quality-gate.v1"


@pytest.mark.parametrize("verdict", get_args(FinalReviewVerdict))
def test_load_final_review_accepts_canonical_verdicts(tmp_path: Path, verdict: str) -> None:
    final_review_path = tmp_path / "final-review.json"
    final_review_path.write_text(json.dumps(_final_review(verdict=verdict), sort_keys=True), encoding="utf-8")

    loaded = load_final_review(final_review_path)

    assert loaded["verdict"] == verdict


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("verdict", "not_a_valid_verdict"),
        ("verdict", ""),
        ("status", "not_a_valid_status"),
    ],
)
def test_load_final_review_rejects_unknown_verdict_and_status(tmp_path: Path, field: str, value: str) -> None:
    final_review_path = tmp_path / "final-review.json"
    final_review_path.write_text(json.dumps(_final_review(**{field: value}), sort_keys=True), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_final_review(final_review_path)

    assert getattr(exc_info.value, "error_class") == "final_review_invalid"


def test_optional_artifact_schema_version_mismatch_is_not_consumed(tmp_path: Path) -> None:
    chunk_results_path = tmp_path / "chunk-results.json"
    chunk_results_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-results.v1", "schema_version": 2}, sort_keys=True),
        encoding="utf-8",
    )

    chunk_results, limitations = load_optional_artifact(chunk_results_path, name="chunk_results")
    telemetry = build_review_telemetry(
        final_review=_final_review(),
        quality_gate=_quality_gate(),
        chunk_results=chunk_results,
        limitations=limitations,
    )

    assert chunk_results is None
    assert limitations == ["artifact_schema_version_mismatch:chunk_results"]
    assert "artifact_schema_version_mismatch:chunk_results" in telemetry.limitations
    assert telemetry.pipeline["chunk_results_status"] is None
