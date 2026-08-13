"""Single shared payload-cost authority for AgentReview v1 chunk planning.

`semantic_chunker.build_semantic_chunk_plan` (the planner) and
`chunk_payload_builder.build_chunk_payloads` (the builder) both call into this
module for: canonical path identity, diff parsing, the context-construction
functions that determine a chunk's real payload shape, and the terminal
hunk-preserving size projection itself. Splitting this cost authority into two
independently-maintained formulas is exactly the defect this module exists to
make structurally impossible (AgentEscala#774, aiops-orchestrator#225).

Soundness argument for `project_min_hunk_preserving_chars`
------------------------------------------------------------
`chunk_payload_builder._apply_payload_budget` reduces an oversized payload
through a strictly ordered shrink ladder -- aux, then checks, then evidence,
then contracts, and only *last* the hunks themselves (truncating them to
stubs, then dropping them). The projection in this module constructs the
payload's *terminal* state directly: every optional context already at the
exact minimal form the ladder converges to, but with every hunk left
completely intact. If that state's canonical length fits the budget, the real
builder -- which always tries this identical state before it is ever allowed
to touch `chunk_hunks` -- is guaranteed to converge without shrinking a single
hunk. This is what makes
`actual_hunk_preserving_payload_chars <= projected_chars <= budget` (P1) hold
by construction rather than by an empirical constant.

The one input the planner cannot observe on its own is which `checks` /
`validation_evidence` document the builder will be given explicitly on its
command line. `assert_projection_inputs_bound` closes that gap: an externally
supplied document must be canonically equivalent (after the same redaction
transform the intake artifact loader already applied) to what the planner
could see embedded in the intake, or the builder fails closed rather than
silently reviewing a payload the planner never actually projected.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, get_args

from app.agent_review.chunk_response_contract import build_chunk_response_contract
from app.agent_review.redaction import RedactionState, redact_value, sanitize_artifact_value
from app.agent_review.schemas import ChunkPayload, ReviewIntake, SemanticGroup, TruncationMetadata


class PathIdentityError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


class ProjectionInputMismatchError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


# ---------------------------------------------------------------------------
# Path identity (rev.3 Amendment 4 / RED-21): the canonical identity used for
# packing and deduplication is never redacted. Redaction only ever produces a
# *display* form for publishable artifacts, so two distinct identities must
# never be allowed to collapse into the same display string undetected.
# ---------------------------------------------------------------------------


def canonical_repo_path(path: object) -> str:
    """Repository-relative path identity. Fails closed on anything that is
    not an unambiguous repo-relative path: not a string, empty, an absolute
    POSIX path, a drive-letter path, a `~`-relative path, or containing a
    `..` traversal segment.
    """
    if not isinstance(path, str):
        raise PathIdentityError("path_identity_invalid", f"path is not a string: {path!r}")
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        raise PathIdentityError("path_identity_empty", "empty path cannot be a repository-relative identity")
    if normalized.startswith("/") or normalized == "~" or normalized.startswith("~/"):
        raise PathIdentityError("path_identity_absolute", f"path is not repository-relative: {path!r}")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise PathIdentityError("path_identity_absolute", f"path is not repository-relative: {path!r}")
    segments = normalized.split("/")
    if any(segment == ".." for segment in segments):
        raise PathIdentityError("path_identity_traversal", f"path contains a traversal segment: {path!r}")
    collapsed = "/".join(segment for segment in segments if segment not in ("", "."))
    if not collapsed:
        raise PathIdentityError("path_identity_empty", "path collapses to empty after normalization")
    return collapsed


def sanitize_display_path(path: str) -> str:
    """Publishable display form of a path. Never used for identity/dedup."""
    normalized = path.replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.startswith("/") or normalized.startswith("~/"):
        return "[LOCAL_PATH_REDACTED]"
    if len(normalized) >= 2 and normalized[1] == ":":
        return "[LOCAL_PATH_REDACTED]"
    return normalized


def assert_no_sanitized_collision(identities: list[str]) -> None:
    """Two distinct canonical identities must never collapse to the same
    sanitized display string -- that would silently merge distinct files
    under one published path. `canonical_repo_path` already rejects every
    identity shape that `sanitize_display_path` would otherwise redact to the
    single literal `[LOCAL_PATH_REDACTED]`, so this is a defensive,
    independently-checked invariant, not the primary enforcement point.
    """
    seen: dict[str, str] = {}
    for identity in identities:
        display = sanitize_display_path(identity)
        if display in seen and seen[display] != identity:
            raise PathIdentityError(
                "path_identity_collision",
                f"distinct path identities collapse to the same sanitized display form: {display!r}",
            )
        seen[display] = identity


# ---------------------------------------------------------------------------
# Canonical serialization -- the single authority both planner projection and
# builder emission measure length against.
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_len(payload: dict[str, Any]) -> int:
    return len(canonical_json(payload))


def materialize_payload(payload: dict[str, Any], *, truncation: TruncationMetadata) -> tuple[ChunkPayload, int]:
    model = ChunkPayload.model_validate({**payload, "truncation": truncation.model_dump(mode="json")})
    dumped = model.model_dump(mode="json")
    return model, canonical_len(dumped)


def stabilize_payload_truncation(
    payload: dict[str, Any],
    truncation: TruncationMetadata,
    *,
    max_iterations: int = 16,
) -> tuple[TruncationMetadata, int]:
    stable = truncation.model_copy(deep=True)
    emitted = stable.emitted_chars
    for _ in range(max_iterations):
        stable.emitted_chars = emitted
        _, current_len = materialize_payload(payload, truncation=stable)
        if current_len == emitted:
            stable.emitted_chars = current_len
            return stable, current_len
        emitted = current_len
    stable.emitted_chars = emitted
    return stable, emitted


# ---------------------------------------------------------------------------
# Diff parsing (single authority; the planner needs real hunk text to size
# chunks honestly, the builder needs it to emit them).
# ---------------------------------------------------------------------------


def diff_by_file(intake: ReviewIntake) -> dict[str, str]:
    full_diff = artifact_text(intake, "full-diff")
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


def artifact_content(intake: ReviewIntake, name: str) -> dict[str, Any] | None:
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


def artifact_text(intake: ReviewIntake, name: str) -> str | None:
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


# ---------------------------------------------------------------------------
# Context construction -- moved from chunk_payload_builder verbatim (module
# only, signatures loosened from `chunk: SemanticChunk` to the raw fields
# actually read, so the planner can call these against a candidate partition
# before a SemanticChunk object exists). This is what makes the projection
# sound for `checks_context`/`evidence_context`/`contracts_context`: their
# minimal forms are NOT input-independent constants (rev.3 Amendment 1
# blocking finding), so the projection must call the *real* construction
# functions the builder will call, not a placeholder.
# ---------------------------------------------------------------------------


def contracts_context(
    intake: ReviewIntake,
    *,
    chunk_files: list[str],
    chunk_contracts: list[str],
    chunk_id: str,
    selected_contract_pack: str | None,
    semantic_group: str,
) -> tuple[dict[str, Any], list[str]]:
    profile = intake.target_profile if isinstance(intake.target_profile, dict) else {}
    contracts = _flatten_contract_rules(profile.get("domain_contracts"))
    packs = _flatten_review_packs(profile.get("review_packs"))
    relevance_keywords = _relevance_keywords(semantic_group)
    chunk_file_set = set(chunk_files)
    referenced_contracts = {item.split(":", 1)[1] for item in chunk_contracts if item.startswith("contract:") and ":" in item}
    include_all_contracts = "target_profile:domain_contracts" in chunk_contracts
    include_all_packs = "target_profile:review_packs" in chunk_contracts
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
        limitations.append(f"contracts_context_not_relevant:{chunk_id}")
    return (
        {
            "domain_contracts": sorted(filtered_contracts, key=lambda item: (item.get("id") or "", item.get("description") or "")),
            "review_packs": sorted(filtered_packs, key=lambda item: (item.get("id") or "", item.get("description") or "")),
        },
        limitations,
    )


def evidence_context(
    intake: ReviewIntake,
    *,
    chunk_files: list[str],
    validation_evidence: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    validation_document = (
        validation_evidence
        if isinstance(validation_evidence, dict)
        else artifact_content(intake, "validation-evidence-result")
    )
    chunk_file_set = set(chunk_files)
    validation_entries = _filter_validation_entries(
        validation_document,
        field_name="blocking_findings",
        chunk_files=chunk_file_set,
    )
    validation_risks = _filter_validation_entries(
        validation_document,
        field_name="validation_risks",
        chunk_files=chunk_file_set,
    )
    facts_for_synthesizer = _validation_facts(validation_document)
    lci = artifact_content(intake, "local-code-intelligence")
    tests = artifact_content(intake, "test-intelligence")
    lci_context, lci_limitations = _filter_lci(lci, chunk_files=chunk_file_set)
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
            "test_intelligence": _filter_test_intelligence(tests, chunk_files=chunk_file_set),
        },
        lci_limitations,
    )


def checks_context(
    checks: dict[str, Any] | None,
    *,
    intake: ReviewIntake,
    chunk_files: set[str],
) -> tuple[dict[str, Any], list[str]]:
    checks_document = checks if isinstance(checks, dict) else artifact_content(intake, "checks")
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
        value = sanitize_display_path(_clean_text(item.get(key)) or "")
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
        is_global = _is_global_item(item)
        item_paths = _paths_from_item(item)
        if not is_global and item_paths and not item_paths.intersection(chunk_files):
            continue
        sanitized = sanitize_artifact_value(item)
        if not isinstance(sanitized, dict):
            continue
        row = _normalize_validation_scope_fields(sanitized)
        if not row:
            continue
        key = canonical_json(row)
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return sorted(rows, key=canonical_json)


def _normalize_validation_scope_fields(item: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(item)
    for key in ("file_path", "file", "original_file", "path"):
        if key not in row:
            continue
        value = sanitize_display_path(_clean_text(row.get(key)) or "")
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
            "file_path": sanitize_display_path(_clean_text(item.get("file_path")) or ""),
            "path": sanitize_display_path(_clean_text(item.get("path")) or ""),
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
    paths = [sanitize_display_path(item) for item in value if isinstance(item, str) and item.strip()]
    return sorted({item for item in paths if item})


def _normalized_contract_patterns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    patterns = [sanitize_display_path(item.strip()) for item in value if isinstance(item, str) and item.strip()]
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


def _relevance_keywords(semantic_group: str) -> tuple[str, ...]:
    mapping = {
        "primary_backend_logic": ("backend", "service", "domain", "api"),
        "api_schema_contract": ("schema", "contract", "api", "model"),
        "frontend_ui": ("frontend", "ui", "component"),
        "tests": ("test", "coverage", "assert"),
        "workflow_aiops": ("workflow", "aiops", "pipeline"),
        "docs_changelog": ("docs", "changelog", "readme"),
        "suspicious_out_of_scope": ("secret", "prod", "deploy", "runtime"),
    }
    return mapping.get(semantic_group, tuple())


# ---------------------------------------------------------------------------
# Minimal-form constants for the two contexts whose terminal shrink state
# genuinely is input-independent (verified against `_shrink_aux_context` /
# `_shrink_contracts_context`: both discard all real content unconditionally).
# `checks_context` and `evidence_context` are deliberately NOT given constant
# minimal forms here -- their minimal forms retain real, unbounded fields
# (`status`, `validation_verdict`, `limitations`); using a placeholder for
# those was rev.2's soundness bug. `minimal_checks_context` /
# `minimal_evidence_context` below reduce the *real* constructed context to
# its real minimal form instead.
# ---------------------------------------------------------------------------

MINIMAL_AUX_CONTEXT: dict[str, Any] = {"status": "omitted_due_to_budget"}
MINIMAL_CONTRACTS_CONTEXT: dict[str, Any] = {"domain_contracts": [], "review_packs": []}


def minimal_checks_context(checks_ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "provided": checks_ctx.get("provided"),
        "status": checks_ctx.get("status"),
        "checks": [],
    }


def minimal_evidence_context(evidence_ctx: dict[str, Any]) -> dict[str, Any]:
    validation = evidence_ctx.get("validation_evidence") if isinstance(evidence_ctx, dict) else None
    return {
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


# ---------------------------------------------------------------------------
# Projection-input binding (rev.3 Amendment 1 / RED-19).
# ---------------------------------------------------------------------------


def assert_projection_inputs_bound(
    intake: ReviewIntake,
    *,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
) -> None:
    """The builder's explicit `--checks` / `--validation-evidence` documents
    must be canonically equivalent to what the planner could already observe
    embedded in the intake, or the projection the planner computed was never
    actually projecting the payload the builder is about to construct.
    Intake-embedded artifact content passed through `redact_value` at
    intake-build time (`artifact_loader.load_declared_artifacts`), so the
    external raw document is redacted with that same transform before
    comparison -- comparing a sanitized document to a raw one would report
    false mismatches on every run.
    """
    _assert_document_bound(intake, artifact_name="checks", external=checks)
    _assert_document_bound(intake, artifact_name="validation-evidence-result", external=validation_evidence)


def _assert_document_bound(
    intake: ReviewIntake,
    *,
    artifact_name: str,
    external: dict[str, Any] | None,
) -> None:
    if external is None:
        return
    if not isinstance(external, dict):
        return
    embedded = artifact_content(intake, artifact_name)
    if embedded is None:
        raise ProjectionInputMismatchError(
            "payload_projection_input_mismatch",
            f"external document supplied for {artifact_name!r} has no corresponding intake artifact "
            "the planner could observe when it projected chunk cost",
        )
    redacted_external = redact_value(copy.deepcopy(external), RedactionState())
    if canonical_json(redacted_external) != canonical_json(embedded):
        raise ProjectionInputMismatchError(
            "payload_projection_input_mismatch",
            f"external document supplied for {artifact_name!r} diverges from the intake-embedded artifact "
            "the planner projected chunk cost against",
        )


# ---------------------------------------------------------------------------
# File status/summary lookup (single authority; was
# chunk_payload_builder._file_context_map). The projection needs this to
# emit each candidate file's REAL status/summary, not a placeholder --
# `chunk_context.files` is never touched by the shrink ladder, so a
# placeholder shorter than the real value would under-estimate the floor.
# ---------------------------------------------------------------------------


def file_context_map(intake: ReviewIntake) -> dict[str, dict[str, Any]]:
    file_context = artifact_content(intake, "file-diff-context")
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


# ---------------------------------------------------------------------------
# Artifact-state limitations (single authority; was
# pr_brief._artifact_state_limitations / ._declared_requiredness). A pure
# function of `intake` alone, so the planner can compute the exact
# contribution to `brief.limitations` this produces, not approximate it.
# ---------------------------------------------------------------------------


def _declared_requiredness(intake: ReviewIntake) -> dict[str, bool]:
    profile = intake.target_profile if isinstance(intake.target_profile, dict) else {}
    declarations = profile.get("artifacts")
    if not isinstance(declarations, list):
        return {}
    requiredness: dict[str, bool] = {}
    for declaration in declarations:
        if not isinstance(declaration, dict):
            continue
        name = _clean_text(declaration.get("name"))
        if name is None:
            continue
        requiredness[name] = bool(declaration.get("required", False))
    return requiredness


def artifact_state_limitations(intake: ReviewIntake) -> list[str]:
    requiredness = _declared_requiredness(intake)
    limitations: list[str] = []
    for status in intake.artifact_status:
        if status.status == "missing":
            if status.name not in requiredness:
                limitations.append(f"artifact_missing:{status.name}")
            elif requiredness[status.name]:
                limitations.append(f"required_artifact_missing:{status.name}")
            else:
                limitations.append(f"optional_artifact_missing:{status.name}")
        elif status.status in {"invalid", "degraded"}:
            limitations.append(f"artifact_invalid:{status.name}")
    return limitations


# ---------------------------------------------------------------------------
# Review identity/metadata resolution (single authority; was
# pr_brief._review_metadata and its private helpers). `build_pr_brief` and
# the v1 planner's cost projection both call this, so a chunk's projected
# `target` / `brief.review` fields are the real values `pr_brief` will
# later produce, not an approximate placeholder -- `target`/`brief` are
# never touched by the shrink ladder either.
# ---------------------------------------------------------------------------


class ReviewIdentityConflictError(ValueError):
    def __init__(self, field_name: str, message: str) -> None:
        super().__init__(message)
        self.error_class = "review_identity_conflict"
        self.field_name = field_name
        self.message = message


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def find_key(document: Any, key: str) -> Any:
    if isinstance(document, dict):
        if key in document:
            return document[key]
        for value in document.values():
            found = find_key(value, key)
            if found is not None:
                return found
    if isinstance(document, list):
        for value in document:
            found = find_key(value, key)
            if found is not None:
                return found
    return None


def artifact_identity_candidates(artifacts: Any, key: str) -> list[tuple[str, Any]]:
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


def resolve_identity_value(
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
        raise ReviewIdentityConflictError(field_name, f"conflicting review identity for {field_name}: {details}")
    if unique_values:
        return unique_values[0]
    return None


def first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def resolve_review_metadata(
    *,
    intake: ReviewIntake,
    chunk_plan_target_repo: str,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    target_repo = resolve_identity_value(
        "target_repo",
        [
            ("intake.target_repo", intake.target_repo),
            ("chunk_plan.target_repo", chunk_plan_target_repo),
            ("intake.target_profile.target_repo", find_key(intake.target_profile, "target_repo")),
            ("checks.target_repo", find_key(checks, "target_repo")),
            ("validation_evidence.target_repo", find_key(validation_evidence, "target_repo")),
            *artifact_identity_candidates(intake.artifacts, "target_repo"),
        ],
        coerce=_clean_text,
    )
    if target_repo is None:
        raise ReviewIdentityConflictError("target_repo", "missing required review identity field: target_repo")

    pr_number = resolve_identity_value(
        "pr_number",
        [
            ("checks.pr_number", find_key(checks, "pr_number")),
            ("validation_evidence.pr_number", find_key(validation_evidence, "pr_number")),
            *artifact_identity_candidates(intake.artifacts, "pr_number"),
        ],
        coerce=coerce_int,
    )
    commit_sha = resolve_identity_value(
        "commit_sha",
        [
            ("checks.commit_sha", find_key(checks, "commit_sha")),
            ("validation_evidence.commit_sha", find_key(validation_evidence, "commit_sha")),
            *artifact_identity_candidates(intake.artifacts, "commit_sha"),
        ],
        coerce=_clean_text,
    )

    mode = first_non_empty(_clean_text(find_key(intake.artifacts, "review_mode")))
    contract_pack = first_non_empty(
        _clean_text(find_key(intake.artifacts, "contract_pack")),
        _clean_text(find_key(intake.artifacts, "pack")),
    )

    return {
        "target_repo": target_repo,
        "pr_number": pr_number,
        "commit_sha": commit_sha,
        "review_mode": mode,
        "contract_pack": contract_pack,
        "warnings": [],
    }


# ---------------------------------------------------------------------------
# Optional-artifact loading with its reason code (single authority; was
# aiops-review-build-payloads.py's private _load_optional_json). Both CLIs
# that can supply --checks/--validation-evidence use this, so the planner
# and builder observe the identical optional_artifact_missing/_invalid
# contribution to brief.limitations for the same input.
# ---------------------------------------------------------------------------


def load_optional_json_with_limitation(path: Any, name: str) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, [f"optional_artifact_missing:{name}"]
    resolved = Path(path)
    if not resolved.exists():
        return None, [f"optional_artifact_missing:{name}"]
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, [f"optional_artifact_invalid:{name}"]
    if not isinstance(raw, dict):
        return None, [f"optional_artifact_invalid:{name}"]
    return raw, []


# ---------------------------------------------------------------------------
# Worst-case chunk_id placeholder. Final chunk numbering is only known once
# `max_blocks` selection (rev.3 SS10) has finished choosing which candidate
# partitions survive, which happens after every candidate has already been
# projected. Using the longest possible valid id keeps the projection
# conservative (over-, never under-, estimating this term) without requiring
# a second projection pass once numbering is final.
# ---------------------------------------------------------------------------

_MAX_CHUNK_INDEX_DIGITS = 3  # supports up to 999 chunks in one plan
_LONGEST_GROUP_NAME = max(get_args(SemanticGroup), key=len)
WORST_CASE_CHUNK_ID = "chunk-" + ("9" * _MAX_CHUNK_INDEX_DIGITS) + "-" + _LONGEST_GROUP_NAME


# ---------------------------------------------------------------------------
# The shared projection (P1).
# ---------------------------------------------------------------------------


def project_min_hunk_preserving_chars(
    *,
    intake: ReviewIntake,
    chunk_files: list[str],
    chunk_contracts: list[str],
    semantic_group: str,
    target: dict[str, Any],
    brief_target: dict[str, Any],
    brief_review: dict[str, Any],
    brief_required_files: list[str],
    brief_limitations: list[str],
    selected_contract_pack: str | None,
    checks: dict[str, Any] | None,
    validation_evidence: dict[str, Any] | None,
    hunks: dict[str, str],
    created_at: str | None,
) -> int:
    chunk_id = WORST_CASE_CHUNK_ID
    chunk_files_sorted = sorted(chunk_files)
    # chunk_context.files is never touched by the shrink ladder, so it must
    # carry the REAL status/summary from file-diff-context, not a
    # placeholder (P2-1): an arbitrarily long status/summary string would
    # otherwise make `projected <= budget` true while the real floor is
    # larger, and the builder would reach hunk reduction regardless of what
    # the planner claimed. This is the same lookup _build_chunk_payload uses.
    file_context = file_context_map(intake)
    display_files = []
    for path in chunk_files_sorted:
        context = file_context.get(path, {})
        display_files.append(
            {
                "path": sanitize_display_path(path),
                "status": _clean_text(context.get("status")) or "unknown",
                "summary": _clean_text(context.get("summary")),
            }
        )
    chunk_hunks_full: list[dict[str, str]] = []
    limitations: list[str] = []
    for path in chunk_files_sorted:
        hunk = hunks.get(path)
        if hunk:
            chunk_hunks_full.append({"path": sanitize_display_path(path), "hunk": hunk})
        else:
            limitations.append(f"chunk_diff_hunk_missing:{sanitize_display_path(path)}")

    contracts_ctx, contract_limitations = contracts_context(
        intake,
        chunk_files=chunk_files_sorted,
        chunk_contracts=chunk_contracts,
        chunk_id=chunk_id,
        selected_contract_pack=selected_contract_pack,
        semantic_group=semantic_group,
    )
    checks_ctx, check_limitations = checks_context(checks, intake=intake, chunk_files=set(chunk_files_sorted))
    evidence_ctx, evidence_limitations = evidence_context(
        intake,
        chunk_files=chunk_files_sorted,
        validation_evidence=validation_evidence,
    )
    del contracts_ctx  # only its minimal (constant) form is ever emitted
    limitations.extend(contract_limitations)
    limitations.extend(check_limitations)
    limitations.extend(evidence_limitations)

    payload_body = {
        "chunk_id": chunk_id,
        "semantic_group": semantic_group,
        "order_index": 0,
        "target": dict(target),
        "brief": {
            **brief_target,
            "review_mode": brief_review.get("mode"),
            "contract_pack": brief_review.get("contract_pack"),
            "required_files": list(brief_required_files),
            "limitations": list(brief_limitations),
        },
        "chunk_context": {
            "files": display_files,
            "chunk_hunks": chunk_hunks_full,
            "contracts_context": dict(MINIMAL_CONTRACTS_CONTEXT),
            "evidence_context": minimal_evidence_context(evidence_ctx),
            "checks_context": minimal_checks_context(checks_ctx),
            "aux_context": dict(MINIMAL_AUX_CONTEXT),
        },
        "coverage": {
            "declared_coverage": "complete",
            "files_in_chunk": [item["path"] for item in display_files],
            "chunk_file_count": len(display_files),
            "hunks_included": len(chunk_hunks_full),
            "chunk_plan_limitations": [],
        },
        "response_contract": build_chunk_response_contract(chunk_id=chunk_id, semantic_group=semantic_group),
        "warnings": [],
        "limitations": _dedupe(limitations),
        "created_at": created_at,
    }

    sanitized = sanitize_artifact_value(payload_body)

    # The real shrink loop only ever measures an in-progress payload with
    # `applied=True` and `truncation_reason="max_chars_exceeded"` baked in --
    # both add real characters that a naive `applied=False` measurement of
    # the same minimal content would miss, which is precisely how an earlier
    # revision of this projection under-estimated the real floor and let
    # `chunk_hunks_reduced` still fire at the projected budget. Every
    # non-hunk section is marked "omitted" here regardless of whether that
    # section actually had anything to shrink (`checks_context`, e.g., is
    # already at its floor whenever no checks document applies): a longer
    # `omitted_sections`/`coverage_impact` list can only ever make the
    # measured length larger, so this stays a safe over-estimate rather than
    # requiring the projection to replay the real loop's exact step order.
    # `original_chars` uses a fixed, generously-sized placeholder for the
    # same reason -- it is embedded in the very payload being measured, and
    # a placeholder with at least as many digits as any realistic real value
    # keeps that field's contribution conservative too.
    worst_case_truncation = TruncationMetadata(
        applied=True,
        original_chars=999_999_999,
        emitted_chars=0,
        omitted_sections=["aux_context", "checks_context", "evidence_context", "contracts_context"],
        truncation_reason="max_chars_exceeded",
        coverage_impact=[
            "auxiliary_context_reduced",
            "checks_context_reduced",
            "evidence_context_reduced",
            "contracts_context_reduced",
        ],
    )
    # Note on soundness: a real build whose FULL, untouched payload already
    # fits the budget never enters the shrink loop at all, so hunks survive
    # there unconditionally regardless of this number -- `projected_chars`
    # only has to bound the loop's non-hunk-shrunk floor (the state
    # immediately before `_shrink_chunk_hunks` could ever be reached), which
    # is exactly what `worst_case_truncation` measures. Taking a further max
    # against the untouched form would only make already-cheap chunks look
    # artificially expensive whenever their full, unshrunk context costs more
    # than this floor -- working directly against the packing improvement
    # this projection exists to enable.
    _, projected_chars = stabilize_payload_truncation(sanitized, worst_case_truncation)
    return projected_chars


# ---------------------------------------------------------------------------
# Tiny generic accessors -- duplicated per-module by convention in this
# package (semantic_chunker, chunk_payload_builder, pr_brief and telemetry
# each already keep their own copies of `_dedupe` et al. rather than sharing
# a grab-bag utility module).
# ---------------------------------------------------------------------------


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
