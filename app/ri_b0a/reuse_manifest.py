"""Loader and markdown-view renderer for the #119.2 AgentReview/ProjectOps
reuse manifest (`config/ri/ri-b0a-2-reuse-manifest.json`).

This module does not define, redefine, or copy any AgentReview or CAEM
schema. It only classifies already-existing contract IDs (verified against
real files on disk) into one of four closed states -- `reuse`, `reference`,
`not_applicable`, `future_adapter` -- and renders that classification as a
deterministic markdown view. Loading is total and fail-closed: malformed
input never raises, it returns ``ReuseManifestLoadResult(ok=False, ...)``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

REUSE_STATES: frozenset[str] = frozenset({"reuse", "reference", "not_applicable", "future_adapter"})

_REQUIRED_TOP_LEVEL_KEYS = {"contract_id", "schema_version", "states", "entries", "generated_from"}
_REQUIRED_ENTRY_KEYS = {"contract_id", "owner", "state", "notes", "ri_b0_role", "source_path"}
_MANIFEST_CONTRACT_ID = "aiops.ri-b0a-2-reuse-manifest.v1"
_SCHEMA_ID_LITERAL_RE = re.compile(r'schema_id:\s*Literal\["([a-z0-9.-]+)"\]')

# Every real AgentReview v2 schema_id maps to a same-named top-level file
# under schemas/agent-review/v2/ (schema_export_v2.py's own filename
# convention is exactly f"{schema_id}.schema.json"), EXCEPT
# agent-review.chunk-response.v2: `ChunkReviewResultV2` has its own
# schema_id but no top-level file of its own -- it's embedded as a $defs
# entry inside its enclosing agent-review.chunk-response-envelope.v2
# schema file. This is the one, currently-true, structural exception;
# adding a second AgentReview contract this way requires extending this
# table explicitly, not guessing a filename convention.
_CANONICAL_FILENAME_EXCEPTIONS: dict[str, str] = {
    "agent-review.chunk-response.v2": "agent-review.chunk-response-envelope.v2.schema.json",
}


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON object keys instead of silently keeping the
    last value, matching `app/caem_consumer/f0.py`'s own established
    discipline for this exact fail-closed contract-loading concern."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReuseManifestError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _is_strict_int(value: object) -> bool:
    """`True`/`False` are NOT accepted where an integer is required --
    `bool` is a subclass of `int` in Python (`True == 1`), matching
    `app/caem_consumer/f0.py`'s own established discipline for this exact
    concern."""
    return type(value) is int


def _real_agent_review_schema_ids(repo_root: Path) -> frozenset[str]:
    """Every `schema_id: Literal["..."]` declaration actually present in
    `app/agent_review/*.py`. Used to verify an entry's `contract_id` isn't
    a typo/fabrication that merely happens to point at an existing but
    unrelated file."""

    agent_review_dir = repo_root / "app" / "agent_review"
    ids: set[str] = set()
    if agent_review_dir.is_dir():
        for py_file in agent_review_dir.glob("*.py"):
            ids.update(_SCHEMA_ID_LITERAL_RE.findall(py_file.read_text(encoding="utf-8")))
    return frozenset(ids)


class ReuseManifestError(ValueError):
    """Raised only internally; `load_reuse_manifest` always catches this and
    returns a `ReuseManifestLoadResult(ok=False, ...)` instead of letting it
    escape."""


@dataclass(frozen=True)
class ReuseManifestEntry:
    contract_id: str
    owner: str
    state: str
    notes: str
    ri_b0_role: str
    source_path: str | None


@dataclass(frozen=True)
class ReuseManifestLoadResult:
    ok: bool
    entries: tuple[ReuseManifestEntry, ...] = ()
    errors: tuple[str, ...] = ()


def _require_dict(value: object, label: str) -> dict:
    if not isinstance(value, dict):
        raise ReuseManifestError(f"{label} must be a JSON object")
    return value


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReuseManifestError(f"{label} must be a non-empty string")
    return value


def _require_list(value: object, label: str) -> list:
    if not isinstance(value, list):
        raise ReuseManifestError(f"{label} must be a JSON array")
    return value


def _load_entry(
    raw: object, *, repo_root: Path, index: int, real_agent_review_schema_ids: frozenset[str]
) -> ReuseManifestEntry:
    entry = _require_dict(raw, f"entries[{index}]")
    extra_keys = set(entry.keys()) - _REQUIRED_ENTRY_KEYS
    if extra_keys:
        raise ReuseManifestError(f"entries[{index}] has unexpected keys: {sorted(extra_keys)}")
    missing_keys = _REQUIRED_ENTRY_KEYS - set(entry.keys())
    if missing_keys:
        raise ReuseManifestError(f"entries[{index}] is missing keys: {sorted(missing_keys)}")

    contract_id = _require_str(entry["contract_id"], f"entries[{index}].contract_id")
    owner = _require_str(entry["owner"], f"entries[{index}].owner")
    state = _require_str(entry["state"], f"entries[{index}].state")
    notes = _require_str(entry["notes"], f"entries[{index}].notes")
    ri_b0_role = _require_str(entry["ri_b0_role"], f"entries[{index}].ri_b0_role")

    if state not in REUSE_STATES:
        raise ReuseManifestError(
            f"entries[{index}].state {state!r} is not one of {sorted(REUSE_STATES)}"
        )

    source_path_raw = entry["source_path"]
    source_path: str | None

    # Correction, found across three independent Codex review rounds: every
    # earlier version of this check was gated on some property of the
    # SUPPLIED source_path (its prefix, its existence, its filename) --
    # which the manifest author fully controls and can therefore always
    # route around (null it out, point it elsewhere, swap it with another
    # entry's). The only gate that cannot be spoofed this way is the
    # contract_id's OWN namespace: if it claims to be an AgentReview
    # contract at all (the "agent-review." prefix), it is checked in full,
    # unconditionally on whatever source_path happens to say.
    if contract_id.startswith("agent-review."):
        if contract_id not in real_agent_review_schema_ids:
            raise ReuseManifestError(
                f"entries[{index}].contract_id {contract_id!r} claims the agent-review. "
                "namespace but is not a real schema_id declared in app/agent_review/*.py"
            )
        expected_filename = _CANONICAL_FILENAME_EXCEPTIONS.get(contract_id, f"{contract_id}.schema.json")
        expected_source_path = f"schemas/agent-review/v2/{expected_filename}"
        if source_path_raw != expected_source_path:
            raise ReuseManifestError(
                f"entries[{index}].contract_id {contract_id!r} is a known AgentReview schema_id "
                f"and must use source_path {expected_source_path!r}, got {source_path_raw!r}"
            )

    if source_path_raw is None:
        source_path = None
    else:
        source_path = _require_str(source_path_raw, f"entries[{index}].source_path")
        if source_path.startswith("/") or ".." in Path(source_path).parts:
            raise ReuseManifestError(f"entries[{index}].source_path is unsafe: {source_path!r}")
        resolved = (repo_root / source_path).resolve()
        if repo_root.resolve() not in resolved.parents and resolved != repo_root.resolve():
            raise ReuseManifestError(f"entries[{index}].source_path escapes repo_root: {source_path!r}")
        if not resolved.is_file():
            raise ReuseManifestError(
                f"entries[{index}].source_path does not exist: {source_path!r}"
            )

    return ReuseManifestEntry(
        contract_id=contract_id,
        owner=owner,
        state=state,
        notes=notes,
        ri_b0_role=ri_b0_role,
        source_path=source_path,
    )


def load_reuse_manifest(manifest_path: str | Path, *, repo_root: str | Path) -> ReuseManifestLoadResult:
    """Load, structurally validate, and cross-check `manifest_path` against
    real files under `repo_root`. Total and fail-closed: never raises."""

    try:
        root_path = Path(manifest_path)
        root_repo = Path(repo_root)
        try:
            raw_text = root_path.read_text(encoding="utf-8")
        except OSError as exc:
            return ReuseManifestLoadResult(ok=False, errors=(f"cannot read manifest: {exc!r}",))
        try:
            doc = json.loads(raw_text, object_pairs_hook=_duplicate_keys)
        except (json.JSONDecodeError, ReuseManifestError) as exc:
            return ReuseManifestLoadResult(ok=False, errors=(f"manifest is not valid JSON: {exc!r}",))

        root = _require_dict(doc, "manifest root")
        extra_top = set(root.keys()) - _REQUIRED_TOP_LEVEL_KEYS
        if extra_top:
            raise ReuseManifestError(f"manifest has unexpected top-level keys: {sorted(extra_top)}")
        missing_top = _REQUIRED_TOP_LEVEL_KEYS - set(root.keys())
        if missing_top:
            raise ReuseManifestError(f"manifest is missing top-level keys: {sorted(missing_top)}")

        contract_id = _require_str(root["contract_id"], "manifest.contract_id")
        if contract_id != _MANIFEST_CONTRACT_ID:
            raise ReuseManifestError(
                f"manifest.contract_id {contract_id!r} != expected {_MANIFEST_CONTRACT_ID!r}"
            )
        if not _is_strict_int(root.get("schema_version")) or root.get("schema_version") != 1:
            raise ReuseManifestError(f"manifest.schema_version must be 1, got {root.get('schema_version')!r}")
        _require_str(root["generated_from"], "manifest.generated_from")

        declared_states = _require_list(root["states"], "manifest.states")
        if set(declared_states) != REUSE_STATES:
            raise ReuseManifestError(
                f"manifest.states {sorted(declared_states)} != expected {sorted(REUSE_STATES)}"
            )

        raw_entries = _require_list(root["entries"], "manifest.entries")
        if not raw_entries:
            raise ReuseManifestError("manifest.entries must be non-empty")

        real_agent_review_schema_ids = _real_agent_review_schema_ids(root_repo)
        entries: list[ReuseManifestEntry] = []
        seen_contract_ids: set[str] = set()
        for index, raw_entry in enumerate(raw_entries):
            entry = _load_entry(
                raw_entry,
                repo_root=root_repo,
                index=index,
                real_agent_review_schema_ids=real_agent_review_schema_ids,
            )
            if entry.contract_id in seen_contract_ids:
                raise ReuseManifestError(f"duplicate entries[].contract_id: {entry.contract_id!r}")
            seen_contract_ids.add(entry.contract_id)
            entries.append(entry)

        # Completeness, not just per-entry correctness: this manifest's
        # whole purpose is to classify EVERY real AgentReview contract, not
        # merely to be internally consistent about whichever ones it
        # happens to still mention. Deleting an entry entirely (as opposed
        # to corrupting one) previously passed silently.
        missing_ids = real_agent_review_schema_ids - seen_contract_ids
        if missing_ids:
            raise ReuseManifestError(
                f"manifest omits real AgentReview schema_id(s): {sorted(missing_ids)}"
            )

        return ReuseManifestLoadResult(ok=True, entries=tuple(entries))
    except ReuseManifestError as exc:
        return ReuseManifestLoadResult(ok=False, errors=(str(exc),))
    except Exception as exc:  # pragma: no cover - defensive, total loader
        return ReuseManifestLoadResult(ok=False, errors=(f"unexpected error: {exc!r}",))


_STATE_HEADINGS: tuple[tuple[str, str], ...] = (
    ("reuse", "Reuse — consumed as-is by RI-B0"),
    ("reference", "Reference — cited for provenance, not consumed directly"),
    ("future_adapter", "Future adapter — needs a translation layer once both sides are real"),
    ("not_applicable", "Not applicable — no RI-B0 relevance today"),
)


def render_reuse_view(result: ReuseManifestLoadResult) -> str:
    """Render a deterministic markdown view from an already-loaded,
    successfully-validated manifest. Raises `ReuseManifestError` if
    `result.ok` is false -- callers must load successfully first."""

    if not result.ok:
        raise ReuseManifestError("cannot render a view from a failed load")

    lines: list[str] = [
        "<!-- GENERATED VIEW -- DO NOT EDIT BY HAND.",
        "Regenerate: python scripts/generate-ri-b0a-2-reuse-view.py",
        "Source: config/ri/ri-b0a-2-reuse-manifest.json",
        "-->",
        "",
        "# RI-B0a.2 — AgentReview/ProjectOps reuse and reference mapping",
        "",
        "Generated from `config/ri/ri-b0a-2-reuse-manifest.json`. This view "
        "classifies every existing AgentReview contract this session found "
        "(10 schemas under `schemas/agent-review/v2/`) plus the ProjectOps "
        "track boundary into exactly one of four states: `reuse`, "
        "`reference`, `future_adapter`, `not_applicable`. No CAEM or "
        "AgentReview schema is copied or redefined here.",
        "",
    ]

    by_state: dict[str, list[ReuseManifestEntry]] = {state: [] for state, _ in _STATE_HEADINGS}
    for entry in result.entries:
        by_state[entry.state].append(entry)

    for state, heading in _STATE_HEADINGS:
        entries = by_state[state]
        lines.append(f"## {heading} ({len(entries)})")
        lines.append("")
        if not entries:
            lines.append("_None._")
            lines.append("")
            continue
        lines.append("| Contract ID | Owner | RI-B0 role | Source |")
        lines.append("|---|---|---|---|")
        for entry in entries:
            source = f"`{entry.source_path}`" if entry.source_path else "—"
            lines.append(
                f"| `{entry.contract_id}` | {entry.owner} | {entry.ri_b0_role} | {source} |"
            )
        lines.append("")
        for entry in entries:
            lines.append(f"**`{entry.contract_id}`** — {entry.notes}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
