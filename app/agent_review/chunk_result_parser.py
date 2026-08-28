"""Structured chunk result parser for offline AgentReview responses."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import ValidationError

from app.agent_review.chunk_artifact_ids import (
    ChunkArtifactIdError,
    chunk_artifact_filename,
    validate_chunk_id,
    validate_chunk_ids,
)
from app.agent_review.finding_normalizer import DedupeState, normalize_chunk_response
from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import (
    CHUNK_RESULTS_SCHEMA,
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    ChunkCoverageNotes,
    ChunkParseFailure,
    ChunkResponse,
    ChunkResults,
    ChunkResultsCoverage,
    NormalizedFinding,
    NormalizedRisk,
    RejectedFinding,
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

_CHUNK_RESULT_STATUSES = frozenset({"complete", "partial", "degraded", "failed"})
_CHUNK_PLAN_REF_STATUSES = frozenset({"complete", "partial", "degraded", "failed"})
_RESULT_IDENTITY_TRUST_LIMITATIONS = frozenset(
    {
        "target_repo_mismatch",
        "chunk_plan_ref_mismatch",
        "chunk_results_schema_mismatch",
        "chunk_plan_schema_mismatch",
        "chunk_results_structure_invalid",
        "chunk_plan_structure_invalid",
        "chunk_results_status_invalid",
        "chunk_execution_duplicate_id",
        "chunk_execution_foreign_id",
        "chunk_execution_state_overlap",
    }
)
_EXACT_INTEGER_REFERENCE_FIELDS = frozenset({"schema_version", "chunk_count"})


@dataclass(frozen=True)
class _NormalizedCoveragePartition:
    """One ordered universe with exactly one effective state per file.

    This is intentionally an internal value rather than a v1 wire model.  The
    file universe is the set of assignment keys, so callers cannot separate a
    flat expected-file list from the plan state that constrains it.
    """

    assignments: tuple[tuple[str, str], ...]
    limitations: tuple[str, ...] = ()
    foreign_files: tuple[str, ...] = ()

    @property
    def expected_files(self) -> list[str]:
        return [file_path for file_path, _ in self.assignments]

    @property
    def states(self) -> dict[str, str]:
        return dict(self.assignments)

    def as_chunk_results_coverage(self) -> ChunkResultsCoverage:
        return ChunkResultsCoverage(
            files_reviewed=[
                file_path for file_path, state in self.assignments if state == "reviewed"
            ],
            files_partial=[
                file_path for file_path, state in self.assignments if state == "partial"
            ],
            files_not_reviewed=[
                file_path
                for file_path, state in self.assignments
                if state == "not_reviewed"
            ],
        )


def _snapshot_chunk_results(
    chunk_results: ChunkResults,
) -> tuple[ChunkResults, list[str]]:
    """Take a safe value snapshot of the freely mutable v1 result model.

    Pydantic validates construction, not later assignment. Downstream code
    must therefore reject non-wire container shapes before iterating them;
    dict keys, tuple values, or sets must never manufacture parsed chunks or
    reviewed files merely because they happen to be iterable.
    """
    limitations: list[str] = []

    chunks_parsed, parsed_valid = _snapshot_string_list(
        getattr(chunk_results, "chunks_parsed", None)
    )
    if not parsed_valid:
        limitations.extend(
            ["chunk_results_structure_invalid", "chunk_execution_foreign_id"]
        )

    raw_failed = getattr(chunk_results, "chunks_failed", None)
    chunks_failed, failed_valid = _snapshot_model_list(raw_failed, ChunkParseFailure)
    if not failed_valid:
        limitations.append("chunk_results_structure_invalid")
        if raw_failed:
            limitations.extend(
                ["chunks_failed_present", "chunk_execution_foreign_id"]
            )

    confirmed_findings, findings_valid = _snapshot_model_list(
        getattr(chunk_results, "confirmed_findings", None),
        NormalizedFinding,
    )
    risks, risks_valid = _snapshot_model_list(
        getattr(chunk_results, "risks", None),
        NormalizedRisk,
    )
    rejected_findings, rejected_valid = _snapshot_model_list(
        getattr(chunk_results, "rejected_findings", None),
        RejectedFinding,
    )
    if not all((findings_valid, risks_valid, rejected_valid)):
        limitations.append("chunk_results_structure_invalid")

    deterministic_limitations, deterministic_valid = _snapshot_string_list(
        getattr(chunk_results, "limitations", None)
    )
    model_limitations, model_limitations_valid = _snapshot_string_list(
        getattr(chunk_results, "model_reported_limitations", None)
    )
    if not deterministic_valid or not model_limitations_valid:
        limitations.append("chunk_results_structure_invalid")

    coverage, coverage_valid = _snapshot_results_coverage(
        getattr(chunk_results, "coverage", None)
    )
    if not coverage_valid:
        limitations.append("chunk_results_structure_invalid")

    target_repo = getattr(chunk_results, "target_repo", None)
    if not isinstance(target_repo, str) or not target_repo:
        target_repo = "unknown"
        limitations.extend(
            ["chunk_results_structure_invalid", "target_repo_mismatch"]
        )

    plan_ref = getattr(chunk_results, "chunk_plan_ref", None)
    if not isinstance(plan_ref, dict):
        plan_ref = {}
        limitations.append("chunk_results_structure_invalid")
    else:
        plan_ref = deepcopy(plan_ref)

    source = getattr(chunk_results, "source", None)
    if not isinstance(source, str) or not source:
        source = "aiops-review-parse-chunks"
        limitations.append("chunk_results_structure_invalid")
    created_at = getattr(chunk_results, "created_at", None)
    if not isinstance(created_at, str) or not created_at:
        created_at = "1970-01-01T00:00:00Z"
        limitations.append("chunk_results_structure_invalid")
    status = getattr(chunk_results, "status", None)
    if not isinstance(status, str):
        status = "degraded"
        limitations.extend(
            ["chunk_results_structure_invalid", "chunk_results_status_invalid"]
        )

    snapshot = ChunkResults.model_construct(
        schema_version=getattr(chunk_results, "schema_version", None),
        schema_id=getattr(chunk_results, "schema_id", None),
        source=source,
        target_repo=target_repo,
        chunk_plan_ref=plan_ref,
        chunks_parsed=chunks_parsed,
        chunks_failed=chunks_failed,
        confirmed_findings=confirmed_findings,
        risks=risks,
        limitations=deterministic_limitations,
        model_reported_limitations=model_limitations,
        rejected_findings=rejected_findings,
        coverage=coverage,
        status=status,
        created_at=created_at,
    )
    return snapshot, _dedupe(limitations)


def _snapshot_semantic_chunk_plan(
    chunk_plan: SemanticChunkPlan | None,
) -> tuple[SemanticChunkPlan | None, list[str]]:
    """Snapshot the v1 expected-file authority without iterable coercion."""
    if chunk_plan is None:
        return None, []

    limitations: list[str] = []
    chunks, chunks_valid = _snapshot_semantic_chunks(
        getattr(chunk_plan, "chunks", None)
    )
    files_covered, covered_valid = _snapshot_string_list(
        getattr(chunk_plan, "files_covered", None)
    )
    files_partial, partial_valid = _snapshot_string_list(
        getattr(chunk_plan, "files_partially_covered", None)
    )
    files_not_covered, not_covered_valid = _snapshot_string_list(
        getattr(chunk_plan, "files_not_covered", None)
    )
    plan_limitations, limitations_valid = _snapshot_string_list(
        getattr(chunk_plan, "limitations", None)
    )
    if not all(
        (
            chunks_valid,
            covered_valid,
            partial_valid,
            not_covered_valid,
            limitations_valid,
        )
    ):
        limitations.append("chunk_plan_structure_invalid")

    target_repo = getattr(chunk_plan, "target_repo", None)
    if not isinstance(target_repo, str) or not target_repo:
        target_repo = "unknown"
        limitations.append("chunk_plan_structure_invalid")
    source = getattr(chunk_plan, "source", None)
    if not isinstance(source, str) or not source:
        source = "aiops-semantic-chunk-planner"
        limitations.append("chunk_plan_structure_invalid")
    created_at = getattr(chunk_plan, "created_at", None)
    if not isinstance(created_at, str) or not created_at:
        created_at = "1970-01-01T00:00:00Z"
        limitations.append("chunk_plan_structure_invalid")
    max_parallel_blocks = getattr(chunk_plan, "max_parallel_blocks", None)
    if type(max_parallel_blocks) is not int:
        max_parallel_blocks = 0
        limitations.append("chunk_plan_structure_invalid")
    status = getattr(chunk_plan, "status", None)
    if not isinstance(status, str):
        status = "failed"
        limitations.append("chunk_plan_structure_invalid")

    snapshot = SemanticChunkPlan.model_construct(
        schema_version=getattr(chunk_plan, "schema_version", None),
        schema_id=getattr(chunk_plan, "schema_id", None),
        source=source,
        target_repo=target_repo,
        max_parallel_blocks=max_parallel_blocks,
        chunks=chunks,
        files_covered=files_covered,
        files_partially_covered=files_partial,
        files_not_covered=files_not_covered,
        limitations=plan_limitations,
        status=status,
        created_at=created_at,
    )
    return snapshot, _dedupe(limitations)


def _snapshot_results_coverage(
    coverage: object,
) -> tuple[ChunkResultsCoverage, bool]:
    if not isinstance(coverage, ChunkResultsCoverage):
        return ChunkResultsCoverage(), False
    reviewed, reviewed_valid = _snapshot_string_list(coverage.files_reviewed)
    partial, partial_valid = _snapshot_string_list(coverage.files_partial)
    not_reviewed, not_reviewed_valid = _snapshot_string_list(
        coverage.files_not_reviewed
    )
    return (
        ChunkResultsCoverage(
            files_reviewed=reviewed,
            files_partial=partial,
            files_not_reviewed=not_reviewed,
        ),
        reviewed_valid and partial_valid and not_reviewed_valid,
    )


def _snapshot_semantic_chunks(
    value: object,
) -> tuple[list[SemanticChunk], bool]:
    if type(value) is not list:
        return [], False
    snapshots: list[SemanticChunk] = []
    valid = True
    for item in value:
        if not isinstance(item, SemanticChunk):
            valid = False
            continue
        files, files_valid = _snapshot_string_list(item.files)
        artifacts, artifacts_valid = _snapshot_string_list(item.artifacts)
        contracts, contracts_valid = _snapshot_string_list(item.contracts)
        depends_on, depends_valid = _snapshot_string_list(item.depends_on)
        item_limitations, item_limitations_valid = _snapshot_string_list(
            item.limitations
        )
        valid = valid and all(
            (
                files_valid,
                artifacts_valid,
                contracts_valid,
                depends_valid,
                item_limitations_valid,
            )
        )
        snapshots.append(
            SemanticChunk.model_construct(
                chunk_id=item.chunk_id,
                semantic_group=item.semantic_group,
                order_index=item.order_index,
                files=files,
                artifacts=artifacts,
                contracts=contracts,
                depends_on=depends_on,
                coverage=item.coverage,
                prompt_budget_chars=item.prompt_budget_chars,
                estimated_chars=item.estimated_chars,
                limitations=item_limitations,
            )
        )
    return snapshots, valid


def _snapshot_model_list(value: object, model_type: Any) -> tuple[list[Any], bool]:
    if type(value) is not list:
        return [], False
    snapshots: list[Any] = []
    valid = True
    for item in value:
        if not isinstance(item, model_type):
            valid = False
            continue
        try:
            snapshots.append(
                model_type.model_validate(
                    item.model_dump(mode="python", warnings=False)
                )
            )
        except (AttributeError, TypeError, ValueError, ValidationError):
            valid = False
    return snapshots, valid


def _snapshot_string_list(value: object) -> tuple[list[str], bool]:
    if type(value) is not list or any(not isinstance(item, str) for item in value):
        return [], False
    return list(value), True


def _reference_field_matches(field: str, reported: object, expected: object) -> bool:
    if field in _EXACT_INTEGER_REFERENCE_FIELDS:
        return (
            type(reported) is int
            and type(expected) is int
            and reported == expected
        )
    return reported == expected


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
    if (
        schema_id != SEMANTIC_CHUNK_PLAN_SCHEMA
        or type(schema_version) is not int
        or schema_version != 1
    ):
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
    if chunk_plan.status == "failed":
        raise ChunkResultParserError(
            "chunk_plan_invalid",
            "semantic chunk plan status is failed",
        )
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

    response_universe = [
        file_path for chunk in chunk_plan.chunks for file_path in chunk.files
    ]
    plan_run_coverage = _normalize_plan_run_coverage_partition(
        chunk_plan,
        chunks_parsed=chunks_parsed,
        chunks_failed=chunks_failed,
    )
    limitations.extend(plan_run_coverage.limitations)
    reported_coverage = _build_normalized_coverage_partition(
        coverage,
        expected_files=response_universe,
    )
    limitations.extend(reported_coverage.limitations)
    coverage = _compose_coverage_partitions(
        plan_run_coverage,
        reported_coverage,
    ).as_chunk_results_coverage()
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
            plan_coverage=plan_run_coverage,
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
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
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
    plan_coverage: _NormalizedCoveragePartition,
    parsed_count: int,
    failed_count: int,
    coverage: ChunkResultsCoverage,
    limitations: list[str],
) -> str:
    if chunk_plan.status in {"degraded", "failed"}:
        return "degraded"
    if any(
        _reason_matches(limitation, reason)
        for limitation in limitations
        for reason in (
            "chunk_plan_status_degraded",
            "chunk_plan_status_failed",
            "chunk_execution_expected_missing",
            "chunk_execution_foreign_id",
            "chunk_execution_duplicate_id",
            "chunk_execution_state_overlap",
        )
    ):
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
    if chunk_plan.status == "partial" or "chunk_plan_status_partial" in limitations:
        return "partial"
    normalized_plan_coverage = plan_coverage.as_chunk_results_coverage()
    if (
        normalized_plan_coverage.files_partial
        or normalized_plan_coverage.files_not_reviewed
    ):
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


def _normalize_plan_coverage_partition(
    chunk_plan: SemanticChunkPlan,
) -> _NormalizedCoveragePartition:
    """Normalize the plan's universe and state claims as one authority value.

    A plan may preserve partial/not-covered paths outside emitted chunks, but
    `files_covered` is not response evidence by itself.  A reviewed state is
    therefore valid only for a path assigned to at least one chunk; otherwise
    the path remains in the universe and is pessimistically unreviewed.
    """
    chunk_files = [
        file_path for chunk in chunk_plan.chunks for file_path in chunk.files
    ]
    expected_files = [
        *chunk_plan.files_covered,
        *chunk_plan.files_partially_covered,
        *chunk_plan.files_not_covered,
        *chunk_files,
    ]
    normalized = _build_normalized_coverage_partition(
        ChunkResultsCoverage(
            files_reviewed=chunk_plan.files_covered,
            files_partial=chunk_plan.files_partially_covered,
            files_not_reviewed=chunk_plan.files_not_covered,
        ),
        expected_files=expected_files,
    )
    effective = normalized.states
    chunk_file_set = set(chunk_files)
    derived_plan_status: str | None = None

    for chunk in chunk_plan.chunks:
        if chunk.coverage == "complete":
            cap = "reviewed"
        elif chunk.coverage == "partial":
            cap = "partial"
            if derived_plan_status is None:
                derived_plan_status = "partial"
        else:
            # `degraded` is the only other schema-valid value. Treat an
            # assignment-mutated unknown value the same way: it must never
            # restore a reviewed claim.
            cap = "not_reviewed"
            derived_plan_status = "degraded"
        for file_path in _dedupe(chunk.files):
            if (
                file_path in effective
                and COVERAGE_STATE_PRIORITY[cap]
                > COVERAGE_STATE_PRIORITY[effective[file_path]]
            ):
                effective[file_path] = cap

    if "file_context_missing" in chunk_plan.limitations:
        derived_plan_status = "degraded"
        effective = {file_path: "not_reviewed" for file_path in effective}
    elif (
        "file_context_fallback_used" in chunk_plan.limitations
        and derived_plan_status is None
    ):
        derived_plan_status = "partial"

    unassigned_reviewed = {
        file_path
        for file_path, state in effective.items()
        if state == "reviewed" and file_path not in chunk_file_set
    }
    for file_path in unassigned_reviewed:
        effective[file_path] = "not_reviewed"

    limitations = list(normalized.limitations)
    for root_reason in ("file_context_missing", "file_context_fallback_used"):
        if root_reason in chunk_plan.limitations:
            limitations.append(root_reason)
    if chunk_plan.status != "complete":
        limitations.append(f"chunk_plan_status_{chunk_plan.status}")
    if derived_plan_status is not None:
        limitations.append(f"chunk_plan_status_{derived_plan_status}")
    if unassigned_reviewed:
        limitations.append("coverage_expected_files_missing")

    return _NormalizedCoveragePartition(
        assignments=tuple(
            (file_path, effective[file_path])
            for file_path in normalized.expected_files
        ),
        limitations=tuple(_dedupe(limitations)),
        foreign_files=normalized.foreign_files,
    )


def _chunk_execution_limitations(
    *,
    chunks_parsed: list[str],
    chunks_failed: list[ChunkParseFailure],
    chunk_plan: SemanticChunkPlan | None,
) -> list[str]:
    """Validate the observable chunk execution ledger without trusting status.

    Reason codes deliberately do not interpolate reported IDs. A foreign ID
    is freely mutable input at the downstream boundaries and must not acquire
    a deterministic namespace merely by being echoed into one.
    """
    parsed_ids, invalid_parsed_id = _validated_chunk_ids(chunks_parsed)
    failed_ids, invalid_failed_id = _validated_chunk_ids(
        getattr(failure, "chunk_id", None) for failure in chunks_failed
    )
    invalid_reported_id = invalid_parsed_id or invalid_failed_id
    parsed_counts = Counter(parsed_ids)
    failed_counts = Counter(failed_ids)
    parsed_set = set(parsed_ids)
    failed_set = set(failed_ids)
    limitations: list[str] = []

    expected_ids, invalid_expected_id = _validated_chunk_ids(
        (chunk.chunk_id for chunk in chunk_plan.chunks)
        if chunk_plan is not None
        else ()
    )
    expected_counts = Counter(expected_ids)
    expected_set = set(expected_ids)
    valid_parsed = parsed_set & expected_set if chunk_plan is not None else parsed_set
    if not valid_parsed:
        limitations.append("chunks_parsed_missing")
    if any(
        count > 1
        for counts in (parsed_counts, failed_counts, expected_counts)
        for count in counts.values()
    ):
        limitations.append("chunk_execution_duplicate_id")
    if parsed_set & failed_set:
        limitations.append("chunk_execution_state_overlap")
    if chunks_failed:
        limitations.append("chunks_failed_present")

    if chunk_plan is not None:
        accounted_ids = parsed_set | failed_set
        if expected_set - accounted_ids:
            limitations.append("chunk_execution_expected_missing")
        if (
            invalid_reported_id
            or invalid_expected_id
            or (parsed_set | failed_set) - expected_set
        ):
            limitations.append("chunk_execution_foreign_id")
    elif invalid_reported_id:
        limitations.append("chunk_execution_foreign_id")

    return _dedupe(limitations)


def _chunk_result_integrity_limitations(
    chunk_results: ChunkResults,
    *,
    chunk_plan: SemanticChunkPlan | None,
    target_repos: Iterable[object] = (),
) -> list[str]:
    """Bind freely mutable result metadata to observable input authority.

    The v1 plan reference has no required digest, so only contradictions that
    are actually present can be rejected. Missing optional reference fields
    remain compatible with legacy v1 carriers; present fields never override
    a supplied plan or a contradictory execution ledger.
    """
    limitations: list[str] = []

    if (
        chunk_results.schema_id != CHUNK_RESULTS_SCHEMA
        or type(chunk_results.schema_version) is not int
        or chunk_results.schema_version != 1
    ):
        limitations.append("chunk_results_schema_mismatch")

    if chunk_plan is not None and (
        chunk_plan.schema_id != SEMANTIC_CHUNK_PLAN_SCHEMA
        or type(chunk_plan.schema_version) is not int
        or chunk_plan.schema_version != 1
    ):
        limitations.append("chunk_plan_schema_mismatch")

    if (
        not isinstance(chunk_results.status, str)
        or chunk_results.status not in _CHUNK_RESULT_STATUSES
    ):
        limitations.append("chunk_results_status_invalid")

    repositories: list[object] = [chunk_results.target_repo, *target_repos]
    if chunk_plan is not None:
        repositories.append(chunk_plan.target_repo)
    if (
        any(not isinstance(value, str) or not value for value in repositories)
        or len(set(repositories)) > 1
    ):
        limitations.append("target_repo_mismatch")

    plan_ref = chunk_results.chunk_plan_ref
    if not isinstance(plan_ref, dict):
        limitations.append("chunk_plan_ref_mismatch")
        return _dedupe(limitations)

    if (
        "schema_id" in plan_ref
        and plan_ref["schema_id"] != SEMANTIC_CHUNK_PLAN_SCHEMA
    ) or (
        "schema_version" in plan_ref
        and not _reference_field_matches(
            "schema_version",
            plan_ref["schema_version"],
            1,
        )
    ):
        limitations.append("chunk_plan_ref_mismatch")

    if (
        "target_repo" in plan_ref
        and plan_ref["target_repo"] != chunk_results.target_repo
    ):
        limitations.append("chunk_plan_ref_mismatch")

    ref_status = plan_ref.get("status") if "status" in plan_ref else None
    if "status" in plan_ref:
        if not isinstance(ref_status, str) or ref_status not in _CHUNK_PLAN_REF_STATUSES:
            limitations.append("chunk_plan_ref_mismatch")
        elif ref_status != "complete":
            limitations.append(f"chunk_plan_status_{ref_status}")

    if chunk_plan is not None:
        expected_ref = {
            "schema_id": chunk_plan.schema_id,
            "schema_version": chunk_plan.schema_version,
            "source": chunk_plan.source,
            "status": chunk_plan.status,
            "created_at": chunk_plan.created_at,
            "target_repo": chunk_plan.target_repo,
            "chunk_count": len(chunk_plan.chunks),
        }
        if any(
            field in plan_ref
            and not _reference_field_matches(field, plan_ref[field], expected)
            for field, expected in expected_ref.items()
        ):
            limitations.append("chunk_plan_ref_mismatch")
        return _dedupe(limitations)

    if "chunk_count" in plan_ref:
        declared_count = plan_ref["chunk_count"]
        if type(declared_count) is not int or declared_count < 0:
            limitations.append("chunk_plan_ref_mismatch")
        else:
            parsed_ids = set(_validated_chunk_ids(chunk_results.chunks_parsed)[0])
            failed_ids = set(
                _validated_chunk_ids(
                    getattr(failure, "chunk_id", None)
                    for failure in chunk_results.chunks_failed
                )[0]
            )
            accounted_count = len(parsed_ids | failed_ids)
            if declared_count > accounted_count:
                limitations.append("chunk_execution_expected_missing")
            elif declared_count < accounted_count:
                limitations.append("chunk_execution_foreign_id")

    return _dedupe(limitations)


def _result_identity_trustworthy(limitations: Iterable[str]) -> bool:
    return not any(
        limitation in _RESULT_IDENTITY_TRUST_LIMITATIONS
        for limitation in limitations
    )


def _normalize_plan_run_coverage_partition(
    chunk_plan: SemanticChunkPlan,
    *,
    chunks_parsed: list[str],
    chunks_failed: list[ChunkParseFailure],
) -> _NormalizedCoveragePartition:
    """Bind plan coverage to one coherent parsed/failed execution ledger."""
    plan_coverage = _normalize_plan_coverage_partition(chunk_plan)
    parsed_ids = set(_validated_chunk_ids(chunks_parsed)[0])
    failed_ids = set(
        _validated_chunk_ids(
            getattr(failure, "chunk_id", None) for failure in chunks_failed
        )[0]
    )
    assigned_chunk_ids: dict[str, list[str | None]] = {
        file_path: [] for file_path in plan_coverage.expected_files
    }
    for chunk in chunk_plan.chunks:
        validated_chunk_id = _validated_chunk_id_or_none(chunk.chunk_id)
        for file_path in _dedupe(chunk.files):
            if file_path in assigned_chunk_ids:
                assigned_chunk_ids[file_path].append(validated_chunk_id)

    assignments: list[tuple[str, str]] = []
    for file_path, state in plan_coverage.assignments:
        required_ids = assigned_chunk_ids[file_path]
        execution_backed = all(
            chunk_id is not None
            and chunk_id in parsed_ids
            and chunk_id not in failed_ids
            for chunk_id in required_ids
        )
        assignments.append(
            (
                file_path,
                state if not required_ids or execution_backed else "not_reviewed",
            )
        )

    execution_limitations = _chunk_execution_limitations(
        chunks_parsed=chunks_parsed,
        chunks_failed=chunks_failed,
        chunk_plan=chunk_plan,
    )
    return _NormalizedCoveragePartition(
        assignments=tuple(assignments),
        limitations=tuple(
            _dedupe([*plan_coverage.limitations, *execution_limitations])
        ),
        foreign_files=plan_coverage.foreign_files,
    )


def _validated_chunk_id_or_none(value: object) -> str | None:
    try:
        return validate_chunk_id(value)
    except ChunkArtifactIdError:
        return None


def _validated_chunk_ids(values: Iterable[object]) -> tuple[list[str], bool]:
    validated: list[str] = []
    invalid = False
    for value in values:
        chunk_id = _validated_chunk_id_or_none(value)
        if chunk_id is None:
            invalid = True
        else:
            validated.append(chunk_id)
    return validated, invalid


def _normalize_coverage_against_partition(
    coverage_notes: ChunkCoverageNotes | ChunkResultsCoverage,
    *,
    authority: _NormalizedCoveragePartition,
) -> _NormalizedCoveragePartition:
    """Revalidate one freely constructible carrier against one authority."""
    return _build_normalized_coverage_partition(
        coverage_notes,
        expected_files=authority.expected_files,
    )


def _coverage_universe_partition(files: list[str]) -> _NormalizedCoveragePartition:
    """Build a neutral reviewed-state partition for an observable universe."""
    expected = _dedupe(files)
    return _NormalizedCoveragePartition(
        assignments=tuple((file_path, "reviewed") for file_path in expected)
    )


def _compose_coverage_partitions(
    authority: _NormalizedCoveragePartition,
    *reported_partitions: _NormalizedCoveragePartition,
) -> _NormalizedCoveragePartition:
    """Join normalized carriers without treating cross-carrier states as overlap."""
    effective = authority.states
    limitations = list(authority.limitations)
    foreign_files = list(authority.foreign_files)

    for reported in reported_partitions:
        limitations.extend(reported.limitations)
        foreign_files.extend(reported.foreign_files)
        for file_path, state in reported.assignments:
            if file_path not in effective:
                foreign_files.append(file_path)
                continue
            if COVERAGE_STATE_PRIORITY[state] > COVERAGE_STATE_PRIORITY[effective[file_path]]:
                effective[file_path] = state

    return _NormalizedCoveragePartition(
        assignments=tuple(
            (file_path, effective[file_path])
            for file_path in authority.expected_files
        ),
        limitations=tuple(_dedupe(limitations)),
        foreign_files=tuple(_dedupe(foreign_files)),
    )


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
    normalized = _build_normalized_coverage_partition(
        coverage_notes,
        expected_files=expected_files,
    )
    return (
        normalized.as_chunk_results_coverage(),
        list(normalized.limitations),
        bool(normalized.foreign_files),
    )


def _build_normalized_coverage_partition(
    coverage_notes: ChunkCoverageNotes | ChunkResultsCoverage,
    *,
    expected_files: list[str],
) -> _NormalizedCoveragePartition:
    expected = _dedupe(expected_files)
    expected_set = set(expected)
    assignments: dict[str, set[str]] = {file_path: set() for file_path in expected}
    foreign_files: list[str] = []

    for state, reported_files in (
        ("reviewed", coverage_notes.files_reviewed),
        ("partial", coverage_notes.files_partial),
        ("not_reviewed", coverage_notes.files_not_reviewed),
    ):
        for file_path in _dedupe(reported_files):
            if file_path not in expected_set:
                foreign_files.append(file_path)
                continue
            assignments[file_path].add(state)

    normalized: list[tuple[str, str]] = []
    expected_file_missing = False
    file_in_multiple_states = False

    for file_path in expected:
        states = assignments[file_path]
        if not states:
            expected_file_missing = True
            normalized.append((file_path, "not_reviewed"))
            continue
        if len(states) > 1:
            file_in_multiple_states = True
        state = max(states, key=COVERAGE_STATE_PRIORITY.__getitem__)
        normalized.append((file_path, state))

    limitations: list[str] = []
    if expected_file_missing:
        limitations.append("coverage_expected_files_missing")
    if file_in_multiple_states:
        limitations.append("coverage_file_in_multiple_states")

    return _NormalizedCoveragePartition(
        assignments=tuple(normalized),
        limitations=tuple(limitations),
        foreign_files=tuple(_dedupe(foreign_files)),
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


def _reason_matches(limitation: str, reason: str) -> bool:
    return limitation == reason or limitation.startswith(f"{reason}:")


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None
