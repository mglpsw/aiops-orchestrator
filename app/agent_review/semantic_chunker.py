"""Deterministic Semantic Chunk Planner for sanitized AgentReview intake."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from app.agent_review import payload_cost_model
from app.agent_review.redaction import RedactionState, redact_text
from app.agent_review.schemas import (
    INTAKE_SCHEMA,
    ReviewIntake,
    SemanticChunk,
    SemanticChunkPlan,
    SemanticGroup,
    utc_now_iso,
)


GROUP_PRIORITY: list[SemanticGroup] = [
    "suspicious_out_of_scope",
    "api_schema_contract",
    "primary_backend_logic",
    "workflow_aiops",
    "frontend_ui",
    "tests",
    "docs_changelog",
    "unknown",
]

FILE_DIFF_ALIASES = {
    "file-diff-context",
    "file-diff-context.json",
}

KNOWN_ARTIFACT_REFS = {
    "file-diff-context": "artifact:file-diff-context",
    "file-diff-context.json": "artifact:file-diff-context",
    "checks": "artifact:checks",
    "checks.json": "artifact:checks",
    "local-code-intelligence": "artifact:local-code-intelligence",
    "local-code-intelligence.json": "artifact:local-code-intelligence",
}

SUSPICIOUS_MARKERS = (
    ".env",
    "secret",
    "secrets",
    "prod",
    "production",
    "deploy",
    "deployment",
    "systemd",
    "docker",
    "compose",
    "ssh",
)

# Bounded, config-level placeholders for fields the planner cannot yet know
# exactly (they are only resolved once `pr_brief.build_pr_brief` runs, later
# in the same pipeline). All three are fixed-shape by construction --
# `commit_sha` is always a 40-character git SHA, `pr_number` is a small
# integer, and `review_mode`/`contract_pack` are short operator-configured
# identifiers, not attacker- or diff-controlled content -- so a placeholder
# of representative length keeps the projection sound without needing to
# replicate `pr_brief._review_metadata`'s full identity-resolution logic
# here. This is unlike `must_review_files`, which genuinely is unbounded
# diff-derived content and is therefore computed exactly (see
# `_required_files_for_projection`).
_PLACEHOLDER_COMMIT_SHA = "f" * 40
_PLACEHOLDER_PR_NUMBER = 999_999_999
_PLACEHOLDER_BRIEF_REVIEW: dict[str, Any] = {"mode": "full", "contract_pack": None}


class IntakeValidationError(ValueError):
    pass


def load_intake(path: Path | str) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise IntakeValidationError("intake_json_invalid") from exc
    if not isinstance(raw, dict):
        raise IntakeValidationError("intake_not_object")
    return raw


@dataclass
class _Candidate:
    group: SemanticGroup
    partition_index: int
    files: list[str]
    contains_must_review: bool


@dataclass
class _PackResult:
    chunks: list[SemanticChunk] = field(default_factory=list)
    files_covered: list[str] = field(default_factory=list)
    files_not_covered: list[str] = field(default_factory=list)
    plan_limitations: list[str] = field(default_factory=list)


def build_semantic_chunk_plan(
    intake: dict[str, Any],
    *,
    max_blocks: int = 6,
    max_chars_per_block: int = 24_000,
    checks: dict[str, Any] | None = None,
    validation_evidence: dict[str, Any] | None = None,
) -> SemanticChunkPlan:
    limitations = validate_intake_contract(intake)
    target_repo = str(intake.get("target_repo", "unknown"))
    created_at = _resolve_created_at(intake)
    artifacts = intake.get("artifacts")
    artifact_status = intake.get("artifact_status")

    if not isinstance(artifacts, dict) or not isinstance(artifact_status, list):
        raise IntakeValidationError("intake_invalid")

    files, extraction_limitations = extract_files_from_intake(intake)
    limitations.extend(extraction_limitations)

    if not files:
        return SemanticChunkPlan(
            target_repo=target_repo,
            max_parallel_blocks=max_blocks,
            chunks=[],
            files_covered=[],
            files_partially_covered=[],
            files_not_covered=[],
            limitations=_dedupe([*limitations, "file_context_missing"]),
            status="degraded",
            created_at=created_at,
        )

    canonical_files, identity_not_covered, identity_limitations = _canonicalize_files(files)
    limitations.extend(identity_limitations)

    try:
        payload_cost_model.assert_no_sanitized_collision(canonical_files)
    except payload_cost_model.PathIdentityError as exc:
        # A collision means identity/dedup cannot be trusted for this intake
        # at all -- fail the whole plan closed rather than silently merge
        # two distinct files under one published path.
        limitations.append(exc.error_class)
        return SemanticChunkPlan(
            target_repo=target_repo,
            max_parallel_blocks=max_blocks,
            chunks=[],
            files_covered=[],
            files_partially_covered=[],
            files_not_covered=_dedupe([*identity_not_covered, *canonical_files]),
            limitations=_dedupe(limitations),
            status="degraded",
            created_at=created_at,
        )

    try:
        review_intake = ReviewIntake.model_validate(intake)
    except ValidationError as exc:
        raise IntakeValidationError("intake_invalid") from exc

    hunks = payload_cost_model.diff_by_file(review_intake)
    # Fail fast, mirroring the builder's own mandatory check
    # (chunk_payload_builder.build_chunk_payloads): if the operator passed
    # --checks / --validation-evidence explicitly here too, for projection
    # precision, it must already be canonically bound to what the intake
    # embeds -- catching a divergence at planning time is strictly better
    # than discovering it only once the builder's hard guard blocks routing.
    payload_cost_model.assert_projection_inputs_bound(
        review_intake,
        checks=checks,
        validation_evidence=validation_evidence,
    )
    effective_checks = (
        checks if isinstance(checks, dict) else payload_cost_model.artifact_content(review_intake, "checks")
    )
    effective_validation_evidence = (
        validation_evidence
        if isinstance(validation_evidence, dict)
        else payload_cost_model.artifact_content(review_intake, "validation-evidence-result")
    )
    required_files = set(_required_files_for_projection(review_intake))
    contract_refs = _contract_refs(intake)
    available_refs = _artifact_refs(artifacts)

    # A must_review file with no observable hunk material (binary, or the
    # diff producer never emitted one) can never actually reach semantic
    # review no matter how it is packed -- fail closed instead of reporting
    # it as covered by an empty payload entry (rev.3 SS11 / RED-16).
    hunk_unavailable_must_review = sorted(
        path for path in canonical_files if path in required_files and not hunks.get(path)
    )
    hunk_unavailable_set = set(hunk_unavailable_must_review)
    for path in hunk_unavailable_must_review:
        limitations.append(f"must_review_hunk_unavailable:{path}")
    packable_files = [path for path in canonical_files if path not in hunk_unavailable_set]

    grouped = group_files_by_semantics(packable_files)

    def project_chunk_cost(group: SemanticGroup, candidate_files: list[str], brief_limitations: list[str]) -> int:
        return payload_cost_model.project_min_hunk_preserving_chars(
            intake=review_intake,
            chunk_files=candidate_files,
            chunk_contracts=contract_refs,
            semantic_group=group,
            target=_placeholder_target(target_repo),
            brief_target=_placeholder_target(target_repo),
            brief_review=_PLACEHOLDER_BRIEF_REVIEW,
            brief_required_files=sorted(required_files),
            brief_limitations=brief_limitations,
            selected_contract_pack=None,
            checks=effective_checks,
            validation_evidence=effective_validation_evidence,
            hunks=hunks,
            created_at=created_at,
        )

    # Amendment 2 (rev.3): packing decisions feed `chunk_plan.limitations`,
    # which is itself embedded in every projected chunk's `brief.limitations`
    # -- the cost that decides packing depends on packing's own output. This
    # loop resolves that self-dependency by monotone ascent: the accumulated
    # limitation set only ever grows (never shrinks), over a domain bounded
    # by the number of files and groups, so it reaches a fixed point in a
    # derivable, finite number of iterations.
    max_iterations = len(canonical_files) * 2 + len(GROUP_PRIORITY) * 2 + 4
    accumulated = sorted(set(limitations))
    pack_result: _PackResult | None = None
    for _ in range(max_iterations):
        pack_result = _pack_all_groups(
            grouped,
            required_files=required_files,
            max_blocks=max_blocks,
            max_chars_per_block=max_chars_per_block,
            available_refs=available_refs,
            contract_refs=contract_refs,
            project_chunk_cost=lambda group, candidate, _acc=accumulated: project_chunk_cost(group, candidate, _acc),
        )
        new_accumulated = sorted(set(accumulated) | set(pack_result.plan_limitations))
        if new_accumulated == accumulated:
            break
        accumulated = new_accumulated
    else:
        raise IntakeValidationError("plan_cost_fixed_point_not_converged")

    assert pack_result is not None
    limitations.extend(pack_result.plan_limitations)
    files_not_covered = _dedupe([*identity_not_covered, *hunk_unavailable_must_review, *pack_result.files_not_covered])

    status = _plan_status(
        intake_status=str(intake.get("status", "")),
        limitations=limitations,
        files_partially_covered=[],
        files_not_covered=files_not_covered,
    )

    return SemanticChunkPlan(
        target_repo=target_repo,
        max_parallel_blocks=max_blocks,
        chunks=pack_result.chunks,
        files_covered=_dedupe(pack_result.files_covered),
        files_partially_covered=[],
        files_not_covered=files_not_covered,
        limitations=_dedupe(limitations),
        status=status,
        created_at=created_at,
    )


def _pack_all_groups(
    grouped: dict[SemanticGroup, list[str]],
    *,
    required_files: set[str],
    max_blocks: int,
    max_chars_per_block: int,
    available_refs: list[str],
    contract_refs: list[str],
    project_chunk_cost: Callable[[SemanticGroup, list[str]], int],
) -> _PackResult:
    candidates: list[_Candidate] = []
    plan_limitations: list[str] = []
    oversize_not_covered: list[str] = []

    for group in GROUP_PRIORITY:
        group_files = grouped.get(group, [])
        if not group_files:
            continue
        bins, oversize = _pack_group_ffd(
            group_files,
            budget=max_chars_per_block,
            project_cost=lambda candidate_files, _group=group: project_chunk_cost(_group, candidate_files),
        )
        for path in oversize:
            reason = "must_review_payload_oversize" if path in required_files else "payload_oversize"
            plan_limitations.append(f"{reason}:{path}")
            oversize_not_covered.append(path)
        for partition_index, bin_files in enumerate(bins):
            candidates.append(
                _Candidate(
                    group=group,
                    partition_index=partition_index,
                    files=sorted(bin_files),
                    contains_must_review=any(path in required_files for path in bin_files),
                )
            )

    # Two-phase max_blocks allocation (rev.3 SS10): generate every candidate
    # partition first, ignoring max_blocks entirely, then select globally --
    # a candidate chunk that itself contains a must_review file is always
    # preferred over one that does not, cutting across group boundaries.
    ordered = sorted(
        candidates,
        key=lambda c: (
            0 if c.contains_must_review else 1,
            GROUP_PRIORITY.index(c.group),
            c.partition_index,
            c.files[0] if c.files else "",
        ),
    )
    selected = ordered[:max_blocks]
    selected_keys = {(c.group, c.partition_index) for c in selected}
    dropped = [c for c in ordered if (c.group, c.partition_index) not in selected_keys]
    if dropped:
        plan_limitations.append("max_blocks_exceeded")
        for candidate in dropped:
            plan_limitations.append(f"chunk_plan_budget_exhausted:{candidate.group}")

    # Emission order is independent of selection order: GROUP_PRIORITY then
    # each group's own partition sequence, matching the plan's existing
    # canonical chunk ordering.
    selected_sorted = sorted(selected, key=lambda c: (GROUP_PRIORITY.index(c.group), c.partition_index))

    chunks: list[SemanticChunk] = []
    files_covered: list[str] = []
    for candidate in selected_sorted:
        chunks.append(
            SemanticChunk(
                chunk_id=f"chunk-{len(chunks) + 1:02d}-{candidate.group}",
                semantic_group=candidate.group,
                order_index=len(chunks),
                files=candidate.files,
                artifacts=_refs_for_group(candidate.group, available_refs),
                contracts=contract_refs,
                depends_on=[],
                coverage="complete",
                prompt_budget_chars=max_chars_per_block,
                estimated_chars=project_chunk_cost(candidate.group, candidate.files),
                limitations=[],
            )
        )
        files_covered.extend(candidate.files)

    files_not_covered = _dedupe([*oversize_not_covered, *[path for candidate in dropped for path in candidate.files]])
    return _PackResult(
        chunks=chunks,
        files_covered=_dedupe(files_covered),
        files_not_covered=files_not_covered,
        plan_limitations=_dedupe(plan_limitations),
    )


def _pack_group_ffd(
    files: list[str],
    *,
    budget: int,
    project_cost: Callable[[list[str]], int],
) -> tuple[list[list[str]], list[str]]:
    """First-fit-decreasing, ordered by each file's own *projected* cost as a
    singleton chunk (descending), tie-broken by canonical path. A file whose
    own singleton projection already exceeds the budget can never fit
    anywhere and is reported oversize rather than silently dropped. Every
    placement re-projects the FULL candidate chunk's cost -- never an
    additive per-file sum, since the payload envelope is shared and
    non-linear across the files in a chunk.
    """
    singleton_costs = {path: project_cost([path]) for path in files}
    oversize = sorted(path for path in files if singleton_costs[path] > budget)
    oversize_set = set(oversize)
    packable = [path for path in files if path not in oversize_set]
    ordered = sorted(packable, key=lambda path: (-singleton_costs[path], path))

    bins: list[list[str]] = []
    for path in ordered:
        placed = False
        for chunk_files in bins:
            candidate = sorted([*chunk_files, path])
            if project_cost(candidate) <= budget:
                chunk_files.append(path)
                placed = True
                break
        if not placed:
            bins.append([path])
    return [sorted(chunk_files) for chunk_files in bins], oversize


def _canonicalize_files(files: list[str]) -> tuple[list[str], list[str], list[str]]:
    valid: list[str] = []
    not_covered: list[str] = []
    limitations: list[str] = []
    for raw in files:
        try:
            valid.append(payload_cost_model.canonical_repo_path(raw))
        except payload_cost_model.PathIdentityError as exc:
            display = payload_cost_model.sanitize_display_path(raw) if isinstance(raw, str) else "[INVALID_PATH]"
            limitations.append(f"{exc.error_class}:{display}")
            not_covered.append(display)
    return _dedupe(valid), _dedupe(not_covered), _dedupe(limitations)


def _required_files_for_projection(review_intake: ReviewIntake) -> list[str]:
    file_context = payload_cost_model.artifact_content(review_intake, "file-diff-context")
    requirements = file_context.get("coverage_requirements") if isinstance(file_context, dict) else None
    if not isinstance(requirements, dict):
        return []
    raw = requirements.get("must_review_files")
    if not isinstance(raw, list):
        return []
    return _dedupe([str(item) for item in raw if isinstance(item, str) and item.strip()])


def _resolve_created_at(intake: dict[str, Any]) -> str:
    value = intake.get("created_at")
    if isinstance(value, str) and value.strip():
        return value
    return utc_now_iso()


def _placeholder_target(target_repo: str) -> dict[str, Any]:
    return {
        "repository": target_repo,
        "pr_number": _PLACEHOLDER_PR_NUMBER,
        "commit_sha": _PLACEHOLDER_COMMIT_SHA,
    }


def validate_intake_schema_envelope(intake: dict[str, Any]) -> list[str]:
    """Single authority for the intake ``schema_id``/``schema_version``
    envelope, reused by every loader that accepts an intake document
    (``final_synthesizer.load_intake``, ``quality_gate.load_intake``, and
    this module's own ``validate_intake_contract``) instead of each keeping
    its own partially-divergent check.

    Accepts exactly two shapes: the modern pair (``schema_id ==
    INTAKE_SCHEMA``, ``schema_version == 1``) or, during the compatibility
    window, the legacy bare form (no ``schema_id`` key, ``schema_version ==
    INTAKE_SCHEMA`` the descriptive string). Any other combination --
    including an unsupported integer version such as ``2``, an unknown
    ``schema_id``, or the hybrid a naive Pydantic default can silently
    reintroduce -- raises ``IntakeValidationError``. Returns any limitations
    accumulated along the way (currently only the legacy-form flag).
    """
    limitations: list[str] = []
    schema_id = intake.get("schema_id")
    schema_version = intake.get("schema_version")
    if schema_id is not None:
        if schema_id != INTAKE_SCHEMA:
            raise IntakeValidationError("intake_schema_id_invalid")
        if type(schema_version) is not int or schema_version != 1:
            # type(...) is int, not isinstance(...), deliberately: bool is a
            # subclass of int in Python, so isinstance(True, int) is True and
            # True == 1 -- an independent adversarial review of this same PR
            # found that a hand-crafted schema_version: true or 1.0 would
            # otherwise pass as if it were the canonical integer 1.
            raise IntakeValidationError("intake_schema_version_invalid")
    elif schema_version == INTAKE_SCHEMA:
        limitations.append("intake_schema_id_missing")
    else:
        # No schema_id at all, and schema_version isn't the descriptive
        # legacy string either -- there is no compatibility form for a
        # bare integer schema_version without schema_id (an unsupported
        # version such as 2 must not slip through here just because
        # schema_id happens to be absent; ReviewIntake's own default would
        # silently backfill schema_id afterwards, hiding the mismatch).
        raise IntakeValidationError("intake_schema_invalid")

    return limitations


def validate_intake_contract(intake: dict[str, Any]) -> list[str]:
    missing = [
        field_name
        for field_name in ("target_repo", "artifacts", "artifact_status", "status")
        if field_name not in intake
    ]
    if missing:
        raise IntakeValidationError(f"intake_missing:{','.join(missing)}")

    return validate_intake_schema_envelope(intake)


def extract_files_from_intake(intake: dict[str, Any]) -> tuple[list[str], list[str]]:
    artifacts = intake.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return [], ["artifacts_invalid"]

    primary_files = _extract_file_diff_context_files(artifacts)
    if primary_files:
        return _dedupe(primary_files), []

    fallback_files: list[str] = []
    for artifact in artifacts.values():
        fallback_files.extend(_files_from_artifact(artifact))
    if fallback_files:
        return _dedupe(fallback_files), ["file_context_fallback_used"]

    return [], ["file_context_missing"]


def group_files_by_semantics(files: list[str]) -> dict[SemanticGroup, list[str]]:
    grouped: dict[SemanticGroup, list[str]] = defaultdict(list)
    for file_path in files:
        grouped[classify_file(file_path)].append(file_path)
    return dict(grouped)


def classify_file(file_path: str) -> SemanticGroup:
    path = file_path.replace("\\", "/").lower()
    name = path.rsplit("/", 1)[-1]

    if any(marker in path for marker in SUSPICIOUS_MARKERS):
        return "suspicious_out_of_scope"
    if path.startswith("tests/") or "/tests/" in path or name.startswith("test_") or name.endswith("_test.py") or ".test." in name:
        return "tests"
    if path.startswith(".github/") or "workflow" in path or path.startswith("scripts/aiops") or ("scripts/" in path and "review" in name):
        return "workflow_aiops"
    if path.startswith("docs/") or name in {"readme", "readme.md", "changelog", "changelog.md"} or name.endswith(".md"):
        return "docs_changelog"
    if (
        path.startswith("frontend/src/")
        or "/components/" in path
        or "/pages/" in path
        or name.endswith((".jsx", ".tsx", ".css"))
    ):
        return "frontend_ui"
    if (
        "schema" in name
        or "schemas.py" in name
        or "models.py" in name
        or "pydantic" in path
        or "response_model" in path
        or path.startswith("backend/api/")
        or path.startswith("app/api/")
        or path.startswith("app/models/")
        or path.startswith("app/schemas/")
    ):
        return "api_schema_contract"
    if (
        path.startswith("backend/services/")
        or path.startswith("backend/models/")
        or path.startswith("backend/domain/")
        or path.startswith("backend/")
        or path.startswith("app/services/")
        or path.startswith("app/domain/")
        or (path.startswith("app/") and name.endswith(".py"))
    ):
        return "primary_backend_logic"
    return "unknown"


def _extract_file_diff_context_files(artifacts: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for name, artifact in artifacts.items():
        artifact_path = str(artifact.get("path", "")) if isinstance(artifact, dict) else ""
        normalized_name = _normalize_artifact_name(str(name))
        normalized_path = _normalize_artifact_name(artifact_path)
        if normalized_name in FILE_DIFF_ALIASES or normalized_path in FILE_DIFF_ALIASES:
            files.extend(_files_from_artifact(artifact))
    return files


def _files_from_artifact(artifact: Any) -> list[str]:
    if not isinstance(artifact, dict):
        return []
    content = artifact.get("content")
    if isinstance(content, dict):
        return _extract_files_list(content.get("files"))
    return []


def _extract_files_list(raw_files: Any) -> list[str]:
    if not isinstance(raw_files, list):
        return []
    files: list[str] = []
    for item in raw_files:
        if isinstance(item, str):
            files.append(_sanitize_output_string(item))
        elif isinstance(item, dict):
            for key in ("path", "file", "filename", "name"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    files.append(_sanitize_output_string(value))
                    break
    return [file for file in files if file]


def _plan_status(
    *,
    intake_status: str,
    limitations: list[str],
    files_partially_covered: list[str],
    files_not_covered: list[str],
) -> str:
    """Plan status is a statement about *coverage*, never about how many
    limitations were recorded (AgentEscala#675, Fix C).

    This deliberately does not test `limitations` for truthiness. Every
    limitation this module raises that actually costs coverage already has a
    structural counterpart in one of the lists checked below -- so nothing
    coverage-bearing is lost by dropping the truthiness test. What *is*
    dropped is the false `partial` that a purely informational limitation
    used to produce: `intake_schema_id_missing` (an intake-envelope fact) or
    `file_context_fallback_used` (a fallback that still yielded every file)
    would stamp a fully covered plan as partial, and
    `final_synthesizer._coverage` then republished it as
    `chunk_plan_status_partial` -- reading, downstream, as a second and
    independent coverage failure that had never happened.

    `files_partially_covered` is retained for wire/field compatibility, but
    the packer built for aiops-orchestrator#225 never populates it: a group
    that no longer fits one chunk is split across several (rev.3 SS8), and an
    individually oversize file is reported as not covered rather than
    partially covered (rev.3 SS3/SS11) -- coverage is binary now, covered or
    not, never silently degraded-but-included.
    """
    if "file_context_missing" in limitations or intake_status == "degraded":
        return "degraded"
    if files_not_covered:
        return "degraded"
    if files_partially_covered:
        return "partial"
    return "complete"


def _refs_for_group(group: SemanticGroup, refs: list[str]) -> list[str]:
    selected = [ref for ref in refs if ref in {"artifact:file-diff-context", "artifact:checks"}]
    if group in {"primary_backend_logic", "api_schema_contract"} and "artifact:local-code-intelligence" in refs:
        selected.append("artifact:local-code-intelligence")
    return _dedupe(selected)


def _artifact_refs(artifacts: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for name, artifact in artifacts.items():
        artifact_path = str(artifact.get("path", "")) if isinstance(artifact, dict) else ""
        for value in (str(name), artifact_path):
            ref = KNOWN_ARTIFACT_REFS.get(_normalize_artifact_name(value))
            if ref:
                refs.append(ref)
    return _dedupe(refs)


def _contract_refs(intake: dict[str, Any]) -> list[str]:
    profile = intake.get("target_profile")
    if isinstance(profile, dict) and profile.get("domain_contracts"):
        return ["target_profile:domain_contracts"]
    return []


def _sanitize_output_string(value: str) -> str:
    return redact_text(value, RedactionState())


def _normalize_artifact_name(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
