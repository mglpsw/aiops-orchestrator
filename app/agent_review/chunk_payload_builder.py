"""Deterministic bounded chunk payload builder for AgentReview."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

from app.agent_review import payload_cost_model
from app.agent_review.chunk_artifact_ids import ChunkArtifactIdError, chunk_artifact_filename
from app.agent_review.chunk_response_contract import build_chunk_response_contract
from app.agent_review.payload_cost_model import (
    canonical_json,
    checks_context,
    contracts_context,
    evidence_context,
    materialize_payload,
    sanitize_display_path as _sanitize_relative_path,
    stabilize_payload_truncation,
)
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
    payload_cost_model.assert_projection_inputs_bound(
        intake,
        checks=checks,
        validation_evidence=validation_evidence,
    )
    chunks = sorted(chunk_plan.chunks, key=lambda item: (item.order_index, item.chunk_id))
    _validate_chunk_plan_uniqueness(chunks)
    diff_map = payload_cost_model.diff_by_file(intake)
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
        display = _sanitize_relative_path(path)
        # C8: mirror the same textual-hunk-vs-binary/metadata-only-block
        # distinction the projection uses (payload_cost_model), or a
        # binary/metadata-only block would be embedded as if it were
        # reviewable diff content.
        if hunk and payload_cost_model.block_has_observable_textual_hunk(hunk):
            chunk_hunks.append({"path": display, "hunk": hunk})
            continue
        limitations.append(f"chunk_diff_hunk_missing:{display}")

    contracts_ctx, contract_limitations = contracts_context(
        intake,
        chunk_files=chunk.files,
        chunk_contracts=chunk.contracts,
        chunk_id=chunk.chunk_id,
        selected_contract_pack=_clean_text(pr_brief.review.get("contract_pack")),
        semantic_group=chunk.semantic_group,
    )
    checks_ctx, check_limitations = checks_context(checks, intake=intake, chunk_files=set(chunk.files))
    evidence_ctx, evidence_limitations = evidence_context(
        intake,
        chunk_files=chunk.files,
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
            "contracts_context": contracts_ctx,
            "evidence_context": evidence_ctx,
            "checks_context": checks_ctx,
            "aux_context": payload_cost_model.aux_context(intake, chunk_files=chunk.files),
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

    # Hard guard (rev.3 SS7): no downstream CLI (parse-chunks / synthesize /
    # quality-gate / telemetry) consumes this manifest -- they all take
    # --chunk-plan / --chunk-results instead -- so a residual coverage
    # divergence recorded only as a manifest limitation would never reach the
    # quality gate. Any file the plan declared covered whose hunk material
    # was reduced, altered, or dropped by the shrink ladder blocks this
    # chunk from being routed at all; it is never downgraded to a mere
    # limitation on an otherwise-emitted payload.
    #
    # The comparison baseline is the SANITIZED hunk text -- captured here,
    # before `_apply_payload_budget` runs -- never the raw `diff_map` hunk.
    # `sanitize_artifact_value` legitimately rewrites hunk content (secret
    # redaction, local-path redaction) independent of the budget; comparing
    # against the raw pre-sanitization text would make every legitimately
    # redacted hunk look like a budget-driven loss.
    original_hunks_by_path = {
        item["path"]: item["hunk"]
        for item in sanitized.get("chunk_context", {}).get("chunk_hunks", [])
        if isinstance(item, dict)
    }

    payload_body, truncation = _apply_payload_budget(sanitized, max_chars=payload_budget)
    payload, _ = materialize_payload(payload_body, truncation=truncation)

    final_hunks_by_path = {
        item.get("path"): item.get("hunk")
        for item in payload.chunk_context.get("chunk_hunks", [])
        if isinstance(item, dict)
    }
    reduced_paths: list[str] = []
    omitted_paths: list[str] = []
    fully_included = 0
    for path, original_hunk in original_hunks_by_path.items():
        final_hunk = final_hunks_by_path.get(path)
        if final_hunk is None:
            omitted_paths.append(path)
        elif final_hunk != original_hunk:
            reduced_paths.append(path)
        else:
            fully_included += 1
    reduced_paths.sort()
    omitted_paths.sort()
    missing_paths = sorted(item["path"] for item in chunk_files if item["path"] not in original_hunks_by_path)

    filename, filename_limitations = _payload_filename(chunk)

    if reduced_paths or omitted_paths:
        guard_limitations = [
            f"chunk_hunk_material_not_transported:{path}" for path in sorted({*reduced_paths, *omitted_paths})
        ]
        entry_limitations = _dedupe([*payload.limitations, *filename_limitations, *guard_limitations])
        manifest_entry = ChunkPayloadManifestEntry(
            chunk_id=chunk.chunk_id,
            semantic_group=chunk.semantic_group,
            order_index=chunk.order_index,
            status="limited",
            payload_path=None,
            payload_sha256=None,
            coverage={
                **dict(payload.coverage),
                "hunks_fully_included": fully_included,
                "hunks_reduced": len(reduced_paths),
                "hunks_omitted": len(omitted_paths),
                "files_with_hunks_reduced": reduced_paths,
                "files_with_hunks_omitted": omitted_paths,
                "files_with_hunks_missing": missing_paths,
            },
            warnings=list(payload.warnings),
            limitations=entry_limitations,
            truncation=payload.truncation,
        )
        return None, manifest_entry, None

    entry_limitations = _dedupe([*payload.limitations, *filename_limitations])
    payload_hash = _sha256_payload(payload.model_dump(mode="json"))
    manifest_entry = ChunkPayloadManifestEntry(
        chunk_id=chunk.chunk_id,
        semantic_group=chunk.semantic_group,
        order_index=chunk.order_index,
        status="limited" if entry_limitations or payload.truncation.applied else "available",
        payload_path=filename,
        payload_sha256=payload_hash,
        coverage={
            **dict(payload.coverage),
            "hunks_fully_included": fully_included,
            "hunks_reduced": 0,
            "hunks_omitted": 0,
            "files_with_hunks_reduced": [],
            "files_with_hunks_omitted": [],
            "files_with_hunks_missing": missing_paths,
        },
        warnings=list(payload.warnings),
        limitations=entry_limitations,
        truncation=payload.truncation,
    )
    return payload, manifest_entry, filename


def _resolve_payload_budget(chunk: SemanticChunk, *, max_chars_per_payload: int | None) -> int:
    if isinstance(chunk.prompt_budget_chars, int) and chunk.prompt_budget_chars > 0:
        effective_budget = chunk.prompt_budget_chars
    else:
        effective_budget = DEFAULT_PAYLOAD_MAX_CHARS
    # Single budget authority (rev.3 SS6): the planner already committed to
    # `chunk.prompt_budget_chars` when it decided this chunk's partition.
    # Silently preferring a different `--payload-max-chars` here would
    # reintroduce the exact planner/builder divergence this fix exists to
    # close -- a smaller override could truncate hunks the plan proved would
    # fit, without the plan ever knowing.
    if max_chars_per_payload is not None and max_chars_per_payload != effective_budget:
        raise ChunkPayloadBuilderError(
            "payload_budget_mismatch",
            f"--payload-max-chars ({max_chars_per_payload}) does not match chunk {chunk.chunk_id}'s "
            f"own planned prompt_budget_chars ({effective_budget}); the planner and builder must "
            "consume a single effective budget",
        )
    return effective_budget


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


# aux_context / _coverage_requirements_for_chunk now live in
# payload_cost_model as the single authority both this builder and the
# planner's exact-original_chars bootstrap call (P2-7,
# aiops-orchestrator#225 PR #227 exact-HEAD adversarial review).


def _apply_payload_budget(payload: dict[str, Any], *, max_chars: int) -> tuple[dict[str, Any], TruncationMetadata]:
    working = copy.deepcopy(payload)
    # P3 hardening (PR #227 round 3): single shared bootstrap authority --
    # see payload_cost_model.bootstrap_untruncated_state.
    untruncated_truncation, untruncated_len = payload_cost_model.bootstrap_untruncated_state(working)
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
        current_truncation, current_len = stabilize_payload_truncation(
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

    final_truncation, _ = stabilize_payload_truncation(
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




# file_context_map now lives in payload_cost_model as the single authority
# both the builder and the planner's cost projection call
# (aiops-orchestrator#225 P2-1) -- the projection must see the exact same
# per-file status/summary the builder will emit into chunk_context.files, a
# field the shrink ladder never touches.
_file_context_map = payload_cost_model.file_context_map



def _sha256_payload(payload: dict[str, Any]) -> str:
    canonical = canonical_json(payload)
    return hashlib.sha256(canonical.encode()).hexdigest()



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
