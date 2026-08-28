"""Structured chunk result parser for offline AgentReview responses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent_review.chunk_artifact_ids import (
    ChunkArtifactIdError,
    chunk_artifact_filename,
    validate_chunk_ids,
)
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


COVERAGE_STATE_PRIORITY = {
    "reviewed": 0,
    "partial": 1,
    "not_reviewed": 2,
}


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
    _validate_chunk_plan_ids(plan)
    return plan


def parse_chunk_results(
    chunk_plan: SemanticChunkPlan,
    *,
    responses_dir: Path | str,
) -> ChunkResults:
    _validate_chunk_plan_ids(chunk_plan)
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
    # Kept strictly apart from `limitations` (AgentEscala#675, Fix A). Every
    # append to `limitations` below is engine-authored; the only model-authored
    # strings in this function land in `model_reported_limitations`.
    model_reported_limitations: list[str] = []
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
        model_reported_limitations.extend(_response_limitations(response))
        normalized_coverage, coverage_limitations, foreign_path_reported = _normalize_coverage_partition(
            response.coverage_notes,
            expected_files=chunk.files,
        )
        if foreign_path_reported:
            coverage_limitations.insert(0, f"coverage_file_not_in_chunk:{chunk.chunk_id}")
        limitations.extend(coverage_limitations)
        coverage.files_reviewed.extend(normalized_coverage.files_reviewed)
        coverage.files_partial.extend(normalized_coverage.files_partial)
        coverage.files_not_reviewed.extend(normalized_coverage.files_not_reviewed)

    coverage, aggregate_coverage_limitations, _ = _normalize_coverage_partition(
        coverage,
        expected_files=[file_path for chunk in chunk_plan.chunks for file_path in chunk.files],
    )
    limitations.extend(aggregate_coverage_limitations)
    results = ChunkResults(
        target_repo=chunk_plan.target_repo,
        chunk_plan_ref=_chunk_plan_ref(chunk_plan),
        chunks_parsed=chunks_parsed,
        chunks_failed=chunks_failed,
        confirmed_findings=confirmed_findings,
        risks=risks,
        limitations=_dedupe(limitations),
        model_reported_limitations=_dedupe(model_reported_limitations),
        rejected_findings=rejected_findings,
        coverage=coverage,
        status=_result_status(
            chunk_plan=chunk_plan,
            parsed_count=len(chunks_parsed),
            failed_count=len(chunks_failed),
            coverage=coverage,
            limitations=limitations,
        ),
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
    try:
        filename = chunk_artifact_filename(chunk.chunk_id, artifact_root=response_root)
    except ChunkArtifactIdError:
        return None
    candidate = (response_root / filename).resolve()
    if not _is_relative_to(candidate, response_root):
        return None
    return candidate


def _validate_chunk_plan_ids(chunk_plan: SemanticChunkPlan) -> None:
    try:
        validate_chunk_ids(chunk.chunk_id for chunk in chunk_plan.chunks)
    except ChunkArtifactIdError as exc:
        raise ChunkResultParserError(exc.error_class, exc.message) from exc


def _failure(chunk: SemanticChunk, error_class: str, message: str) -> ChunkParseFailure:
    return ChunkParseFailure(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        error_class=error_class,
        message=message,
    )


def _result_status(
    *,
    chunk_plan: SemanticChunkPlan,
    parsed_count: int,
    failed_count: int,
    coverage: ChunkResultsCoverage,
    limitations: list[str],
) -> str:
    if chunk_plan.status == "degraded":
        return "degraded"
    if not chunk_plan.chunks:
        return "degraded"
    if parsed_count == 0:
        return "degraded"
    if failed_count:
        return "partial"
    # H1-B (C10, post-merge debt #205; PR #231 review round 2, P1): a
    # `partial` chunk_plan (unproven coverage, e.g. file_context_fallback_
    # used) must not read downstream as `complete` just because every
    # chunk that was actually produced happened to parse cleanly --
    # final_synthesizer and quality_gate already treat "partial" as
    # blocking wherever they read `chunk_results.status`.
    if chunk_plan.status == "partial":
        return "partial"
    if coverage.files_partial or coverage.files_not_reviewed:
        return "partial"
    if any(
        limitation in {"coverage_expected_files_missing", "coverage_file_in_multiple_states"}
        or limitation.startswith("coverage_file_not_in_chunk:")
        for limitation in limitations
    ):
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
    """Flatten the model's self-reported limitations, verbatim.

    `ChunkResponseLimitation.type` and `.detail` are both unconstrained
    `str | None` straight out of the chunk response, so everything this
    returns is LLM-authored text. It feeds `ChunkResults.model_reported_
    limitations` and nothing else: routing it into `limitations` -- as this
    engine did before AgentEscala#675 -- let the model publish prose as
    though it were a deterministic reason code, and let it double-count a
    single cause by echoing a real code back with a sentence attached
    (`X` from the engine plus `X:<prose>` from the model, both surviving a
    dedupe that compares whole strings).

    Note the flattened `f"{type}:{detail}"` shape is deliberately preserved:
    the leading token stays the model's claimed code, so a target can still
    line a model observation up with the deterministic code it refers to
    without the two ever sharing a namespace.
    """
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


def _expected_plan_files(chunk_plan: SemanticChunkPlan) -> list[str]:
    """Return the plan-wide file universe used by downstream coverage checks."""
    files = [
        *chunk_plan.files_covered,
        *chunk_plan.files_partially_covered,
        *chunk_plan.files_not_covered,
    ]
    for chunk in chunk_plan.chunks:
        files.extend(chunk.files)
    return _dedupe(files)


def _normalize_coverage_partition(
    coverage_notes: ChunkCoverageNotes | ChunkResultsCoverage,
    *,
    expected_files: list[str],
) -> tuple[ChunkResultsCoverage, list[str], bool]:
    """Return one pessimistic coverage state for every expected file.

    This is the sole partition authority. Callers may aggregate its output and
    pass that aggregate back through the same authority, which keeps the union
    disjoint when a path occurs in more than one chunk.
    """
    expected = _dedupe(expected_files)
    expected_set = set(expected)
    assignments: dict[str, set[str]] = {file_path: set() for file_path in expected}
    foreign_path_reported = False

    for state, reported_files in (
        ("reviewed", coverage_notes.files_reviewed),
        ("partial", coverage_notes.files_partial),
        ("not_reviewed", coverage_notes.files_not_reviewed),
    ):
        for file_path in _dedupe(reported_files):
            if file_path not in expected_set:
                foreign_path_reported = True
                continue
            assignments[file_path].add(state)

    normalized: dict[str, list[str]] = {
        "reviewed": [],
        "partial": [],
        "not_reviewed": [],
    }
    expected_file_missing = False
    file_in_multiple_states = False

    for file_path in expected:
        states = assignments[file_path]
        if not states:
            expected_file_missing = True
            normalized["not_reviewed"].append(file_path)
            continue
        if len(states) > 1:
            file_in_multiple_states = True
        state = max(states, key=COVERAGE_STATE_PRIORITY.__getitem__)
        normalized[state].append(file_path)

    limitations: list[str] = []
    if expected_file_missing:
        limitations.append("coverage_expected_files_missing")
    if file_in_multiple_states:
        limitations.append("coverage_file_in_multiple_states")

    return (
        ChunkResultsCoverage(
            files_reviewed=normalized["reviewed"],
            files_partial=normalized["partial"],
            files_not_reviewed=normalized["not_reviewed"],
        ),
        limitations,
        foreign_path_reported,
    )


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
