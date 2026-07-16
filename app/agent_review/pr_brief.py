"""Deterministic PR brief builder for bounded AgentReview payload preparation."""

from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict
from typing import Any

from app.agent_review.redaction import sanitize_artifact_value
from app.agent_review.schemas import PRBrief, RedactionReport, ReviewIntake, SemanticChunkPlan, TruncationMetadata

DEFAULT_PR_BRIEF_MAX_CHARS = 16_000


class PRBriefError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def build_pr_brief(
    *,
    intake: ReviewIntake,
    chunk_plan: SemanticChunkPlan,
    redaction_report: RedactionReport,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
    max_chars: int | None = None,
    optional_limitations: list[str] | None = None,
) -> PRBrief:
    if chunk_plan.target_repo != intake.target_repo:
        warnings = [f"target_repo_mismatch:intake={intake.target_repo}:chunk_plan={chunk_plan.target_repo}"]
    else:
        warnings = []

    brief_limitations = [*intake.limitations, *chunk_plan.limitations, *(optional_limitations or [])]
    brief_limitations.extend(_artifact_state_limitations(intake))

    checks_summary = _checks_summary(checks, intake=intake)
    validation_summary = _validation_summary(validation_evidence, intake=intake)
    metadata = _review_metadata(intake=intake, checks=checks, validation_evidence=validation_evidence)
    warnings.extend(metadata["warnings"])

    payload = {
        "target": {
            "repository": intake.target_repo,
            "pr_number": metadata["pr_number"],
            "commit_sha": metadata["commit_sha"],
        },
        "review": {
            "mode": metadata["review_mode"],
            "contract_pack": metadata["contract_pack"],
        },
        "changed_files_summary": _changed_files_summary(intake, chunk_plan=chunk_plan),
        "semantic_groups": _semantic_groups(chunk_plan),
        "coverage": _coverage_summary(intake, chunk_plan=chunk_plan),
        "checks": checks_summary,
        "validation_evidence": validation_summary,
        "redaction": {
            "output_safe_for_llm": redaction_report.output_safe_for_llm,
            "secret_like_values_found": redaction_report.secret_like_values_found,
            "replacements_by_type": dict(sorted(redaction_report.replacements_by_type.items())),
            "limitations": list(redaction_report.limitations),
        },
        "artifacts": _artifact_matrix(intake),
        "warnings": _dedupe(warnings),
        "limitations": _dedupe(brief_limitations),
        "inputs": {
            "intake": _input_ref(intake.model_dump(mode="json")),
            "chunk_plan": _input_ref(chunk_plan.model_dump(mode="json")),
            "redaction_report": _input_ref(redaction_report.model_dump(mode="json")),
            "checks": _optional_input_ref(checks),
            "validation_evidence": _optional_input_ref(validation_evidence),
        },
        "created_at": intake.created_at,
    }

    budget = _resolve_brief_budget(intake) if max_chars is None else max_chars
    if budget <= 0:
        raise PRBriefError("brief_budget_invalid", "pr brief budget must be greater than zero")
    truncated_payload, truncation = _apply_budget(payload, max_chars=budget)
    sanitized = sanitize_artifact_value(truncated_payload)
    return PRBrief.model_validate({**sanitized, "truncation": truncation.model_dump(mode="json")})


def _artifact_matrix(intake: ReviewIntake) -> dict[str, Any]:
    details = []
    provided: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    for status in sorted(intake.artifact_status, key=lambda item: item.name):
        if status.status == "available":
            provided.append(status.name)
        elif status.status == "missing":
            missing.append(status.name)
        else:
            invalid.append(status.name)
        details.append(
            {
                "name": status.name,
                "status": status.status,
                "available": status.available,
                "valid": status.valid,
                "path": status.path,
                "limitations": list(status.limitations),
                "error_class": status.error_class,
            }
        )
    return {
        "provided": provided,
        "missing": missing,
        "invalid": invalid,
        "details": details,
    }


def _artifact_state_limitations(intake: ReviewIntake) -> list[str]:
    limitations: list[str] = []
    for status in intake.artifact_status:
        if status.status == "missing":
            limitations.append(f"artifact_missing:{status.name}")
        elif status.status in {"invalid", "degraded"}:
            limitations.append(f"artifact_invalid:{status.name}")
    return limitations


def _changed_files_summary(intake: ReviewIntake, *, chunk_plan: SemanticChunkPlan) -> dict[str, Any]:
    file_context = _artifact_content(intake, "file-diff-context")
    file_items = _file_items(file_context)
    status_counts: Counter[str] = Counter()
    files: list[dict[str, Any]] = []
    for item in file_items:
        path = _sanitize_relative_path(str(item.get("path") or ""))
        if not path:
            continue
        status = str(item.get("status") or "unknown")
        status_counts[status] += 1
        files.append(
            {
                "path": path,
                "status": status,
                "summary": _clean_text(item.get("summary")),
            }
        )
    if not files:
        for path in _ordered_unique(chunk_plan.files_covered):
            files.append({"path": _sanitize_relative_path(path), "status": "unknown", "summary": None})
            status_counts["unknown"] += 1
    return {
        "total_files": len(files),
        "status_counts": dict(sorted(status_counts.items())),
        "files": sorted(files, key=lambda item: item["path"]),
    }


def _semantic_groups(chunk_plan: SemanticChunkPlan) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"chunk_ids": [], "files": []})
    for chunk in sorted(chunk_plan.chunks, key=lambda item: (item.order_index, item.chunk_id)):
        bucket = grouped[chunk.semantic_group]
        bucket["chunk_ids"].append(chunk.chunk_id)
        bucket["files"].extend(chunk.files)
    groups: list[dict[str, Any]] = []
    for semantic_group, payload in sorted(grouped.items(), key=lambda item: item[0]):
        files = _ordered_unique(payload["files"])
        groups.append(
            {
                "semantic_group": semantic_group,
                "chunk_count": len(payload["chunk_ids"]),
                "chunk_ids": payload["chunk_ids"],
                "file_count": len(files),
                "files": files,
            }
        )
    return groups


def _coverage_summary(intake: ReviewIntake, *, chunk_plan: SemanticChunkPlan) -> dict[str, Any]:
    requirements = _coverage_requirements(intake)
    return {
        "required_files": requirements.get("must_review_files", []),
        "recommended_files": requirements.get("should_review_files", []),
        "optional_files": requirements.get("may_summarize_files", []),
        "files_covered": list(chunk_plan.files_covered),
        "files_partially_covered": list(chunk_plan.files_partially_covered),
        "files_not_covered": list(chunk_plan.files_not_covered),
    }


def _checks_summary(checks: dict[str, Any] | None, *, intake: ReviewIntake) -> dict[str, Any]:
    checks_document = checks if isinstance(checks, dict) else _artifact_content(intake, "checks")
    if not isinstance(checks_document, dict):
        return {"provided": False, "status": None, "checks_total": 0, "failed_checks": 0, "checks": []}
    checks_list = checks_document.get("checks")
    rows: list[dict[str, Any]] = []
    if isinstance(checks_list, list):
        for item in checks_list:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "name": _clean_text(item.get("name")),
                    "status": _clean_text(item.get("status")) or "unknown",
                    "command": _clean_text(item.get("command")),
                }
            )
    rows = sorted(rows, key=lambda item: ((item.get("name") or ""), item.get("status") or ""))
    failed_checks = sum(1 for item in rows if (item.get("status") or "").lower() not in {"passed", "ok", "success"})
    return {
        "provided": True,
        "status": _clean_text(checks_document.get("status")) or _clean_text(checks_document.get("validation_level")),
        "checks_total": len(rows),
        "failed_checks": failed_checks,
        "checks": rows,
    }


def _validation_summary(validation_evidence: dict[str, Any] | None, *, intake: ReviewIntake) -> dict[str, Any]:
    document = (
        validation_evidence
        if isinstance(validation_evidence, dict)
        else _artifact_content(intake, "validation-evidence-result")
    )
    if not isinstance(document, dict):
        return {"provided": False, "status": None, "verdict": None, "blocking_findings": [], "limitations": []}
    findings = document.get("blocking_findings")
    normalized_findings: list[dict[str, Any]] = []
    if isinstance(findings, list):
        for item in findings:
            if not isinstance(item, dict):
                continue
            normalized_findings.append(
                {
                    "title": _clean_text(item.get("title")),
                    "severity": _clean_text(item.get("severity")),
                    "file_path": _sanitize_relative_path(_clean_text(item.get("file_path")) or ""),
                }
            )
    return {
        "provided": True,
        "status": _clean_text(document.get("status")),
        "verdict": _clean_text(document.get("validation_verdict")),
        "blocking_findings": sorted(
            normalized_findings,
            key=lambda item: ((item.get("severity") or ""), (item.get("file_path") or ""), (item.get("title") or "")),
        ),
        "limitations": _string_list(document.get("limitations")),
    }


def _review_metadata(
    *,
    intake: ReviewIntake,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    sources = [checks, validation_evidence, intake.model_dump(mode="json")]
    pr_candidates = [
        _coerce_int(_find_key(source, "pr_number")) for source in sources if _coerce_int(_find_key(source, "pr_number")) is not None
    ]
    commit_candidates = [
        _clean_text(_find_key(source, "commit_sha"))
        for source in sources
        if isinstance(_clean_text(_find_key(source, "commit_sha")), str)
    ]
    mode = _first_non_empty(
        _clean_text(_find_key(intake.artifacts, "review_mode")),
        _clean_text(_find_key(intake.artifacts, "mode")),
    )
    contract_pack = _first_non_empty(
        _clean_text(_find_key(intake.artifacts, "contract_pack")),
        _clean_text(_find_key(intake.artifacts, "pack")),
    )

    warnings: list[str] = []
    if len(set(pr_candidates)) > 1:
        warnings.append("pr_number_conflict_across_artifacts")
    if len({value for value in commit_candidates if value}) > 1:
        warnings.append("commit_sha_conflict_across_artifacts")
    return {
        "pr_number": pr_candidates[0] if pr_candidates else None,
        "commit_sha": commit_candidates[0] if commit_candidates else None,
        "review_mode": mode,
        "contract_pack": contract_pack,
        "warnings": warnings,
    }


def _apply_budget(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], TruncationMetadata]:
    working = copy.deepcopy(payload)
    original_chars = _canonical_len(working)
    omitted_sections: list[str] = []
    coverage_impact: list[str] = []

    if original_chars <= max_chars:
        return (
            working,
            TruncationMetadata(applied=False, original_chars=original_chars, emitted_chars=original_chars),
        )

    shrinkers = [
        ("validation_evidence.blocking_findings", "coverage_validation_evidence_reduced", _shrink_validation_findings),
        ("checks.checks", "coverage_checks_reduced", _shrink_checks_rows),
        ("artifacts.details", "coverage_artifact_details_reduced", _shrink_artifact_details),
        ("semantic_groups.files", "coverage_semantic_context_reduced", _shrink_semantic_group_files),
        ("changed_files_summary.files", "coverage_changed_files_reduced", _shrink_changed_files),
    ]

    while _canonical_len(working) > max_chars:
        changed = False
        for section, impact, shrink in shrinkers:
            if shrink(working):
                changed = True
                if section not in omitted_sections:
                    omitted_sections.append(section)
                    coverage_impact.append(impact)
                break
        if not changed:
            break

    emitted_chars = _canonical_len(working)
    return (
        working,
        TruncationMetadata(
            applied=True,
            original_chars=original_chars,
            emitted_chars=emitted_chars,
            omitted_sections=omitted_sections,
            truncation_reason=(
                "max_chars_exceeded"
                if emitted_chars <= max_chars
                else "max_chars_exceeded_minimum_required_sections"
            ),
            coverage_impact=coverage_impact,
        ),
    )


def _shrink_validation_findings(payload: dict[str, Any]) -> bool:
    findings = payload.get("validation_evidence", {}).get("blocking_findings")
    if isinstance(findings, list) and findings:
        findings.pop()
        return True
    return False


def _shrink_checks_rows(payload: dict[str, Any]) -> bool:
    rows = payload.get("checks", {}).get("checks")
    if isinstance(rows, list) and rows:
        rows.pop()
        return True
    return False


def _shrink_artifact_details(payload: dict[str, Any]) -> bool:
    rows = payload.get("artifacts", {}).get("details")
    if isinstance(rows, list) and rows:
        rows.pop()
        return True
    return False


def _shrink_semantic_group_files(payload: dict[str, Any]) -> bool:
    groups = payload.get("semantic_groups")
    if not isinstance(groups, list):
        return False
    for group in reversed(groups):
        if not isinstance(group, dict):
            continue
        files = group.get("files")
        if isinstance(files, list) and files:
            files.pop()
            group["file_count"] = len(files)
            return True
    return False


def _shrink_changed_files(payload: dict[str, Any]) -> bool:
    rows = payload.get("changed_files_summary", {}).get("files")
    if isinstance(rows, list) and rows:
        rows.pop()
        payload["changed_files_summary"]["total_files"] = len(rows)
        return True
    return False


def _resolve_brief_budget(intake: ReviewIntake) -> int:
    profile = intake.target_profile if isinstance(intake.target_profile, dict) else {}
    candidates = [
        _coerce_int(_find_key(profile, "pr_brief_max_chars")),
        _coerce_int(_find_key(profile, "brief_max_chars")),
        _coerce_int(_find_key(profile, "max_brief_chars")),
    ]
    for candidate in candidates:
        if candidate and candidate > 0:
            return candidate
    return DEFAULT_PR_BRIEF_MAX_CHARS


def _artifact_content(intake: ReviewIntake, name: str) -> dict[str, Any] | None:
    candidates = {name, f"{name}.json"}
    for artifact_name, artifact in intake.artifacts.items():
        normalized = str(artifact_name).replace("\\", "/").rsplit("/", 1)[-1]
        if normalized not in candidates:
            continue
        if isinstance(artifact, dict):
            content = artifact.get("content")
            if isinstance(content, dict):
                return content
    return None


def _file_items(file_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(file_context, dict):
        return []
    files = file_context.get("files")
    if not isinstance(files, list):
        return []
    return [item for item in files if isinstance(item, dict)]


def _coverage_requirements(intake: ReviewIntake) -> dict[str, list[str]]:
    file_context = _artifact_content(intake, "file-diff-context")
    requirements = file_context.get("coverage_requirements") if isinstance(file_context, dict) else None
    if not isinstance(requirements, dict):
        return {
            "must_review_files": [],
            "should_review_files": [],
            "may_summarize_files": [],
        }
    return {
        "must_review_files": _ordered_unique(_string_list(requirements.get("must_review_files"))),
        "should_review_files": _ordered_unique(_string_list(requirements.get("should_review_files"))),
        "may_summarize_files": _ordered_unique(_string_list(requirements.get("may_summarize_files"))),
    }


def _sanitize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.startswith("/") or normalized.startswith("~/"):
        return "[LOCAL_PATH_REDACTED]"
    if len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/":
        return "[LOCAL_PATH_REDACTED]"
    return normalized


def _optional_input_ref(document: dict[str, Any] | None) -> dict[str, Any]:
    if document is None:
        return {"provided": False}
    return _input_ref(document) | {"provided": True}


def _input_ref(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_id": document.get("schema_id"),
        "schema_version": document.get("schema_version"),
        "source": document.get("source"),
        "status": document.get("status"),
        "created_at": document.get("created_at"),
    }


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
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


def _canonical_len(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _ordered_unique(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
