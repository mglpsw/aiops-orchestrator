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
import fnmatch
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


SOURCE_STATE_PRESENT_VALID = "PRESENT_VALID"
SOURCE_STATE_ABSENT = "ABSENT"
SOURCE_STATE_INVALID = "INVALID"

SUBTYPE_MALFORMED_SHAPE = "MALFORMED_SHAPE"
SUBTYPE_UNSUPPORTED_NONEMPTY = "UNSUPPORTED_NONEMPTY"
SUBTYPE_INVALID_IDENTITY = "INVALID_IDENTITY"
SUBTYPE_OTHER_TYPED_INVALID = "OTHER_TYPED_INVALID"

RELATION_STATE_RESOLVED = "RESOLVED"
RELATION_STATE_UNRESOLVED = "UNRESOLVED"
RELATION_STATE_NOT_REQUIRED = "NOT_REQUIRED_FOR_THIS_INPUT"

APPLICABILITY_APPLICABLE = "APPLICABLE"
APPLICABILITY_NOT_APPLICABLE = "NOT_APPLICABLE"

CRITICAL_CONTRACT_LIMITATION_PREFIXES: tuple[str, ...] = (
    "required_contract_context_lost:",
    "required_contract_pack_context_lost:",
    "unresolved_contract_binding:",
    "unresolved_contract_reference:",
    "orphan_contract_binding:",
    "invalid_source_contract:",
    "invalid_source_review_packs:",
    "malformed_contract_bindings:",
    "required_source_absent:",
    "selected_contract_pack_missing:",
)

REQUIRED_CONTEXT_LOSS_PREFIXES: tuple[str, ...] = (
    "required_contract_context_lost:",
    "required_contract_pack_context_lost:",
)


def is_required_context_loss(limitation: str) -> bool:
    return any(limitation.startswith(prefix) for prefix in REQUIRED_CONTEXT_LOSS_PREFIXES)


FORMAT_LEGACY_FLAT = "legacy_flat"
FORMAT_MODERN_MAPPING = "modern_mapping"


def detect_domain_contracts_format(document: Any) -> str:
    """Detect whether domain_contracts artifact follows legacy flat rules list or modern domain mapping."""
    if isinstance(document, list):
        return FORMAT_LEGACY_FLAT
    if isinstance(document, dict) and "rules" in document and isinstance(document.get("rules"), list):
        return FORMAT_LEGACY_FLAT
    return FORMAT_MODERN_MAPPING


def detect_review_packs_format(document: Any, extra_bindings: Any = None) -> str:
    """Detect whether review_packs follows legacy flat packs list or modern pack mapping."""
    if isinstance(document, list):
        return FORMAT_LEGACY_FLAT
    if isinstance(document, dict) and isinstance(document.get("packs"), list):
        return FORMAT_LEGACY_FLAT
    return FORMAT_MODERN_MAPPING




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


def canonical_repo_pattern(pattern: object) -> str:
    """Repository-relative *pattern* identity: GlobSyntax AND RepositoryBoundary at once.

    The boundary (not a string, empty, absolute, drive-letter, `~`-relative, `..`
    traversal) is `canonical_repo_path`'s -- it is structural and therefore
    already glob-transparent (`*`, `?`, `[...]` survive untouched) -- so there is
    exactly one boundary authority. On top of it a pattern must be a
    well-formed glob: no control characters and every `[` closed.
    """
    identity = canonical_repo_path(pattern)
    if any(ord(char) < 32 for char in identity):
        raise PathIdentityError("pattern_syntax_invalid", f"pattern contains a control character: {pattern!r}")
    open_at = identity.find("[")
    while open_at != -1:
        close_at = identity.find("]", open_at + 2)
        if close_at == -1:
            raise PathIdentityError("pattern_syntax_invalid", f"pattern has an unclosed character class: {pattern!r}")
        open_at = identity.find("[", close_at + 1)
    return identity


def sanitize_display_path(path: str) -> str:
    """Publishable display form of a path. Never used for identity/dedup,
    and never an admission authority (`DisplayPath` does not decide validity)."""
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


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")


def block_has_observable_textual_hunk(block: str) -> bool:
    """C8 (post-merge debt #205): a `diff_by_file(...)` value is any
    non-empty rendered git diff block for a path -- that includes a
    binary-only block ("Binary files a/x and b/x differ") and a
    metadata-only block (pure rename/mode change, no content diff), neither
    of which contains a single line of semantically reviewable, line-level
    content. `bool(block)` conflates all three with an observable textual
    hunk. The one structural signal that reliably distinguishes a real
    unified-diff hunk is its header line (`@@ -l,s +l,s @@`); content lines
    are always prefixed with `+`/`-`/` ` so this can't false-positive on a
    changed line that merely contains the literal text `@@`.
    """
    return any(_HUNK_HEADER_RE.match(line) for line in block.splitlines())


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


# P2-8 (PR #227 exact-HEAD adversarial review, round 3): the projection
# previously passed the *canonical identity* set as `brief_required_files`,
# which fixes must_review membership but under-projects whenever the real
# wire spelling (whatever the file-diff-context artifact actually wrote,
# e.g. "./a.py") is longer than its canonicalized form -- `pr_brief.
# _coverage_requirements`/`_coverage_summary` never canonicalizes this
# field; `PRBrief.coverage.required_files` is the ordered-unique *wire*
# representation, verbatim, and that is exactly what `_build_chunk_payload`
# embeds via `pr_brief.coverage.get("required_files")`. This is the single
# shared authority for that exact byte representation -- both pr_brief.py's
# real output and this projection must derive it the same way. Membership/
# priority/oversize classification must use the separate, canonicalized
# identity view instead (see semantic_chunker._required_files_for_projection),
# never this one.
def required_files_wire(review_intake: ReviewIntake) -> list[str]:
    file_context = artifact_content(review_intake, "file-diff-context")
    requirements = file_context.get("coverage_requirements") if isinstance(file_context, dict) else None
    if not isinstance(requirements, dict):
        return []
    return _dedupe(_string_list(requirements.get("must_review_files")))


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


def parse_contract_refs(intake_data: dict[str, Any] | ReviewIntake) -> tuple[list[str], list[str]]:
    """Extracts and validates explicit contract references from intake and target_profile.
    Enforces that 'contracts' and 'contract_refs' must be list[str] of nonempty identities.
    Returns (refs, limitations).
    """
    raw_intake = intake_data.model_dump(mode="json") if hasattr(intake_data, "model_dump") else intake_data
    if not isinstance(raw_intake, dict):
        return [], []

    refs: list[str] = []
    limitations: list[str] = []

    profile = raw_intake.get("target_profile")
    if isinstance(profile, dict):
        if profile.get("domain_contracts"):
            refs.append("target_profile:domain_contracts")
        if profile.get("review_packs"):
            refs.append("target_profile:review_packs")

        for key in ("contracts", "contract_refs"):
            if key in profile and profile[key] is not None:
                val = profile[key]
                if not isinstance(val, list):
                    limitations.append("unresolved_contract_reference:MALFORMED_CONTAINER")
                else:
                    for elem in val:
                        if not isinstance(elem, str):
                            limitations.append("unresolved_contract_reference:MALFORMED_MEMBER")
                        elif not elem.strip():
                            limitations.append("unresolved_contract_reference:EMPTY_IDENTITY")
                        else:
                            refs.append(elem.strip())

    for key in ("contracts", "contract_refs"):
        if key in raw_intake and raw_intake[key] is not None:
            val = raw_intake[key]
            if not isinstance(val, list):
                limitations.append("unresolved_contract_reference:MALFORMED_CONTAINER")
            else:
                for elem in val:
                    if not isinstance(elem, str):
                        limitations.append("unresolved_contract_reference:MALFORMED_MEMBER")
                    elif not elem.strip():
                        limitations.append("unresolved_contract_reference:EMPTY_IDENTITY")
                    else:
                        refs.append(elem.strip())

    return _dedupe(refs), _dedupe(limitations)


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
    raw_domain_contracts = profile.get("domain_contracts")
    raw_review_packs = profile.get("review_packs")
    extra_bindings = profile.get("contract_bindings")

    contracts, c_state, c_sub, c_limits = normalize_domain_contracts(raw_domain_contracts)
    packs, bindings, p_state, p_sub, p_limits = normalize_review_packs(raw_review_packs, extra_bindings=extra_bindings)
    c_format = detect_domain_contracts_format(raw_domain_contracts)
    p_format = detect_review_packs_format(raw_review_packs, extra_bindings=extra_bindings)

    limitations: list[str] = []
    limitations.extend(c_limits)
    limitations.extend(p_limits)
    _, ref_limits = parse_contract_refs(intake)
    limitations.extend(ref_limits)

    has_invalid_source = (c_state == SOURCE_STATE_INVALID or p_state == SOURCE_STATE_INVALID)
    has_malformed_bindings = any(lim.startswith("malformed_contract_bindings:") for lim in limitations)

    relevance_keywords = _relevance_keywords(semantic_group)
    chunk_file_set = set(chunk_files)
    include_all_contracts = "target_profile:domain_contracts" in chunk_contracts
    include_all_packs = "target_profile:review_packs" in chunk_contracts

    has_unresolved = False
    referenced_contracts: set[str] = set()
    for item in chunk_contracts:
        if item.startswith("contract:") and ":" in item:
            ref_id = item.split(":", 1)[1]
            if not ref_id.strip():
                limitations.append("unresolved_contract_reference:EMPTY_IDENTITY")
                has_unresolved = True
            else:
                referenced_contracts.add(ref_id.strip())

    contracts_by_id = {c["id"]: c for c in contracts if c.get("id")}
    packs_by_id = {p["id"]: p for p in packs if p.get("id")}

    # An explicit `contract:<id>` that names a LEGACY pack is the baseline's pack reference
    # (resolved, not a contract identity reference); everything else is a contract reference.
    legacy_pack_ref_hits = (
        {ref for ref in referenced_contracts if ref in packs_by_id} if p_format == FORMAT_LEGACY_FLAT else set()
    )
    explicit_contract_refs = referenced_contracts - (legacy_pack_ref_hits - set(contracts_by_id))

    # Required source: any DECLARED contract identity reference requires the contract source.
    # SourceAbsent != RelationUnresolved -- both facts are preserved.
    declared_refs: set[str] = set()
    for origin_refs in _declared_contract_ref_origins(
        packs, bindings, explicit_contract_refs, pack_mode=p_format
    ).values():
        declared_refs |= origin_refs
    if declared_refs and c_state == SOURCE_STATE_ABSENT:
        limitations.append("required_source_absent:domain_contracts")

    # Resolve EffectiveContractRefs(pack_id)
    for p in packs:
        eff_refs: set[str] = set()
        if p_format == FORMAT_MODERN_MAPPING:
            dc = p.get("domain_contract")
            if dc:
                eff_refs.add(dc)
            for ref in bindings.get(p.get("id", ""), []):
                eff_refs.add(ref)
        p["effective_contracts"] = sorted(eff_refs)
        for ref in p["effective_contracts"]:
            if ref not in contracts_by_id:
                limitations.append(f"unresolved_contract_binding:{p.get('id', 'unknown')}:{ref}")
                has_unresolved = True

    # Check orphan bindings
    for bound_pid in bindings:
        if bound_pid not in packs_by_id:
            limitations.append(f"orphan_contract_binding:{bound_pid}")
            has_unresolved = True

    # Explicit contract reference totality (Finding 4178603200)
    for ref in sorted(explicit_contract_refs - set(contracts_by_id.keys())):
        limitations.append(f"unresolved_contract_reference:{ref}")
        has_unresolved = True

    # Evaluate applicable packs -- one mode, one predicate set.
    selected_pack = selected_contract_pack.strip() if selected_contract_pack else ""
    applicable_packs: list[dict[str, Any]] = []
    selected_pack_matched = False
    pack_matcher = modern_pack_matches_selected if p_format == FORMAT_MODERN_MAPPING else legacy_pack_matches_selected

    for pack in packs:
        matches = False
        is_selected = False
        if selected_pack and pack_matcher(pack, selected_pack):
            matches = True
            selected_pack_matched = True
            is_selected = True
        elif p_format == FORMAT_LEGACY_FLAT:
            matches = _legacy_pack_applies(
                pack,
                include_all=include_all_packs,
                referenced=referenced_contracts,
                keywords=relevance_keywords,
            )
        else:
            matches = _contract_matches_chunk(pack, chunk_files=chunk_file_set, format=FORMAT_MODERN_MAPPING)

        if matches:
            pack_entry = dict(pack)
            if is_selected:
                pack_entry["_explicitly_selected"] = True
            applicable_packs.append(pack_entry)

    # Finding 4178603183: emit missing limitation whenever selected pack cannot be resolved and source is not invalid
    if selected_pack and not selected_pack_matched and p_state != SOURCE_STATE_INVALID:
        limitations.append(f"selected_contract_pack_missing:{selected_pack}")
        has_unresolved = True

    # B2 (Section 11-12): Every applicable pack preserves its identity as required context.
    # A legacy description-only pack has no identity to preserve; its only admitted
    # carrier (the description) is the floor -- identity is never fabricated.
    for ap in applicable_packs:
        reasons: list[str] = ["applicable_pack_identity" if ap.get("id") else "applicable_pack_description_carrier"]
        if ap.pop("_explicitly_selected", False):
            reasons.append("explicit_selection")
        if ap.get("effective_contracts"):
            reasons.append("effective_contract_binding")
        ap["required"] = True
        ap["required_reasons"] = reasons

    # Evaluate applicable contracts
    required_contract_ids: set[str] = set()
    for ap in applicable_packs:
        for ref in ap.get("effective_contracts", []):
            required_contract_ids.add(ref)

    applicable_contracts: list[dict[str, Any]] = []
    for contract in contracts:
        cid = contract.get("id")
        is_required = bool(cid and (cid in required_contract_ids or cid in referenced_contracts))
        if is_required:
            matches = True
        elif c_format == FORMAT_LEGACY_FLAT:
            matches = _legacy_contract_applies(
                contract,
                chunk_files=chunk_file_set,
                include_all=include_all_contracts,
                referenced=referenced_contracts,
                keywords=relevance_keywords,
            )
        else:
            matches = _contract_matches_chunk(contract, chunk_files=chunk_file_set, format=FORMAT_MODERN_MAPPING)

        if matches:
            contract_copy = dict(contract)
            if is_required:
                contract_copy["required"] = True
            applicable_contracts.append(contract_copy)

    filtered_contracts = sorted(applicable_contracts, key=lambda item: (item.get("id") or "", item.get("description") or ""))
    filtered_packs = sorted(applicable_packs, key=lambda item: (item.get("id") or "", item.get("description") or ""))

    has_typed_error = (
        has_invalid_source
        or has_malformed_bindings
        or has_unresolved
        or any(lim.startswith(("required_source_absent:", "selected_contract_pack_missing:", "invalid_source_", "unresolved_contract_reference:")) for lim in limitations)
    )

    if not filtered_contracts and not filtered_packs and not has_typed_error:
        limitations.append(f"contracts_context_not_relevant:{chunk_id}")

    return (
        {
            "domain_contracts": filtered_contracts,
            "review_packs": filtered_packs,
        },
        _dedupe(limitations),
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
        item_scope_paths, had_unresolvable = _item_scope_paths(item)
        is_global = _is_global_item(item)
        if had_unresolvable and not item_scope_paths and not is_global:
            # An unresolvable path is a real but invalid scope claim -- it
            # must never fall through to the document/global-scope branch
            # below (which is reserved for items that genuinely never had
            # a path field at all). Only when NO usable canonical path
            # survives at all: an item with one valid and one invalid path
            # field still has a real, matchable scope via the valid one
            # (PR #231 review round 2, P1).
            name = _clean_text(item.get("name")) or "unknown_check"
            limitations.append(f"check_scope_unclassified:{name}")
            continue
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


def _matches_legacy_pattern(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.strip()
        if not normalized:
            continue
        if normalized.endswith("*") and path.startswith(normalized[:-1]):
            return True
        if normalized in path:
            return True
    return False


def _matches_modern_pattern(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        normalized = pattern.strip()
        if not normalized:
            continue
        if fnmatch.fnmatchcase(path, normalized):
            return True
    return False


def _matches_pattern(path: str, patterns: list[str], *, format: str = FORMAT_MODERN_MAPPING) -> bool:
    if format == FORMAT_LEGACY_FLAT:
        return _matches_legacy_pattern(path, patterns)
    return _matches_modern_pattern(path, patterns)


def _contract_matches_chunk(
    contract: dict[str, Any],
    *,
    chunk_files: set[str],
    format: str = FORMAT_MODERN_MAPPING,
) -> bool:
    contract_paths = _paths_from_item(contract)
    if contract_paths:
        if contract_paths.intersection(chunk_files):
            return True
        # Legacy `paths` are exact canonical identities only (frozen baseline
        # 6bbd2f9): substring/prefix recovery belongs to legacy `patterns`, never
        # to `paths`. Glob-bearing paths are a MODERN-mode semantic.
        if format != FORMAT_LEGACY_FLAT:
            pattern_candidates = [p for p in contract_paths if any(char in p for char in "*?[]")]
            if pattern_candidates and any(_matches_modern_pattern(path, pattern_candidates) for path in chunk_files):
                return True
    patterns = _normalized_contract_patterns(contract.get("patterns"))
    if patterns and any(_matches_pattern(path, patterns, format=format) for path in chunk_files):
        return True
    return _is_global_item(contract)


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


MODERN_SCOPE_DOMAIN: frozenset[str] = frozenset({"global"})


def classify_scope(value: Any) -> tuple[bool, bool]:
    """Single authority for the contract/pack `scope` scalar: returns (is_global, supported).

    `global` (any case, surrounding whitespace ignored) is the ONLY value any
    consumer gives semantics to; an absent/empty scope carries none and is
    supported (nothing to discard). Any other nonempty value is *unsupported* --
    a modern source must reject it instead of accepting and silently dropping
    its meaning. Domain contracts, review packs and `_is_global_item` all consume
    this one function (proved extensionally in the cycle-3 suite).
    """
    if not isinstance(value, str):
        return False, value is None
    normalized = value.strip().lower()
    if not normalized:
        return False, True
    if normalized in MODERN_SCOPE_DOMAIN:
        return True, True
    return False, False


def _is_global_item(item: dict[str, Any]) -> bool:
    if classify_scope(item.get("scope"))[0]:
        return True
    return item.get("is_global") is True


def _item_scope_paths(item: dict[str, Any]) -> tuple[set[str], bool]:
    """Canonical path identity for scope joins (C5, aiops-orchestrator#205
    post-merge debt), plus whether any path-bearing field was present but
    unresolvable. `chunk.files` is always `canonical_repo_path` output, so
    any externally-sourced path compared against it must go through the
    same authority -- `sanitize_display_path` alone leaves `./`/`.`
    segments uncollapsed and never rejects an absolute/traversal path,
    which previously let a merely differently-spelled but identical path
    silently fail to join, and let an unresolvable path occupy the scope
    set as a bogus, never-matching identity instead of falling through to
    each caller's own "no derivable scope" handling.

    The second return value exists because "no path field present at all"
    and "a path field was present but invalid" are not the same fact, and
    conflating them is itself a defect (PR #231 adversarial review round
    1, P2): a caller that treats an empty scope set as "no scope info ->
    fall back to global/document-wide inclusion" would otherwise silently
    promote an item with an actually-invalid path to global scope --
    strictly worse than the pre-fix bogus-non-matching-identity behavior
    it replaced. Callers that already have their own "unclassified"
    fallback (checks_context) must route an unresolvable path there
    instead of the global/document-scope branch; callers with no
    unclassified channel at all (_filter_validation_entries) must exclude
    it outright, not include it everywhere.
    """
    paths: set[str] = set()
    had_unresolvable = False
    for key in ("file_path", "file", "original_file", "path"):
        raw = _clean_text(item.get(key))
        if not raw:
            continue
        try:
            paths.add(canonical_repo_path(raw))
        except PathIdentityError:
            had_unresolvable = True
    for key in ("files", "paths", "source_files", "related_files"):
        value = item.get(key)
        if not isinstance(value, list):
            continue
        for raw in value:
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                paths.add(canonical_repo_path(raw))
            except PathIdentityError:
                had_unresolvable = True
    return paths, had_unresolvable


def _paths_from_item(item: dict[str, Any]) -> set[str]:
    return _item_scope_paths(item)[0]


def _canonical_paths_in_chunk(raw_paths: list[str], *, chunk_files: set[str]) -> list[str]:
    """Canonicalize each raw path (C5) and keep only those that join the
    (already canonical) chunk file set, sorted for a deterministic order.
    A path that fails to canonicalize is dropped, not compared raw."""
    matched: set[str] = set()
    for raw in raw_paths:
        try:
            canonical = canonical_repo_path(raw)
        except PathIdentityError:
            continue
        if canonical in chunk_files:
            matched.add(canonical)
    return sorted(matched)


def _filter_lci(document: dict[str, Any] | None, *, chunk_files: set[str]) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(document, dict):
        return {"provided": False, "files_analyzed": [], "confirmed_local_failures": []}, []
    analyzed = _canonical_paths_in_chunk(_string_list(document.get("files_analyzed")), chunk_files=chunk_files)
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
    changed_tests = _canonical_paths_in_chunk(_string_list(document.get("changed_tests")), chunk_files=chunk_files)
    failed_tests = _canonical_paths_in_chunk(_string_list(document.get("failed_tests")), chunk_files=chunk_files)
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
        item_paths, had_unresolvable = _item_scope_paths(item)
        # An unresolvable path is a real but invalid scope claim -- exclude
        # it outright rather than let an empty scope set fall through as
        # "no scope info" and get promoted to every chunk. Only when NO
        # usable canonical path survives at all: an item with one valid
        # and one invalid path field still has a real, matchable scope via
        # the valid one (PR #231 review round 2, P1).
        if had_unresolvable and not item_paths and not is_global:
            continue
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


RESERVED_DOMAIN_CONTRACT_METADATA_KEYS: frozenset[str] = frozenset(
    {"version", "schema_version", "updated", "system", "metadata"}
)


def _is_valid_reserved_metadata(key: str, value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or not k.strip():
                return False
            if not isinstance(v, (str, int, float, bool, dict, list)):
                return False
        return True
    return False


# ---------------------------------------------------------------------------
# Modern Domain Contract Grammar & Bounded Shape Registry
# ---------------------------------------------------------------------------

MODERN_CONTRACT_IDENTITY_FIELDS: frozenset[str] = frozenset({"id"})

MODERN_CONTRACT_SCALAR_FIELDS: frozenset[str] = frozenset({
    "description",
    "scope",
    "is_global",
    "canonical_authority",
    "display_authority",
    "path",
    "file_path",
})

MODERN_CONTRACT_PATH_LIST_FIELDS: frozenset[str] = frozenset({
    "paths",
    "files",
    "source_files",
    "related_files",
    "patterns",
})

# Sections whose members are bounded rule dicts (or strings for 'rules')
MODERN_CONTRACT_RULE_SECTIONS: frozenset[str] = frozenset({
    "rules",
    "slot_rules",
})

MODERN_CONTRACT_KNOWN_FIELDS: frozenset[str] = (
    MODERN_CONTRACT_IDENTITY_FIELDS
    | MODERN_CONTRACT_SCALAR_FIELDS
    | MODERN_CONTRACT_PATH_LIST_FIELDS
    | MODERN_CONTRACT_RULE_SECTIONS
)

# Rule dict bounded grammar (Section 8 & 28)
MODERN_RULE_DICT_REQUIRED_FIELDS: frozenset[str] = frozenset({"rule"})
MODERN_RULE_DICT_OPTIONAL_FIELDS: frozenset[str] = frozenset({
    "invariant",
    "rationale",
    "field",
    "expected_value",
})
ALL_MODERN_RULE_DICT_FIELDS: frozenset[str] = (
    MODERN_RULE_DICT_REQUIRED_FIELDS | MODERN_RULE_DICT_OPTIONAL_FIELDS
)

MODERN_PACK_IDENTITY_AUTHORITY: str = (
    "mapping key is the sole authoritative pack identity; nested id is unsupported"
)
MODERN_MAPPING_PACK_SEMANTIC_FIELDS: frozenset[str] = frozenset({
    "description",
    "domain_contract",
    "recommended_review_preset",
    "paths",
    "patterns",
    "is_global",
    "scope",
})
TARGET_METADATA_NOT_USED_AS_RELATION: frozenset[str] = frozenset({
    "critical",
    "allow_external_review",
    "require_full_diff",
    "require_final_files_when_available",
    "notes",
})
MODERN_MAPPING_PACK_VALUE_FIELDS: frozenset[str] = frozenset(
    MODERN_MAPPING_PACK_SEMANTIC_FIELDS | TARGET_METADATA_NOT_USED_AS_RELATION
)
LEGACY_FLAT_PACK_ITEM_FIELDS: frozenset[str] = frozenset(
    {"id", "description", "paths", "patterns", "recommended_review_preset", "is_global", "scope"}
    | TARGET_METADATA_NOT_USED_AS_RELATION
)
GATE_A_SEMANTIC_PACK_FIELDS: frozenset[str] = frozenset({
    "id",
    "description",
    "paths",
    "patterns",
    "domain_contract",
    "recommended_review_preset",
    "is_global",
    "scope",
})

GENERIC_NAMED_STRING_SECTION_RULE: str = (
    "any unknown nested key with list[nonempty str] value is admitted as named string section"
)
DOMAIN_CONTRACT_UNKNOWN_FIELD_POLICY: str = (
    "unknown field not matching generic named string section fails closed with invalid_source_contract:UNSUPPORTED_NONEMPTY"
)
REVIEW_PACK_UNKNOWN_FIELD_POLICY: str = (
    "unknown modern pack value key fails closed with invalid_source_review_packs:UNSUPPORTED_NONEMPTY"
)
REVIEW_PACK_LEGACY_INPUT_POLICY: str = (
    "legacy flat packs list admitted as differential baseline; domain_contract in legacy flat pack is ignored and does not establish EffectiveContractRefs"
)
REVIEW_PACK_MIXED_SHAPE_POLICY: str = (
    "legacy packs list combined with nonempty contract_bindings fails closed with invalid_source_review_packs:UNSUPPORTED_NONEMPTY as INVALID_MIXED_SHAPE"
)

# ---------------------------------------------------------------------------
# C1 -- modern admission totality. Every path-bearing field a MODERN source can
# carry is declared here, once, and validated by one authority before any
# normalization/projection (`DisplayPath` never decides validity). Legacy flat
# sources keep the frozen baseline's lenient handling (6bbd2f9) and never
# reach these validators.
# ---------------------------------------------------------------------------

MODERN_CONTRACT_EXACT_PATH_FIELDS: tuple[str, ...] = (
    "path",
    "file_path",
    "files",
    "paths",
    "source_files",
    "related_files",
)
MODERN_CONTRACT_PATTERN_FIELDS: tuple[str, ...] = ("patterns",)
MODERN_PACK_EXACT_PATH_FIELDS: tuple[str, ...] = ("paths",)
MODERN_PACK_PATTERN_FIELDS: tuple[str, ...] = ("patterns",)

# Review-pack document envelope. Owner evidence: the real target's
# `.aiops/review-packs.yaml` (AgentEscala@b281ca5d) has top-level keys
# {version, updated, packs}; `contract_bindings` is the engine-owned relation
# carrier. No other key has owner/source evidence, so none is admitted.
REVIEW_PACK_ENVELOPE_KEYS: frozenset[str] = frozenset({"packs", "contract_bindings", "version", "updated"})
REVIEW_PACK_ENVELOPE_METADATA_KEYS: frozenset[str] = frozenset({"version", "updated"})


def _modern_path_bearing_invalid(
    item: dict[str, Any],
    *,
    exact_fields: tuple[str, ...],
    pattern_fields: tuple[str, ...],
) -> bool:
    """True when any path-bearing member of a modern item is not a valid repository-relative
    identity (exact fields) / pattern identity (pattern fields). One invalid member
    invalidates the whole source: never discard-invalid + retain-valid."""
    for field in exact_fields:
        if field not in item:
            continue
        value = item[field]
        members = [value] if isinstance(value, str) else value
        for member in members:
            if isinstance(member, str) and not member.strip():
                continue
            try:
                canonical_repo_path(member)
            except PathIdentityError:
                return True
    for field in pattern_fields:
        if field not in item:
            continue
        for member in item[field]:
            try:
                canonical_repo_pattern(member)
            except PathIdentityError:
                return True
    return False


def _modern_scope_unsupported(item: dict[str, Any]) -> bool:
    return "scope" in item and not classify_scope(item["scope"])[1]


def _review_pack_envelope_invalid(document: dict[str, Any]) -> bool:
    """Closed envelope grammar for a modern review-packs document."""
    for key, value in document.items():
        if not isinstance(key, str) or key not in REVIEW_PACK_ENVELOPE_KEYS:
            return True
        if key in REVIEW_PACK_ENVELOPE_METADATA_KEYS:
            if isinstance(value, bool) or not isinstance(value, (str, int, float)):
                return True
    return False


def _modern_exact_paths(value: Any) -> list[str]:
    members = [value] if isinstance(value, str) else (value if isinstance(value, list) else [])
    return sorted({canonical_repo_path(member) for member in members if isinstance(member, str) and member.strip()})


def _modern_patterns(value: Any) -> list[str]:
    members = value if isinstance(value, list) else []
    return sorted({canonical_repo_pattern(member) for member in members if isinstance(member, str) and member.strip()})


def _is_valid_rule_dict(d: Any) -> bool:
    """Validates that a rule dict matches the bounded modern rule dict shape."""
    if not isinstance(d, dict):
        return False
    if any(k not in ALL_MODERN_RULE_DICT_FIELDS for k in d.keys()):
        return False
    if "rule" not in d:
        return False
    rule_val = d["rule"]
    if not isinstance(rule_val, str) or not rule_val.strip():
        return False
    if "invariant" in d and not isinstance(d["invariant"], bool):
        return False
    if "rationale" in d and (not isinstance(d["rationale"], str) or not d["rationale"].strip()):
        return False
    if "field" in d and (not isinstance(d["field"], str) or not d["field"].strip()):
        return False
    if "expected_value" in d and not isinstance(d["expected_value"], (str, int, float, bool)):
        return False
    return True


def _validate_contract_declared_field_types(item: dict[str, Any]) -> bool:
    """Validates that declared fields on a contract match expected types.
    Prevents malformed types (e.g. scalar strings for paths/patterns, empty list members,
    invalid section members) from being silently sanitized away.
    Admits generic named list[str] sections where value is list of non-empty strings.
    """
    for f in MODERN_CONTRACT_PATH_LIST_FIELDS:
        if f in item:
            val = item[f]
            if not isinstance(val, list) or any(not isinstance(elem, str) or not elem.strip() for elem in val):
                return False

    str_fields = ("path", "file_path", "scope", "canonical_authority", "display_authority")
    for f in str_fields:
        if f in item:
            val = item[f]
            if not isinstance(val, str):
                return False

    if "description" in item:
        val = item["description"]
        if not isinstance(val, str) or not val.strip():
            return False

    if "id" in item:
        val = item["id"]
        if not isinstance(val, str) or not val.strip():
            return False

    if "is_global" in item and not isinstance(item["is_global"], bool):
        return False

    if "rules" in item:
        val = item["rules"]
        if not isinstance(val, list):
            return False
        for elem in val:
            if isinstance(elem, str):
                if not elem.strip():
                    return False
            elif isinstance(elem, dict):
                if not _is_valid_rule_dict(elem):
                    return False
            else:
                return False

    if "slot_rules" in item:
        val = item["slot_rules"]
        if not isinstance(val, list):
            return False
        for elem in val:
            if not isinstance(elem, dict) or not _is_valid_rule_dict(elem):
                return False

    # Generic named list[str] sections (Section 6 & 7):
    for k, v in item.items():
        if k not in MODERN_CONTRACT_KNOWN_FIELDS:
            if isinstance(v, list):
                if any(not isinstance(elem, str) or not elem.strip() for elem in v):
                    return False

    return True


def _clean_contract_dict_item(
    item: dict[str, Any],
    default_id: str | None = None,
    *,
    modern: bool = False,
) -> dict[str, Any]:
    cid = (default_id.strip() if default_id else (_clean_text(item.get("id")) or ""))
    desc = _clean_text(item.get("description")) or cid
    scope = _clean_text(item.get("scope"))
    is_global = item.get("is_global") is True or classify_scope(scope)[0]
    canonical_authority = _clean_text(item.get("canonical_authority"))
    display_authority = _clean_text(item.get("display_authority"))

    non_section_keys = {
        "id",
        "description",
        "scope",
        "is_global",
        "canonical_authority",
        "display_authority",
        "file_path",
        "path",
        "files",
        "paths",
        "source_files",
        "related_files",
        "patterns",
    }

    sections: dict[str, list[dict[str, Any]]] = {}
    all_rule_texts: list[str] = []

    for sec_name, sec_val in item.items():
        if sec_name in non_section_keys or not isinstance(sec_val, list):
            continue
        cleaned_sec_items: list[dict[str, Any]] = []
        for elem in sec_val:
            if isinstance(elem, str):
                elem_text = elem.strip()
                if elem_text:
                    cleaned_sec_items.append({"text": elem_text})
            elif isinstance(elem, dict):
                elem_text = _clean_text(elem.get("rule"))
                sec_dict: dict[str, Any] = {}
                if elem_text:
                    sec_dict["text"] = elem_text
                if "invariant" in elem and isinstance(elem["invariant"], bool):
                    sec_dict["invariant"] = elem["invariant"]
                if "rationale" in elem and isinstance(elem["rationale"], str) and elem["rationale"].strip():
                    sec_dict["rationale"] = elem["rationale"].strip()
                if "field" in elem and isinstance(elem["field"], str) and elem["field"].strip():
                    sec_dict["field"] = elem["field"].strip()
                if "expected_value" in elem and isinstance(elem["expected_value"], (str, int, float, bool)):
                    sec_dict["expected_value"] = elem["expected_value"]
                if sec_dict:
                    cleaned_sec_items.append(sec_dict)
        if cleaned_sec_items:
            sections[sec_name] = cleaned_sec_items
            if sec_name in ("rules", "slot_rules"):
                for si in cleaned_sec_items:
                    if "text" in si:
                        all_rule_texts.append(si["text"])
        elif not sec_val:
            # Preserve empty section identity: present [] != absent (Finding N4 / Section 17)
            sections[sec_name] = []

    row = {
        "id": cid,
        "description": desc,
        "scope": scope,
        "is_global": is_global,
        "canonical_authority": canonical_authority,
        "display_authority": display_authority,
        **(
            {
                # MODERN: identity is the canonical repository-relative form
                # (already validated by `_modern_path_bearing_invalid`).
                "file_path": (_modern_exact_paths(item.get("file_path")) or [""])[0],
                "path": (_modern_exact_paths(item.get("path")) or [""])[0],
                "files": _modern_exact_paths(item.get("files")),
                "paths": _modern_exact_paths(item.get("paths")),
                "source_files": _modern_exact_paths(item.get("source_files")),
                "related_files": _modern_exact_paths(item.get("related_files")),
                "patterns": _modern_patterns(item.get("patterns")),
            }
            if modern
            else {
                # LEGACY: frozen baseline display projection.
                "file_path": sanitize_display_path(_clean_text(item.get("file_path")) or ""),
                "path": sanitize_display_path(_clean_text(item.get("path")) or ""),
                "files": _sanitize_contract_paths(item.get("files")),
                "paths": _sanitize_contract_paths(item.get("paths")),
                "source_files": _sanitize_contract_paths(item.get("source_files")),
                "related_files": _sanitize_contract_paths(item.get("related_files")),
                "patterns": _normalized_contract_patterns(item.get("patterns")),
            }
        ),
    }
    if all_rule_texts:
        row["rules"] = all_rule_texts
    if sections:
        row["sections"] = sections
    return _drop_empty_contract_fields(row)


def normalize_domain_contracts(document: Any) -> tuple[list[dict[str, Any]], str, str | None, list[str]]:
    """Normalizes domain_contracts into a canonical list of contract dicts,
    returning (contracts, source_state, invalid_subtype, limitations).
    Preserves legacy flat rules list, and admits bounded modern shapes:
    - nested bounded domain contract mapping (key is contract id, value is dict)
    - named list[str] sections (key is section id, value is list of strings)
    - reserved top-level metadata envelope (version, schema_version, updated, system, metadata)
    """
    if document is None:
        return [], SOURCE_STATE_ABSENT, None, []

    if isinstance(document, list):
        if not document:
            return [], SOURCE_STATE_PRESENT_VALID, None, []
        if all(isinstance(item, dict) for item in document):
            for item in document:
                if not _validate_contract_declared_field_types(item):
                    return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
            rows = [_clean_contract_dict_item(item) for item in document]
            cleaned = [r for r in rows if r.get("id") or r.get("description")]
            return sorted(cleaned, key=lambda item: (item.get("id") or "", item.get("description") or "")), SOURCE_STATE_PRESENT_VALID, None, []
        return [], SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]

    if not isinstance(document, dict):
        return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]

    # Check for legacy envelope {"rules": [...]}
    if "rules" in document:
        rules = document.get("rules")
        if not isinstance(rules, list):
            return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
        if all(isinstance(item, dict) for item in rules):
            for item in rules:
                if not _validate_contract_declared_field_types(item):
                    return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
            rows = [_clean_contract_dict_item(item) for item in rules]
            cleaned = [r for r in rows if r.get("id") or r.get("description")]
            return sorted(cleaned, key=lambda item: (item.get("id") or "", item.get("description") or "")), SOURCE_STATE_PRESENT_VALID, None, []
        return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]

    # Modern bounded shapes: non-reserved keys represent contract identities
    rows = []
    seen_contract_keys: set[str] = set()
    for key, value in document.items():
        if not isinstance(key, str) or not key.strip():
            return [], SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, ["invalid_source_contract:INVALID_IDENTITY"]
        clean_key = key.strip()
        if clean_key in RESERVED_DOMAIN_CONTRACT_METADATA_KEYS:
            if not _is_valid_reserved_metadata(clean_key, value):
                return [], SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]
            continue

        # Normalization collision check (Finding 4178603213 sibling census)
        if clean_key in seen_contract_keys:
            return [], SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, ["invalid_source_contract:INVALID_IDENTITY"]
        seen_contract_keys.add(clean_key)

        if isinstance(value, list):
            if not value:
                rows.append({"id": clean_key, "description": clean_key, "rules": [], "sections": {"rules": []}})
            elif all(isinstance(rule_str, str) for rule_str in value):
                if any(not rule_str.strip() for rule_str in value):
                    return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
                rules_list = [r.strip() for r in value]
                sec_items = [{"text": r} for r in rules_list]
                rows.append({
                    "id": clean_key,
                    "description": clean_key,
                    "rules": rules_list,
                    "sections": {"rules": sec_items},
                })
            elif all(isinstance(rule_dict, dict) for rule_dict in value):
                for rdict in value:
                    if not _is_valid_rule_dict(rdict):
                        return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
                rows.append(_clean_contract_dict_item({"id": clean_key, "rules": value}, default_id=clean_key))
            else:
                return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
        elif isinstance(value, dict):
            # Check for unknown unsupported fields (Section 7 & 27: anything not in known fields that is NOT an admitted named list[str] section)
            for k, v in value.items():
                if k not in MODERN_CONTRACT_KNOWN_FIELDS:
                    if not isinstance(v, list):
                        return [], SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]

            # Nested id conflict check (Finding 4178603188)
            if "id" in value:
                nested_id = _clean_text(value.get("id"))
                if nested_id and nested_id != clean_key:
                    return [], SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, ["invalid_source_contract:INVALID_IDENTITY"]
            if not _validate_contract_declared_field_types(value):
                return [], SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, ["invalid_source_contract:MALFORMED_SHAPE"]
            if _modern_scope_unsupported(value):
                return [], SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]
            if _modern_path_bearing_invalid(
                value,
                exact_fields=MODERN_CONTRACT_EXACT_PATH_FIELDS,
                pattern_fields=MODERN_CONTRACT_PATTERN_FIELDS,
            ):
                return [], SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, ["invalid_source_contract:INVALID_IDENTITY"]
            row = _clean_contract_dict_item(value, default_id=clean_key, modern=True)
            rows.append(row)
        else:
            return [], SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, ["invalid_source_contract:UNSUPPORTED_NONEMPTY"]

    return sorted(rows, key=lambda item: (item.get("id") or "", item.get("description") or "")), SOURCE_STATE_PRESENT_VALID, None, []


def _validate_pack_declared_types(item: dict[str, Any]) -> bool:
    """Validates declared field types on review pack items, including semantic fields
    and target metadata (Section 10).
    """
    if "paths" in item and (not isinstance(item["paths"], list) or any(not isinstance(p, str) or not p.strip() for p in item["paths"])):
        return False
    if "patterns" in item and (not isinstance(item["patterns"], list) or any(not isinstance(p, str) or not p.strip() for p in item["patterns"])):
        return False
    if "recommended_review_preset" in item and not isinstance(item["recommended_review_preset"], str):
        return False
    if "description" in item and not isinstance(item["description"], str):
        return False
    if "is_global" in item and not isinstance(item["is_global"], bool):
        return False
    if "scope" in item and not isinstance(item["scope"], str):
        return False
    if "critical" in item and not isinstance(item["critical"], bool):
        return False
    if "allow_external_review" in item and not isinstance(item["allow_external_review"], bool):
        return False
    if "require_full_diff" in item and not isinstance(item["require_full_diff"], bool):
        return False
    if "require_final_files_when_available" in item and not isinstance(item["require_final_files_when_available"], bool):
        return False
    if "notes" in item and not isinstance(item["notes"], str):
        return False
    return True


def _legacy_pack_row(item: dict[str, Any]) -> dict[str, Any]:
    """LegacyPackProjection: exactly the frozen baseline's (6bbd2f9 `_flatten_review_packs`)
    three-field projection. Modern-only fields (paths, patterns, scope, is_global,
    domain_contract) never acquire semantics in a legacy pack."""
    return _drop_empty_contract_fields(
        {
            "id": _clean_text(item.get("id")),
            "description": _clean_text(item.get("description")),
            "recommended_review_preset": _clean_text(item.get("recommended_review_preset")),
        }
    )


def _legacy_pack_row_admitted(row: dict[str, Any]) -> bool:
    """A legacy pack row is admitted when it carries an identity OR a description.
    A description-only pack is a valid baseline pack (selectable through the legacy
    description matcher); its identity is never fabricated. A row with neither
    carries no context at all."""
    return bool(row.get("id") or row.get("description"))


def _legacy_pack_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [_legacy_pack_row(item) for item in items]
    admitted = [row for row in rows if _legacy_pack_row_admitted(row)]
    return sorted(admitted, key=lambda item: (item.get("id") or "", item.get("description") or ""))


def normalize_review_packs(
    document: Any,
    extra_bindings: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]], str, str | None, list[str]]:
    """Normalizes review_packs and contract_bindings into canonical structures.
    Returns (packs, contract_bindings, source_state, invalid_subtype, limitations).
    Preserves legacy flat packs list, and admits bounded modern shapes:
    - packs mapping: dict[str, dict] where key is pack_id
    - contract_bindings: dict[str, list[str]] mapping pack_id to list of contract identities
    """
    limitations: list[str] = []
    contract_bindings: dict[str, list[str]] = {}

    raw_bindings = None
    if isinstance(document, dict) and "contract_bindings" in document:
        raw_bindings = document.get("contract_bindings")
    elif extra_bindings is not None:
        raw_bindings = extra_bindings

    if raw_bindings is not None:
        if not isinstance(raw_bindings, dict):
            limitations.append("malformed_contract_bindings:must_be_mapping")
        else:
            bindings_valid = True
            seen_binding_keys: set[str] = set()
            for k, v in raw_bindings.items():
                if not isinstance(k, str) or not k.strip():
                    limitations.append("malformed_contract_bindings:invalid_key")
                    bindings_valid = False
                    break
                clean_k = k.strip()
                if clean_k in seen_binding_keys:
                    limitations.append("malformed_contract_bindings:duplicate_normalized_key")
                    bindings_valid = False
                    break
                seen_binding_keys.add(clean_k)
                if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
                    limitations.append("malformed_contract_bindings:must_be_list_of_strings")
                    bindings_valid = False
                    break
                contract_bindings[clean_k] = _dedupe([x.strip() for x in v if x.strip()])
            if not bindings_valid:
                contract_bindings.clear()

    if document is None:
        if contract_bindings:
            limitations.append("required_source_absent:review_packs")
        return [], contract_bindings, SOURCE_STATE_ABSENT, None, limitations

    if isinstance(document, list):
        if raw_bindings:
            return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]
        if not document:
            return [], contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations
        if all(isinstance(item, dict) for item in document):
            for item in document:
                if not _validate_pack_declared_types(item):
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]
            return _legacy_pack_rows(document), contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations
        return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]

    if not isinstance(document, dict):
        return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]

    raw_packs = document.get("packs")

    # Modern document envelope grammar (the frozen legacy `{"packs": [...]}` envelope keeps the
    # baseline's leniency toward sibling keys; a modern document is a closed grammar).
    if not isinstance(raw_packs, list) and _review_pack_envelope_invalid(document):
        return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]

    if raw_packs is None:
        if contract_bindings:
            limitations.append("orphan_contract_binding:packs_absent")
        return [], contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations

    if isinstance(raw_packs, list):
        if raw_bindings:
            return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]
        if not raw_packs:
            return [], contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations
        if all(isinstance(item, dict) for item in raw_packs):
            for item in raw_packs:
                if not _validate_pack_declared_types(item):
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]
            return _legacy_pack_rows(raw_packs), contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations
        return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]

    if isinstance(raw_packs, dict):
        rows = []
        seen_pack_keys: set[str] = set()
        for pack_id, pval in raw_packs.items():
            if not isinstance(pack_id, str) or not pack_id.strip():
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, [*limitations, "invalid_source_review_packs:INVALID_IDENTITY"]
            clean_pid = pack_id.strip()
            if clean_pid in seen_pack_keys:
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, [*limitations, "invalid_source_review_packs:INVALID_IDENTITY"]
            seen_pack_keys.add(clean_pid)
            if not isinstance(pval, dict):
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]

            # Section 12: Modern mapping id field adjudication
            if "id" in pval:
                nested_id = _clean_text(pval.get("id"))
                if nested_id and nested_id != clean_pid:
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, [*limitations, "invalid_source_review_packs:INVALID_IDENTITY"]
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]

            # Section 13: Unknown modern pack fields check
            for k in pval.keys():
                if k not in MODERN_MAPPING_PACK_VALUE_FIELDS:
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]

            # Validate declared field types before sanitizing
            if not _validate_pack_declared_types(pval):
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]
            if "domain_contract" in pval:
                if not isinstance(pval["domain_contract"], str):
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]
                if not pval["domain_contract"].strip():
                    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, [*limitations, "invalid_source_review_packs:INVALID_IDENTITY"]

            # C1: scope value domain, then repository-relative path/pattern identity, before any projection.
            if _modern_scope_unsupported(pval):
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_UNSUPPORTED_NONEMPTY, [*limitations, "invalid_source_review_packs:UNSUPPORTED_NONEMPTY"]
            if _modern_path_bearing_invalid(
                pval,
                exact_fields=MODERN_PACK_EXACT_PATH_FIELDS,
                pattern_fields=MODERN_PACK_PATTERN_FIELDS,
            ):
                return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_INVALID_IDENTITY, [*limitations, "invalid_source_review_packs:INVALID_IDENTITY"]

            rows.append(_drop_empty_contract_fields({
                "id": clean_pid,
                "description": _clean_text(pval.get("description")) or clean_pid,
                "domain_contract": _clean_text(pval.get("domain_contract")),
                "recommended_review_preset": _clean_text(pval.get("recommended_review_preset")),
                "paths": _modern_exact_paths(pval.get("paths")),
                "patterns": _modern_patterns(pval.get("patterns")),
                "is_global": pval.get("is_global") is True or classify_scope(pval.get("scope"))[0],
            }))
        return sorted(rows, key=lambda item: (item.get("id") or "", item.get("description") or "")), contract_bindings, SOURCE_STATE_PRESENT_VALID, None, limitations

    return [], contract_bindings, SOURCE_STATE_INVALID, SUBTYPE_MALFORMED_SHAPE, [*limitations, "invalid_source_review_packs:MALFORMED_SHAPE"]


def _flatten_contract_rules(document: Any) -> list[dict[str, Any]]:
    contracts, _, _, _ = normalize_domain_contracts(document)
    return contracts


def _flatten_review_packs(document: Any) -> list[dict[str, Any]]:
    packs, _, _, _, _ = normalize_review_packs(document)
    return packs


def modern_pack_matches_selected(pack: dict[str, Any], selected_pack: str) -> bool:
    """MODERN identity: the selected pack is the exact (case-sensitive) mapping key. No
    alias, no fuzziness, no target knowledge -- every target-specific decision comes
    from supplied configuration, never from an engine branch."""
    if not selected_pack:
        return False
    return (_clean_text(pack.get("id")) or "") == selected_pack.strip()


def legacy_pack_matches_selected(pack: dict[str, Any], selected_pack: str) -> bool:
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


def _review_pack_matches_selected(pack: dict[str, Any], selected_pack: str) -> bool:
    return modern_pack_matches_selected(pack, selected_pack)


# ---------------------------------------------------------------------------
# LEGACY applicability -- the frozen baseline's (6bbd2f9) predicates, one named
# function per predicate so each is independently discriminated by the durable
# differential oracle (tests/fixtures/agent_review/v1_c2_legacy_differential_observations.json).
# Modern semantics never reach these functions, and legacy rows carry only the
# baseline projection, so a modern-only field cannot leak into legacy applicability.
# ---------------------------------------------------------------------------


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


def _legacy_relevance_match(item: dict[str, Any], keywords: tuple[str, ...]) -> bool:
    if not keywords:
        return False
    text = ((item.get("id") or "") + " " + (item.get("description") or "")).lower()
    return any(keyword in text for keyword in keywords)


def _legacy_contract_include_all(contract: dict[str, Any], include_all: bool) -> bool:
    """`target_profile:domain_contracts` is an UNCONDITIONAL request to include every
    legacy rule -- scoped or not, whatever the chunk's files."""
    return include_all


def _legacy_pack_include_all(pack: dict[str, Any], include_all: bool) -> bool:
    return include_all


def _legacy_contract_explicit_ref(contract: dict[str, Any], referenced: set[str]) -> bool:
    return contract.get("id") in referenced


def _legacy_pack_explicit_ref(pack: dict[str, Any], referenced: set[str]) -> bool:
    return pack.get("id") in referenced


def _legacy_contract_applies(
    contract: dict[str, Any],
    *,
    chunk_files: set[str],
    include_all: bool,
    referenced: set[str],
    keywords: tuple[str, ...],
) -> bool:
    return bool(
        _legacy_contract_include_all(contract, include_all)
        or _legacy_contract_explicit_ref(contract, referenced)
        or _contract_matches_chunk(contract, chunk_files=chunk_files, format=FORMAT_LEGACY_FLAT)
        or _legacy_relevance_match(contract, keywords)
    )


def _legacy_pack_applies(
    pack: dict[str, Any],
    *,
    include_all: bool,
    referenced: set[str],
    keywords: tuple[str, ...],
) -> bool:
    # The baseline's `_contract_matches_chunk(pack)` disjunct is structurally dead: the
    # baseline pack projection carries no path/pattern/scope field, and neither does
    # `_legacy_pack_row`. (Selection is evaluated by the caller.)
    return bool(
        _legacy_pack_include_all(pack, include_all)
        or _legacy_pack_explicit_ref(pack, referenced)
        or _legacy_relevance_match(pack, keywords)
    )


DECLARED_CONTRACT_REF_ORIGINS: tuple[str, ...] = ("pack_inline", "binding", "explicit")


def _declared_contract_ref_origins(
    packs: list[dict[str, Any]],
    bindings: dict[str, list[str]],
    explicit_contract_refs: set[str],
    *,
    pack_mode: str,
) -> dict[str, set[str]]:
    """DeclaredContractRefs, partitioned by origin. A *declared* contract identity reference
    requires the domain_contracts source; the mere presence of `contract_bindings` is not
    the criterion.

    MODERN: pack.domain_contract  U  contract_bindings[*]  U  explicit `contract:<id>`.
    LEGACY flat `pack.domain_contract` is NON-AUTHORITATIVE and never enters the set.
    """
    inline: set[str] = set()
    if pack_mode == FORMAT_MODERN_MAPPING:
        inline = {pack["domain_contract"] for pack in packs if pack.get("domain_contract")}
    bound = {ref for refs in bindings.values() for ref in refs}
    return {"pack_inline": inline, "binding": bound, "explicit": set(explicit_contract_refs)}


def clean_contracts_context_for_payload(ctx: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(ctx, dict):
        return ctx
    cleaned = copy.deepcopy(ctx)
    for p in cleaned.get("review_packs", []):
        if isinstance(p, dict):
            p.pop("required_reasons", None)
            if "effective_contracts" in p and not p["effective_contracts"]:
                p.pop("effective_contracts", None)
    return cleaned


UNIDENTIFIED_LEGACY_PACK_LOSS_LABEL = "unidentified_legacy_pack"


def required_pack_floor_keys(pack: dict[str, Any]) -> frozenset[str]:
    """Keys of a required pack that the shrink ladder must preserve. An identified pack
    floors to its identity (+ relation); an identity-less legacy pack floors to its only
    admitted carrier, the description -- never to a fabricated `id`."""
    base = {"id", "effective_contracts", "required"}
    if not pack.get("id"):
        base = {"description", "effective_contracts", "required"}
    return frozenset(base)


def minimal_pack_context(pack: dict[str, Any]) -> dict[str, Any]:
    min_pack: dict[str, Any] = {}
    if pack.get("id"):
        min_pack["id"] = pack["id"]
    elif pack.get("description"):
        min_pack["description"] = pack["description"]
    if pack.get("required") is True:
        min_pack["required"] = True
    eff = pack.get("effective_contracts")
    if eff:
        min_pack["effective_contracts"] = list(eff)
    return min_pack


def minimal_contracts_context(contracts_ctx: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contracts_ctx, dict):
        return {"domain_contracts": [], "review_packs": []}
    domain_contracts = contracts_ctx.get("domain_contracts")
    required_contracts = [
        dict(item) for item in (domain_contracts or [])
        if isinstance(item, dict) and item.get("required") is True
    ]
    review_packs = contracts_ctx.get("review_packs")
    required_packs = [
        minimal_pack_context(item) for item in (review_packs or [])
        if isinstance(item, dict) and item.get("required") is True
    ]
    return clean_contracts_context_for_payload({
        "domain_contracts": required_contracts,
        "review_packs": required_packs,
    })



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
        raw_path = _clean_text(item.get("path"))
        if not raw_path:
            continue
        # C5: key by the same canonical identity chunk.files uses, or a
        # merely differently-spelled but identical path (e.g. `./x` vs
        # `x`) silently misses this entry and the lookup falls back to a
        # placeholder status/summary. A path that cannot be canonicalized
        # is dropped (fail closed) rather than indexed under a raw, never
        # matching key.
        try:
            path = canonical_repo_path(raw_path)
        except PathIdentityError:
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


# P2-6 (PR #227 exact-HEAD adversarial review): the planner and the builder
# are separate process invocations that can, in principle, be given
# different --checks/--validation-evidence flags -- the planner cannot
# observe what flags the later, independent builder invocation will
# actually be given, only what it itself received. `optional_artifact_
# missing:<name>` fires purely on CLI flag *presence* (`load_optional_json_
# with_limitation`, above), independent of whether the artifact is embedded
# in intake, so a planner invocation that happens to receive --checks
# cannot conclude the builder invocation will too. Both possible reason
# codes are the same length regardless of which one would actually fire
# (`missing`/`invalid`, 7 characters either way), so the projection includes
# both unconditionally rather than trusting invocation-to-invocation flag
# symmetry it has no way to verify.
WORST_CASE_OPTIONAL_ARTIFACT_LIMITATIONS: list[str] = [
    "optional_artifact_missing:checks",
    "optional_artifact_missing:validation_evidence",
]


# ---------------------------------------------------------------------------
# H1-B / C6 (post-merge debt, #205): pr_brief.build_pr_brief's own
# _apply_budget can append this reason code to pr_brief.limitations once
# every shrinker has bottomed out and the resolved brief budget is still
# exceeded -- and chunk_context.brief.limitations in the real builder is
# `list(pr_brief.limitations)` verbatim. The projection has no way to know
# in advance whether the target's resolved brief budget will actually be
# tight enough to trigger this (that would mean re-running the real shrink
# ladder at projection time), so -- same pattern as
# WORST_CASE_OPTIONAL_ARTIFACT_LIMITATIONS above -- it must assume the
# worst case unconditionally rather than silently under-count.
# ---------------------------------------------------------------------------
BRIEF_BUDGET_UNDER_MINIMUM_LIMITATION = "brief_budget_under_minimum_required_sections"


# ---------------------------------------------------------------------------
# Worst-case chunk_id placeholder. Final chunk numbering is only known once
# `max_blocks` selection (rev.3 SS10) has finished choosing which candidate
# partitions survive, which happens after every candidate has already been
# projected. Using the longest possible valid id keeps the projection
# conservative (over-, never under-, estimating this term) without requiring
# a second projection pass once numbering is final.
#
# P2-7 (PR #227 exact-HEAD adversarial review): a fixed 3-digit assumption
# ("up to 999 chunks") was an empirical guess, not a proven bound --
# `--max-blocks` has no upper-bound validation, so an operator invocation
# could in principle exceed it. The chunk numbering format itself
# (`chunk-{index:02d}-{group}`) provides the real, provable bound instead:
# `index` can never exceed the selection cap `max_blocks` (packing only
# ever selects `ordered[:max_blocks]` candidates), so the worst-case digit
# count is derived directly from the `max_blocks` value this specific plan
# is actually being built with.
# ---------------------------------------------------------------------------

_LONGEST_GROUP_NAME = max(get_args(SemanticGroup), key=len)


def worst_case_chunk_id(max_blocks: int) -> str:
    index_digits = max(2, len(str(max(int(max_blocks), 1))))
    return "chunk-" + ("9" * index_digits) + "-" + _LONGEST_GROUP_NAME


# P2-9 (PR #227 exact-HEAD adversarial review, round 3): `order_index` was
# hardcoded to 0 in the projected payload body, but the real value
# (`_pack_all_groups`: `order_index=len(chunks)`, assigned during emission
# over `selected_sorted`) can be as large as `max_blocks - 1` -- the same
# selection cap `worst_case_chunk_id` already derives its bound from. Using
# the real maximum directly (rather than a padded placeholder) is exact,
# not merely conservative: no real order_index for this plan can ever
# exceed it, and the serialized integer's digit count only grows with the
# value, so this never relies on incidental slack from chunk_id or
# semantic_group length elsewhere in the payload.
def worst_case_order_index(max_blocks: int) -> int:
    return max(int(max_blocks) - 1, 0)


# ---------------------------------------------------------------------------
# Auxiliary context construction (single authority; was
# chunk_payload_builder._aux_context / ._coverage_requirements_for_chunk).
# Needed only for the exact `original_chars` bootstrap below (P2-7) -- the
# projected *floor* always uses `MINIMAL_AUX_CONTEXT`, a genuinely
# input-independent constant (`_shrink_aux_context` discards all real
# content unconditionally), so this is not used for the floor itself.
# ---------------------------------------------------------------------------


def aux_context(intake: ReviewIntake, *, chunk_files: list[str]) -> dict[str, Any]:
    project_context_doc = artifact_content(intake, "project-context")
    semantic_context_doc = artifact_content(intake, "semantic-context")
    file_context_doc = artifact_content(intake, "file-diff-context")
    modules = _get(project_context_doc, "modules")
    selected_modules: list[dict[str, Any]] = []
    if isinstance(modules, dict):
        for path in sorted(chunk_files):
            if path in modules:
                selected_modules.append({"path": path, "description": _clean_text(modules.get(path))})

    requirements = _get(file_context_doc, "coverage_requirements")
    return {
        "project_context": {
            "provided": isinstance(project_context_doc, dict),
            "status": _clean_text(_get(project_context_doc, "status")),
            "modules": selected_modules,
            "gaps": _string_list(_get(project_context_doc, "gaps")),
        },
        "semantic_context": {
            "provided": isinstance(semantic_context_doc, dict),
            "status": _clean_text(_get(semantic_context_doc, "status")),
            "scope": _clean_text(_get(semantic_context_doc, "scope")),
            "change_type": _clean_text(_get(semantic_context_doc, "change_type")),
            "must_hold": _string_list(_get(semantic_context_doc, "must_hold")),
        },
        "coverage_requirements": _coverage_requirements_for_chunk(requirements, chunk_files=set(chunk_files)),
    }


def _coverage_requirements_for_chunk(requirements: Any, *, chunk_files: set[str]) -> dict[str, list[str]]:
    if not isinstance(requirements, dict):
        return {"must_review_files": [], "should_review_files": [], "may_summarize_files": []}
    result: dict[str, list[str]] = {}
    for key in ("must_review_files", "should_review_files", "may_summarize_files"):
        values = [item for item in _string_list(requirements.get(key)) if item in chunk_files]
        result[key] = values
    return result


# ---------------------------------------------------------------------------
# Exact `original_chars` bootstrap (single authority). `original_chars` is
# itself embedded in the payload being measured, so establishing its own
# correct value takes an initial bootstrap pass plus a confirmation pass.
#
# P3 hardening (PR #227 exact-HEAD adversarial review, round 3):
# chunk_payload_builder._apply_payload_budget used to keep its own,
# independent copy of this exact 3-pass sequence rather than calling this
# module -- functionally identical today, but a structural drift risk: the
# real builder's untruncated state and the planner's exact-floor projection
# (P2-7) could silently diverge if one copy were ever edited without the
# other. Both now consume this single function; there is no second
# implementation of the bootstrap left anywhere in the package.
# ---------------------------------------------------------------------------


def bootstrap_untruncated_state(payload: dict[str, Any]) -> tuple[TruncationMetadata, int]:
    """Returns the stable (applied=False) `TruncationMetadata` for `payload`
    together with its exact untruncated length, once `original_chars` has
    reached its own self-consistent fixed point."""
    base_truncation, original_chars = stabilize_payload_truncation(
        payload,
        TruncationMetadata(applied=False, original_chars=0, emitted_chars=0),
    )
    untruncated_truncation, untruncated_len = stabilize_payload_truncation(
        payload,
        TruncationMetadata(
            applied=False,
            original_chars=original_chars,
            emitted_chars=base_truncation.emitted_chars,
        ),
    )
    return stabilize_payload_truncation(
        payload,
        untruncated_truncation.model_copy(update={"original_chars": untruncated_len}),
    )


def bootstrap_original_chars(payload: dict[str, Any]) -> int:
    return bootstrap_untruncated_state(payload)[1]


# ---------------------------------------------------------------------------
# The shared projection (P1).
# ---------------------------------------------------------------------------


def project_min_hunk_preserving_chars(
    *,
    intake: ReviewIntake,
    chunk_files: list[str],
    chunk_contracts: list[str],
    semantic_group: str,
    max_blocks: int,
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
    chunk_id = worst_case_chunk_id(max_blocks)
    order_index = worst_case_order_index(max_blocks)
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
        # C8: a non-empty block can be binary-only or metadata-only, with
        # no line-level content to review -- embedding it as "hunk" text
        # would misrepresent it as reviewable diff content.
        if hunk and block_has_observable_textual_hunk(hunk):
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
    aux_ctx = aux_context(intake, chunk_files=chunk_files_sorted)
    limitations.extend(contract_limitations)
    limitations.extend(check_limitations)
    limitations.extend(evidence_limitations)
    deduped_limitations = _dedupe(limitations)

    def base_payload_body(chunk_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": chunk_id,
            "semantic_group": semantic_group,
            "order_index": order_index,
            "target": dict(target),
            "brief": {
                **brief_target,
                "review_mode": brief_review.get("mode"),
                "contract_pack": brief_review.get("contract_pack"),
                "required_files": list(brief_required_files),
                # C7 (P3): the real builder's pr_brief always dedupes
                # brief_limitations before publishing pr_brief.limitations
                # (pr_brief.py's own _dedupe), and the real chunk payload
                # embeds that deduped list verbatim -- project the same
                # representation, not the raw, possibly-duplicated input.
                "limitations": _dedupe(brief_limitations),
            },
            "chunk_context": chunk_context,
            "coverage": {
                "declared_coverage": "complete",
                "files_in_chunk": [item["path"] for item in display_files],
                "chunk_file_count": len(display_files),
                "hunks_included": len(chunk_hunks_full),
                "chunk_plan_limitations": [],
            },
            "response_contract": build_chunk_response_contract(chunk_id=chunk_id, semantic_group=semantic_group),
            "warnings": [],
            "limitations": deduped_limitations,
            "created_at": created_at,
        }

    # Exact original_chars (P2-7): rather than an empirically-sized
    # placeholder with no proven bound, build the FULL (real, non-minimal)
    # payload -- the same shape `_build_chunk_payload` constructs before
    # `_apply_payload_budget` ever runs -- and bootstrap its true
    # untruncated length the same way the real shrink loop establishes its
    # own `original_chars`. This is exact, not approximate: same content,
    # same sanitizer, same bootstrap.
    full_payload_body = base_payload_body(
        {
            "files": display_files,
            "chunk_hunks": chunk_hunks_full,
            "contracts_context": clean_contracts_context_for_payload(contracts_ctx),
            "evidence_context": evidence_ctx,
            "checks_context": checks_ctx,
            "aux_context": aux_ctx,
        }
    )
    sanitized_full = sanitize_artifact_value(full_payload_body)
    exact_original_chars = bootstrap_original_chars(sanitized_full)

    # The terminal (minimal-non-hunk) state, measured against that exact
    # original_chars. The real shrink loop only ever measures an
    # in-progress payload with `applied=True` and
    # `truncation_reason="max_chars_exceeded"` baked in -- both add real
    # characters a naive `applied=False` measurement of the same minimal
    # content would miss, which is precisely how an earlier revision of
    # this projection under-estimated the real floor and let
    # `chunk_hunks_reduced` still fire at the projected budget. Every
    # non-hunk section is marked "omitted" here regardless of whether that
    # section actually had anything to shrink (`checks_context`, e.g., is
    # already at its floor whenever no checks document applies): a longer
    # `omitted_sections`/`coverage_impact` list can only ever make the
    # measured length larger, so this stays a safe over-estimate rather
    # than requiring the projection to replay the real loop's exact step
    # order.
    minimal_payload_body = base_payload_body(
        {
            "files": display_files,
            "chunk_hunks": chunk_hunks_full,
            "contracts_context": minimal_contracts_context(contracts_ctx),
            "evidence_context": minimal_evidence_context(evidence_ctx),
            "checks_context": minimal_checks_context(checks_ctx),
            "aux_context": dict(MINIMAL_AUX_CONTEXT),
        }
    )
    sanitized_minimal = sanitize_artifact_value(minimal_payload_body)
    worst_case_truncation = TruncationMetadata(
        applied=True,
        original_chars=exact_original_chars,
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
    _, projected_chars = stabilize_payload_truncation(sanitized_minimal, worst_case_truncation)
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
