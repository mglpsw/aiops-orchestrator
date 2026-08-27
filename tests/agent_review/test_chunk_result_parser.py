from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_review.chunk_result_parser import ChunkResultParserError, parse_chunk_results
from app.agent_review.schemas import ChunkResults, SemanticChunk, SemanticChunkPlan


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
    limitations: list[dict[str, object]] | None = None,
    coverage_notes: dict[str, object] | None = None,
) -> Path:
    payload = {
        "schema_version": 1,
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "confirmed_findings": confirmed_findings if confirmed_findings is not None else [],
        "risks": risks if risks is not None else [],
        "limitations": limitations if limitations is not None else [],
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


def _assert_total_coverage_partition(
    results: ChunkResults,
    *,
    expected: list[str],
    reviewed: list[str],
    partial: list[str],
    not_reviewed: list[str],
) -> None:
    assert results.coverage.files_reviewed == reviewed
    assert results.coverage.files_partial == partial
    assert results.coverage.files_not_reviewed == not_reviewed
    reported = [
        *results.coverage.files_reviewed,
        *results.coverage.files_partial,
        *results.coverage.files_not_reviewed,
    ]
    assert sorted(reported) == sorted(expected)
    assert len(reported) == len(set(reported))


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
    assert "coverage_expected_files_missing" not in results.limitations
    assert results.status == "partial"


def test_u2_all_noncritical_files_not_reviewed_are_total_and_partial(tmp_path: Path) -> None:
    files = ["src/a.py", "src/b.py"]
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={"files_not_reviewed": files},
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=[],
        partial=[],
        not_reviewed=files,
    )
    assert results.status == "partial"


def test_u2_noncritical_partial_file_makes_results_partial(tmp_path: Path) -> None:
    files = ["src/reviewed.py", "src/partial.py"]
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={
            "files_reviewed": [files[0]],
            "files_partial": [files[1]],
        },
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=[files[0]],
        partial=[files[1]],
        not_reviewed=[],
    )
    assert results.status == "partial"


def test_u2_reviewed_and_not_reviewed_files_remain_disjoint_and_partial(tmp_path: Path) -> None:
    files = ["src/reviewed.py", "src/not_reviewed.py"]
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={
            "files_reviewed": [files[0]],
            "files_not_reviewed": [files[1]],
        },
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=[files[0]],
        partial=[],
        not_reviewed=[files[1]],
    )
    assert results.status == "partial"


def test_u2_omitted_expected_file_is_pessimized_to_not_reviewed(tmp_path: Path) -> None:
    files = ["src/reviewed.py", "src/omitted.py"]
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={"files_reviewed": [files[0]]},
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=[files[0]],
        partial=[],
        not_reviewed=[files[1]],
    )
    assert results.status == "partial"
    assert results.limitations.count("coverage_expected_files_missing") == 1


@pytest.mark.parametrize(
    ("reported", "reviewed", "partial", "not_reviewed"),
    [
        pytest.param(
            {"files_reviewed": ["src/a.py"], "files_partial": ["src/a.py"]},
            [],
            ["src/a.py"],
            [],
            id="reviewed-plus-partial",
        ),
        pytest.param(
            {"files_reviewed": ["src/a.py"], "files_not_reviewed": ["src/a.py"]},
            [],
            [],
            ["src/a.py"],
            id="reviewed-plus-not-reviewed",
        ),
        pytest.param(
            {"files_partial": ["src/a.py"], "files_not_reviewed": ["src/a.py"]},
            [],
            [],
            ["src/a.py"],
            id="partial-plus-not-reviewed",
        ),
        pytest.param(
            {
                "files_reviewed": ["src/a.py"],
                "files_partial": ["src/a.py"],
                "files_not_reviewed": ["src/a.py"],
            },
            [],
            [],
            ["src/a.py"],
            id="all-three-states",
        ),
    ],
)
def test_u2_overlaps_use_worst_state_and_make_results_partial(
    tmp_path: Path,
    reported: dict[str, object],
    reviewed: list[str],
    partial: list[str],
    not_reviewed: list[str],
) -> None:
    chunk = _chunk(files=["src/a.py"])
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk, coverage_notes=reported)

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=chunk.files,
        reviewed=reviewed,
        partial=partial,
        not_reviewed=not_reviewed,
    )
    assert results.status == "partial"
    assert results.limitations.count("coverage_file_in_multiple_states") == 1


def test_u2_cross_chunk_overlap_uses_worst_state_in_global_union(tmp_path: Path) -> None:
    shared = "src/shared.py"
    first = _chunk(
        chunk_id="chunk-01-primary_backend_logic",
        files=[shared],
    )
    second = _chunk(
        chunk_id="chunk-02-api_schema_contract",
        group="api_schema_contract",
        files=[shared],
    )
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=first,
        coverage_notes={"files_reviewed": [shared]},
    )
    _write_response(
        responses,
        chunk=second,
        coverage_notes={"files_partial": [shared]},
    )

    results = parse_chunk_results(_plan([first, second]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=[shared],
        reviewed=[],
        partial=[shared],
        not_reviewed=[],
    )
    assert results.status == "partial"
    assert results.limitations.count("coverage_file_in_multiple_states") == 1


def test_u2_foreign_path_cannot_replace_an_omitted_expected_file(tmp_path: Path) -> None:
    files = ["src/reviewed.py", "src/omitted.py"]
    foreign = "src/foreign.py"
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        coverage_notes={"files_reviewed": [files[0], foreign]},
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=[files[0]],
        partial=[],
        not_reviewed=[files[1]],
    )
    assert foreign not in results.model_dump_json()
    assert results.status == "partial"
    assert results.limitations.count(f"coverage_file_not_in_chunk:{chunk.chunk_id}") == 1
    assert results.limitations.count("coverage_expected_files_missing") == 1


def test_u2_multiple_chunks_form_one_total_disjoint_partition(tmp_path: Path) -> None:
    first = _chunk(
        chunk_id="chunk-01-primary_backend_logic",
        files=["src/a.py", "src/b.py"],
    )
    second = _chunk(
        chunk_id="chunk-02-api_schema_contract",
        group="api_schema_contract",
        files=["src/c.py"],
    )
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=first)
    _write_response(
        responses,
        chunk=second,
        coverage_notes={"files_partial": second.files},
    )

    results = parse_chunk_results(_plan([first, second]), responses_dir=responses)

    expected = [*first.files, *second.files]
    _assert_total_coverage_partition(
        results,
        expected=expected,
        reviewed=first.files,
        partial=second.files,
        not_reviewed=[],
    )
    assert results.status == "partial"
    assert "coverage_expected_files_missing" not in results.limitations
    assert "coverage_file_in_multiple_states" not in results.limitations


def test_u2_all_expected_files_exactly_reviewed_remains_complete(tmp_path: Path) -> None:
    files = ["src/a.py", "src/b.py"]
    chunk = _chunk(files=files)
    responses = _responses_dir(tmp_path)
    _write_response(responses, chunk=chunk, coverage_notes={"files_reviewed": files})

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    _assert_total_coverage_partition(
        results,
        expected=files,
        reviewed=files,
        partial=[],
        not_reviewed=[],
    )
    assert results.status == "complete"
    assert not any(limitation.startswith("coverage_") for limitation in results.limitations)


@pytest.mark.parametrize(
    "invalid_chunk_id",
    [
        "chunk/backend",
        "chunk\\backend",
        ".",
        "..",
        "chunk\x00backend",
        "chunk\nbackend",
        "ghp_abcdefghijk_sensitive",
        "/home/reviewer/chunk-backend",
    ],
)
def test_parser_rejects_the_same_response_incompatible_chunk_ids(
    tmp_path: Path,
    invalid_chunk_id: str,
) -> None:
    chunk = _chunk(chunk_id=invalid_chunk_id)
    responses = _responses_dir(tmp_path)

    with pytest.raises(ChunkResultParserError) as exc:
        parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert exc.value.error_class == "chunk_plan_chunk_id_invalid"
    assert invalid_chunk_id not in exc.value.message


def test_parser_accepts_non_empty_response_following_structured_contract(tmp_path: Path) -> None:
    chunk = _chunk()
    responses = _responses_dir(tmp_path)
    _write_response(
        responses,
        chunk=chunk,
        confirmed_findings=[
            {
                "severity": "P2",
                "title": "Schedule validation skips inactive doctor guard",
                "file_path": "backend/services/schedule.py",
                "impact": "Inactive doctors could be scheduled.",
                "evidence": "The changed hunk removes the inactive-doctor guard.",
                "line_or_hunk": "L42-L48",
                "contract_id": None,
                "confidence": "high",
                "dedupe_key": None,
            }
        ],
        risks=[
            {
                "title": "Caller behavior still needs validation",
                "reason": "The chunk does not include every caller.",
                "missing_evidence": "caller paths",
                "suggested_validation": "Inspect local code intelligence.",
            }
        ],
        limitations=[{"type": "diff_scope", "detail": "Only the assigned file was reviewed."}],
        coverage_notes={
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.status == "complete"
    assert len(results.confirmed_findings) == 1
    assert results.confirmed_findings[0].source_artifact is None
    assert results.confirmed_findings[0].line_or_hunk == "L42-L48"
    assert results.risks[0].title == "Caller behavior still needs validation"
    # The model's own limitation is preserved verbatim -- in the model-reported
    # namespace, never in the deterministic one (AgentEscala#675, Fix A).
    assert "diff_scope:Only the assigned file was reviewed." in results.model_reported_limitations
    assert "diff_scope:Only the assigned file was reviewed." not in results.limitations


def test_model_authored_limitations_never_enter_the_deterministic_namespace(tmp_path: Path) -> None:
    """AgentEscala#675 / Fix A. `ChunkResponse.limitations` is free text from
    the model: `type` and `detail` are both unconstrained `str | None`.

    Before this fix the parser flattened them to f"{type}:{detail}" and
    `extend`-ed the *same* list that carries engine-authored reason codes. Two
    consequences, both observed in production comments: the published
    "deterministic" limitation list contained sentences the model wrote, and a
    model that echoed a deterministic code back with prose produced a second,
    apparently independent entry for one cause.
    """
    responses = _responses_dir(tmp_path)
    chunk = _chunk()
    _write_response(
        responses,
        chunk=chunk,
        limitations=[
            # The model echoing a real deterministic code back at us, with prose.
            {"type": "intake_schema_id_missing", "detail": "The intake schema ID is missing from the payload."},
            # A pure invention with no deterministic counterpart.
            {"type": "contracts_context_not_relevant", "detail": "The contracts context was not relevant here."},
        ],
    )

    results = parse_chunk_results(_plan([chunk]), responses_dir=responses)

    assert results.model_reported_limitations == [
        "intake_schema_id_missing:The intake schema ID is missing from the payload.",
        "contracts_context_not_relevant:The contracts context was not relevant here.",
    ]
    # Nothing the model wrote leaks into the deterministic namespace ...
    assert results.limitations == []
    # ... and in particular it cannot manufacture a deterministic cause by
    # naming one.
    assert not any(item.startswith("intake_schema_id_missing") for item in results.limitations)


def test_model_echo_of_a_deterministic_code_is_not_a_second_cause(tmp_path: Path) -> None:
    """Fix A, dedupe half. When the deterministic code IS present, the model
    echoing it with free text must not make it count twice."""
    responses = _responses_dir(tmp_path)
    chunk = _chunk()
    _write_response(
        responses,
        chunk=chunk,
        limitations=[{"type": "chunk_plan_status_degraded", "detail": "the plan looked degraded to me"}],
    )

    # A degraded plan puts the real deterministic code into `limitations`.
    results = parse_chunk_results(_plan([chunk], status="degraded"), responses_dir=responses)

    assert results.limitations.count("chunk_plan_status_degraded") == 1
    assert results.limitations == ["chunk_plan_status_degraded"]
    assert results.model_reported_limitations == [
        "chunk_plan_status_degraded:the plan looked degraded to me"
    ]


def test_partial_chunk_plan_status_propagates_to_result_status(tmp_path: Path) -> None:
    """H1-B / PR #231 review round 2, P1: a `partial` chunk_plan.status
    (e.g. C10's file_context_fallback_used case) must not silently become
    `complete` just because every chunk that WAS produced parsed cleanly --
    final_synthesizer/quality_gate already treat `chunk_results.status ==
    "partial"` as blocking in every gate check, but _result_status never
    read `chunk_plan.status == "partial"` in the first place, so a partial
    plan's uncertainty never reached them."""
    responses = _responses_dir(tmp_path)
    chunk = _chunk()
    _write_response(responses, chunk=chunk, confirmed_findings=[_finding(severity="P2")])

    results = parse_chunk_results(_plan([chunk], status="partial"), responses_dir=responses)

    assert results.status == "partial"
