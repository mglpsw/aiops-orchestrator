"""Deterministic telemetry for AgentReview final review and quality gate artifacts."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agent_review.quality_gate import validate_final_review_document
from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import (
    CHUNK_RESULTS_SCHEMA,
    FINAL_REVIEW_SCHEMA,
    INTAKE_SCHEMA,
    QUALITY_GATE_SCHEMA,
    REDACTION_REPORT_SCHEMA,
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    TELEMETRY_SCHEMA,
    ChunkResults,
    FinalReview,
    RedactionReport,
    ReviewIntake,
    ReviewQualityGate,
    ReviewTelemetry,
    SemanticChunkPlan,
)


OPTIONAL_ARTIFACTS = (
    "chunk_results",
    "chunk_plan",
    "intake",
    "redaction_report",
    "checks",
    "validation_evidence",
    "test_intelligence",
    "local_code_intelligence",
)
SEVERITIES = ("P0", "P1", "P2", "P3")
PERFORMANCE_KEYS = (
    "duration_ms",
    "duration_seconds",
    "elapsed_ms",
    "elapsed_seconds",
    "bundle_chars",
    "bundle_size_chars",
    "max_bundle_chars",
)

_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")


class TelemetryError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def load_json_object(path: Path | str, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TelemetryError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise TelemetryError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise TelemetryError(error_class, "input JSON must be an object")
    return raw


def load_final_review(path: Path | str) -> dict[str, Any]:
    raw = load_json_object(path, error_class="final_review_invalid")
    if raw.get("schema_id") != FINAL_REVIEW_SCHEMA or raw.get("schema_version") != 1:
        raise TelemetryError("final_review_invalid", "final review schema is invalid")
    try:
        validated_document = validate_final_review_document(raw)
        if validated_document.verdict_unknown:
            raise TelemetryError("final_review_invalid", "final review verdict is invalid")
        validated = FinalReview.model_validate(raw)
    except TelemetryError:
        raise
    except Exception as exc:
        raise TelemetryError("final_review_invalid", "final review structure is invalid") from exc
    return validated.model_dump(mode="json")


def load_quality_gate(path: Path | str) -> ReviewQualityGate:
    raw = load_json_object(path, error_class="quality_gate_invalid")
    if raw.get("schema_id") != QUALITY_GATE_SCHEMA or raw.get("schema_version") != 1:
        raise TelemetryError("quality_gate_invalid", "quality gate schema is invalid")
    try:
        return ReviewQualityGate.model_validate(raw)
    except ValidationError as exc:
        raise TelemetryError("quality_gate_invalid", "quality gate structure is invalid") from exc


def load_optional_artifact(path: Path | str | None, *, name: str) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, [f"optional_artifact_missing:{name}"]
    try:
        raw = load_json_object(path, error_class=f"{name}_invalid")
        return _validate_optional_artifact(raw, name=name), []
    except TelemetryError as exc:
        return None, [exc.error_class]


def build_review_telemetry(
    *,
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    chunk_results: dict[str, Any] | None = None,
    chunk_plan: dict[str, Any] | None = None,
    intake: dict[str, Any] | None = None,
    redaction_report: dict[str, Any] | None = None,
    checks: dict[str, Any] | None = None,
    validation_evidence: dict[str, Any] | None = None,
    test_intelligence: dict[str, Any] | None = None,
    local_code_intelligence: dict[str, Any] | None = None,
    pr_number: int | None = None,
    commit_sha: str | None = None,
    review_mode: str | None = None,
    contract_pack: str | None = None,
    critical_pr: bool | None = None,
    limitations: list[str] | None = None,
) -> ReviewTelemetry:
    optional_docs = {
        "chunk_results": chunk_results,
        "chunk_plan": chunk_plan,
        "intake": intake,
        "redaction_report": redaction_report,
        "checks": checks,
        "validation_evidence": validation_evidence,
        "test_intelligence": test_intelligence,
        "local_code_intelligence": local_code_intelligence,
    }
    normalized_limitations = _dedupe(limitations or [])
    normalized_limitations.extend(_schema_limitations(optional_docs))
    normalized_limitations = _dedupe(normalized_limitations)

    telemetry = ReviewTelemetry(
        status=_status(normalized_limitations, quality_gate),
        target=_target(
            final_review,
            chunk_results=chunk_results,
            intake=intake,
            quality_gate=quality_gate,
            pr_number=pr_number,
            commit_sha=commit_sha,
            critical_pr=critical_pr,
        ),
        pipeline=_pipeline(final_review, chunk_results=chunk_results, chunk_plan=chunk_plan, intake=intake),
        coverage=_coverage(final_review, chunk_results=chunk_results, chunk_plan=chunk_plan),
        findings=_findings(final_review, chunk_results=chunk_results),
        review=_review(final_review, review_mode=review_mode, contract_pack=contract_pack),
        quality_gate=_quality_gate(quality_gate),
        validation_evidence=_validation_evidence(validation_evidence),
        redaction=_redaction(redaction_report),
        model=_model(final_review, chunk_results, intake, checks, validation_evidence, test_intelligence, local_code_intelligence),
        performance=_performance(final_review, quality_gate.model_dump(mode="json"), *optional_docs.values()),
        inputs=_inputs(final_review, quality_gate, optional_docs),
        warnings=_dedupe([*quality_gate.warnings, *_consistency_warnings(final_review, quality_gate, chunk_results, chunk_plan)]),
        limitations=normalized_limitations,
    )
    return sanitize_review_telemetry(telemetry)


def sanitize_review_telemetry(telemetry: ReviewTelemetry) -> ReviewTelemetry:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(telemetry.model_dump(mode="json"), state)
    redacted = _redact_local_paths(redacted)
    return ReviewTelemetry.model_validate(redacted)


def _status(limitations: list[str], quality_gate: ReviewQualityGate) -> str:
    if quality_gate.status == "failed":
        return "degraded"
    if limitations:
        return "partial"
    return "complete"


def _target(
    final_review: dict[str, Any],
    *,
    chunk_results: dict[str, Any] | None,
    intake: dict[str, Any] | None,
    quality_gate: ReviewQualityGate,
    pr_number: int | None,
    commit_sha: str | None,
    critical_pr: bool | None,
) -> dict[str, Any]:
    gate_inputs = quality_gate.inputs if isinstance(quality_gate.inputs, dict) else {}
    return {
        "repository": _first_string(
            final_review.get("target_repo"),
            _get(chunk_results, "target_repo"),
            _get(intake, "target_repo"),
        ),
        "pr_number": pr_number,
        "commit_sha": commit_sha,
        "critical_pr": critical_pr if critical_pr is not None else gate_inputs.get("critical_pr"),
    }


def _pipeline(
    final_review: dict[str, Any],
    *,
    chunk_results: dict[str, Any] | None,
    chunk_plan: dict[str, Any] | None,
    intake: dict[str, Any] | None,
) -> dict[str, Any]:
    chunks = _get(chunk_plan, "chunks")
    chunks_failed = _get(chunk_results, "chunks_failed")
    chunks_parsed = _get(chunk_results, "chunks_parsed")
    return {
        "intake_status": _get(intake, "status"),
        "chunk_count": len(chunks) if isinstance(chunks, list) else _count_or_none(final_review, "counts", "chunks_parsed"),
        "chunks_planned": len(chunks) if isinstance(chunks, list) else None,
        "chunks_reviewed": len(chunks_parsed) if isinstance(chunks_parsed, list) else _count_or_none(final_review, "counts", "chunks_parsed"),
        "chunks_failed": len(chunks_failed) if isinstance(chunks_failed, list) else _count_or_none(final_review, "counts", "chunks_failed"),
        "chunks_degraded": _chunks_degraded(chunk_plan),
        "chunk_results_status": _get(chunk_results, "status"),
        "chunk_plan_status": _get(chunk_plan, "status"),
        "completeness_status": _get(final_review, "status"),
    }


def _coverage(
    final_review: dict[str, Any],
    *,
    chunk_results: dict[str, Any] | None,
    chunk_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    review_coverage = final_review.get("coverage") if isinstance(final_review.get("coverage"), dict) else {}
    chunk_coverage = _get(chunk_results, "coverage")
    plan_covered = _get(chunk_plan, "files_covered")
    plan_partial = _get(chunk_plan, "files_partially_covered")
    plan_not_covered = _get(chunk_plan, "files_not_covered")
    files_covered = _authoritative_string_list(review_coverage.get("files_reviewed"), _get(chunk_coverage, "files_reviewed"))
    files_partial = _authoritative_string_list(review_coverage.get("files_partial"), _get(chunk_coverage, "files_partial"))
    files_not_covered = _authoritative_string_list(
        review_coverage.get("files_not_reviewed"), _get(chunk_coverage, "files_not_reviewed")
    )
    expected_files = _authoritative_string_list(
        review_coverage.get("expected_files"),
        _dedupe(
            [
                *(_string_list(plan_covered) or []),
                *(_string_list(plan_partial) or []),
                *(_string_list(plan_not_covered) or []),
            ]
        ),
    )
    return {
        "status": _coverage_status(final_review, chunk_plan),
        "expected_files": len(expected_files) if expected_files is not None else None,
        "files_covered": len(files_covered) if files_covered is not None else None,
        "files_partial": len(files_partial) if files_partial is not None else None,
        "files_not_covered": len(files_not_covered) if files_not_covered is not None else None,
    }


def _findings(final_review: dict[str, Any], *, chunk_results: dict[str, Any] | None) -> dict[str, Any]:
    findings = _list(final_review.get("confirmed_findings"))
    risks = _list(final_review.get("risks"))
    rejected_summary = final_review.get("rejected_summary") if isinstance(final_review.get("rejected_summary"), dict) else {}
    by_severity = {severity: 0 for severity in SEVERITIES}
    counts = final_review.get("counts") if isinstance(final_review.get("counts"), dict) else {}
    raw_counts = counts.get("findings_by_severity")
    if isinstance(raw_counts, dict):
        for severity in SEVERITIES:
            value = raw_counts.get(severity)
            by_severity[severity] = value if isinstance(value, int) else 0
    else:
        by_severity.update(Counter(str(finding.get("severity")) for finding in findings if isinstance(finding, dict)))
    chunk_rejected = _list(_get(chunk_results, "rejected_findings"))
    confirmed_count = _int_or_none(counts.get("confirmed_findings_total"))
    risks_count = _int_or_none(counts.get("risks_total"))
    rejected_count = _int_or_none(rejected_summary.get("total"))
    return {
        "by_severity": by_severity,
        "confirmed_count": confirmed_count if confirmed_count is not None else len(findings),
        "risks_count": risks_count if risks_count is not None else len(risks),
        "downgraded_findings_count": sum(
            1 for risk in risks if isinstance(risk, dict) and risk.get("source") == "downgraded_finding"
        ),
        "rejected_findings_count": rejected_count if rejected_count is not None else len(chunk_rejected),
    }


def _review(final_review: dict[str, Any], *, review_mode: str | None, contract_pack: str | None) -> dict[str, Any]:
    return {
        "status": final_review.get("status"),
        "verdict": final_review.get("verdict"),
        "review_mode": review_mode or _find_key(final_review, "review_mode"),
        "contract_pack": contract_pack or _find_key(final_review, "contract_pack"),
        "limitations_count": len(_list(final_review.get("limitations"))),
    }


def _quality_gate(quality_gate: ReviewQualityGate) -> dict[str, Any]:
    return {
        "status": quality_gate.status,
        "normalized_verdict": quality_gate.normalized_verdict,
        "quality_score": quality_gate.quality_score,
        "manual_review_required": quality_gate.manual_review_required,
        "blocked_reasons": list(quality_gate.blocked_reasons),
        "blocked_reasons_count": len(quality_gate.blocked_reasons),
        "warnings_count": len(quality_gate.warnings),
        "limitations_count": len(quality_gate.limitations),
    }


def _validation_evidence(validation_evidence: dict[str, Any] | None) -> dict[str, Any]:
    if validation_evidence is None:
        return {"provided": False, "status": None}
    return {
        "provided": True,
        "status": validation_evidence.get("status"),
        "schema_id": validation_evidence.get("schema_id"),
    }


def _redaction(redaction_report: dict[str, Any] | None) -> dict[str, Any]:
    if redaction_report is None:
        return {"provided": False, "status": None}
    safe = redaction_report.get("output_safe_for_llm")
    return {
        "provided": True,
        "status": "safe" if safe is True else "unsafe" if safe is False else redaction_report.get("status"),
        "secret_like_values_found": redaction_report.get("secret_like_values_found"),
        "redacted_lines_present": redaction_report.get("redacted_lines_present"),
    }


def _model(*documents: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "provider": _first_found(documents, "provider"),
        "model": _first_found(documents, "model"),
        "preset": _first_found(documents, "preset"),
    }


def _performance(*documents: dict[str, Any] | None) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in PERFORMANCE_KEYS:
        found = _first_found(documents, key)
        values[key] = found if isinstance(found, (int, float)) else None
    return values


def _inputs(
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    optional_docs: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    inputs = {
        "final_review": _input_ref(final_review, provided=True, required=True),
        "review_quality_gate": _input_ref(quality_gate.model_dump(mode="json"), provided=True, required=True),
    }
    for name, document in optional_docs.items():
        inputs[name] = _input_ref(document, provided=document is not None, required=False)
    return inputs


def _consistency_warnings(
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    chunk_results: dict[str, Any] | None,
    chunk_plan: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    final_verdict = final_review.get("verdict")
    if isinstance(final_verdict, str) and final_verdict != quality_gate.normalized_verdict:
        warnings.append("artifact_divergence:final_review_verdict_vs_quality_gate_normalized_verdict")

    planned_chunks = {
        chunk.get("chunk_id")
        for chunk in _list(_get(chunk_plan, "chunks"))
        if isinstance(chunk, dict) and isinstance(chunk.get("chunk_id"), str)
    }
    if planned_chunks:
        parsed_chunks = {chunk for chunk in _list(_get(chunk_results, "chunks_parsed")) if isinstance(chunk, str)}
        failed_chunks = {
            failure.get("chunk_id")
            for failure in _list(_get(chunk_results, "chunks_failed"))
            if isinstance(failure, dict) and isinstance(failure.get("chunk_id"), str)
        }
        reported_chunks = parsed_chunks | failed_chunks
        if reported_chunks and reported_chunks != planned_chunks:
            warnings.append("artifact_divergence:chunk_plan_vs_chunk_results")

    counts = final_review.get("counts") if isinstance(final_review.get("counts"), dict) else {}
    confirmed_count = _int_or_none(counts.get("confirmed_findings_total"))
    if confirmed_count is not None and confirmed_count != len(_list(final_review.get("confirmed_findings"))):
        warnings.append("artifact_divergence:final_review_confirmed_findings_count")
    risks_count = _int_or_none(counts.get("risks_total"))
    if risks_count is not None and risks_count != len(_list(final_review.get("risks"))):
        warnings.append("artifact_divergence:final_review_risks_count")
    return warnings


def _input_ref(document: dict[str, Any] | None, *, provided: bool, required: bool) -> dict[str, Any]:
    if document is None:
        return {"provided": False, "required": required}
    return {
        "provided": provided,
        "required": required,
        "schema_id": document.get("schema_id"),
        "schema_version": document.get("schema_version"),
        "source": document.get("source"),
        "status": document.get("status"),
        "created_at": document.get("created_at"),
    }


def _schema_limitations(optional_docs: dict[str, dict[str, Any] | None]) -> list[str]:
    expected = {
        "chunk_results": (CHUNK_RESULTS_SCHEMA, 1),
        "chunk_plan": (SEMANTIC_CHUNK_PLAN_SCHEMA, 1),
        "intake": (INTAKE_SCHEMA, INTAKE_SCHEMA),
        "redaction_report": (REDACTION_REPORT_SCHEMA, REDACTION_REPORT_SCHEMA),
    }
    limitations: list[str] = []
    for name, (schema_id, schema_version) in expected.items():
        document = optional_docs.get(name)
        if document is None:
            continue
        document_schema_id = document.get("schema_id")
        document_schema_version = document.get("schema_version")
        if document_schema_id is not None and document_schema_id != schema_id:
            limitations.append(f"optional_artifact_schema_unexpected:{name}")
        elif document_schema_id is None and document_schema_version not in {schema_id, schema_version}:
            limitations.append(f"optional_artifact_schema_unexpected:{name}")
    return limitations


def _validate_optional_artifact(raw: dict[str, Any], *, name: str) -> dict[str, Any]:
    validators = {
        "chunk_results": (CHUNK_RESULTS_SCHEMA, 1, ChunkResults),
        "chunk_plan": (SEMANTIC_CHUNK_PLAN_SCHEMA, 1, SemanticChunkPlan),
        "intake": (INTAKE_SCHEMA, INTAKE_SCHEMA, ReviewIntake),
        "redaction_report": (REDACTION_REPORT_SCHEMA, REDACTION_REPORT_SCHEMA, RedactionReport),
    }
    validator = validators.get(name)
    if validator is None:
        return raw

    schema_id, schema_version, model = validator
    raw_schema_id = raw.get("schema_id")
    raw_schema_version = raw.get("schema_version")
    if raw_schema_id is not None and raw_schema_id != schema_id:
        raise TelemetryError(f"artifact_schema_id_mismatch:{name}", "optional artifact schema_id is incompatible")
    if raw_schema_id is None and raw_schema_version not in {schema_id, schema_version}:
        raise TelemetryError(f"artifact_schema_id_mismatch:{name}", "optional artifact schema_id is incompatible")
    if raw_schema_version != schema_version:
        raise TelemetryError(
            f"artifact_schema_version_mismatch:{name}",
            "optional artifact schema_version is incompatible",
        )
    try:
        validated = model.model_validate(raw)
    except ValidationError as exc:
        raise TelemetryError(f"artifact_structure_invalid:{name}", "optional artifact structure is invalid") from exc
    return validated.model_dump(mode="json")


def _chunks_degraded(chunk_plan: dict[str, Any] | None) -> int | None:
    chunks = _get(chunk_plan, "chunks")
    if not isinstance(chunks, list):
        return None
    return sum(1 for chunk in chunks if isinstance(chunk, dict) and chunk.get("coverage") == "degraded")


def _coverage_status(final_review: dict[str, Any], chunk_plan: dict[str, Any] | None) -> str | None:
    status = final_review.get("status")
    if status in {"partial", "degraded", "failed"}:
        return str(status)
    return str(chunk_plan.get("status")) if isinstance(chunk_plan, dict) and chunk_plan.get("status") else status


def _get(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        return document.get(key)
    return None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)]


def _authoritative_string_list(primary: Any, fallback: Any) -> list[str] | None:
    normalized = _string_list(primary)
    if normalized is not None:
        return normalized
    return _string_list(fallback)


def _count_or_none(document: dict[str, Any], section: str, key: str) -> int | None:
    value = document.get(section)
    if isinstance(value, dict):
        return _int_or_none(value.get(key))
    return None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _find_key(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        if key in document:
            return document[key]
        for value in document.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    if isinstance(document, list):
        for value in document:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _first_found(documents: tuple[dict[str, Any] | None, ...], key: str) -> Any:
    for document in documents:
        found = _find_key(document, key)
        if found is not None:
            return found
    return None


def _redact_local_paths(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_local_paths(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_local_paths(item) for item in value]
    if isinstance(value, str):
        if _is_absolute_path(value):
            return "[LOCAL_PATH_REDACTED]"
        return _redact_local_paths_in_text(value)
    return value


def _redact_local_paths_in_text(value: str) -> str:
    redacted = _WINDOWS_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", value)
    return _UNIX_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", redacted)


def _is_absolute_path(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("/") or stripped.startswith("~/") or bool(re.match(r"^[A-Za-z]:\\", stripped))


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
