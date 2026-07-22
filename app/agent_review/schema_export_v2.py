"""Stable JSON Schema rendering for the isolated AgentReview v2 contracts."""

from __future__ import annotations

import json

from app.agent_review.contracts_v2 import (
    CHUNK_RESPONSE_ENVELOPE_V2_ADAPTER,
    AgentReviewRunV2,
    ChunkPayloadV2,
    ReviewReadinessV2,
    TargetProfileV2,
)


def render_v2_json_schemas() -> dict[str, dict[str, object]]:
    schemas: dict[str, dict[str, object]] = {
        "agent-review.run.v2.schema.json": AgentReviewRunV2.model_json_schema(mode="validation"),
        "agent-review.chunk-payload.v2.schema.json": ChunkPayloadV2.model_json_schema(mode="validation"),
        "agent-review.chunk-response-envelope.v2.schema.json": CHUNK_RESPONSE_ENVELOPE_V2_ADAPTER.json_schema(
            mode="validation"
        ),
        "agent-review.target-profile.v2.schema.json": TargetProfileV2.model_json_schema(mode="validation"),
        "agent-review.review-readiness.v2.schema.json": ReviewReadinessV2.model_json_schema(mode="validation"),
    }
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schemas["agent-review.chunk-response-envelope.v2.schema.json"]["title"] = (
        "ChunkResponseEnvelopeV2"
    )
    return dict(sorted(schemas.items()))


def render_v2_json_schema_text(schema: dict[str, object]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
