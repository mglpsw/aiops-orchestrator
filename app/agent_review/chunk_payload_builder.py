"""Deterministic bounded chunk payload builder for AgentReview."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import defaultdict
from typing import Any

from app.agent_review.chunk_artifact_ids import ChunkArtifactIdError, chunk_artifact_filename
from app.agent_review.chunk_response_contract import build_chunk_response_contract
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
    _validate_identity_consistency(
        intake=intake,
        chunk_plan=chunk_plan,
        pr_brief=pr_brief,
        checks=checks,
        validation_evidence=validation_evidence,
    )
    chunks = sorted(chunk_plan.chunks, key=lambda item: (item.order_index, item.chunk_id))
    _validate_chunk_plan_uniqueness(chunks)
    diff_map = _diff_by_file(intake)
    file_context = _file_context_map(intake)

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
            if filename in payloads:
                raise ChunkPayloadBuilderError(
                    "chunk_plan_duplicate_payload_filename",
                    f"duplicate payload filename generated for chunk plan: {filename}",
                )
            payloads[filename] = payload

    if len(manifest_chunks) != len(chunks):
        raise ChunkPayloadBuilderError(
            "chunk_plan_manifest_mismatch",
            "chunk payload manifest must contain exactly one entry per planned chunk",
        )
    available_entries = [entry for entry in manifest_chunks if entry.payload_path]
    if len(payloads) != len(available_entries):
        raise ChunkPayloadBuilderError(
            "chunk_plan_manifest_mismatch",
            "payload_count must match available manifest entries",
        )

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
    sanitized_manifest = sanitize_artifact_value(manifest.model_dump(mode="json"))
    return ChunkPayloadManifest.model_validate(sanitized_manifest), payloads


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
            chunk_hunks.append({"path": _sanitize_relative_path(path), "hunk": hunk})
            continue
        limitations.append(f"chunk_diff_hunk_missing:{_sanitize_relative_path(path)}")

    contracts_context, contract_limitations = _contracts_context(
        intake,
        chunk=chunk,
        selected_contract_pack=_clean_text(pr_brief.review.get("contract_pack")),
    )
    checks_context, check_limitations = _checks_context(checks, intake=intake, chunk_files=set(chunk.files))
    evidence_context, evidence_limitations = _evidence_context(
        intake,
        chunk=chunk,
        validation_evidence=validation_evidence,
    )
    limitations.extend(contract_limitations)
    limitations.extend(check_limitations)
    limitations.extend(evidence_limitations)

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
            "contracts_context": contracts_context,
            "evidence_context": evidence_context,
            "checks_context": checks_context,
            "aux_context": _aux_context(intake, chunk=chunk),
        },
        "coverage": {
            "declared_coverage": chunk.coverage,
            "files_in_chunk": [item["path"] for item in chunk_files],
            "chunk_file_count": len(chunk_files),
            "hunks_included": len(chunk_hunks),
            "chunk_plan_limitations": list(chunk.limitations),
        },
        "response_contract": build_chunk_response_contract(
            chunk_id=chunk.chunk_id,
            semantic_group=chunk.semantic_group,
        ),
        "warnings": _dedupe(warnings),
        "limitations": _dedupe(limitations),
        "created_at": pr_brief.created_at,
    }

    sanitized = sanitize_artifact_value(payload_body)
    payload_body, truncation = _apply_payload_budget(sanitized, max_chars=payload_budget)
    payload, _ = _materialize_payload(payload_body, truncation=truncation)

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


def _validate_chunk_plan_uniqueness(chunks: list[SemanticChunk]) -> None:
    seen_chunk_ids: set[str] = set()
    seen_order_indexes: set[int] = set()
    seen_filenames: set[str] = set()
    for chunk in chunks:
        try:
            filename = chunk_artifact_filename(chunk.chunk_id)
        except ChunkArtifactIdError as exc:
            raise ChunkPayloadBuilderError(exc.error_class, exc.message) from exc
        if chunk.chunk_id in seen_chunk_ids:
            raise ChunkPayloadBuilderError(
                "chunk_plan_duplicate_chunk_id",
                f"duplicate chunk_id in chunk plan: {chunk.chunk_id}",
            )
        seen_chunk_ids.add(chunk.chunk_id)
        if chunk.order_index in seen_order_indexes:
            raise ChunkPayloadBuilderError(
                "chunk_plan_duplicate_order_index",
                f"duplicate order_index in chunk plan: {chunk.order_index}",
            )
        seen_order_indexes.add(chunk.order_index)
        if filename in seen_filenames:
            raise ChunkPayloadBuilderError(
                "chunk_plan_duplicate_payload_filename",
                f"duplicate payload filename in chunk plan: {filename}",
            )
        seen_filenames.add(filename)


def _validate_identity_consistency(
    *,
    intake: ReviewIntake,
    chunk_plan: SemanticChunkPlan,
    pr_brief: PRBrief,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
) -> None:
    target_repo = _resolve_identity_value(
        "target_repo",
        [
            ("intake.target_repo", intake.target_repo),
            ("chunk_plan.target_repo", chunk_plan.target_repo),
            ("pr_brief.target.repository", _clean_text(_get(pr_brief.target, "repository"))),
            ("checks.target_repo", _find_key(checks, "target_repo")),
            ("validation_evidence.target_repo", _find_key(validation_evidence, "target_repo")),
            *_artifact_identity_candidates(intake.artifacts, "target_repo"),
        ],
        coerce=_clean_text,
    )
    if target_repo is None:
        raise ChunkPayloadBuilderError("review_identity_conflict", "missing required review identity field: target_repo")
    pr_number = _resolve_identity_value(
        "pr_number",
        [
            ("pr_brief.target.pr_number", _get(pr_brief.target, "pr_number")),
            ("checks.pr_number", _find_key(checks, "pr_number")),
            ("validation_evidence.pr_number", _find_key(validation_evidence, "pr_number")),
            *_artifact_identity_candidates(intake.artifacts, "pr_number"),
        ],
        coerce=_coerce_int,
    )
    commit_sha = _resolve_identity_value(
        "commit_sha",
        [
            ("pr_brief.target.commit_sha", _get(pr_brief.target, "commit_sha")),
            ("checks.commit_sha", _find_key(checks, "commit_sha")),
            ("validation_evidence.commit_sha", _find_key(validation_evidence, "commit_sha")),
            *_artifact_identity_candidates(intake.artifacts, "commit_sha"),
        ],
        coerce=_clean_text,
    )

    if _clean_text(_get(pr_brief.target, "repository")) != intake.target_repo:
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            "pr_brief target repository must match intake target repository",
        )
    if chunk_plan.target_repo != intake.target_repo:
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            "chunk plan target repository must match intake target repository",
        )
    if target_repo != intake.target_repo:
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            "resolved target repository must match intake target repository",
        )
    if pr_number is not None and _coerce_int(_get(pr_brief.target, "pr_number")) != pr_number:
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            "pr_brief pr_number must match resolved review identity",
        )
    if commit_sha is not None and _clean_text(_get(pr_brief.target, "commit_sha")) != commit_sha:
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            "pr_brief commit_sha must match resolved review identity",
        )


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
        raise ChunkPayloadBuilderError(
            "review_identity_conflict",
            f"conflicting review identity for {field_name}: {details}",
        )
    if unique_values:
        return unique_values[0]
    return None


def _contracts_context(
    intake: ReviewIntake,
    *,
    chunk: SemanticChunk,
    selected_contract_pack: str | None,
) -> tuple[dict[str, Any], list[str]]:
    profile = intake.target_profile if isinstance(intake.target_profile, dict) else {}
    contracts = _flatten_contract_rules(profile.get("domain_contracts"))
    packs = _flatten_review_packs(profile.get("review_packs"))
    relevance_keywords = _relevance_keywords(chunk)
    chunk_file_set = set(chunk.files)
    referenced_contracts = {item.split(":", 1)[1] for item in chunk.contracts if item.startswith("contract:") and ":" in item}
    include_all_contracts = "target_profile:domain_contracts" in chunk.contracts
    include_all_packs = "target_profile:review_packs" in chunk.contracts
    selected_pack = (selected_contract_pack or "").lower()

    filtered_contracts = [
        item
        for item in contracts
        if (
            include_all_contracts
            or item.get("id") in referenced_contracts
            or _contract_matches_chunk(item, chunk_files=chunk_file_set)
            or (
                relevance_keywords
                and any(keyword in (item.get("id", "") + " " + item.get("description", "")).lower() for keyword in relevance_keywords)
            )
        )
    ]
    filtered_packs = [
        item
        for item in packs
        if (
            include_all_packs
            or item.get("id") in referenced_contracts
            or (selected_pack and _review_pack_matches_selected(item, selected_pack))
            or _contract_matches_chunk(item, chunk_files=chunk_file_set)
            or (
                relevance_keywords
                and any(keyword in (item.get("id", "") + " " + item.get("description", "")).lower() for keyword in relevance_keywords)
            )
        )
    ]
    limitations: list[str] = []
    if not filtered_contracts and not filtered_packs:
        limitations.append(f"contracts_context_not_relevant:{chunk.chunk_id}")
    return (
        {
            "domain_contracts": sorted(filtered_contracts, key=lambda item: (item.get("id") or "", item.get("description") or "")),
            "review_packs": sorted(filtered_packs, key=lambda item: (item.get("id") or "", item.get("description") or "")),
        },
        limitations,
    )


def _evidence_context(
    intake: ReviewIntake,
    *,
    chunk: SemanticChunk,
    validation_evidence: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    validation_document = (
        validation_evidence
        if isinstance(validation_evidence, dict)
        else _artifact_content(intake, "validation-evidence-result")
    )
    chunk_files = set(chunk.files)
    validation_entries = _filter_validation_entries(
        validation_document,
        field_name="blocking_findings",
        chunk_files=chunk_files,
    )
    validation_risks = _filter_validation_entries(
        validation_document,
        field_name="validation_risks",
        chunk_files=chunk_files,
    )
    facts_for_synthesizer = _validation_facts(validation_document)
    lci = _artifact_content(intake, "local-code-intelligence")
    tests = _artifact_content(intake, "test-intelligence")
    lci_context, lci_limitations = _filter_lci(lci, chunk_files=set(chunk.files))
    return (
        {
            "validation_evidence": {
                "provided": isinstance(validation_document, dict),
                "status": _clean_text(_get(validation_document, "status")),
                "validation_verdict": _clean_text(_get(validation_document, "validation_verdict")),
                "blocking_findings": validation_entries,
                "validation_risks": validation_risks,
                "facts_for_synthesizer": facts_for_synthesizer,
                "limitations": _string_list(_get(validation_document, "limitations")),
            },
            "local_code_intelligence": lci_context,
            "test_intelligence": _filter_test_intelligence(tests, chunk_files=set(chunk.files)),
        },
        lci_limitations,
    )


def _checks_context(
    checks: dict[str, Any] | None,
    *,
    intake: ReviewIntake,
    chunk_files: set[str],
) -> tuple[dict[str, Any], list[str]]:
    checks_document = checks if isinstance(checks, dict) else _artifact_content(intake, "checks")
    if not isinstance(checks_document, dict):
        return {"provided": False, "status": None, "checks": []}, []
    checks_rows = [item for item in _list(checks_document.get("checks")) if isinstance(item, dict)]
    has_row_level_scope = any(_paths_from_item(item) or _is_global_item(item) for item in checks_rows)
    document_scope = _clean_text(checks_document.get("scope"))
    document_mode = _clean_text(checks_document.get("mode"))
    rows = []
    limitations: list[str] = []
    for item in checks_rows:
        item_scope_paths = _paths_from_item(item)
        is_global = _is_global_item(item)
        if item_scope_paths:
            if not item_scope_paths.intersection(chunk_files):
                continue
        elif not is_global:
            applies_to_chunk = True
            if document_scope:
                applies_to_chunk = _document_scope_applies_to_chunk(document_scope, chunk_files=chunk_files)
            if (not has_row_level_scope or document_scope or document_mode) and applies_to_chunk:
                rows.append(
                    {
                        "name": _clean_text(item.get("name")),
                        "status": _clean_text(item.get("status")) or "unknown",
                        "command": _clean_text(item.get("command")),
                        "scope": f"document:{document_scope}" if document_scope else "document",
                    }
                )
                continue
            name = _clean_text(item.get("name")) or "unknown_check"
            limitations.append(f"check_scope_unclassified:{name}")
            continue
        rows.append(
            {
                "name": _clean_text(item.get("name")),
                "status": _clean_text(item.get("status")) or "unknown",
                "command": _clean_text(item.get("command")),
                "scope": "global" if is_global else "file",
            }
        )
    return (
        {
            "provided": True,
            "status": _clean_text(checks_document.get("status")) or _clean_text(checks_document.get("validation_level")),
            "checks": sorted(rows, key=lambda item: ((item.get("name") or ""), item.get("status") or "")),
        },
        _dedupe(limitations),
    )


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


def _contract_matches_chunk(contract: dict[str, Any], *, chunk_files: set[str]) -> bool:
    contract_paths = _paths_from_item(contract)
    if contract_paths and contract_paths.intersection(chunk_files):
        return True
    patterns = _normalized_contract_patterns(contract.get("patterns"))
    if patterns and any(_matches_pattern(path, patterns) for path in chunk_files):
        return True
    return _is_global_item(contract)


def _matches_pattern(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.strip()
        if not normalized:
            continue
        if normalized.endswith("*") and path.startswith(normalized[:-1]):
            return True
        if normalized in path:
            return True
    return False


def _document_scope_applies_to_chunk(scope: str, *, chunk_files: set[str]) -> bool:
    normalized = scope.strip().lower()
    if not normalized:
        return True
    if normalized in {"global", "all", "document"}:
        return True
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    for file_path in chunk_files:
        lowered = file_path.lower()
        if normalized in lowered:
            return True
        if tokens and any(token in lowered for token in tokens):
            return True
    return False


def _is_global_item(item: dict[str, Any]) -> bool:
    scope = _clean_text(item.get("scope"))
    if scope and scope.lower() == "global":
        return True
    return item.get("is_global") is True


def _paths_from_item(item: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for key in ("file_path", "file", "original_file", "path"):
        value = _sanitize_relative_path(_clean_text(item.get(key)) or "")
        if value:
            paths.add(value)
    for key in ("files", "paths", "source_files", "related_files"):
        for value in _sanitize_contract_paths(item.get(key)):
            paths.add(value)
    return paths


def _filter_lci(document: dict[str, Any] | None, *, chunk_files: set[str]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(document, dict):
        return {"provided": False, "files_analyzed": [], "confirmed_local_failures": []}, []
    analyzed = [path for path in _string_list(document.get("files_analyzed")) if path in chunk_files]
    scoped_failures: list[dict[str, Any]] = []
    limitations = list(_string_list(document.get("limitations")))
    for item in _list(document.get("confirmed_local_failures")):
        if not isinstance(item, dict):
            continue
        if _is_global_item(item):
            scoped_failures.append(item)
            continue
        item_paths = _paths_from_item(item)
        if item_paths:
            if item_paths.intersection(chunk_files):
                scoped_failures.append(item)
            continue
        title = _clean_text(item.get("title")) or "unnamed_local_failure"
        limitations.append(f"lci_scope_unclassified:{title}")
    deduped_limitations = _dedupe(limitations)
    return (
        {
            "provided": True,
            "mode": _clean_text(document.get("mode")),
            "files_analyzed": analyzed,
            "confirmed_local_failures": scoped_failures,
            "limitations": deduped_limitations,
        },
        [item for item in deduped_limitations if item.startswith("lci_scope_unclassified:")],
    )


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


def _filter_validation_entries(
    document: dict[str, Any] | None,
    *,
    field_name: str,
    chunk_files: set[str],
) -> list[dict[str, Any]]:
    entries = _get(document, field_name)
    if not isinstance(entries, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        item_paths = _paths_from_item(item)
        if item_paths and not item_paths.intersection(chunk_files):
            continue
        sanitized = sanitize_artifact_value(item)
        if not isinstance(sanitized, dict):
            continue
        row = _normalize_validation_scope_fields(sanitized)
        if not row:
            continue
        key = _canonical_json(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return sorted(rows, key=_canonical_json)


def _normalize_validation_scope_fields(item: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(item)
    for key in ("file_path", "file", "original_file", "path"):
        if key not in row:
            continue
        value = _sanitize_relative_path(_clean_text(row.get(key)) or "")
        if value:
            row[key] = value
        else:
            row.pop(key, None)
    for key in ("files", "paths", "source_files", "related_files"):
        if key not in row:
            continue
        values = _sanitize_contract_paths(row.get(key))
        if values:
            row[key] = values
        else:
            row.pop(key, None)
    return row


def _validation_facts(document: dict[str, Any] | None) -> list[str]:
    facts: set[str] = set()
    for item in _list(_get(document, "facts_for_synthesizer")):
        cleaned = _clean_text(item) if isinstance(item, str) else None
        if not cleaned:
            continue
        sanitized = sanitize_artifact_value(cleaned)
        if isinstance(sanitized, str) and sanitized.strip():
            facts.add(sanitized.strip())
    return sorted(facts)


def _flatten_contract_rules(document: Any) -> list[dict[str, Any]]:
    rules = _get(document, "rules")
    if not isinstance(rules, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in rules:
        if not isinstance(item, dict):
            continue
        row = {
            "id": _clean_text(item.get("id")),
            "description": _clean_text(item.get("description")),
            "scope": _clean_text(item.get("scope")),
            "is_global": item.get("is_global") is True,
            "file_path": _sanitize_relative_path(_clean_text(item.get("file_path")) or ""),
            "path": _sanitize_relative_path(_clean_text(item.get("path")) or ""),
            "files": _sanitize_contract_paths(item.get("files")),
            "paths": _sanitize_contract_paths(item.get("paths")),
            "source_files": _sanitize_contract_paths(item.get("source_files")),
            "related_files": _sanitize_contract_paths(item.get("related_files")),
            "patterns": _normalized_contract_patterns(item.get("patterns")),
        }
        rows.append(_drop_empty_contract_fields(row))
    return sorted(rows, key=lambda item: (item.get("id") or "", item.get("description") or ""))


def _sanitize_contract_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    paths = [_sanitize_relative_path(item) for item in value if isinstance(item, str) and item.strip()]
    return sorted({item for item in paths if item})


def _normalized_contract_patterns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    patterns = [_sanitize_relative_path(item.strip()) for item in value if isinstance(item, str) and item.strip()]
    return sorted({item for item in patterns if item})


def _drop_empty_contract_fields(row: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, str) and not value:
            continue
        if value is None:
            continue
        if key == "is_global" and value is False:
            continue
        cleaned[key] = value
    return cleaned


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


def _review_pack_matches_selected(pack: dict[str, Any], selected_pack: str) -> bool:
    if not selected_pack:
        return False
    pack_id = _clean_text(pack.get("id")) or ""
    description = _clean_text(pack.get("description")) or ""
    selected = selected_pack.lower()
    id_lower = pack_id.lower()
    description_lower = description.lower()
    return (
        id_lower == selected
        or description_lower == selected
        or selected in id_lower
        or selected in description_lower
    )


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
    base_truncation, original_chars = _stabilize_payload_truncation(
        working,
        TruncationMetadata(applied=False, original_chars=0, emitted_chars=0),
    )
    untruncated_truncation, untruncated_len = _stabilize_payload_truncation(
        working,
        TruncationMetadata(
            applied=False,
            original_chars=original_chars,
            emitted_chars=base_truncation.emitted_chars,
        ),
    )
    untruncated_truncation, untruncated_len = _stabilize_payload_truncation(
        working,
        untruncated_truncation.model_copy(update={"original_chars": untruncated_len}),
    )
    original_chars = untruncated_len
    omitted_sections: list[str] = []
    coverage_impact: list[str] = []
    if untruncated_len <= max_chars:
        return working, untruncated_truncation

    shrinkers = [
        ("aux_context", "auxiliary_context_reduced", _shrink_aux_context),
        ("checks_context", "checks_context_reduced", _shrink_checks_context),
        ("evidence_context", "evidence_context_reduced", _shrink_evidence_context),
        ("contracts_context", "contracts_context_reduced", _shrink_contracts_context),
        ("chunk_hunks", "chunk_hunks_reduced", _shrink_chunk_hunks),
    ]

    while True:
        current_truncation, current_len = _stabilize_payload_truncation(
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
            _refresh_hunk_coverage(working)
            return working, current_truncation

        changed = False
        for section, impact, shrink in shrinkers:
            if shrink(working):
                changed = True
                _refresh_hunk_coverage(working)
                if section not in omitted_sections:
                    omitted_sections.append(section)
                    coverage_impact.append(impact)
                break
        if not changed:
            limitations = _get(working, "limitations")
            if isinstance(limitations, list) and "payload_budget_under_minimum_required_content" not in limitations:
                limitations.append("payload_budget_under_minimum_required_content")
            break

    final_truncation, _ = _stabilize_payload_truncation(
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
    _refresh_hunk_coverage(working)
    return working, final_truncation


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
        facts = validation.get("facts_for_synthesizer")
        if isinstance(facts, list) and facts:
            facts.pop()
            return True
        risks = validation.get("validation_risks")
        if isinstance(risks, list) and risks:
            risks.pop()
            return True
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
            "validation_risks": [],
            "facts_for_synthesizer": [],
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


def _refresh_hunk_coverage(payload: dict[str, Any]) -> None:
    chunk_context = _get(payload, "chunk_context")
    coverage = _get(payload, "coverage")
    if not isinstance(chunk_context, dict) or not isinstance(coverage, dict):
        return
    hunks = chunk_context.get("chunk_hunks")
    if isinstance(hunks, list):
        coverage["hunks_included"] = len(hunks)


def _payload_filename(chunk: SemanticChunk) -> tuple[str, list[str]]:
    try:
        return chunk_artifact_filename(chunk.chunk_id), []
    except ChunkArtifactIdError as exc:
        raise ChunkPayloadBuilderError(exc.error_class, exc.message) from exc


def _diff_by_file(intake: ReviewIntake) -> dict[str, str]:
    full_diff = _artifact_text(intake, "full-diff")
    if not full_diff:
        return {}
    result: dict[str, str] = {}
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        path = _resolve_diff_block_path(buffer)
        if path:
            rendered = "\n".join(buffer).strip()
            if rendered:
                result[path] = rendered
        buffer = []

    for line in full_diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            buffer = [line]
            continue
        if buffer:
            buffer.append(line)
    flush()
    return dict(sorted(result.items()))


def _resolve_diff_block_path(block_lines: list[str]) -> str | None:
    header_path = _parse_diff_path(block_lines[0])
    plus_path: str | None = None
    minus_path: str | None = None
    rename_to_path: str | None = None
    for line in block_lines[1:]:
        if line.startswith("rename to "):
            rename_to_path = _normalize_diff_path(line[len("rename to ") :])
            continue
        if line.startswith("+++ "):
            marker_path = _normalize_diff_path(line[4:])
            if marker_path == "/dev/null":
                plus_path = "/dev/null"
            elif marker_path:
                plus_path = marker_path
            continue
        if line.startswith("--- "):
            marker_path = _normalize_diff_path(line[4:])
            if marker_path and marker_path != "/dev/null":
                minus_path = marker_path
    if rename_to_path:
        return rename_to_path
    if plus_path and plus_path != "/dev/null":
        return plus_path
    if plus_path == "/dev/null" and minus_path:
        return minus_path
    return header_path


def _parse_diff_path(line: str) -> str | None:
    if not line.startswith("diff --git "):
        return None
    parsed = _split_diff_git_header(line[len("diff --git ") :])
    if len(parsed) < 2:
        return None
    return _normalize_diff_path(parsed[1])


def _split_diff_git_header(raw: str) -> list[str]:
    parts: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        while index < length and raw[index].isspace():
            index += 1
        if index >= length:
            break
        if raw[index] == '"':
            token = ['"']
            index += 1
            while index < length:
                char = raw[index]
                token.append(char)
                index += 1
                if char == "\\" and index < length:
                    token.append(raw[index])
                    index += 1
                    continue
                if char == '"':
                    break
            parts.append("".join(token))
            continue
        start = index
        while index < length and not raw[index].isspace():
            index += 1
        parts.append(raw[start:index])
    return parts


def _normalize_diff_path(raw_path: str) -> str | None:
    value = _decode_git_path(raw_path)
    if not value:
        return None
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return value.replace("\\", "/")


def _decode_git_path(raw_path: str) -> str:
    text = raw_path.strip()
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    inner = text[1:-1]
    decoded = bytearray()
    index = 0
    while index < len(inner):
        char = inner[index]
        if char != "\\":
            decoded.extend(char.encode("utf-8"))
            index += 1
            continue
        if index + 1 >= len(inner):
            decoded.append(ord("\\"))
            break
        next_char = inner[index + 1]
        octal = inner[index + 1 : index + 4]
        if len(octal) == 3 and re.fullmatch(r"[0-7]{3}", octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        escape_map = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}
        mapped = escape_map.get(next_char)
        if mapped is not None:
            decoded.extend(mapped.encode("utf-8"))
            index += 2
            continue
        decoded.extend(next_char.encode("utf-8"))
        index += 2
    return decoded.decode("utf-8", errors="replace")


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
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def _materialize_payload(payload: dict[str, Any], *, truncation: TruncationMetadata) -> tuple[ChunkPayload, int]:
    model = ChunkPayload.model_validate({**payload, "truncation": truncation.model_dump(mode="json")})
    dumped = model.model_dump(mode="json")
    return model, _canonical_len(dumped)


def _stabilize_payload_truncation(
    payload: dict[str, Any],
    truncation: TruncationMetadata,
    *,
    max_iterations: int = 16,
) -> tuple[TruncationMetadata, int]:
    stable = truncation.model_copy(deep=True)
    emitted = stable.emitted_chars
    for _ in range(max_iterations):
        stable.emitted_chars = emitted
        _, current_len = _materialize_payload(payload, truncation=stable)
        if current_len == emitted:
            stable.emitted_chars = current_len
            return stable, current_len
        emitted = current_len
    stable.emitted_chars = emitted
    return stable, emitted


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
        if not isinstance(artifact, dict):
            continue
        if key in artifact:
            candidates.append((f"intake.artifacts.{artifact_name}.{key}", artifact.get(key)))
        content = artifact.get("content")
        if isinstance(content, dict) and key in content:
            candidates.append((f"intake.artifacts.{artifact_name}.content.{key}", content.get(key)))
    return candidates


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
