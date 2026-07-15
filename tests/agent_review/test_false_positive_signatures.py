from __future__ import annotations

import json
from copy import deepcopy

from app.agent_review.false_positive_signatures import (
    build_false_positive_signatures,
    finding_signature_basis,
    signature_for_basis,
)
from app.agent_review.schemas import ReviewQualityGate, ReviewTelemetry


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-docs_changelog",
        "semantic_group": "docs_changelog",
        "severity": "P1",
        "title": "  Docs   mention TOKEN rotation  ",
        "file_path": "./docs\\release.md",
        "line_or_hunk": "L10",
        "evidence": "Authorization: ******",
        "source_artifact": "artifact:file-diff-context",
        "contract_id": " Review.Docs-Severity ",
        "impact": "Docs-only wording was over-severe.",
        "confidence": "high",
        "source_chunks": ["chunk-01-docs_changelog"],
        "semantic_groups": ["docs_changelog"],
    }
    payload.update(overrides)
    return payload


def _final_review(findings: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.final-review.v1",
        "source": "aiops-review-synthesize",
        "target_repo": "mglpsw/AgentEscala",
        "status": "complete",
        "verdict": "changes_requested",
        "summary": "Synthetic final review fixture.",
        "confirmed_findings": findings if findings is not None else [_finding()],
        "risks": [
            {
                "chunk_id": "chunk-02-tests",
                "semantic_group": "tests",
                "source": "downgraded_finding",
                "title": "Risk must not become a candidate",
                "reason": "missing_required_evidence",
            }
        ],
        "limitations": [],
        "rejected_summary": {"total": 1, "by_reason": {"missing_file_path": 1}, "sample_titles": ["Rejected"]},
        "coverage": {},
        "counts": {"confirmed_findings_total": len(findings) if findings is not None else 1},
        "inputs": {},
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _quality_gate() -> ReviewQualityGate:
    return ReviewQualityGate.model_validate(
        {
            "schema_version": 1,
            "schema_id": "agent-review.quality-gate.v1",
            "source": "aiops-review-quality-gate",
            "status": "passed",
            "normalized_verdict": "changes_requested",
            "quality_score": 0.95,
            "manual_review_required": False,
            "created_at": "2026-06-02T00:00:00Z",
        }
    )


def _telemetry(**overrides: object) -> ReviewTelemetry:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.telemetry.v1",
        "source": "aiops-review-telemetry",
        "status": "complete",
        "target": {"repository": "mglpsw/AgentEscala"},
        "pipeline": {},
        "coverage": {},
        "findings": {},
        "review": {},
        "quality_gate": {},
        "validation_evidence": {},
        "redaction": {},
        "model": {},
        "performance": {},
        "inputs": {},
        "warnings": [],
        "limitations": [],
    }
    payload.update(overrides)
    return ReviewTelemetry.model_validate(payload)


def _chunk_results(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.chunk-results.v1",
        "source": "aiops-review-parse-chunks",
        "target_repo": "mglpsw/AgentEscala",
        "chunk_plan_ref": {"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        "chunks_parsed": ["chunk-01-docs_changelog"],
        "chunks_failed": [],
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "rejected_findings": [{"reason": "missing_file_path", "title": "Rejected"}],
        "coverage": {},
        "status": "complete",
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _build(final_review: dict[str, object], **kwargs: object):
    return build_false_positive_signatures(
        final_review=final_review,
        quality_gate=_quality_gate(),
        review_telemetry=_telemetry(),
        chunk_results=_chunk_results(),
        **kwargs,
    )


def test_signature_basis_is_normalized_and_stable() -> None:
    basis, limitation = finding_signature_basis(_finding())
    assert limitation is None
    assert basis == {
        "normalized_title": "docs mention token rotation",
        "file_path": "docs/release.md",
        "contract_id": "review.docs-severity",
    }
    signature = signature_for_basis(basis)

    changed = _finding(severity="P3", confidence="low", line_or_hunk="L999", evidence="different evidence")
    changed_basis, changed_limitation = finding_signature_basis(changed)
    assert changed_limitation is None
    assert signature_for_basis(changed_basis) == signature


def test_absolute_or_unsafe_paths_do_not_leak_or_generate_candidates() -> None:
    artifact = _build(_final_review([_finding(file_path="/home/runner/work/AgentEscala/docs/release.md")]))

    assert artifact.candidates == []
    assert artifact.limitations == ["finding_signature_path_invalid:0"]
    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "/home/runner" not in rendered
    assert "AgentEscala/docs" not in rendered


def test_input_order_does_not_change_output_bytes() -> None:
    first_finding = _finding(title="First", file_path="docs/first.md", contract_id="review.docs")
    second_finding = _finding(title="Second", file_path="docs/second.md", contract_id="review.docs")
    first = _build(_final_review([first_finding, second_finding]))
    second = _build(_final_review([second_finding, first_finding]))

    assert json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def test_only_final_review_confirmed_findings_are_candidates() -> None:
    artifact = _build(_final_review())

    assert len(artifact.candidates) == 1
    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "Risk must not become a candidate" not in rendered
    assert "Rejected" not in rendered


def test_manual_marker_valid_absent_unmatched_duplicate_and_conflict() -> None:
    candidate_signature = signature_for_basis(finding_signature_basis(_finding())[0])
    unmatched_signature = "fp:v1:" + "0" * 64
    markers = {
        "schema_id": "agent-review.false-positive-markers.v1",
        "schema_version": 1,
        "source": "manual",
        "markers": [
            {
                "finding_signature": candidate_signature,
                "reason": "docs_only_overseverity",
                "suggested_rule": "Docs findings default to P3",
                "contract_id": "review.docs-severity",
            },
            {
                "finding_signature": candidate_signature,
                "reason": "docs_only_overseverity",
                "suggested_rule": "Docs findings default to P3",
                "contract_id": "review.docs-severity",
            },
            {"finding_signature": unmatched_signature, "reason": "missing_source_artifact"},
        ],
    }

    artifact = _build(_final_review(), markers_document=markers)

    assert len(artifact.markers) == 2
    assert artifact.candidates[0].matched_markers == [
        {
            "contract_id": "review.docs-severity",
            "finding_signature": candidate_signature,
            "matched": True,
            "reason": "docs_only_overseverity",
            "source": "manual",
            "suggested_rule": "Docs findings default to P3",
        }
    ]
    assert f"manual_marker_unmatched:{unmatched_signature}" in artifact.warnings

    conflict_markers = deepcopy(markers)
    conflict_markers["markers"].append(
        {
            "finding_signature": candidate_signature,
            "reason": "contract_obsolete",
            "suggested_rule": "Different rule",
        }
    )
    conflict = _build(_final_review(), markers_document=conflict_markers)
    assert f"manual_marker_conflict:{candidate_signature}" in conflict.warnings
    assert conflict.candidates[0].matched_markers == []


def test_invalid_marker_reason_becomes_limitation() -> None:
    signature = signature_for_basis(finding_signature_basis(_finding())[0])
    artifact = _build(
        _final_review(),
        markers_document={
            "schema_id": "agent-review.false-positive-markers.v1",
            "schema_version": 1,
            "source": "manual",
            "markers": [{"finding_signature": signature, "reason": "unknown"}],
        },
    )

    assert artifact.candidates[0].matched_markers == []
    assert f"manual_marker_reason_invalid:{signature}" in artifact.limitations


def test_sanitizes_secrets_ct102_and_absolute_paths_from_output() -> None:
    signature = signature_for_basis(finding_signature_basis(_finding())[0])
    artifact = _build(
        _final_review(),
        markers_document={
            "schema_id": "agent-review.false-positive-markers.v1",
            "schema_version": 1,
            "source": "manual",
            "markers": [
                {
                    "finding_signature": signature,
                    "reason": "docs_only_overseverity",
                    "suggested_rule": "Authorization: ****** at /home/runner/work/repo; avoid CT102",
                }
            ],
        },
    )

    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    for forbidden in ("Authorization", "Bearer", "ghp_1234567890abcdef", "/home/runner", "CT102"):
        assert forbidden not in rendered
