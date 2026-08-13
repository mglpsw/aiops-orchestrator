from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_review.quality_gate import (
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
