"""Deterministic Semantic Chunk Planner for sanitized AgentReview intake."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.agent_review.redaction import RedactionState, redact_text
from app.agent_review.schemas import (
    INTAKE_SCHEMA,
    SemanticChunk,
    SemanticChunkPlan,
    SemanticGroup,
)


GROUP_PRIORITY: list[SemanticGroup] = [
    "suspicious_out_of_scope",
    "api_schema_contract",
    "primary_backend_logic",
    "workflow_aiops",
    "frontend_ui",
    "tests",
    "docs_changelog",
    "unknown",
]

FILE_DIFF_ALIASES = {
    "file-diff-context",
    "file-diff-context.json",
}

KNOWN_ARTIFACT_REFS = {
    "file-diff-context": "artifact:file-diff-context",
    "file-diff-context.json": "artifact:file-diff-context",
    "checks": "artifact:checks",
    "checks.json": "artifact:checks",
    "local-code-intelligence": "artifact:local-code-intelligence",
    "local-code-intelligence.json": "artifact:local-code-intelligence",
}

SUSPICIOUS_MARKERS = (
    ".env",
    "secret",
    "secrets",
    "prod",
    "production",
    "deploy",
    "deployment",
    "systemd",
    "docker",
    "compose",
    "ssh",
)


class IntakeValidationError(ValueError):
    pass


def load_intake(path: Path | str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntakeValidationError("intake_json_invalid") from exc
    if not isinstance(raw, dict):
        raise IntakeValidationError("intake_not_object")
    return raw


def build_semantic_chunk_plan(
    intake: dict[str, Any],
    *,
    max_blocks: int = 6,
    max_chars_per_block: int = 24_000,
) -> SemanticChunkPlan:
    limitations = validate_intake_contract(intake)
    target_repo = str(intake.get("target_repo", "unknown"))
    artifacts = intake.get("artifacts")
    artifact_status = intake.get("artifact_status")

    if not isinstance(artifacts, dict) or not isinstance(artifact_status, list):
        raise IntakeValidationError("intake_invalid")

    files, extraction_limitations = extract_files_from_intake(intake)
    limitations.extend(extraction_limitations)

    if not files:
        return SemanticChunkPlan(
            target_repo=target_repo,
            max_parallel_blocks=max_blocks,
            chunks=[],
            files_covered=[],
            files_partially_covered=[],
            files_not_covered=[],
            limitations=_dedupe([*limitations, "file_context_missing"]),
            status="degraded",
        )

    grouped = group_files_by_semantics(files)
    available_refs = _artifact_refs(artifacts)
    contract_refs = _contract_refs(intake)
    chunks: list[SemanticChunk] = []
    files_covered: list[str] = []
    files_partially_covered: list[str] = []
    files_not_covered: list[str] = []

    for group in GROUP_PRIORITY:
        group_files = grouped.get(group, [])
        if not group_files:
            continue
        if len(chunks) >= max_blocks:
            files_not_covered.extend(group_files)
            limitations.append("max_blocks_exceeded")
            continue

        included, partial, chunk_limitations = _budget_group_files(
            group,
            group_files,
            max_chars_per_block=max_chars_per_block,
        )
        coverage = "complete"
        if partial:
            coverage = "partial"
            files_partially_covered.extend(partial)
            limitations.extend(chunk_limitations)
        files_covered.extend(included)

        chunks.append(
            SemanticChunk(
                chunk_id=f"chunk-{len(chunks) + 1:02d}-{group}",
                semantic_group=group,
                order_index=len(chunks),
                files=included,
                artifacts=_refs_for_group(group, available_refs),
                contracts=contract_refs,
                depends_on=[],
                coverage=coverage,
                prompt_budget_chars=max_chars_per_block,
                estimated_chars=_estimate_files(included),
                limitations=chunk_limitations,
            )
        )

    status = _plan_status(
        intake_status=str(intake.get("status", "")),
        limitations=limitations,
        files_partially_covered=files_partially_covered,
        files_not_covered=files_not_covered,
    )

    return SemanticChunkPlan(
        target_repo=target_repo,
        max_parallel_blocks=max_blocks,
        chunks=chunks,
        files_covered=_dedupe(files_covered),
        files_partially_covered=_dedupe(files_partially_covered),
        files_not_covered=_dedupe(files_not_covered),
        limitations=_dedupe(limitations),
        status=status,
    )


def validate_intake_contract(intake: dict[str, Any]) -> list[str]:
    missing = [
        field
        for field in ("target_repo", "artifacts", "artifact_status", "status")
        if field not in intake
    ]
    if missing:
        raise IntakeValidationError(f"intake_missing:{','.join(missing)}")

    limitations: list[str] = []
    schema_id = intake.get("schema_id")
    schema_version = intake.get("schema_version")
    if schema_id is not None:
        if schema_id != INTAKE_SCHEMA:
            raise IntakeValidationError("intake_schema_id_invalid")
        if not isinstance(schema_version, int):
            raise IntakeValidationError("intake_schema_version_invalid")
    elif schema_version == INTAKE_SCHEMA:
        limitations.append("intake_schema_id_missing")
    elif isinstance(schema_version, int):
        limitations.append("intake_schema_id_missing")
    else:
        raise IntakeValidationError("intake_schema_invalid")

    return limitations


def extract_files_from_intake(intake: dict[str, Any]) -> tuple[list[str], list[str]]:
    artifacts = intake.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return [], ["artifacts_invalid"]

    primary_files = _extract_file_diff_context_files(artifacts)
    if primary_files:
        return _dedupe(primary_files), []

    fallback_files: list[str] = []
    for artifact in artifacts.values():
        fallback_files.extend(_files_from_artifact(artifact))
    if fallback_files:
        return _dedupe(fallback_files), ["file_context_fallback_used"]

    return [], ["file_context_missing"]


def group_files_by_semantics(files: list[str]) -> dict[SemanticGroup, list[str]]:
    grouped: dict[SemanticGroup, list[str]] = defaultdict(list)
    for file_path in files:
        grouped[classify_file(file_path)].append(file_path)
    return dict(grouped)


def classify_file(file_path: str) -> SemanticGroup:
    path = file_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]

    if any(marker in path for marker in SUSPICIOUS_MARKERS):
        return "suspicious_out_of_scope"
    if path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py") or ".test." in name:
        return "tests"
    if path.startswith(".github/") or "workflow" in path or path.startswith("scripts/aiops") or ("scripts/" in path and "review" in name):
        return "workflow_aiops"
    if path.startswith("docs/") or name in {"readme", "readme.md", "changelog", "changelog.md"} or name.endswith(".md"):
        return "docs_changelog"
    if (
        path.startswith("frontend/src/")
        or "/components/" in path
        or "/pages/" in path
        or name.endswith((".jsx", ".tsx", ".css"))
    ):
        return "frontend_ui"
    if (
        "schema" in name
        or "schemas.py" in name
        or "models.py" in name
        or "pydantic" in path
        or "response_model" in path
        or path.startswith("backend/api/")
        or path.startswith("app/api/")
        or path.startswith("app/models/")
        or path.startswith("app/schemas/")
    ):
        return "api_schema_contract"
    if (
        path.startswith("backend/services/")
        or path.startswith("backend/models/")
        or path.startswith("backend/domain/")
        or path.startswith("backend/")
        or path.startswith("app/services/")
        or path.startswith("app/domain/")
        or (path.startswith("app/") and name.endswith(".py"))
    ):
        return "primary_backend_logic"
    return "unknown"


def _extract_file_diff_context_files(artifacts: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for name, artifact in artifacts.items():
        artifact_path = str(artifact.get("path", "")) if isinstance(artifact, dict) else ""
        normalized_name = _normalize_artifact_name(str(name))
        normalized_path = _normalize_artifact_name(artifact_path)
        if normalized_name in FILE_DIFF_ALIASES or normalized_path in FILE_DIFF_ALIASES:
            files.extend(_files_from_artifact(artifact))
    return files


def _files_from_artifact(artifact: Any) -> list[str]:
    if not isinstance(artifact, dict):
        return []
    content = artifact.get("content")
    if isinstance(content, dict):
        return _extract_files_list(content.get("files"))
    return []


def _extract_files_list(raw_files: Any) -> list[str]:
    if not isinstance(raw_files, list):
        return []
    files: list[str] = []
    for item in raw_files:
        if isinstance(item, str):
            files.append(_sanitize_output_string(item))
        elif isinstance(item, dict):
            for key in ("path", "file", "filename", "name"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    files.append(_sanitize_output_string(value))
                    break
    return [file for file in files if file]


def _budget_group_files(
    group: SemanticGroup,
    files: list[str],
    *,
    max_chars_per_block: int,
) -> tuple[list[str], list[str], list[str]]:
    included: list[str] = []
    partial: list[str] = []
    current_estimate = 0
    for file_path in files:
        file_estimate = _estimate_files([file_path])
        next_estimate = current_estimate + file_estimate
        if file_estimate > max_chars_per_block:
            partial.append(file_path)
            if not included:
                included.append(file_path)
                current_estimate = file_estimate
            continue
        if next_estimate > max_chars_per_block:
            partial.append(file_path)
            continue
        included.append(file_path)
        current_estimate = next_estimate

    limitations = [f"chunk_budget_exceeded:{group}"] if partial else []
    return included, partial, limitations


def _plan_status(
    *,
    intake_status: str,
    limitations: list[str],
    files_partially_covered: list[str],
    files_not_covered: list[str],
) -> str:
    if "file_context_missing" in limitations or intake_status == "degraded":
        return "degraded"
    if files_not_covered:
        return "degraded"
    if files_partially_covered or limitations:
        return "partial"
    return "complete"


def _refs_for_group(group: SemanticGroup, refs: list[str]) -> list[str]:
    selected = [ref for ref in refs if ref in {"artifact:file-diff-context", "artifact:checks"}]
    if group in {"primary_backend_logic", "api_schema_contract"} and "artifact:local-code-intelligence" in refs:
        selected.append("artifact:local-code-intelligence")
    return _dedupe(selected)


def _artifact_refs(artifacts: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for name, artifact in artifacts.items():
        artifact_path = str(artifact.get("path", "")) if isinstance(artifact, dict) else ""
        for value in (str(name), artifact_path):
            ref = KNOWN_ARTIFACT_REFS.get(_normalize_artifact_name(value))
            if ref:
                refs.append(ref)
    return _dedupe(refs)


def _contract_refs(intake: dict[str, Any]) -> list[str]:
    profile = intake.get("target_profile")
    if isinstance(profile, dict) and profile.get("domain_contracts"):
        return ["target_profile:domain_contracts"]
    return []


def _estimate_files(files: list[str]) -> int:
    return sum(max(256, len(file_path) * 12) for file_path in files)


def _sanitize_output_string(value: str) -> str:
    return redact_text(value, RedactionState())


def _normalize_artifact_name(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
