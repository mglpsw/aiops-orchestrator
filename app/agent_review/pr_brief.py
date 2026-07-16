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
    warnings: list[str] = []

    brief_limitations = [*intake.limitations, *chunk_plan.limitations, *(optional_limitations or [])]
    brief_limitations.extend(_artifact_state_limitations(intake))

    checks_summary = _checks_summary(checks, intake=intake)
    validation_summary = _validation_summary(validation_evidence, intake=intake)
    metadata = _review_metadata(
        intake=intake,
        chunk_plan=chunk_plan,
        checks=checks,
        validation_evidence=validation_evidence,
    )
    warnings.extend(metadata["warnings"])

    payload = {
        "target": {
            "repository": metadata["target_repo"],
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
    sanitized = sanitize_artifact_value(payload)
    truncated_payload, truncation = _apply_budget(sanitized, max_chars=budget)
    model, _ = _materialize_brief(truncated_payload, truncation=truncation)
    return model


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
    chunk_plan: SemanticChunkPlan,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    target_repo = _resolve_identity_value(
        "target_repo",
        [
            ("intake.target_repo", intake.target_repo),
            ("chunk_plan.target_repo", chunk_plan.target_repo),
            ("intake.target_profile.target_repo", _find_key(intake.target_profile, "target_repo")),
            ("checks.target_repo", _find_key(checks, "target_repo")),
            ("validation_evidence.target_repo", _find_key(validation_evidence, "target_repo")),
            *_artifact_identity_candidates(intake.artifacts, "target_repo"),
        ],
        coerce=_clean_text,
    )
    if target_repo is None:
        raise PRBriefError("review_identity_conflict", "missing required review identity field: target_repo")

    pr_number = _resolve_identity_value(
        "pr_number",
        [
            ("checks.pr_number", _find_key(checks, "pr_number")),
            ("validation_evidence.pr_number", _find_key(validation_evidence, "pr_number")),
            *_artifact_identity_candidates(intake.artifacts, "pr_number"),
        ],
        coerce=_coerce_int,
    )
    commit_sha = _resolve_identity_value(
        "commit_sha",
        [
            ("checks.commit_sha", _find_key(checks, "commit_sha")),
            ("validation_evidence.commit_sha", _find_key(validation_evidence, "commit_sha")),
            *_artifact_identity_candidates(intake.artifacts, "commit_sha"),
        ],
        coerce=_clean_text,
    )

    mode = _first_non_empty(_clean_text(_find_key(intake.artifacts, "review_mode")))
    contract_pack = _first_non_empty(
        _clean_text(_find_key(intake.artifacts, "contract_pack")),
        _clean_text(_find_key(intake.artifacts, "pack")),
    )

    return {
        "target_repo": target_repo,
        "pr_number": pr_number,
        "commit_sha": commit_sha,
        "review_mode": mode,
        "contract_pack": contract_pack,
        "warnings": [],
    }


def _apply_budget(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], TruncationMetadata]:
    working = copy.deepcopy(payload)
    base_truncation, original_chars = _stabilize_truncation(
        working,
        TruncationMetadata(applied=False, original_chars=0, emitted_chars=0),
    )
    untruncated_truncation, untruncated_len = _stabilize_truncation(
        working,
        TruncationMetadata(
            applied=False,
            original_chars=original_chars,
            emitted_chars=base_truncation.emitted_chars,
        ),
    )
    original_chars = untruncated_len
    omitted_sections: list[str] = []
    coverage_impact: list[str] = []

    if untruncated_len <= max_chars:
        return working, untruncated_truncation

    shrinkers = [
        ("validation_evidence.blocking_findings", "coverage_validation_evidence_reduced", _shrink_validation_findings),
        ("checks.checks", "coverage_checks_reduced", _shrink_checks_rows),
        ("artifacts.details", "coverage_artifact_details_reduced", _shrink_artifact_details),
        ("semantic_groups.files", "coverage_semantic_context_reduced", _shrink_semantic_group_files),
        ("changed_files_summary.files", "coverage_changed_files_reduced", _shrink_changed_files),
    ]

    while True:
        current_truncation, current_len = _stabilize_truncation(
            working,
            TruncationMetadata(
                applied=True,
                original_chars=original_chars,
                emitted_chars=0,
                omitted_sections=list(omitted_sections),
                truncation_reason="max_chars_exceeded",
                coverage_impact=list(coverage_impact),
            ),
        )
        if current_len <= max_chars:
            return working, current_truncation

        changed = False
        for section, impact, shrink in shrinkers:
            if shrink(working):
                changed = True
                if section not in omitted_sections:
                    omitted_sections.append(section)
                    coverage_impact.append(impact)
                break
        if not changed:
            limitations = working.get("limitations")
            if isinstance(limitations, list) and "brief_budget_under_minimum_required_sections" not in limitations:
                limitations.append("brief_budget_under_minimum_required_sections")
            break

    final_truncation, _ = _stabilize_truncation(
        working,
        TruncationMetadata(
            applied=True,
            original_chars=original_chars,
            emitted_chars=0,
            omitted_sections=omitted_sections,
            truncation_reason="max_chars_exceeded_minimum_required_sections",
            coverage_impact=coverage_impact,
        ),
    )
    return working, final_truncation


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


def _artifact_identity_candidates(artifacts: Any, key: str) -> list[tuple[str, Any]]:
    if not isinstance(artifacts, dict):
        return []
    candidates: list[tuple[str, Any]] = []
    for artifact_name in sorted(artifacts):
        artifact = artifacts[artifact_name]
        found = _find_key(artifact, key)
        if found is not None:
            candidates.append((f"intake.artifacts.{artifact_name}.{key}", found))
    return candidates


def _canonical_len(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _materialize_brief(payload: dict[str, Any], *, truncation: TruncationMetadata) -> tuple[PRBrief, int]:
    model = PRBrief.model_validate({**payload, "truncation": truncation.model_dump(mode="json")})
    dumped = model.model_dump(mode="json")
    return model, _canonical_len(dumped)


def _stabilize_truncation(
    payload: dict[str, Any],
    truncation: TruncationMetadata,
    *,
    max_iterations: int = 16,
) -> tuple[TruncationMetadata, int]:
    stable = truncation.model_copy(deep=True)
    emitted = stable.emitted_chars
    for _ in range(max_iterations):
        stable.emitted_chars = emitted
        _, current_len = _materialize_brief(payload, truncation=stable)
        if current_len == emitted:
            stable.emitted_chars = current_len
            return stable, current_len
        emitted = current_len
    stable.emitted_chars = emitted
    return stable, emitted


def _resolve_identity_value(
    field_name: str,
    candidates: list[tuple[str, Any]],
    *,
    coerce,
) -> Any:
    values_by_source: dict[str, Any] = {}
    for source, raw in candidates:
        value = coerce(raw)
        if value is None:
            continue
        values_by_source[source] = value
    unique_values = sorted({value for value in values_by_source.values()}, key=lambda item: str(item))
    if len(unique_values) > 1:
        details = ",".join(f"{source}={values_by_source[source]}" for source in sorted(values_by_source))
        raise PRBriefError(
            "review_identity_conflict",
            f"conflicting review identity for {field_name}: {details}",
        )
    if unique_values:
        return unique_values[0]
    return None


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
