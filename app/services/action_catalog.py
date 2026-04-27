"""Action Catalog — loads and exposes the allowlisted action catalog.

This module is the single source of truth for what actions the Action Planner
may select. It is intentionally isolated from executors, shells, SSH, Docker,
LLM calls, and remediation flows.

Design constraints (v1):
  - Fail-closed: missing or invalid YAML raises CatalogLoadError.
  - Commands are stored internally but NEVER exposed through the API layer.
  - Only mode=readonly entries are valid in v1; others are rejected at load time.
  - action_ids must be unique; duplicates raise CatalogLoadError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import BASE_DIR

DEFAULT_CATALOG_PATH: Path = BASE_DIR / "config" / "actions.yaml"

# Blocked command patterns (mirrors validate_actions_catalog.sh)
_BLOCKED_COMMAND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s"),
    re.compile(r"chmod\s+777"),
    re.compile(r"docker\s+exec\b"),
    re.compile(r"(?<!\w)ssh\b"),
    re.compile(r"\|\s*bash\b"),
    re.compile(r"\|\s*sh\b"),
    re.compile(r"curl\s.*\|\s*\w*sh"),
    re.compile(r"git\s+push\b"),
    re.compile(r"docker[\s-]compose\s+up\b"),
    re.compile(r"systemctl\s+restart\b"),
    re.compile(r"systemctl\s+start\b"),
    re.compile(r"systemctl\s+stop\b"),
    re.compile(r"systemctl\s+disable\b"),
]

_REQUIRED_FIELDS = ("action_id", "risk", "mode", "timeout_seconds", "requires_approval", "command")
_ALLOWED_RISKS_V1 = frozenset({"low"})
_ALLOWED_MODES_V1 = frozenset({"readonly"})


class CatalogLoadError(Exception):
    """Raised when the action catalog cannot be loaded or is invalid."""


@dataclass(frozen=True)
class CatalogEntry:
    """Internal (trusted) representation of one action catalog entry."""

    action_id: str
    description: str
    command: str          # internal only — never exposed via API
    mode: str
    risk: str
    timeout_seconds: int
    requires_approval: bool
    tags: list[str] = field(default_factory=list)


class ActionCatalog:
    """In-memory index of validated catalog entries.

    Instantiate via ``load_catalog()``. Do not construct directly in
    production code unless you are writing tests with a fixture catalog.
    """

    def __init__(self, entries: dict[str, CatalogEntry], version: str = "unknown") -> None:
        self._entries = dict(entries)
        self.version = version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, action_id: str) -> CatalogEntry | None:
        """Return the entry for *action_id*, or None if not found."""
        return self._entries.get(action_id)

    def all_entries(self) -> list[CatalogEntry]:
        return list(self._entries.values())

    @property
    def count(self) -> int:
        return len(self._entries)

    def action_ids(self) -> frozenset[str]:
        return frozenset(self._entries)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_catalog(path: Path | str | None = None) -> ActionCatalog:
    """Load and validate the action catalog from *path* (default: config/actions.yaml).

    Raises ``CatalogLoadError`` on any problem — the caller must handle it.
    The catalog is fail-closed by design.
    """
    resolved = Path(path) if path is not None else DEFAULT_CATALOG_PATH

    if not resolved.exists():
        raise CatalogLoadError(f"Action catalog not found: {resolved}")

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogLoadError(f"Action catalog YAML is invalid: {exc}") from exc

    if not isinstance(raw, dict):
        raise CatalogLoadError("Action catalog root must be a YAML mapping")

    version = str(raw.get("version", "unknown"))
    raw_catalog = raw.get("catalog")

    if not isinstance(raw_catalog, list) or len(raw_catalog) == 0:
        raise CatalogLoadError("Action catalog 'catalog' key must be a non-empty list")

    entries: dict[str, CatalogEntry] = {}
    errors: list[str] = []

    for i, item in enumerate(raw_catalog):
        _validate_entry(i, item, entries, errors)

    if errors:
        raise CatalogLoadError(
            f"Action catalog has {len(errors)} error(s):\n" + "\n".join(f"  - {e}" for e in errors)
        )

    return ActionCatalog(entries, version=version)


# ---------------------------------------------------------------------------
# Internal validation helpers
# ---------------------------------------------------------------------------


def _validate_entry(
    index: int,
    item: Any,
    entries: dict[str, CatalogEntry],
    errors: list[str],
) -> None:
    if not isinstance(item, dict):
        errors.append(f"Item {index} is not a mapping")
        return

    action_id: str = item.get("action_id", f"<item {index}>")

    # Required fields present and non-null
    for field_name in _REQUIRED_FIELDS:
        if field_name not in item:
            errors.append(f"'{action_id}': missing required field '{field_name}'")
            return
        if item[field_name] is None:
            errors.append(f"'{action_id}': field '{field_name}' is null")
            return

    # Unique action_id
    if action_id in entries:
        errors.append(f"Duplicate action_id: '{action_id}'")
        return

    # v1 policy: only readonly mode
    mode = str(item["mode"]).strip()
    if mode not in _ALLOWED_MODES_V1:
        errors.append(f"'{action_id}': mode '{mode}' not allowed in v1 (only: {sorted(_ALLOWED_MODES_V1)})")
        return

    # v1 policy: only low risk
    risk = str(item["risk"]).strip()
    if risk not in _ALLOWED_RISKS_V1:
        errors.append(f"'{action_id}': risk '{risk}' not allowed in v1 (only: {sorted(_ALLOWED_RISKS_V1)})")
        return

    # Blocked command patterns
    command = str(item["command"]).strip()
    for pattern in _BLOCKED_COMMAND_PATTERNS:
        if pattern.search(command):
            errors.append(f"'{action_id}': blocked pattern '{pattern.pattern}' in command")
            return

    entries[action_id] = CatalogEntry(
        action_id=action_id,
        description=str(item.get("description", "")),
        command=command,
        mode=mode,
        risk=risk,
        timeout_seconds=int(item["timeout_seconds"]),
        requires_approval=bool(item["requires_approval"]),
        tags=list(item.get("tags") or []),
    )
