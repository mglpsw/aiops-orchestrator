from __future__ import annotations

import yaml

from app.agent_review.contract_suggestions import build_contract_suggestions, suggestions_to_yaml
from app.agent_review.false_positive_signatures import build_false_positive_signatures, signature_for_basis
from app.agent_review.schemas import ReviewQualityGate, ReviewTelemetry


def _finding() -> dict[str, object]:
    return {
        "chunk_id": "chunk-01-docs_changelog",
        "semantic_group": "docs_changelog",
        "severity": "P1",
        "title": "Docs finding",
        "file_path": "docs/release.md",
        "evidence": "docs only",
        "contract_id": "review.docs-severity",
        "impact": "overseverity",
        "confidence": "high",
        "source_chunks": ["chunk-01-docs_changelog"],
        "semantic_groups": ["docs_changelog"],
    }


def _final_review() -> dict[str, object]:
    return {
        "schema_version": 1,
        "schema_id": "agent-review.final-review.v1",
        "source": "aiops-review-synthesize",
        "target_repo": "mglpsw/AgentEscala",
        "status": "complete",
        "verdict": "changes_requested",
        "summary": "Synthetic final review fixture.",
        "confirmed_findings": [_finding()],
        "risks": [],
        "limitations": [],
        "rejected_summary": {"total": 0, "by_reason": {}, "sample_titles": []},
        "coverage": {},
        "counts": {"confirmed_findings_total": 1},
        "inputs": {},
        "created_at": "2026-06-02T00:00:00Z",
    }


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
        }
    )


def _telemetry() -> ReviewTelemetry:
    return ReviewTelemetry.model_validate(
        {
            "schema_version": 1,
            "schema_id": "agent-review.telemetry.v1",
            "source": "aiops-review-telemetry",
            "status": "complete",
            "target": {"repository": "mglpsw/AgentEscala"},
        }
    )


def _signatures(markers: list[dict[str, object]]):
    return build_false_positive_signatures(
        final_review=_final_review(),
        quality_gate=_quality_gate(),
        review_telemetry=_telemetry(),
        chunk_results=None,
        markers_document={
            "schema_id": "agent-review.false-positive-markers.v1",
            "schema_version": 1,
            "source": "manual",
            "markers": markers,
        },
    )


def test_suggestion_requires_matched_manual_suggested_rule() -> None:
    base = _signatures([])
    signature = signature_for_basis(base.candidates[0].basis)
    unmatched = "fp:v1:" + "f" * 64
    suggestions = build_contract_suggestions(
        _signatures(
            [
                {"finding_signature": signature, "reason": "docs_only_overseverity"},
                {
                    "finding_signature": unmatched,
                    "reason": "docs_only_overseverity",
                    "suggested_rule": "Unmatched rule must not appear",
                },
            ]
        )
    )
    assert base.candidates[0].signature == signature
    assert suggestions.suggestions == []

    suggestions = build_contract_suggestions(
        _signatures(
            [
                {
                    "finding_signature": signature,
                    "reason": "docs_only_overseverity",
                    "suggested_rule": "Docs findings default to P3 unless deterministic high-impact evidence exists",
                    "contract_id": "review.docs-severity",
                }
            ]
        )
    )
    assert len(suggestions.suggestions) == 1
    suggestion = suggestions.suggestions[0]
    assert suggestion.suggestion_id.startswith("contract-suggestion:v1:")
    assert suggestion.finding_signature == signature
    assert suggestion.reason == "docs_only_overseverity"
    assert suggestion.contract_id == "review.docs-severity"
    assert suggestion.provenance == {"marker_source": "manual"}
    assert suggestions.apply_mode == "manual_only"
    assert suggestions.applied is False
    assert suggestions.target == {"repository": "mglpsw/AgentEscala"}


def test_suggestion_id_and_yaml_are_deterministic_and_round_trip() -> None:
    signature = signature_for_basis(_signatures([]).candidates[0].basis)
    marker = {
        "finding_signature": signature,
        "reason": "docs_only_overseverity",
        "suggested_rule": "Docs findings default to P3",
        "contract_id": "review.docs-severity",
    }

    first = build_contract_suggestions(_signatures([marker]))
    second = build_contract_suggestions(_signatures([marker]))
    assert first.model_dump(mode="json") == second.model_dump(mode="json")

    rendered = suggestions_to_yaml(first)
    assert rendered == suggestions_to_yaml(second)
    loaded = yaml.safe_load(rendered)
    assert loaded["schema_id"] == "agent-review.contract-suggestions.v1"
    assert loaded["schema_version"] == 1
    assert loaded["apply_mode"] == "manual_only"
    assert loaded["applied"] is False
    assert loaded["suggestions"][0]["suggestion_id"] == first.suggestions[0].suggestion_id


def test_suggestions_sanitize_sensitive_text_without_applying_contracts() -> None:
    signature = signature_for_basis(_signatures([]).candidates[0].basis)
    suggestions = build_contract_suggestions(
        _signatures(
            [
                {
                    "finding_signature": signature,
                    "reason": "contract_obsolete",
                    "suggested_rule": "Authorization ****** /home/runner/work/repo CT102",
                    "contract_id": "review.docs-severity",
                }
            ]
        )
    )
    rendered = suggestions_to_yaml(suggestions)
    for forbidden in ("Authorization", "Bearer", "ghp_1234567890abcdef", "/home/runner", "CT102"):
        assert forbidden not in rendered
