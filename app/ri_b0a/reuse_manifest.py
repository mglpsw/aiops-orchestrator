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
        if source_path.startswith("schemas/agent-review/v2/"):
            if contract_id not in real_agent_review_schema_ids:
                raise ReuseManifestError(
                    f"entries[{index}].contract_id {contract_id!r} is not a real schema_id "
                    "declared in app/agent_review/*.py -- source_path exists but does not "
                    "establish this contract_id"
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
            doc = json.loads(raw_text)
        except json.JSONDecodeError as exc:
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
        if root.get("schema_version") != 1:
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
