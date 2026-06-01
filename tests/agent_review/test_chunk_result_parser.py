from __future__ import annotations

import json
from pathlib import Path

from app.agent_review.chunk_result_parser import parse_chunk_results
from app.agent_review.schemas import SemanticChunk, SemanticChunkPlan


FIXTURE_SECRET = "AGENTESCALA_PHASE3_FIXTURE_SECRET"


def _chunk(
    *,
    chunk_id: str = "chunk-01-primary_backend_logic",
    group: str = "primary_backend_logic",
    files: list[str] | None = None,
) -> SemanticChunk:
    return SemanticChunk(
        chunk_id=chunk_id,
        semantic_group=group,  # type: ignore[arg-type]
        order_index=0,
        files=files if files is not None else ["backend/services/schedule.py"],
        artifacts=["artifact:file-diff-context", "artifact:checks"],
        contracts=["target_profile:domain_contracts"],
        coverage="complete",
        prompt_budget_chars=24_000,
        estimated_chars=512,
        limitations=[],
    )


def _plan(chunks: list[SemanticChunk] | None = None, *, status: str = "complete") -> SemanticChunkPlan:
    return SemanticChunkPlan(
        target_repo="mglpsw/AgentEscala",
        max_parallel_blocks=6,
        chunks=chunks if chunks is not None else [_chunk()],
        files_covered=["backend/services/schedule.py"],
        status=status,  # type: ignore[arg-type]
    )


def _responses_dir(tmp_path: Path) -> Path:
    path = tmp_path / "chunk-responses"
    path.mkdir()
    return path


def _write_response(
    responses_dir: Path,
    *,
    chunk: SemanticChunk,
    confirmed_findings: list[dict[str, object]] | None = None,
    risks: list[dict[str, object]] | None = None,
    coverage_notes: dict[str, object] | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": confirmed_findings if confirmed_findings is not None else [],
        "risks": risks if risks is not None else [],
        "limitations": [],
        "coverage_notes": coverage_notes if coverage_notes is not None else {"files_reviewed": chunk.files},
    }
    path = responses_dir / f"{chunk.chunk_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _finding(**overrides: object) -> dict[str, object]:
    finding: dict[str, object] = {
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
    finding.update(overrides)
    return finding


def test_parse_valid_response_keeps_confirmed_p2_finding(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk, confirmed_findings=[_finding(severity="P2")])

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.status == "complete"
    assert len(results.confirmed_findings) == 1
    assert results.confirmed_findings[0].severity == "P2"
    assert results.confirmed_findings[0].file_path == "backend/services/schedule.py"
    assert results.risks == []
    assert results.rejected_findings == []


def test_p1_without_evidence_is_downgraded_to_risk(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[_finding(severity="P1", evidence=None, dedupe_key=None)],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.status == "complete"
    assert results.confirmed_findings == []
    assert results.risks[0].source == "downgraded_finding"
    assert results.risks[0].reason == "missing_required_evidence"


def test_finding_without_file_path_is_rejected(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk, confirmed_findings=[_finding(file_path=None)])

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.confirmed_findings == []
    assert results.rejected_findings[0].reason == "missing_file_path"


def test_finding_outside_chunk_is_rejected(tmp_path: Path) -> None:
    chunk = _chunk(files=["backend/services/schedule.py"])
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[_finding(file_path="backend/services/other.py")],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.rejected_findings[0].reason == "file_not_in_chunk"
    assert results.confirmed_findings == []


def test_redacted_only_evidence_does_not_confirm_p1(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[_finding(severity="P1", evidence="[REDACTED]", dedupe_key=None)],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.confirmed_findings == []
    assert results.risks[0].reason == "redacted_or_placeholder_only_evidence"


def test_speculative_finding_is_downgraded_to_risk(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[_finding(evidence="This could allow inactive doctors to be scheduled.", dedupe_key=None)],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.confirmed_findings == []
    assert results.risks[0].reason == "speculative_language"


def test_unsupported_test_failure_source_is_downgraded(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[
            _finding(
                title="Pytest failure in schedule validation",
                evidence="pytest reports a failed test.",
                source_artifact="artifact:file-diff-context",
                dedupe_key=None,
            )
        ],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.confirmed_findings == []
    assert results.risks[0].reason == "unsupported_test_failure_source"


def test_invalid_response_marks_chunk_failed(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    (responses / f"{chunk.chunk_id}.json").write_text("{not-json", encoding="utf-8")

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.status == "degraded"
    assert results.chunks_parsed == []
    assert results.chunks_failed[0].error_class == "chunk_response_json_invalid"


def test_missing_response_generates_chunk_parse_failure_and_partial_status(tmp_path: Path) -> None:
    first = _chunk(chunk_id="chunk-01-primary_backend_logic", files=["backend/services/schedule.py"])
    second = _chunk(
        chunk_id="chunk-02-api_schema_contract",
        group="api_schema_contract",
        files=["backend/api/schedule.py"],
    )
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=first)

    results = parse_chunk_results(_plan([first, second]), responses_dir=responses)

    assert results.status == "partial"
    assert results.chunks_parsed == [first.chunk_id]
    assert results.chunks_failed[0].chunk_id == second.chunk_id
    assert results.chunks_failed[0].error_class == "chunk_response_missing"


def test_parser_does_not_generate_findings_when_response_has_none(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk)

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.status == "complete"
    assert results.confirmed_findings == []
    assert results.rejected_findings == []
    assert results.risks == []


def test_parser_never_raises_severity(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk, confirmed_findings=[_finding(severity="P3")])

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.confirmed_findings[0].severity == "P3"


def test_risk_source_distinguishes_chunk_risk_and_downgraded_finding(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        risks=[
            {
                "title": "Schedule validation needs follow-up",
                "reason": "The chunk did not include enough surrounding context.",
                "missing_evidence": "caller path",
                "suggested_validation": "Review local code intelligence.",
            }
        ],
        confirmed_findings=[_finding(severity="P1", evidence=None, dedupe_key=None)],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert [risk.source for risk in results.risks] == ["chunk_risk", "downgraded_finding"]


def test_dedupe_does_not_consume_key_for_downgraded_finding(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[
            _finding(dedupe_key="same-key", evidence=None),
            _finding(dedupe_key="same-key"),
        ],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert len(results.confirmed_findings) == 1
    assert results.confirmed_findings[0].dedupe_key == "same-key"
    assert results.risks[0].source == "downgraded_finding"
    assert results.risks[0].reason == "missing_required_evidence"
    assert results.rejected_findings == []


def test_dedupe_rejects_second_confirmed_finding_with_same_key(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[
            _finding(dedupe_key="same-key"),
            _finding(title="Same issue repeated", dedupe_key="same-key"),
        ],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert len(results.confirmed_findings) == 1
    assert results.rejected_findings[0].reason == "duplicate_dedupe_key"


def test_dedupe_uses_structural_key_without_writing_dedupe_key(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    finding = _finding(dedupe_key=None)
    responses_payload = [finding, dict(finding)]
    _write_response(responses, chunk=chunk, confirmed_findings=responses_payload)

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert len(results.confirmed_findings) == 1
    assert results.confirmed_findings[0].dedupe_key is None
    assert results.rejected_findings[0].reason == "duplicate_dedupe_key"


def test_output_does_not_contain_secret_fixture(tmp_path: Path) -> None:
    secret_path = f"backend/services/token={FIXTURE_SECRET}.py"
    chunk = _chunk(files=[secret_path])
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[
            _finding(
                file_path=secret_path,
                evidence=f"token={FIXTURE_SECRET} appears in the changed hunk.",
                dedupe_key=None,
            )
        ],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    rendered = results.model_dump_json()
    assert FIXTURE_SECRET not in rendered
    assert "[REDACTED]" in rendered


def test_coverage_notes_filter_out_files_not_assigned_to_chunk(tmp_path: Path) -> None:
    chunk = _chunk(files=["backend/api/a.py"])
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={
            "files_reviewed": ["backend/api/a.py", "frontend/src/other.jsx"],
            "files_partial": ["frontend/src/partial.jsx"],
            "files_not_reviewed": ["frontend/src/not_reviewed.jsx"],
        },
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.coverage.files_reviewed == ["backend/api/a.py"]
    assert results.coverage.files_partial == []
    assert results.coverage.files_not_reviewed == []
    assert "frontend/src/other.jsx" not in results.model_dump_json()
    assert f"coverage_file_not_in_chunk:{chunk.chunk_id}" in results.limitations
