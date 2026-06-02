"""Deterministic quality gate for synthesized AgentReview artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import (
    CHUNK_RESULTS_SCHEMA,
    FINAL_REVIEW_SCHEMA,
    INTAKE_SCHEMA,
    REDACTION_REPORT_SCHEMA,
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    ChunkResults,
    FinalReview,
    FinalReviewVerdict,
    RedactionReport,
    ReviewIntake,
    ReviewQualityGate,
    SemanticChunkPlan,
)


ALLOWED_FINAL_VERDICTS = set(get_args(FinalReviewVerdict))
ALLOWED_FINAL_STATUSES = {"complete", "partial", "degraded", "failed"}
BLOCKER_SEVERITIES = {"P0", "P1"}
FOLLOWUP_SEVERITIES = {"P2"}
MINOR_SEVERITIES = {"P3"}

PLACEHOLDER_EVIDENCE = {
    "[redacted]",
    "***masked***",
    "redacted",
    "masked",
    "placeholder",
    "dummy",
    "example",
    "fake-token",
    "faketoken",
    "test-token",
    "testtoken",
}
TRUNCATION_ONLY_EVIDENCE = {
    "...",
    "…",
    "truncated",
    "[truncated]",
    "(truncated)",
    "output truncated",
    "content truncated",
    "texto truncado",
}
TEST_FAILURE_TERMS = (
    "test failure",
    "failed test",
    "failing test",
    "test failed",
    "pytest",
    "assertionerror",
    "falha de teste",
    "teste falhou",
)
TEST_FAILURE_SOURCES = {"checks", "test-intelligence", "local-code-intelligence"}
OPERATIONAL_TERM_PATTERNS = (
    re.compile(r"\bct102\b"),
    re.compile(r"\bprod\b"),
    re.compile(r"\bproduction\b"),
    re.compile(r"\bdeploy\b"),
    re.compile(r"\bdeployment\b"),
    re.compile(r"\brestart\b"),
    re.compile(r"\bruntime\b"),
)
OPERATIONAL_TRUSTED_SOURCES = {
    "checks",
    "test-intelligence",
    "local-code-intelligence",
    "file-diff-context",
    "runtime-inventory",
    "deployment-log",
}
OPERATIONAL_EVIDENCE_TERMS = (
    "failed",
    "failure",
    "error",
    "changed",
    "executed",
    "triggered",
    "started",
    "stopped",
    "restart",
    "deploy",
    "deployment",
    "ct102",
    "production",
    "runtime",
)

_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")


class QualityGateError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


@dataclass(frozen=True)
class FinalReviewDocument:
    raw: dict[str, Any]
    verdict_unknown: bool = False


def load_json_object(path: Path | str, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QualityGateError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise QualityGateError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise QualityGateError(error_class, "input JSON must be an object")
    return raw


def load_final_review(path: Path | str) -> FinalReviewDocument:
    return validate_final_review_document(load_json_object(path, error_class="final_review_invalid"))


def validate_final_review_document(raw: dict[str, Any]) -> FinalReviewDocument:
    _validate_final_review_shape(raw)
    verdict = _clean(raw.get("verdict"))
    if verdict not in ALLOWED_FINAL_VERDICTS:
        return FinalReviewDocument(raw=raw, verdict_unknown=True)

    try:
        FinalReview.model_validate(raw)
    except ValidationError as exc:
        raise QualityGateError("final_review_invalid", "final review structure is invalid") from exc
    return FinalReviewDocument(raw=raw, verdict_unknown=False)


def load_chunk_results(path: Path | str) -> ChunkResults:
    raw = load_json_object(path, error_class="chunk_results_invalid")
    if raw.get("schema_id") != CHUNK_RESULTS_SCHEMA or raw.get("schema_version") != 1:
        raise QualityGateError("chunk_results_invalid", "chunk results schema is invalid")
    try:
        return ChunkResults.model_validate(raw)
    except ValidationError as exc:
        raise QualityGateError("chunk_results_invalid", "chunk results structure is invalid") from exc


def load_intake(path: Path | str) -> ReviewIntake:
    raw = load_json_object(path, error_class="intake_invalid")
    if raw.get("schema_version") != INTAKE_SCHEMA and raw.get("schema_id") != INTAKE_SCHEMA:
        raise QualityGateError("intake_invalid", "intake schema is invalid")
    try:
        return ReviewIntake.model_validate(raw)
    except ValidationError as exc:
        raise QualityGateError("intake_invalid", "intake structure is invalid") from exc


def load_semantic_chunk_plan(path: Path | str) -> SemanticChunkPlan:
    raw = load_json_object(path, error_class="chunk_plan_invalid")
    if raw.get("schema_id") != SEMANTIC_CHUNK_PLAN_SCHEMA or raw.get("schema_version") != 1:
        raise QualityGateError("chunk_plan_invalid", "semantic chunk plan schema is invalid")
    try:
        return SemanticChunkPlan.model_validate(raw)
    except ValidationError as exc:
        raise QualityGateError("chunk_plan_invalid", "semantic chunk plan structure is invalid") from exc


def load_redaction_report(path: Path | str) -> RedactionReport:
    raw = load_json_object(path, error_class="redaction_report_invalid")
    if raw.get("schema_version") != REDACTION_REPORT_SCHEMA and raw.get("schema_id") != REDACTION_REPORT_SCHEMA:
        raise QualityGateError("redaction_report_invalid", "redaction report schema is invalid")
    try:
        return RedactionReport.model_validate(raw)
    except ValidationError as exc:
        raise QualityGateError("redaction_report_invalid", "redaction report structure is invalid") from exc


def load_checks(path: Path | str) -> dict[str, Any]:
    return load_json_object(path, error_class="checks_invalid")


def evaluate_review_quality_gate(
    final_review: FinalReviewDocument,
    chunk_results: ChunkResults,
    *,
    intake: ReviewIntake | None = None,
    chunk_plan: SemanticChunkPlan | None = None,
    redaction_report: RedactionReport | None = None,
    checks: dict[str, Any] | None = None,
    critical_pr: bool = False,
) -> ReviewQualityGate:
    raw = final_review.raw
    final_status = _clean(raw.get("status"))
    final_verdict = _clean(raw.get("verdict"))
    limitations = _initial_limitations(raw, chunk_results)
    warnings = _initial_warnings(chunk_results)
    blocked_reasons: list[str] = []

    if final_review.verdict_unknown:
        limitations.append("final_review_verdict_unknown")

    reliable_blockers, unreliable_warnings = _confirmed_blockers(raw, chunk_results)
    warnings.extend(unreliable_warnings)
    for blocker in reliable_blockers:
        blocked_reasons.append(
            "confirmed_blocker:"
            f"{_clean(blocker.get('severity'))}:"
            f"{_clean(blocker.get('file_path'))}"
        )

    coverage_gaps = _critical_coverage_gaps(
        raw,
        chunk_results,
        intake=intake,
        chunk_plan=chunk_plan,
        critical_pr=critical_pr,
    )
    limitations.extend(coverage_gaps)

    input_degraded = (
        final_status in {"partial", "degraded", "failed"}
        or chunk_results.status in {"partial", "degraded", "failed"}
    )
    has_critical_gap = bool(coverage_gaps)
    has_untrusted_blocker_candidate = any(warning.startswith("untrusted_blocker:") for warning in warnings)
    has_minimum_material = _has_minimum_material(raw, chunk_results)

    if final_review.verdict_unknown:
        status = "failed"
        normalized_verdict = "review_unavailable"
        manual_review_required = True
    elif not has_minimum_material:
        limitations.append("review_material_missing")
        status = "failed"
        normalized_verdict = "review_unavailable"
        manual_review_required = True
    elif reliable_blockers:
        if final_verdict == "approved":
            blocked_reasons.insert(0, "approved_with_confirmed_blocker")
        status = "degraded" if input_degraded else "passed"
        normalized_verdict = "changes_requested"
        manual_review_required = False
    elif final_verdict == "changes_requested":
        blocked_reasons.append("changes_requested_without_confirmed_blocker")
        status = "manual_review_required"
        normalized_verdict = "manual_review_required"
        manual_review_required = True
    elif input_degraded or has_critical_gap:
        status = "manual_review_required"
        normalized_verdict = "manual_review_required"
        manual_review_required = True
    elif has_untrusted_blocker_candidate:
        status = "manual_review_required"
        normalized_verdict = "manual_review_required"
        manual_review_required = True
    elif final_verdict == "review_unavailable":
        status = "failed"
        normalized_verdict = "review_unavailable"
        manual_review_required = True
    elif final_verdict == "manual_review_required":
        status = "manual_review_required"
        normalized_verdict = "manual_review_required"
        manual_review_required = True
    else:
        status = "passed"
        normalized_verdict = _non_blocking_verdict(raw, chunk_results)
        manual_review_required = False

    gate = ReviewQualityGate(
        status=status,  # type: ignore[arg-type]
        normalized_verdict=normalized_verdict,  # type: ignore[arg-type]
        quality_score=_quality_score(
            status=status,
            final_status=final_status,
            chunk_status=chunk_results.status,
            warnings=warnings,
            limitations=limitations,
        ),
        manual_review_required=manual_review_required,
        second_opinion_requested=False,
        second_opinion_status="not_required",
        blocked_reasons=_dedupe(blocked_reasons),
        warnings=_dedupe(warnings),
        limitations=_dedupe(limitations),
        inputs=_inputs(
            raw,
            chunk_results,
            intake=intake,
            chunk_plan=chunk_plan,
            redaction_report=redaction_report,
            checks=checks,
            critical_pr=critical_pr,
        ),
        created_at=_created_at(raw, chunk_results),
    )
    return sanitize_quality_gate(gate)


def sanitize_quality_gate(gate: ReviewQualityGate) -> ReviewQualityGate:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(gate.model_dump(mode="json"), state)
    redacted = _redact_local_paths(redacted)
    return ReviewQualityGate.model_validate(redacted)


def _validate_final_review_shape(raw: dict[str, Any]) -> None:
    if raw.get("schema_id") != FINAL_REVIEW_SCHEMA or raw.get("schema_version") != 1:
        raise QualityGateError("final_review_invalid", "final review schema is invalid")
    required_strings = ("target_repo", "status", "verdict", "summary")
    for field in required_strings:
        if not isinstance(raw.get(field), str):
            raise QualityGateError("final_review_invalid", f"final review field {field} is invalid")
    if raw["status"] not in ALLOWED_FINAL_STATUSES:
        raise QualityGateError("final_review_invalid", "final review status is invalid")
    for field in ("confirmed_findings", "risks", "limitations"):
        if not isinstance(raw.get(field), list):
            raise QualityGateError("final_review_invalid", f"final review field {field} is invalid")
    for field in ("confirmed_findings", "risks"):
        if any(not isinstance(item, dict) for item in raw[field]):
            raise QualityGateError("final_review_invalid", f"final review field {field} is invalid")
    for field in ("rejected_summary", "coverage", "counts", "inputs"):
        if field in raw and not isinstance(raw.get(field), dict):
            raise QualityGateError("final_review_invalid", f"final review field {field} is invalid")


def _initial_limitations(raw: dict[str, Any], chunk_results: ChunkResults) -> list[str]:
    limitations = [str(value) for value in raw.get("limitations", []) if isinstance(value, str)]
    limitations.extend(chunk_results.limitations)
    final_status = _clean(raw.get("status"))
    if final_status in {"partial", "degraded", "failed"}:
        limitations.append(f"final_review_status_{final_status}")
    if chunk_results.status in {"partial", "degraded", "failed"}:
        limitations.append(f"chunk_results_status_{chunk_results.status}")
    if chunk_results.chunks_failed:
        limitations.append("chunks_failed_present")
    return limitations


def _initial_warnings(chunk_results: ChunkResults) -> list[str]:
    warnings: list[str] = []
    for failure in chunk_results.chunks_failed:
        warnings.append(f"chunk_failed:{failure.chunk_id}:{failure.error_class}")
    return warnings


def _confirmed_blockers(
    raw: dict[str, Any],
    chunk_results: ChunkResults,
) -> tuple[list[dict[str, Any]], list[str]]:
    parsed_chunks = set(chunk_results.chunks_parsed)
    reliable: list[dict[str, Any]] = []
    warnings: list[str] = []

    for finding in _findings(raw):
        severity = _clean(finding.get("severity"))
        if severity not in BLOCKER_SEVERITIES:
            continue
        reasons = _blocker_unreliable_reasons(finding, parsed_chunks)
        if reasons:
            for reason in reasons:
                warnings.append(_finding_warning(finding, reason))
            continue
        reliable.append(finding)

    return reliable, warnings


def _blocker_unreliable_reasons(finding: dict[str, Any], parsed_chunks: set[str]) -> list[str]:
    reasons: list[str] = []
    file_path = _clean(finding.get("file_path"))
    impact = _clean(finding.get("impact"))
    evidence = _clean(finding.get("evidence"))
    source_artifact = _clean(finding.get("source_artifact"))
    line_or_hunk = _clean(finding.get("line_or_hunk"))

    if not file_path:
        reasons.append("missing_file_path")
    if not impact:
        reasons.append("missing_impact")
    if not evidence:
        reasons.append("missing_evidence")
    elif _is_placeholder_or_truncation_only(evidence):
        reasons.append("redacted_or_placeholder_only_evidence")
    if not source_artifact and not line_or_hunk:
        reasons.append("missing_source_artifact_or_line_or_hunk")
    if not _has_parsed_source_chunk(finding, parsed_chunks):
        reasons.append("source_chunk_not_parsed")
    if _is_test_failure_claim(finding) and not _source_matches(source_artifact, TEST_FAILURE_SOURCES):
        reasons.append("unsupported_test_failure_source")
    if _is_operational_claim(finding) and not _has_reliable_operational_evidence(finding):
        reasons.append("operational_claim_requires_explicit_evidence")
    return reasons


def _has_parsed_source_chunk(finding: dict[str, Any], parsed_chunks: set[str]) -> bool:
    source_chunks = finding.get("source_chunks")
    if isinstance(source_chunks, list) and source_chunks:
        return all(isinstance(chunk_id, str) and chunk_id in parsed_chunks for chunk_id in source_chunks)
    chunk_id = _clean(finding.get("chunk_id"))
    return bool(chunk_id and chunk_id in parsed_chunks)


def _is_test_failure_claim(finding: dict[str, Any]) -> bool:
    text = _finding_text(finding)
    return any(term in text for term in TEST_FAILURE_TERMS)


def _is_operational_claim(finding: dict[str, Any]) -> bool:
    return _contains_operational_term(_finding_text(finding))


def _has_reliable_operational_evidence(finding: dict[str, Any]) -> bool:
    source_artifact = _clean(finding.get("source_artifact"))
    evidence = _clean(finding.get("evidence")).lower()
    return (
        _source_matches(source_artifact, OPERATIONAL_TRUSTED_SOURCES)
        and _contains_operational_term(evidence)
        and any(term in evidence for term in OPERATIONAL_EVIDENCE_TERMS)
        and "docs-only" not in evidence
        and "guardrail" not in evidence
        and "prohibition" not in evidence
        and "proibição" not in evidence
    )


def _contains_operational_term(text: str) -> bool:
    return any(pattern.search(text) for pattern in OPERATIONAL_TERM_PATTERNS)


def _critical_coverage_gaps(
    raw: dict[str, Any],
    chunk_results: ChunkResults,
    *,
    intake: ReviewIntake | None,
    chunk_plan: SemanticChunkPlan | None,
    critical_pr: bool,
) -> list[str]:
    if not critical_pr:
        return []

    gaps: list[str] = []
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    reviewed = _string_set(coverage.get("files_reviewed"))
    partial = _string_set(coverage.get("files_partial"))
    not_reviewed = _string_set(coverage.get("files_not_reviewed"))
    if not reviewed and not partial and not not_reviewed:
        gaps.append("critical_coverage_missing")
    if _string_set(coverage.get("missing_expected_files")):
        gaps.append("critical_expected_files_missing")
    if chunk_plan is not None and chunk_plan.files_not_covered:
        gaps.append("critical_chunk_plan_files_not_covered")

    covered = reviewed | partial
    must_review = _must_review_files(intake)
    missing_must_review = sorted(file_path for file_path in must_review if file_path not in covered)
    if missing_must_review:
        gaps.append("critical_must_review_files_not_covered")

    if chunk_results.coverage.files_not_reviewed and not reviewed and not partial:
        gaps.append("critical_all_chunk_coverage_not_reviewed")
    return gaps


def _must_review_files(intake: ReviewIntake | None) -> set[str]:
    if intake is None:
        return set()
    payload = intake.model_dump(mode="json")
    discovered: set[str] = set()
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return discovered
    for artifact in artifacts.values():
        if not isinstance(artifact, dict):
            continue
        content = artifact.get("content")
        if isinstance(content, dict):
            coverage_requirements = content.get("coverage_requirements")
            if isinstance(coverage_requirements, dict):
                discovered.update(_string_set(coverage_requirements.get("must_review_files")))
    return discovered


def _has_minimum_material(raw: dict[str, Any], chunk_results: ChunkResults) -> bool:
    coverage = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    rejected_summary = raw.get("rejected_summary") if isinstance(raw.get("rejected_summary"), dict) else {}
    return any(
        [
            chunk_results.chunks_parsed,
            chunk_results.chunks_failed,
            chunk_results.limitations,
            chunk_results.rejected_findings,
            _findings(raw),
            _risks(raw),
            raw.get("limitations"),
            _string_set(coverage.get("files_reviewed")),
            _string_set(coverage.get("files_partial")),
            _string_set(coverage.get("files_not_reviewed")),
            rejected_summary.get("total"),
        ]
    )


def _non_blocking_verdict(raw: dict[str, Any], chunk_results: ChunkResults) -> str:
    final_verdict = _clean(raw.get("verdict"))
    severities = {_clean(finding.get("severity")) for finding in _findings(raw)}
    if severities & FOLLOWUP_SEVERITIES:
        return "approve_with_required_followup"
    if _risks(raw):
        return "approve_with_required_followup"
    if final_verdict == "approve_with_required_followup":
        return "approve_with_required_followup"
    if severities & MINOR_SEVERITIES:
        return "approve_with_minor_notes"
    if raw.get("limitations") or chunk_results.limitations:
        return "approve_with_minor_notes"
    rejected = raw.get("rejected_summary")
    if isinstance(rejected, dict) and rejected.get("total"):
        return "approve_with_minor_notes"
    if final_verdict == "approve_with_minor_notes":
        return "approve_with_minor_notes"
    return "approved"


def _quality_score(
    *,
    status: str,
    final_status: str,
    chunk_status: str,
    warnings: list[str],
    limitations: list[str],
) -> float:
    score = 1.0
    if status == "failed":
        score -= 0.55
    elif status == "manual_review_required":
        score -= 0.35
    elif status == "degraded":
        score -= 0.2
    if final_status in {"partial", "degraded", "failed"}:
        score -= 0.1
    if chunk_status in {"partial", "degraded", "failed"}:
        score -= 0.1
    score -= min(0.2, len(warnings) * 0.02)
    score -= min(0.2, len(limitations) * 0.02)
    return round(max(0.0, min(1.0, score)), 4)


def _inputs(
    raw: dict[str, Any],
    chunk_results: ChunkResults,
    *,
    intake: ReviewIntake | None,
    chunk_plan: SemanticChunkPlan | None,
    redaction_report: RedactionReport | None,
    checks: dict[str, Any] | None,
    critical_pr: bool,
) -> dict[str, Any]:
    return {
        "final_review": {
            "provided": True,
            "schema_id": raw.get("schema_id"),
            "schema_version": raw.get("schema_version"),
            "source": raw.get("source"),
            "status": raw.get("status"),
            "verdict": raw.get("verdict"),
            "created_at": raw.get("created_at"),
        },
        "chunk_results": {
            "provided": True,
            "schema_id": chunk_results.schema_id,
            "schema_version": chunk_results.schema_version,
            "source": chunk_results.source,
            "status": chunk_results.status,
            "created_at": chunk_results.created_at,
        },
        "intake": _optional_model_ref(intake),
        "chunk_plan": _optional_model_ref(chunk_plan),
        "redaction_report": _optional_model_ref(redaction_report),
        "checks": _optional_raw_ref(checks),
        "critical_pr": critical_pr,
    }


def _optional_model_ref(document: Any | None) -> dict[str, Any]:
    if document is None:
        return {"provided": False}
    payload = document.model_dump(mode="json")
    return {
        "provided": True,
        "schema_id": payload.get("schema_id"),
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "status": payload.get("status"),
        "created_at": payload.get("created_at"),
    }


def _optional_raw_ref(document: dict[str, Any] | None) -> dict[str, Any]:
    if document is None:
        return {"provided": False}
    return {
        "provided": True,
        "schema_id": document.get("schema_id"),
        "schema_version": document.get("schema_version"),
        "source": document.get("source"),
        "status": document.get("status"),
        "created_at": document.get("created_at"),
    }


def _created_at(raw: dict[str, Any], chunk_results: ChunkResults) -> str:
    for value in (raw.get("created_at"), chunk_results.created_at):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "1970-01-01T00:00:00Z"


def _findings(raw: dict[str, Any]) -> list[dict[str, Any]]:
    findings = raw.get("confirmed_findings")
    if not isinstance(findings, list):
        return []
    return [finding for finding in findings if isinstance(finding, dict)]


def _risks(raw: dict[str, Any]) -> list[dict[str, Any]]:
    risks = raw.get("risks")
    if not isinstance(risks, list):
        return []
    return [risk for risk in risks if isinstance(risk, dict)]


def _finding_warning(finding: dict[str, Any], reason: str) -> str:
    severity = _clean(finding.get("severity")) or "unknown"
    title = _clean(finding.get("title")) or "untitled"
    return f"untrusted_blocker:{reason}:{severity}:{title}"


def _finding_text(finding: dict[str, Any]) -> str:
    return " ".join(
        _clean(finding.get(field)).lower()
        for field in ("title", "evidence", "impact")
        if _clean(finding.get(field))
    )


def _source_matches(source_artifact: str, allowed: set[str]) -> bool:
    normalized = source_artifact.lower().replace("artifact:", "")
    return any(source == normalized or source in normalized for source in allowed)


def _is_placeholder_or_truncation_only(evidence: str) -> bool:
    normalized = evidence.strip().lower().strip("\"'")
    compact = re.sub(r"[\s_]+", "-", normalized)
    if normalized in PLACEHOLDER_EVIDENCE or compact in PLACEHOLDER_EVIDENCE:
        return True
    if "[redacted]" in normalized or "***masked***" in normalized:
        return True
    if normalized in TRUNCATION_ONLY_EVIDENCE:
        return True
    if "truncated" in normalized and len(normalized.split()) <= 5:
        return True
    return False


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item.strip()}


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


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
