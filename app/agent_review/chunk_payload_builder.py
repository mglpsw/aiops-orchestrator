"""Deterministic bounded chunk payload builder for AgentReview."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import defaultdict
from typing import Any

from app.agent_review.redaction import sanitize_artifact_value
from app.agent_review.schemas import (
    ChunkPayload,
    ChunkPayloadManifest,
    ChunkPayloadManifestEntry,
    PRBrief,
    ReviewIntake,
    SemanticChunk,
    SemanticChunkPlan,
    TruncationMetadata,
)

DEFAULT_PAYLOAD_MAX_CHARS = 24_000


class ChunkPayloadBuilderError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def build_chunk_payloads(
    *,
    intake: ReviewIntake,
    chunk_plan: SemanticChunkPlan,
    pr_brief: PRBrief,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
    max_chars_per_payload: int | None = None,
    optional_limitations: list[str] | None = None,
) -> tuple[ChunkPayloadManifest, dict[str, ChunkPayload]]:
    diff_map = _diff_by_file(intake)
    file_context = _file_context_map(intake)
    chunks = sorted(chunk_plan.chunks, key=lambda item: (item.order_index, item.chunk_id))

    payloads: dict[str, ChunkPayload] = {}
    manifest_chunks: list[ChunkPayloadManifestEntry] = []
    manifest_warnings: list[str] = []
    manifest_limitations = [*chunk_plan.limitations, *pr_brief.limitations, *(optional_limitations or [])]

    for chunk in chunks:
        payload, entry, filename = _build_chunk_payload(
            chunk=chunk,
            intake=intake,
            pr_brief=pr_brief,
            checks=checks,
            validation_evidence=validation_evidence,
            file_context=file_context,
            diff_map=diff_map,
            max_chars_per_payload=max_chars_per_payload,
        )
        manifest_chunks.append(entry)
        manifest_warnings.extend(entry.warnings)
        manifest_limitations.extend(entry.limitations)
        if payload is not None and filename is not None:
            payloads[filename] = payload

    manifest = ChunkPayloadManifest(
        target_repo=chunk_plan.target_repo,
        chunk_plan_ref={
            "schema_id": chunk_plan.schema_id,
            "schema_version": chunk_plan.schema_version,
            "source": chunk_plan.source,
            "status": chunk_plan.status,
            "chunk_count": len(chunks),
            "created_at": chunk_plan.created_at,
        },
        pr_brief_ref={
            "schema_id": pr_brief.schema_id,
            "schema_version": pr_brief.schema_version,
            "source": pr_brief.source,
            "created_at": pr_brief.created_at,
            "sha256": _sha256_payload(pr_brief.model_dump(mode="json")),
        },
        payload_count=len(payloads),
        chunks=manifest_chunks,
        warnings=_dedupe(manifest_warnings),
        limitations=_dedupe(manifest_limitations),
        created_at=pr_brief.created_at,
    )
    return manifest, payloads


def _build_chunk_payload(
    *,
    chunk: SemanticChunk,
    intake: ReviewIntake,
    pr_brief: PRBrief,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
    file_context: dict[str, dict[str, Any]],
    diff_map: dict[str, str],
    max_chars_per_payload: int | None,
) -> tuple[ChunkPayload | None, ChunkPayloadManifestEntry, str | None]:
    payload_budget = _resolve_payload_budget(chunk, max_chars_per_payload=max_chars_per_payload)
    if payload_budget <= 0:
        raise ChunkPayloadBuilderError("payload_budget_invalid", "chunk payload budget must be greater than zero")

    warnings: list[str] = []
    limitations = list(chunk.limitations)
    if not chunk.files:
        limitations.append(f"chunk_has_no_files:{chunk.chunk_id}")

    chunk_files = []
    for path in sorted(chunk.files):
        context = file_context.get(path, {})
        chunk_files.append(
            {
                "path": _sanitize_relative_path(path),
                "status": _clean_text(context.get("status")) or "unknown",
                "summary": _clean_text(context.get("summary")),
            }
        )
    if any(item["path"] == "[LOCAL_PATH_REDACTED]" for item in chunk_files):
        warnings.append(f"chunk_path_redacted:{chunk.chunk_id}")

    chunk_hunks = []
    for path in sorted(chunk.files):
        hunk = diff_map.get(path)
        if hunk:
            chunk_hunks.append({"path": path, "hunk": hunk})

    payload_body = {
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "order_index": chunk.order_index,
        "target": {
            "repository": intake.target_repo,
            "pr_number": pr_brief.target.get("pr_number"),
            "commit_sha": pr_brief.target.get("commit_sha"),
        },
        "brief": {
            "repository": pr_brief.target.get("repository"),
            "pr_number": pr_brief.target.get("pr_number"),
            "commit_sha": pr_brief.target.get("commit_sha"),
            "review_mode": pr_brief.review.get("mode"),
            "contract_pack": pr_brief.review.get("contract_pack"),
            "required_files": pr_brief.coverage.get("required_files"),
            "limitations": list(pr_brief.limitations),
        },
        "chunk_context": {
            "files": chunk_files,
            "chunk_hunks": chunk_hunks,
            "contracts_context": _contracts_context(intake, chunk=chunk),
            "evidence_context": _evidence_context(intake, chunk=chunk, validation_evidence=validation_evidence),
            "checks_context": _checks_context(checks, intake=intake),
            "aux_context": _aux_context(intake, chunk=chunk),
        },
        "coverage": {
            "declared_coverage": chunk.coverage,
            "files_in_chunk": [item["path"] for item in chunk_files],
            "chunk_file_count": len(chunk_files),
            "hunks_included": len(chunk_hunks),
            "chunk_plan_limitations": list(chunk.limitations),
        },
        "response_contract": {
            "schema_version": 1,
            "required_fields": [
                "schema_version",
                "chunk_id",
                "semantic_group",
                "confirmed_findings",
                "risks",
                "limitations",
                "coverage_notes",
            ],
            "finding_requirements": [
                "severity",
                "title",
                "file_path",
                "impact",
                "evidence",
                "source_artifact_or_line_or_hunk",
            ],
            "forbidden_content": [
                "absolute_paths",
                "tokens",
                "headers",
                "cookies",
                "env_dumps",
                "raw_provider_payload",
                "raw_prompt_or_response",
            ],
        },
        "warnings": _dedupe(warnings),
        "limitations": _dedupe(limitations),
        "created_at": pr_brief.created_at,
    }

    payload_body, truncation = _apply_payload_budget(payload_body, max_chars=payload_budget)
    sanitized = sanitize_artifact_value(payload_body)
    payload = ChunkPayload.model_validate({**sanitized, "truncation": truncation.model_dump(mode="json")})

    filename, filename_limitations = _payload_filename(chunk)
    entry_limitations = _dedupe([*payload.limitations, *filename_limitations])
    payload_hash = _sha256_payload(payload.model_dump(mode="json"))
    manifest_entry = ChunkPayloadManifestEntry(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        order_index=chunk.order_index,
        status="limited" if entry_limitations or payload.truncation.applied else "available",
        payload_path=filename,
        payload_sha256=payload_hash,
        coverage=dict(payload.coverage),
        warnings=list(payload.warnings),
        limitations=entry_limitations,
        truncation=payload.truncation,
    )
    return payload, manifest_entry, filename


def _resolve_payload_budget(chunk: SemanticChunk, *, max_chars_per_payload: int | None) -> int:
    if max_chars_per_payload is not None:
        return max_chars_per_payload
    if isinstance(chunk.prompt_budget_chars, int) and chunk.prompt_budget_chars > 0:
        return chunk.prompt_budget_chars
    return DEFAULT_PAYLOAD_MAX_CHARS


def _contracts_context(intake: ReviewIntake, *, chunk: SemanticChunk) -> dict[str, Any]:
    profile = intake.target_profile if isinstance(intake.target_profile, dict) else {}
    contracts = _flatten_contract_rules(profile.get("domain_contracts"))
    packs = _flatten_review_packs(profile.get("review_packs"))
    relevance_keywords = _relevance_keywords(chunk)

    filtered_contracts = [
        item
        for item in contracts
        if not relevance_keywords
        or any(keyword in (item.get("id", "") + " " + item.get("description", "")).lower() for keyword in relevance_keywords)
    ]
    if not filtered_contracts:
        filtered_contracts = contracts[:2]
    filtered_packs = [
        item
        for item in packs
        if not relevance_keywords
        or any(keyword in (item.get("id", "") + " " + item.get("description", "")).lower() for keyword in relevance_keywords)
    ]
    if not filtered_packs:
        filtered_packs = packs[:1]
    return {
        "domain_contracts": filtered_contracts,
        "review_packs": filtered_packs,
    }


def _evidence_context(
    intake: ReviewIntake,
    *,
    chunk: SemanticChunk,
    validation_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    validation_document = (
        validation_evidence
        if isinstance(validation_evidence, dict)
        else _artifact_content(intake, "validation-evidence-result")
    )
    validation_entries = _filter_validation_findings(validation_document, chunk_files=set(chunk.files))
    lci = _artifact_content(intake, "local-code-intelligence")
    tests = _artifact_content(intake, "test-intelligence")
    return {
        "validation_evidence": {
            "provided": isinstance(validation_document, dict),
            "status": _clean_text(_get(validation_document, "status")),
            "validation_verdict": _clean_text(_get(validation_document, "validation_verdict")),
            "blocking_findings": validation_entries,
            "limitations": _string_list(_get(validation_document, "limitations")),
        },
        "local_code_intelligence": _filter_lci(lci, chunk_files=set(chunk.files)),
        "test_intelligence": _filter_test_intelligence(tests, chunk_files=set(chunk.files)),
    }


def _checks_context(checks: dict[str, Any] | None, *, intake: ReviewIntake) -> dict[str, Any]:
    checks_document = checks if isinstance(checks, dict) else _artifact_content(intake, "checks")
    if not isinstance(checks_document, dict):
        return {"provided": False, "status": None, "checks": []}
    rows = []
    for item in _list(checks_document.get("checks")):
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "name": _clean_text(item.get("name")),
                "status": _clean_text(item.get("status")) or "unknown",
                "command": _clean_text(item.get("command")),
            }
        )
    return {
        "provided": True,
        "status": _clean_text(checks_document.get("status")) or _clean_text(checks_document.get("validation_level")),
        "checks": sorted(rows, key=lambda item: ((item.get("name") or ""), item.get("status") or "")),
    }


def _aux_context(intake: ReviewIntake, *, chunk: SemanticChunk) -> dict[str, Any]:
    project_context = _artifact_content(intake, "project-context")
    semantic_context = _artifact_content(intake, "semantic-context")
    file_context = _artifact_content(intake, "file-diff-context")
    modules = _get(project_context, "modules")
    selected_modules: list[dict[str, Any]] = []
    if isinstance(modules, dict):
        for path in sorted(chunk.files):
            if path in modules:
                selected_modules.append({"path": path, "description": _clean_text(modules.get(path))})

    requirements = _get(file_context, "coverage_requirements")
    return {
        "project_context": {
            "provided": isinstance(project_context, dict),
            "status": _clean_text(_get(project_context, "status")),
            "modules": selected_modules,
            "gaps": _string_list(_get(project_context, "gaps")),
        },
        "semantic_context": {
            "provided": isinstance(semantic_context, dict),
            "status": _clean_text(_get(semantic_context, "status")),
            "scope": _clean_text(_get(semantic_context, "scope")),
            "change_type": _clean_text(_get(semantic_context, "change_type")),
            "must_hold": _string_list(_get(semantic_context, "must_hold")),
        },
        "coverage_requirements": _coverage_requirements_for_chunk(requirements, chunk_files=set(chunk.files)),
    }


def _coverage_requirements_for_chunk(requirements: Any, *, chunk_files: set[str]) -> dict[str, list[str]]:
    if not isinstance(requirements, dict):
        return {"must_review_files": [], "should_review_files": [], "may_summarize_files": []}
    result: dict[str, list[str]] = {}
    for key in ("must_review_files", "should_review_files", "may_summarize_files"):
        values = [item for item in _string_list(requirements.get(key)) if item in chunk_files]
        result[key] = values
    return result


def _filter_lci(document: dict[str, Any] | None, *, chunk_files: set[str]) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {"provided": False, "files_analyzed": []}
    analyzed = [path for path in _string_list(document.get("files_analyzed")) if path in chunk_files]
    return {
        "provided": True,
        "mode": _clean_text(document.get("mode")),
        "files_analyzed": analyzed,
        "confirmed_local_failures": _list(document.get("confirmed_local_failures")),
        "limitations": _string_list(document.get("limitations")),
    }


def _filter_test_intelligence(document: dict[str, Any] | None, *, chunk_files: set[str]) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {"provided": False, "changed_tests": [], "failed_tests": []}
    changed_tests = [path for path in _string_list(document.get("changed_tests")) if path in chunk_files]
    failed_tests = [path for path in _string_list(document.get("failed_tests")) if path in chunk_files]
    return {
        "provided": True,
        "mode": _clean_text(document.get("mode")),
        "changed_tests": changed_tests,
        "failed_tests": failed_tests,
        "limitations": _string_list(document.get("limitations")),
    }


def _filter_validation_findings(document: dict[str, Any] | None, *, chunk_files: set[str]) -> list[dict[str, Any]]:
    findings = _get(document, "blocking_findings")
    if not isinstance(findings, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        file_path = _clean_text(item.get("file_path"))
        if file_path and file_path not in chunk_files:
            continue
        rows.append(
            {
                "title": _clean_text(item.get("title")),
                "severity": _clean_text(item.get("severity")),
                "file_path": _sanitize_relative_path(file_path or ""),
            }
        )
    return sorted(rows, key=lambda item: ((item.get("severity") or ""), (item.get("file_path") or ""), (item.get("title") or "")))


def _flatten_contract_rules(document: Any) -> list[dict[str, Any]]:
    rules = _get(document, "rules")
    if not isinstance(rules, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": _clean_text(item.get("id")),
                "description": _clean_text(item.get("description")),
            }
        )
    return sorted(rows, key=lambda item: (item.get("id") or "", item.get("description") or ""))


def _flatten_review_packs(document: Any) -> list[dict[str, Any]]:
    packs = _get(document, "packs")
    if not isinstance(packs, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in packs:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "id": _clean_text(item.get("id")),
                "description": _clean_text(item.get("description")),
                "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),
            }
        )
    return sorted(rows, key=lambda item: (item.get("id") or "", item.get("description") or ""))


def _relevance_keywords(chunk: SemanticChunk) -> tuple[str, ...]:
    mapping = {
        "primary_backend_logic": ("backend", "service", "domain", "api"),
        "api_schema_contract": ("schema", "contract", "api", "model"),
        "frontend_ui": ("frontend", "ui", "component"),
        "tests": ("test", "coverage", "assert"),
        "workflow_aiops": ("workflow", "aiops", "pipeline"),
        "docs_changelog": ("docs", "changelog", "readme"),
        "suspicious_out_of_scope": ("secret", "prod", "deploy", "runtime"),
    }
    return mapping.get(chunk.semantic_group, tuple())


def _apply_payload_budget(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], TruncationMetadata]:
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
        ("aux_context", "auxiliary_context_reduced", _shrink_aux_context),
        ("checks_context", "checks_context_reduced", _shrink_checks_context),
        ("evidence_context", "evidence_context_reduced", _shrink_evidence_context),
        ("contracts_context", "contracts_context_reduced", _shrink_contracts_context),
        ("chunk_hunks", "chunk_hunks_reduced", _shrink_chunk_hunks),
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
    limitations = _get(working, "limitations")
    if isinstance(limitations, list) and emitted_chars > max_chars:
        limitations.append("payload_budget_under_minimum_required_content")
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


def _shrink_aux_context(payload: dict[str, Any]) -> bool:
    aux = _get(_get(payload, "chunk_context"), "aux_context")
    if isinstance(aux, dict) and aux and aux != {"status": "omitted_due_to_budget"}:
        _get(payload, "chunk_context")["aux_context"] = {"status": "omitted_due_to_budget"}
        return True
    return False


def _shrink_checks_context(payload: dict[str, Any]) -> bool:
    checks = _get(_get(payload, "chunk_context"), "checks_context")
    if not isinstance(checks, dict):
        return False
    rows = checks.get("checks")
    if isinstance(rows, list) and rows:
        rows.pop()
        return True
    minimal = {
        "provided": checks.get("provided"),
        "status": checks.get("status"),
        "checks": [],
    }
    if checks != minimal:
        _get(payload, "chunk_context")["checks_context"] = minimal
        return True
    return False


def _shrink_evidence_context(payload: dict[str, Any]) -> bool:
    evidence = _get(_get(payload, "chunk_context"), "evidence_context")
    if not isinstance(evidence, dict):
        return False
    validation = evidence.get("validation_evidence")
    if isinstance(validation, dict):
        findings = validation.get("blocking_findings")
        if isinstance(findings, list) and findings:
            findings.pop()
            return True
    lci = evidence.get("local_code_intelligence")
    if isinstance(lci, dict):
        analyzed = lci.get("files_analyzed")
        if isinstance(analyzed, list) and analyzed:
            analyzed.pop()
            return True
    minimal = {
        "validation_evidence": {
            "provided": _get(validation, "provided") if isinstance(validation, dict) else False,
            "status": _get(validation, "status") if isinstance(validation, dict) else None,
            "validation_verdict": _get(validation, "validation_verdict") if isinstance(validation, dict) else None,
            "blocking_findings": [],
            "limitations": _get(validation, "limitations") if isinstance(validation, dict) else [],
        },
        "local_code_intelligence": {"provided": False, "files_analyzed": []},
        "test_intelligence": {"provided": False, "changed_tests": [], "failed_tests": []},
    }
    if evidence != minimal:
        _get(payload, "chunk_context")["evidence_context"] = minimal
        return True
    return False


def _shrink_contracts_context(payload: dict[str, Any]) -> bool:
    contracts = _get(_get(payload, "chunk_context"), "contracts_context")
    if not isinstance(contracts, dict):
        return False
    for key in ("review_packs", "domain_contracts"):
        items = contracts.get(key)
        if isinstance(items, list) and items:
            items.pop()
            return True
    minimal = {"domain_contracts": [], "review_packs": []}
    if contracts != minimal:
        _get(payload, "chunk_context")["contracts_context"] = minimal
        return True
    return False


def _shrink_chunk_hunks(payload: dict[str, Any]) -> bool:
    chunk_context = _get(payload, "chunk_context")
    if not isinstance(chunk_context, dict):
        return False
    hunks = chunk_context.get("chunk_hunks")
    if not isinstance(hunks, list) or not hunks:
        return False
    for item in reversed(hunks):
        if not isinstance(item, dict):
            continue
        hunk = item.get("hunk")
        if isinstance(hunk, str) and len(hunk) > 512:
            item["hunk"] = hunk[:509].rstrip() + "..."
            return True
    hunks.pop()
    return True


def _payload_filename(chunk: SemanticChunk) -> tuple[str, list[str]]:
    if _is_safe_filename(chunk.chunk_id):
        return f"{chunk.chunk_id}.json", []
    safe = f"chunk-{chunk.order_index + 1:02d}.json"
    return safe, [f"chunk_id_not_safe_for_filename:{chunk.chunk_id}"]


def _is_safe_filename(value: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    return bool(value) and all(char in allowed for char in value)


def _diff_by_file(intake: ReviewIntake) -> dict[str, str]:
    full_diff = _artifact_text(intake, "full-diff")
    if not full_diff:
        return {}
    result: dict[str, str] = {}
    current_path: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current_path, buffer
        if current_path and buffer:
            rendered = "\n".join(buffer).strip()
            if rendered:
                result[current_path] = rendered
        current_path = None
        buffer = []

    for line in full_diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            current_path = _parse_diff_path(line)
            buffer = [line]
            continue
        if current_path is not None:
            buffer.append(line)
    flush()
    return dict(sorted(result.items()))


def _parse_diff_path(line: str) -> str | None:
    parts = line.split()
    if len(parts) < 4:
        return None
    right = parts[3]
    if right.startswith("b/"):
        right = right[2:]
    return right.replace("\\", "/")


def _file_context_map(intake: ReviewIntake) -> dict[str, dict[str, Any]]:
    file_context = _artifact_content(intake, "file-diff-context")
    files = _get(file_context, "files")
    if not isinstance(files, list):
        return {}
    mapped: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = _clean_text(item.get("path"))
        if not path:
            continue
        mapped[path] = item
    return mapped


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


def _artifact_text(intake: ReviewIntake, name: str) -> str | None:
    candidates = {name, f"{name}.diff", f"{name}.txt"}
    for artifact_name, artifact in intake.artifacts.items():
        normalized = str(artifact_name).replace("\\", "/").rsplit("/", 1)[-1]
        if normalized not in candidates:
            continue
        if isinstance(artifact, dict):
            content = artifact.get("content")
            if isinstance(content, str):
                return content
    return None


def _sha256_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sanitize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.startswith("/") or normalized.startswith("~/"):
        return "[LOCAL_PATH_REDACTED]"
    if len(normalized) >= 2 and normalized[1] == ":":
        return "[LOCAL_PATH_REDACTED]"
    return normalized


def _canonical_len(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _get(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        return document.get(key)
    return None


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
