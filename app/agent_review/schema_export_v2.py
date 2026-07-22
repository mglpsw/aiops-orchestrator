"""Stable JSON Schema rendering for the isolated AgentReview v2 contracts."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from app.agent_review.contracts_v2 import (
    AgentReviewRunV2,
    ChunkPayloadV2,
    ChunkResponseEnvelopeV2,
    ReviewReadinessV2,
    TargetProfileV2,
)


_STABLE_DEFINITION_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _definition_target(reference: object) -> str | None:
    if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
        return None
    return reference.removeprefix("#/$defs/")


def _normalize_definition_names(schema: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(schema)
    definitions = normalized.get("$defs")
    if not isinstance(definitions, dict):
        return normalized

    aliases: dict[str, str] = {}
    concrete: dict[str, dict[str, Any]] = {}
    for name, definition in definitions.items():
        if not isinstance(definition, dict):
            raise RuntimeError(f"JSON Schema definition {name!r} is not an object")
        target = _definition_target(definition.get("$ref")) if set(definition) == {"$ref"} else None
        if target is None:
            concrete[name] = definition
        else:
            aliases[name] = target

    def resolve(name: str, seen: frozenset[str] = frozenset()) -> str:
        if name in seen:
            raise RuntimeError(f"cyclic JSON Schema definition alias: {name}")
        target = aliases.get(name)
        return resolve(target, seen | {name}) if target is not None else name

    stable_names: dict[str, str] = {}
    used_titles: dict[str, str] = {}
    for original, definition in concrete.items():
        title = definition.get("title")
        candidate = title if isinstance(title, str) and _STABLE_DEFINITION_NAME_RE.fullmatch(title) else original
        previous = used_titles.get(candidate)
        if previous is not None and previous != original:
            raise RuntimeError(f"ambiguous JSON Schema definition title: {candidate}")
        used_titles[candidate] = original
        stable_names[original] = candidate
    for alias in aliases:
        target = resolve(alias)
        if target not in stable_names:
            raise RuntimeError(f"JSON Schema alias {alias!r} targets unknown definition {target!r}")
        stable_names[alias] = stable_names[target]

    def rewrite(value: Any) -> Any:
        if isinstance(value, dict):
            rewritten = {key: rewrite(child) for key, child in value.items()}
            target = _definition_target(rewritten.get("$ref"))
            if target is not None and target in stable_names:
                rewritten["$ref"] = f"#/$defs/{stable_names[target]}"
            return rewritten
        if isinstance(value, list):
            return [rewrite(child) for child in value]
        return value

    stable_definitions: dict[str, Any] = {}
    for original, definition in concrete.items():
        stable_name = stable_names[original]
        rewritten = rewrite(definition)
        if stable_name in stable_definitions and stable_definitions[stable_name] != rewritten:
            raise RuntimeError(f"conflicting normalized JSON Schema definition: {stable_name}")
        stable_definitions[stable_name] = rewritten

    normalized.pop("$defs")
    normalized = rewrite(normalized)
    normalized["$defs"] = dict(sorted(stable_definitions.items()))
    return normalized


def _const_json_type(value: object) -> str | None:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _normalize_const_types(value: Any) -> Any:
    if isinstance(value, dict):
        normalized = {key: _normalize_const_types(child) for key, child in value.items()}
        if "const" in normalized and "type" not in normalized:
            json_type = _const_json_type(normalized["const"])
            if json_type is not None:
                normalized["type"] = json_type
        return normalized
    if isinstance(value, list):
        return [_normalize_const_types(child) for child in value]
    return value


def normalize_v2_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove Pydantic-internal ref aliases and normalize stable JSON semantics."""

    return _normalize_const_types(_normalize_definition_names(schema))


def render_v2_json_schemas() -> dict[str, dict[str, object]]:
    schemas: dict[str, dict[str, object]] = {
        "agent-review.run.v2.schema.json": AgentReviewRunV2.model_json_schema(mode="validation"),
        "agent-review.chunk-payload.v2.schema.json": ChunkPayloadV2.model_json_schema(mode="validation"),
        "agent-review.chunk-response-envelope.v2.schema.json": ChunkResponseEnvelopeV2.model_json_schema(
            mode="validation"
        ),
        "agent-review.target-profile.v2.schema.json": TargetProfileV2.model_json_schema(mode="validation"),
        "agent-review.review-readiness.v2.schema.json": ReviewReadinessV2.model_json_schema(mode="validation"),
    }
    schemas = {filename: normalize_v2_json_schema(schema) for filename, schema in schemas.items()}
    for schema in schemas.values():
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schemas["agent-review.chunk-response-envelope.v2.schema.json"]["title"] = (
        "ChunkResponseEnvelopeV2"
    )
    return dict(sorted(schemas.items()))


def render_v2_json_schema_text(schema: dict[str, object]) -> str:
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
