"""Structured chunk result parser for offline AgentReview responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent_review.finding_normalizer import DedupeState, normalize_chunk_response
from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import (
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    ChunkCoverageNotes,
    ChunkParseFailure,
    ChunkResponse,
    ChunkResults,
    ChunkResultsCoverage,
    SemanticChunk,
    SemanticChunkPlan,
)


class ChunkResultParserError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def load_json_object(path: Path | str, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ChunkResultParserError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise ChunkResultParserError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise ChunkResultParserError(error_class, "input JSON must be an object")
    return raw


def load_chunk_plan(path: Path | str) -> SemanticChunkPlan:
    raw = load_json_object(path, error_class="chunk_plan_invalid")
    schema_id = raw.get("schema_id")
    schema_version = raw.get("schema_version")
    if schema_id != SEMANTIC_CHUNK_PLAN_SCHEMA or schema_version != 1:
        raise ChunkResultParserError("chunk_plan_invalid", "semantic chunk plan schema is invalid")
    try:
        plan = SemanticChunkPlan.model_validate(raw)
    except ValidationError as exc:
        raise ChunkResultParserError("chunk_plan_invalid", "semantic chunk plan structure is invalid") from exc
    if plan.status == "failed":
        raise ChunkResultParserError("chunk_plan_invalid", "semantic chunk plan status is failed")
    return plan


def parse_chunk_results(
    chunk_plan: SemanticChunkPlan,
    *,
    responses_dir: Path | str,
) -> ChunkResults:
    response_root = Path(responses_dir).resolve()
    if not response_root.exists() or not response_root.is_dir():
        raise ChunkResultParserError("responses_dir_invalid", "responses-dir must be an existing directory")

    chunks_parsed: list[str] = []
    chunks_failed: list[ChunkParseFailure] = []
    confirmed_findings = []
    risks = []
    rejected_findings = []
    coverage = ChunkResultsCoverage()
    limitations = list(chunk_plan.limitations)
    dedupe_state = DedupeState()

    if chunk_plan.status == "degraded":
        limitations.append("chunk_plan_status_degraded")
    if not chunk_plan.chunks:
        limitations.append("chunk_plan_has_no_chunks")

    for chunk in chunk_plan.chunks:
        response_path = _expected_response_path(response_root, chunk)
        if response_path is None:
            chunks_failed.append(_failure(chunk, "chunk_response_path_invalid", "response path escapes responses-dir"))
            coverage.files_not_reviewed.extend(chunk.files)
            limitations.append("chunk_response_path_invalid")
            continue
        if not response_path.exists():
            chunks_failed.append(_failure(chunk, "chunk_response_missing", "chunk response file is missing"))
            coverage.files_not_reviewed.extend(chunk.files)
            limitations.append("chunk_response_missing")
            continue

        response = _load_chunk_response(response_path, chunk)
        if isinstance(response, ChunkParseFailure):
            chunks_failed.append(response)
            coverage.files_not_reviewed.extend(chunk.files)
            limitations.append(response.error_class)
            continue

        chunks_parsed.append(chunk.chunk_id)
        normalized = normalize_chunk_response(response, chunk=chunk, dedupe_state=dedupe_state)
        confirmed_findings.extend(normalized.confirmed_findings)
        risks.extend(normalized.risks)
        rejected_findings.extend(normalized.rejected_findings)
        limitations.extend(normalized.limitations)
        limitations.extend(_response_limitations(response))
        filtered_coverage, coverage_limitations = _filter_coverage_notes(response.coverage_notes, chunk)
        limitations.extend(coverage_limitations)
        coverage.files_reviewed.extend(filtered_coverage.files_reviewed)
        coverage.files_partial.extend(filtered_coverage.files_partial)
        coverage.files_not_reviewed.extend(filtered_coverage.files_not_reviewed)

    coverage = ChunkResultsCoverage(
        files_reviewed=_dedupe(coverage.files_reviewed),
        files_partial=_dedupe(coverage.files_partial),
        files_not_reviewed=_dedupe(coverage.files_not_reviewed),
    )
    results = ChunkResults(
        target_repo=chunk_plan.target_repo,
        chunk_plan_ref=_chunk_plan_ref(chunk_plan),
        chunks_parsed=chunks_parsed,
        chunks_failed=chunks_failed,
        confirmed_findings=confirmed_findings,
        risks=risks,
        limitations=_dedupe(limitations),
        rejected_findings=rejected_findings,
        coverage=coverage,
        status=_result_status(chunk_plan=chunk_plan, parsed_count=len(chunks_parsed), failed_count=len(chunks_failed)),
    )
    return _sanitize_results(results)


def _load_chunk_response(response_path: Path, chunk: SemanticChunk) -> ChunkResponse | ChunkParseFailure:
    try:
        raw = json.loads(response_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _failure(chunk, "chunk_response_json_invalid", "chunk response JSON is invalid")
    if not isinstance(raw, dict):
        return _failure(chunk, "chunk_response_schema_invalid", "chunk response must be a JSON object")
    if raw.get("schema_version") != 1:
        return _failure(chunk, "chunk_response_schema_invalid", "chunk response schema_version must be 1")

    try:
        response = ChunkResponse.model_validate(raw)
    except ValidationError:
        return _failure(chunk, "chunk_response_schema_invalid", "chunk response structure is invalid")

    if response.chunk_id != chunk.chunk_id or response.semantic_group != chunk.semantic_group:
        return _failure(chunk, "chunk_response_mismatch", "chunk response does not match semantic chunk")
    return response


def _expected_response_path(response_root: Path, chunk: SemanticChunk) -> Path | None:
    if "/" in chunk.chunk_id or "\\" in chunk.chunk_id or chunk.chunk_id in {".", ".."}:
        return None
    candidate = (response_root / f"{chunk.chunk_id}.json").resolve()
    if not _is_relative_to(candidate, response_root):
        return None
    return candidate


def _failure(chunk: SemanticChunk, error_class: str, message: str) -> ChunkParseFailure:
    return ChunkParseFailure(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        error_class=error_class,
        message=message,
    )


def _result_status(*, chunk_plan: SemanticChunkPlan, parsed_count: int, failed_count: int) -> str:
    if chunk_plan.status == "degraded":
        return "degraded"
    if not chunk_plan.chunks:
        return "degraded"
    if parsed_count == 0:
        return "degraded"
    if failed_count:
        return "partial"
    return "complete"


def _chunk_plan_ref(chunk_plan: SemanticChunkPlan) -> dict[str, Any]:
    return {
        "schema_id": chunk_plan.schema_id,
        "schema_version": chunk_plan.schema_version,
        "source": chunk_plan.source,
        "status": chunk_plan.status,
        "created_at": chunk_plan.created_at,
        "chunk_count": len(chunk_plan.chunks),
    }


def _response_limitations(response: ChunkResponse) -> list[str]:
    limitations: list[str] = []
    for limitation in response.limitations:
        limitation_type = _clean(limitation.type)
        detail = _clean(limitation.detail)
        if limitation_type and detail:
            limitations.append(f"{limitation_type}:{detail}")
        elif limitation_type:
            limitations.append(limitation_type)
        elif detail:
            limitations.append(detail)
    return limitations


def _filter_coverage_notes(
    coverage_notes: ChunkCoverageNotes,
    chunk: SemanticChunk,
) -> tuple[ChunkResultsCoverage, list[str]]:
    chunk_files = set(chunk.files)
    removed = False

    def keep_chunk_files(files: list[str]) -> list[str]:
        nonlocal removed
        filtered: list[str] = []
        for file_path in files:
            if file_path in chunk_files:
                filtered.append(file_path)
            else:
                removed = True
        return filtered

    filtered = ChunkResultsCoverage(
        files_reviewed=keep_chunk_files(coverage_notes.files_reviewed),
        files_partial=keep_chunk_files(coverage_notes.files_partial),
        files_not_reviewed=keep_chunk_files(coverage_notes.files_not_reviewed),
    )
    limitations = [f"coverage_file_not_in_chunk:{chunk.chunk_id}"] if removed else []
    return filtered, limitations


def _sanitize_results(results: ChunkResults) -> ChunkResults:
    redaction_state = RedactionState()
    redaction_state.record_file()
    redacted = redact_value(results.model_dump(mode="json"), redaction_state)
    return ChunkResults.model_validate(redacted)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
