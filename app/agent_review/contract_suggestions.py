"""Manual-only suggested contract updates from false-positive markers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import yaml

from app.agent_review.false_positive_signatures import _sanitize_string, _sanitize_value
from app.agent_review.schemas import ContractSuggestions, FalsePositiveSignatures


def build_contract_suggestions(signatures: FalsePositiveSignatures) -> ContractSuggestions:
    matched = {
        marker["finding_signature"]: marker
        for candidate in signatures.candidates
        for marker in candidate.matched_markers
        if marker.get("source") == "manual" and marker.get("suggested_rule")
    }
    suggestions = []
    for signature, marker in sorted(matched.items()):
        suggested_rule = _sanitize_string(str(marker["suggested_rule"]).strip())
        if not suggested_rule:
            continue
        payload = {
            "finding_signature": signature,
            "reason": marker["reason"],
            "contract_id": marker.get("contract_id"),
            "suggested_rule": suggested_rule,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        suggestions.append(
            {
                "suggestion_id": "contract-suggestion:v1:" + hashlib.sha256(canonical.encode()).hexdigest(),
                **payload,
                "provenance": {"marker_source": "manual"},
            }
        )
    artifact = ContractSuggestions(
        target=signatures.target,
        suggestions=sorted(suggestions, key=lambda item: item["suggestion_id"]),
        limitations=signatures.limitations,
    )
    return ContractSuggestions.model_validate(_sanitize_value(artifact.model_dump(mode="json")))


def suggestions_to_yaml(suggestions: ContractSuggestions) -> str:
    rendered = yaml.safe_dump(suggestions.model_dump(mode="json"), sort_keys=True, allow_unicode=True)
    yaml.safe_load(rendered)
    return rendered
