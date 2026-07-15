from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.agent_review.contract_suggestions import build_contract_suggestions
from app.agent_review.false_positive_signatures import (
    build_false_positive_signatures,
    finding_signature_basis,
    load_optional_chunk_results,
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
        "quality_gate": {
            "status": "passed",
            "normalized_verdict": "changes_requested",
            "manual_review_required": False,
        },
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
        quality_gate=kwargs.pop("quality_gate", _quality_gate()),
        review_telemetry=kwargs.pop("review_telemetry", _telemetry()),
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


@pytest.mark.parametrize(
    ("file_path", "expected_file_path"),
    [
        ("docs/CT102_RUNTIME_TRANSITION_V019.md", "docs/[REDACTED]_RUNTIME_TRANSITION_V019.md"),
        ("docs/Authorization_Bearer_MIGRATION.md", "docs/[REDACTED]_[REDACTED]_MIGRATION.md"),
        ("docs/DATABASE_URL_GUIDE.md", "docs/[REDACTED]_GUIDE.md"),
    ],
)
def test_signature_basis_sanitizes_relative_paths_before_hashing(
    file_path: str, expected_file_path: str
) -> None:
    finding = _finding(
        file_path=file_path,
        title="Authorization: ******",
        contract_id="DATABASE_URL=******db.example.com/app",
    )

    basis, limitation = finding_signature_basis(finding)
    assert limitation is None
    assert basis is not None
    assert basis["file_path"] == expected_file_path

    artifact = _build(_final_review([finding]))
    candidate = artifact.candidates[0]
    assert candidate.basis == basis
    assert candidate.signature == signature_for_basis(candidate.basis)

    markers = {
        "schema_id": "agent-review.false-positive-markers.v1",
        "schema_version": 1,
        "source": "manual",
        "markers": [
            {
                "finding_signature": signature_for_basis(candidate.basis),
                "reason": "docs_only_overseverity",
                "suggested_rule": "Docs findings default to P3 unless deterministic high-impact evidence exists",
                "contract_id": "review.docs-severity",
            }
        ],
    }
    signed = _build(_final_review([finding]), markers_document=markers)
    assert signed.candidates[0].matched_markers == [
        {
            "contract_id": "review.docs-severity",
            "finding_signature": candidate.signature,
            "matched": True,
            "reason": "docs_only_overseverity",
            "source": "manual",
            "suggested_rule": "Docs findings default to P3 unless deterministic high-impact evidence exists",
        }
    ]

    suggestions = build_contract_suggestions(signed)
    assert len(suggestions.suggestions) == 1
    assert suggestions.suggestions[0].finding_signature == candidate.signature
    assert suggestions.suggestions[0].suggestion_id.startswith("contract-suggestion:v1:")

    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    for forbidden in (
        file_path,
        "Authorization",
        "Bearer",
        "DATABASE_URL",
        "correct-horse-battery-staple",
        "/home/runner",
        "CT102",
    ):
        assert forbidden not in rendered
    assert expected_file_path in rendered


def test_signature_is_recalculable_from_sanitized_published_basis() -> None:
    github_pat = "github_pat_" + "A" * 22 + "_" + "B" * 59
    database_url = "DATABASE_URL=postgres://" + "aiops:correct-horse-battery-staple" + "@db.example.com/app"
    finding = _finding(
        title=(
            "Authorization: Bearer "
            "eyJzdWIiOiJhaW9wcyJ9.signature "
            f"{github_pat}"
        ),
        contract_id=database_url,
    )

    first = _build(_final_review([finding]))
    second = _build(_final_review([finding]))

    assert len(first.candidates) == 1
    candidate = first.candidates[0]
    assert candidate.signature == signature_for_basis(candidate.basis)
    rendered = json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    for forbidden in (
        "Authorization",
        "Bearer",
        github_pat,
        "correct-horse-battery-staple",
        "postgres://aiops",
        "DATABASE_URL",
    ):
        assert forbidden not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.parametrize("file_path", ["/home/runner/work/AgentEscala/docs/release.md", "../docs/release.md"])
def test_absolute_or_unsafe_paths_do_not_leak_or_generate_candidates(file_path: str) -> None:
    artifact = _build(_final_review([_finding(file_path=file_path)]))

    assert artifact.candidates == []
    assert artifact.limitations == ["finding_signature_path_invalid:0"]
    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "/home/runner" not in rendered
    assert "../docs/release.md" not in rendered
    assert "AgentEscala/docs" not in rendered


def test_input_order_does_not_change_output_bytes() -> None:
    first_finding = _finding(title="First", file_path="docs/first.md", contract_id="review.docs")
    second_finding = _finding(title="Second", file_path="docs/second.md", contract_id="review.docs")
    first = _build(_final_review([first_finding, second_finding]))
    second = _build(_final_review([second_finding, first_finding]))

    assert json.dumps(first.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def test_duplicate_findings_are_deduplicated_by_signature_and_marker_matches_candidate() -> None:
    duplicate_a = _finding(title="Duplicate finding", severity="P1", confidence="high")
    duplicate_b = _finding(title=" duplicate   finding ", severity="P3", confidence="low")
    signature = signature_for_basis(_build(_final_review([duplicate_a])).candidates[0].basis)
    markers = {
        "schema_id": "agent-review.false-positive-markers.v1",
        "schema_version": 1,
        "source": "manual",
        "markers": [
            {
                "finding_signature": signature,
                "reason": "docs_only_overseverity",
                "suggested_rule": "Docs findings default to P3",
                "contract_id": "review.docs-severity",
            }
        ],
    }

    artifact = _build(_final_review([duplicate_b, duplicate_a]), markers_document=markers)

    assert [candidate.signature for candidate in artifact.candidates] == [signature]
    assert artifact.candidates[0].matched_markers == [
        {
            "contract_id": "review.docs-severity",
            "finding_signature": signature,
            "matched": True,
            "reason": "docs_only_overseverity",
            "source": "manual",
            "suggested_rule": "Docs findings default to P3",
        }
    ]


def test_only_final_review_confirmed_findings_are_candidates() -> None:
    artifact = _build(_final_review())

    assert len(artifact.candidates) == 1
    rendered = json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    assert "Risk must not become a candidate" not in rendered
    assert "Rejected" not in rendered


def test_manual_marker_valid_absent_unmatched_duplicate_and_conflict() -> None:
    candidate_signature = signature_for_basis(_build(_final_review()).candidates[0].basis)
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
    signature = signature_for_basis(_build(_final_review()).candidates[0].basis)
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


def test_quality_gate_divergence_from_telemetry_warns_without_recalculating_gate() -> None:
    gate = _quality_gate()
    telemetry = _telemetry(
        quality_gate={
            "status": "manual_review_required",
            "normalized_verdict": "approved",
            "manual_review_required": True,
        }
    )

    artifact = _build(_final_review(), quality_gate=gate, review_telemetry=telemetry)

    assert artifact.inputs["review_quality_gate"]["status"] == "passed"
    assert artifact.inputs["review_quality_gate"]["normalized_verdict"] == "changes_requested"
    assert artifact.inputs["review_quality_gate"]["manual_review_required"] is False
    assert "artifact_divergence:quality_gate_status" in artifact.warnings
    assert "artifact_divergence:quality_gate_normalized_verdict" in artifact.warnings
    assert "artifact_divergence:quality_gate_manual_review_required" in artifact.warnings


def test_quality_gate_equivalent_to_telemetry_has_no_quality_gate_warning() -> None:
    artifact = _build(_final_review(), quality_gate=_quality_gate(), review_telemetry=_telemetry())

    assert all(not warning.startswith("artifact_divergence:quality_gate_") for warning in artifact.warnings)


def test_invalid_chunk_results_structure_is_not_consumed_for_provenance(tmp_path: Path) -> None:
    path = tmp_path / "chunk-results.json"
    payload = _chunk_results(chunks_parsed=[123])
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    chunk_results, limitations = load_optional_chunk_results(path)
    artifact = build_false_positive_signatures(
        final_review=_final_review(),
        quality_gate=_quality_gate(),
        review_telemetry=_telemetry(),
        chunk_results=chunk_results,
        limitations=limitations,
    )

    assert chunk_results is None
    assert limitations == ["artifact_structure_invalid:chunk_results"]
    assert "artifact_structure_invalid:chunk_results" in artifact.limitations
    assert artifact.candidates[0].provenance["chunk_results_used_for_provenance"] is False
    assert artifact.candidates[0].provenance["source_chunks_in_chunk_results"] == []


def test_chunk_results_schema_and_version_mismatch_have_distinct_limitations(tmp_path: Path) -> None:
    schema_path = tmp_path / "chunk-results-schema.json"
    version_path = tmp_path / "chunk-results-version.json"
    schema_path.write_text(json.dumps({**_chunk_results(), "schema_id": "wrong"}, sort_keys=True), encoding="utf-8")
    version_path.write_text(json.dumps({**_chunk_results(), "schema_version": 2}, sort_keys=True), encoding="utf-8")

    schema_results, schema_limitations = load_optional_chunk_results(schema_path)
    version_results, version_limitations = load_optional_chunk_results(version_path)

    assert schema_results is None
    assert schema_limitations == ["artifact_schema_id_mismatch:chunk_results"]
    assert version_results is None
    assert version_limitations == ["artifact_schema_version_mismatch:chunk_results"]


def test_sanitizes_secrets_ct102_and_absolute_paths_from_output() -> None:
    signature = signature_for_basis(_build(_final_review()).candidates[0].basis)
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
