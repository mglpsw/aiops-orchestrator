"""Deterministic false-positive signatures for AgentReview findings."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from app.agent_review.quality_gate import validate_final_review_document
from app.agent_review.redaction import REDACTED, RedactionState, redact_value
from app.agent_review.schemas import (
    CHUNK_RESULTS_SCHEMA,
    FALSE_POSITIVE_MARKERS_SCHEMA,
    FINAL_REVIEW_SCHEMA,
    QUALITY_GATE_SCHEMA,
    TELEMETRY_SCHEMA,
    ChunkResults,
    FalsePositiveReason,
    FalsePositiveSignatures,
    FinalReview,
    ReviewQualityGate,
    ReviewTelemetry,
)

ALLOWED_REASONS = set(get_args(FalsePositiveReason))

_UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")
_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
_SIGNATURE_RE = re.compile(r"^fp:v1:[0-9a-f]{64}$")


class FalsePositiveError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def load_json_object(path: Path | str, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FalsePositiveError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise FalsePositiveError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise FalsePositiveError(error_class, "input JSON must be an object")
    return raw


def load_final_review(path: Path | str) -> dict[str, Any]:
    raw = load_json_object(path, error_class="final_review_invalid")
    if raw.get("schema_id") != FINAL_REVIEW_SCHEMA or raw.get("schema_version") != 1:
        raise FalsePositiveError("final_review_invalid", "final review schema is invalid")
    try:
        validated = validate_final_review_document(raw)
        if validated.verdict_unknown:
            raise FalsePositiveError("final_review_invalid", "final review verdict is invalid")
        return FinalReview.model_validate(raw).model_dump(mode="json")
    except FalsePositiveError:
        raise
    except Exception as exc:
        raise FalsePositiveError("final_review_invalid", "final review structure is invalid") from exc


def load_quality_gate(path: Path | str) -> ReviewQualityGate:
    raw = load_json_object(path, error_class="quality_gate_invalid")
    if raw.get("schema_id") != QUALITY_GATE_SCHEMA or raw.get("schema_version") != 1:
        raise FalsePositiveError("quality_gate_invalid", "quality gate schema is invalid")
    try:
        return ReviewQualityGate.model_validate(raw)
    except ValidationError as exc:
        raise FalsePositiveError("quality_gate_invalid", "quality gate structure is invalid") from exc


def load_review_telemetry(path: Path | str) -> ReviewTelemetry:
    raw = load_json_object(path, error_class="review_telemetry_invalid")
    if raw.get("schema_id") != TELEMETRY_SCHEMA or raw.get("schema_version") != 1:
        raise FalsePositiveError("review_telemetry_invalid", "review telemetry schema is invalid")
    try:
        return ReviewTelemetry.model_validate(raw)
    except ValidationError as exc:
        raise FalsePositiveError("review_telemetry_invalid", "review telemetry structure is invalid") from exc


def load_optional_chunk_results(path: Path | str | None) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, ["optional_artifact_missing:chunk_results"]
    try:
        raw = load_json_object(path, error_class="chunk_results_invalid")
    except FalsePositiveError as exc:
        return None, [exc.error_class]
    if raw.get("schema_id") != CHUNK_RESULTS_SCHEMA:
        return None, ["artifact_schema_id_mismatch:chunk_results"]
    if raw.get("schema_version") != 1:
        return None, ["artifact_schema_version_mismatch:chunk_results"]
    try:
        return ChunkResults.model_validate(raw).model_dump(mode="json"), []
    except ValidationError:
        return None, ["artifact_structure_invalid:chunk_results"]


def load_optional_markers(path: Path | str | None) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, []
    try:
        raw = load_json_object(path, error_class="false_positive_markers_invalid")
    except FalsePositiveError as exc:
        return None, [exc.error_class]
    if raw.get("schema_id") != FALSE_POSITIVE_MARKERS_SCHEMA or raw.get("schema_version") != 1:
        return None, ["false_positive_markers_schema_invalid"]
    if raw.get("source") != "manual" or not isinstance(raw.get("markers"), list):
        return None, ["false_positive_markers_invalid"]
    return raw, []


def finding_signature_basis(finding: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    title = _normalize_title(finding.get("title"))
    file_path = _normalize_relative_path(finding.get("file_path"))
    contract_id = _normalize_contract_id(finding.get("contract_id"))
    if not title:
        return None, "finding_signature_title_invalid"
    if file_path is None:
        return None, "finding_signature_path_invalid"
    title = _sanitize_basis_string(title)
    contract_id = _sanitize_basis_string(contract_id) if contract_id else None
    return {"contract_id": contract_id, "file_path": file_path, "normalized_title": title}, None


def signature_for_basis(basis: dict[str, Any]) -> str:
    canonical = json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "fp:v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def build_false_positive_signatures(
    *,
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    review_telemetry: ReviewTelemetry,
    chunk_results: dict[str, Any] | None = None,
    markers_document: dict[str, Any] | None = None,
    limitations: list[str] | None = None,
) -> FalsePositiveSignatures:
    candidates, candidate_limitations = _candidates(final_review, chunk_results)
    markers, marker_warnings, marker_limitations, conflicted = _markers(markers_document)
    by_signature = {candidate["signature"]: candidate for candidate in candidates}
    warnings = [*_cross_artifact_warnings(final_review, quality_gate, review_telemetry, chunk_results), *marker_warnings]

    for marker in markers:
        signature = marker["finding_signature"]
        if signature not in by_signature:
            warnings.append(f"manual_marker_unmatched:{signature}")
            marker["matched"] = False
            continue
        marker["matched"] = True
        if signature not in conflicted:
            by_signature[signature]["matched_markers"].append(_marker_public(marker))

    for candidate in candidates:
        candidate["matched_markers"] = sorted(
            candidate["matched_markers"],
            key=lambda item: (item["finding_signature"], item["reason"], item.get("suggested_rule") or "", item.get("contract_id") or ""),
        )

    artifact = FalsePositiveSignatures(
        target={"repository": _sanitize_string(str(final_review.get("target_repo") or ""))},
        candidates=[_sanitize_value(candidate) for candidate in sorted(candidates, key=lambda item: item["signature"])],
        markers=[_sanitize_value(_marker_public(marker)) for marker in markers],
        warnings=_sorted_strings(warnings),
        limitations=_sorted_strings([*(limitations or []), *candidate_limitations, *marker_limitations]),
        inputs=_inputs(final_review, quality_gate, review_telemetry, chunk_results, markers_document),
    )
    return sanitize_false_positive_signatures(artifact)


def sanitize_false_positive_signatures(artifact: FalsePositiveSignatures) -> FalsePositiveSignatures:
    redacted = _sanitize_value(artifact.model_dump(mode="json"))
    return FalsePositiveSignatures.model_validate(redacted)


def _candidates(final_review: dict[str, Any], chunk_results: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    limitations: list[str] = []
    candidates_by_signature: dict[str, dict[str, Any]] = {}
    for index, finding in enumerate(_list(final_review.get("confirmed_findings"))):
        basis, limitation = finding_signature_basis(finding)
        if basis is None:
            limitations.append(f"{limitation}:{index}")
            continue
        signature = signature_for_basis(basis)
        candidate = {
            "signature": signature,
            "basis": basis,
            "finding": {
                "title": _clean_string(finding.get("title")),
                "file_path": basis["file_path"],
                "contract_id": _clean_optional_string(finding.get("contract_id")),
                "severity": _clean_optional_string(finding.get("severity")),
                "confidence": _clean_optional_string(finding.get("confidence")),
            },
            "provenance": _provenance(finding, chunk_results),
            "matched_markers": [],
        }
        current = candidates_by_signature.get(signature)
        if current is None or _candidate_sort_key(candidate) < _candidate_sort_key(current):
            candidates_by_signature[signature] = candidate
    return list(candidates_by_signature.values()), limitations


def _markers(markers_document: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str], list[str], set[str]]:
    if markers_document is None:
        return [], [], [], set()
    warnings: list[str] = []
    limitations: list[str] = []
    deduped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    by_signature: dict[str, set[tuple[str, str, str]]] = {}
    for index, marker in enumerate(markers_document.get("markers", [])):
        if not isinstance(marker, dict):
            limitations.append(f"manual_marker_invalid:{index}")
            continue
        signature = marker.get("finding_signature")
        reason = marker.get("reason")
        suggested_rule = _clean_optional_string(marker.get("suggested_rule"))
        contract_id = _clean_optional_string(marker.get("contract_id"))
        if not isinstance(signature, str) or not _SIGNATURE_RE.match(signature):
            limitations.append(f"manual_marker_signature_invalid:{index}")
            continue
        if reason not in ALLOWED_REASONS:
            limitations.append(f"manual_marker_reason_invalid:{signature}")
            continue
        suggested_rule = _sanitize_string(suggested_rule) if suggested_rule else None
        contract_id = _sanitize_string(contract_id) if contract_id else None
        item = {
            "finding_signature": signature,
            "reason": reason,
            "suggested_rule": suggested_rule,
            "contract_id": contract_id,
            "source": "manual",
        }
        key = (signature, reason, suggested_rule or "", contract_id or "")
        deduped[key] = item
        by_signature.setdefault(signature, set()).add((reason, suggested_rule or "", contract_id or ""))
    conflicted = {signature for signature, variants in by_signature.items() if len(variants) > 1}
    for signature in conflicted:
        warnings.append(f"manual_marker_conflict:{signature}")
    markers = sorted(deduped.values(), key=lambda item: (item["finding_signature"], item["reason"], item.get("suggested_rule") or "", item.get("contract_id") or ""))
    return markers, warnings, limitations, conflicted


def _marker_public(marker: dict[str, Any]) -> dict[str, Any]:
    return {
        key: marker[key]
        for key in ("finding_signature", "reason", "suggested_rule", "contract_id", "source", "matched")
        if key in marker and marker[key] is not None
    }


def _provenance(finding: dict[str, Any], chunk_results: dict[str, Any] | None) -> dict[str, Any]:
    source_chunks = _string_list(finding.get("source_chunks")) or _string_list(finding.get("chunk_id"))
    semantic_groups = _string_list(finding.get("semantic_groups")) or _string_list(finding.get("semantic_group"))
    parsed = set(_string_list((chunk_results or {}).get("chunks_parsed")) or [])
    return {
        "candidate_authority": "final-review.confirmed_findings",
        "chunk_results_used_for_provenance": chunk_results is not None,
        "source_chunks": sorted(source_chunks),
        "semantic_groups": sorted(semantic_groups),
        "source_chunks_in_chunk_results": sorted(chunk for chunk in source_chunks if chunk in parsed),
    }


def _cross_artifact_warnings(
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    review_telemetry: ReviewTelemetry,
    chunk_results: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    telemetry_repo = review_telemetry.target.get("repository") if isinstance(review_telemetry.target, dict) else None
    if telemetry_repo and final_review.get("target_repo") and telemetry_repo != final_review.get("target_repo"):
        warnings.append("artifact_divergence:final_review_vs_review_telemetry_target_repo")
    telemetry_gate = review_telemetry.quality_gate if isinstance(review_telemetry.quality_gate, dict) else {}
    if telemetry_gate.get("status") is not None and telemetry_gate.get("status") != quality_gate.status:
        warnings.append("artifact_divergence:quality_gate_status")
    if telemetry_gate.get("normalized_verdict") is not None and telemetry_gate.get("normalized_verdict") != quality_gate.normalized_verdict:
        warnings.append("artifact_divergence:quality_gate_normalized_verdict")
    if (
        telemetry_gate.get("manual_review_required") is not None
        and telemetry_gate.get("manual_review_required") != quality_gate.manual_review_required
    ):
        warnings.append("artifact_divergence:quality_gate_manual_review_required")
    if chunk_results is not None and chunk_results.get("target_repo") and chunk_results.get("target_repo") != final_review.get("target_repo"):
        warnings.append("artifact_divergence:final_review_vs_chunk_results_target_repo")
    return warnings


def _inputs(
    final_review: dict[str, Any],
    quality_gate: ReviewQualityGate,
    review_telemetry: ReviewTelemetry,
    chunk_results: dict[str, Any] | None,
    markers_document: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "final_review": {"provided": True, "schema_id": final_review.get("schema_id"), "schema_version": final_review.get("schema_version")},
        "review_quality_gate": _quality_gate_input(quality_gate),
        "review_telemetry": {"provided": True, "schema_id": review_telemetry.schema_id, "schema_version": review_telemetry.schema_version},
        "chunk_results": {"provided": chunk_results is not None, "schema_id": (chunk_results or {}).get("schema_id"), "schema_version": (chunk_results or {}).get("schema_version")},
        "false_positive_markers": {"provided": markers_document is not None, "schema_id": (markers_document or {}).get("schema_id"), "schema_version": (markers_document or {}).get("schema_version")},
    }


def _quality_gate_input(quality_gate: ReviewQualityGate) -> dict[str, Any]:
    return {
        "provided": True,
        "schema_id": quality_gate.schema_id,
        "schema_version": quality_gate.schema_version,
        "status": quality_gate.status,
        "normalized_verdict": quality_gate.normalized_verdict,
        "manual_review_required": quality_gate.manual_review_required,
        "quality_score": quality_gate.quality_score,
    }


def _normalize_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.casefold()
    return normalized or None


def _normalize_contract_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _normalize_relative_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or _DRIVE_PATH_RE.match(raw):
        return None
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        parts.append(part)
    normalized = "/".join(parts)
    if not normalized or normalized.startswith("/") or _UNIX_ABSOLUTE_PATH_RE.search(normalized) or _WINDOWS_ABSOLUTE_PATH_RE.search(normalized):
        return None
    return normalized


def _sanitize_value(value: Any) -> Any:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(value, state)
    return _redact_for_artifact(redacted)


def _sanitize_basis_string(value: str) -> str:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(value, state)
    return _sanitize_string(str(redacted))


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(candidate["signature"]),
        json.dumps(candidate["finding"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        json.dumps(candidate["provenance"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _redact_for_artifact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_for_artifact(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_for_artifact(item) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def _sanitize_string(value: str) -> str:
    redacted = _UNIX_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", value)
    redacted = _WINDOWS_ABSOLUTE_PATH_RE.sub("[LOCAL_PATH_REDACTED]", redacted)
    redacted = re.sub(r"(?i)authorization", REDACTED, redacted)
    redacted = re.sub(r"(?i)bearer", REDACTED, redacted)
    redacted = re.sub(r"(?i)cookie", REDACTED, redacted)
    redacted = re.sub(r"(?i)database_url", REDACTED, redacted)
    redacted = re.sub(r"(?i)api[_ -]?key", REDACTED, redacted)
    redacted = re.sub(r"(?i)ct102", REDACTED, redacted)
    return redacted


def _clean_string(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clean_optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _sorted_strings(values: list[str]) -> list[str]:
    return sorted({str(value) for value in values if isinstance(value, str) and value})
