"""Compact deterministic response contract for AgentReview chunk payloads."""

from __future__ import annotations

from typing import Any

from app.agent_review.schemas import (
    ChunkCoverageNotes,
    ChunkResponse,
    ChunkResponseFinding,
    ChunkResponseLimitation,
    ChunkResponseRisk,
)


def build_chunk_response_contract(*, chunk_id: str, semantic_group: str) -> dict[str, Any]:
    finding_fields = {
        "severity": "string:P0|P1|P2|P3",
        "title": "string",
        "file_path": "string:must_belong_to_chunk",
        "line_or_hunk": "string|null",
        "evidence": "string",
        "source_artifact": "string|null",
        "contract_id": "string|null",
        "impact": "string",
        "confidence": "high|medium|low|null",
        "dedupe_key": "string|null",
    }
    risk_fields = {
        "title": "string",
        "reason": "string",
        "missing_evidence": "string|null",
        "suggested_validation": "string|null",
    }
    limitation_fields = {
        "type": "string|null",
        "detail": "string|null",
    }
    coverage_fields = {
        "files_reviewed": "array<string:relative_path>",
        "files_partial": "array<string:relative_path>",
        "files_not_reviewed": "array<string:relative_path>",
    }
    field_shapes = {
        "schema_version": {"type": "integer", "const": 1},
        "chunk_id": {"type": "string", "const": chunk_id},
        "semantic_group": {"type": "string", "const": semantic_group},
        "confirmed_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "fields": finding_fields,
                "required": ["severity", "title", "file_path", "impact", "evidence"],
                "provenance": {
                    "at_least_one_of": ["source_artifact", "line_or_hunk"],
                    "require_non_empty": True,
                },
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "fields": risk_fields,
                "required": ["title", "reason"],
                "required_non_empty": ["title", "reason"],
            },
        },
        "limitations": {
            "type": "array",
            "items": {
                "type": "object",
                "fields": limitation_fields,
                "at_least_one_non_empty": ["type", "detail"],
                "not": "array_of_strings",
            },
        },
        "coverage_notes": {
            "type": "object",
            "fields": coverage_fields,
            "required": ["files_reviewed", "files_partial", "files_not_reviewed"],
        },
    }
    _assert_contract_matches_models(field_shapes)
    return {
        "schema_version": 1,
        "output_format": "json_object_only",
        "required_fields": list(ChunkResponse.model_fields),
        "field_shapes": field_shapes,
        "finding_requirements": ["severity", "title", "file_path", "impact", "evidence"],
        "finding_provenance_fields": ["source_artifact", "line_or_hunk"],
        "finding_provenance_requirement": "at_least_one_of:source_artifact,line_or_hunk",
        "minimum_valid_template": {
            "schema_version": 1,
            "chunk_id": chunk_id,
            "semantic_group": semantic_group,
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "coverage_notes": {
                "files_reviewed": [],
                "files_partial": [],
                "files_not_reviewed": [],
            },
        },
        "forbidden_output": ["markdown", "code_fences", "text_outside_json"],
        "forbidden_content": [
            "absolute_paths",
            "tokens",
            "headers",
            "cookies",
            "env_dumps",
            "raw_provider_payload",
            "raw_prompt_or_response",
        ],
    }


def _assert_contract_matches_models(field_shapes: dict[str, Any]) -> None:
    expected = (
        (field_shapes, ChunkResponse),
        (field_shapes["confirmed_findings"]["items"]["fields"], ChunkResponseFinding),
        (field_shapes["risks"]["items"]["fields"], ChunkResponseRisk),
        (field_shapes["limitations"]["items"]["fields"], ChunkResponseLimitation),
        (field_shapes["coverage_notes"]["fields"], ChunkCoverageNotes),
    )
    for contract_fields, model in expected:
        if set(contract_fields) != set(model.model_fields):
            raise RuntimeError("chunk response contract fields diverge from response models")
