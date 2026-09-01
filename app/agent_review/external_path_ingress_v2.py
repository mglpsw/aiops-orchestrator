"""Central external-path ingress authority for AgentReview v2 (#200-G4B).

The predecessor (#283) tried to close external filesystem failures by adding
``try/except`` at individual call sites. Fresh review kept finding the same
class at sibling sites and with sibling exception types. This module replaces
that open-ended model with one provenance boundary:

    raw caller path -> this authority -> typed capability -> consumer

A capability is intentionally more than a checked ``Path``. Reads and
directory enumeration that are still part of ingress happen through the
capability, so an external filesystem race cannot escape through a downstream
``read_text``/``read_bytes`` after a one-time check.

This module does NOT govern engine-derived paths, controlled execution subjects,
AgentReview temp directories, or v1. It also does not claim to defend against a
host attacker with arbitrary code execution.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

EXTERNAL_PATH_INVALID_REASON_V2 = "external_path_invalid"
EXTERNAL_PATH_RESOLUTION_FAILED_REASON_V2 = "external_path_resolution_failed"
EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2 = "external_path_escapes_root"
EXTERNAL_PATH_MISSING_REASON_V2 = "external_path_missing"
EXTERNAL_PATH_WRONG_TYPE_REASON_V2 = "external_path_wrong_type"
EXTERNAL_PATH_UNREADABLE_REASON_V2 = "external_path_unreadable"
EXTERNAL_DIRECTORY_UNREADABLE_REASON_V2 = "external_directory_unreadable"
EXTERNAL_OUTPUT_PARENT_UNUSABLE_REASON_V2 = "external_output_parent_unusable"
EXTERNAL_OUTPUT_ALREADY_DIRECTORY_REASON_V2 = "external_output_already_directory"


class ExternalPathIngressError(ValueError):
    """Content-free refusal for caller-controlled filesystem material."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _as_path_v2(raw_path: str | os.PathLike[str] | Path) -> Path:
    try:
        path = Path(raw_path)
    except (TypeError, ValueError, OSError) as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_INVALID_REASON_V2) from exc
    if not str(path):
        raise ExternalPathIngressError(EXTERNAL_PATH_INVALID_REASON_V2)
    return path


def _resolve_v2(path: Path) -> Path:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_RESOLUTION_FAILED_REASON_V2) from exc


def _resolve_root_v2(root: str | os.PathLike[str] | Path | None) -> Path | None:
    if root is None:
        return None
    return _resolve_v2(_as_path_v2(root))


def _enforce_containment_v2(path: Path, *, root: Path | None) -> None:
    if root is None:
        return
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_ESCAPES_ROOT_REASON_V2) from exc


def _stat_v2(path: Path):
    try:
        return path.stat()
    except FileNotFoundError as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc


@dataclass(frozen=True)
class ExternalInputFileV2:
    """Validated caller-selected input file.

    Consumers should use these methods rather than reconstructing a raw path and
    performing a second unowned read. Errors remain content/path-free.
    """

    _resolved_path: Path
    _root: Path | None

    @property
    def resolved_path(self) -> Path:
        """Resolved path for APIs that require a Path after ingress validation."""
        return self._resolved_path

    def read_bytes(self) -> bytes:
        # Re-resolve and re-check containment immediately before the read. This
        # does not eliminate host-level TOCTOU, but prevents a stale one-time
        # validation from being treated as permanent authority.
        resolved = _resolve_v2(self._resolved_path)
        _enforce_containment_v2(resolved, root=self._root)
        try:
            with resolved.open("rb") as handle:
                return handle.read()
        except FileNotFoundError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc

    def read_text(self, *, encoding: str = "utf-8") -> str:
        try:
            return self.read_bytes().decode(encoding)
        except UnicodeDecodeError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc


@dataclass(frozen=True)
class ExternalInputDirectoryV2:
    """Validated caller-selected directory with ingress-owned enumeration."""

    _resolved_path: Path
    _root: Path | None

    @property
    def resolved_path(self) -> Path:
        return self._resolved_path

    def iter_input_files(self) -> tuple[ExternalInputFileV2, ...]:
        resolved = _resolve_v2(self._resolved_path)
        _enforce_containment_v2(resolved, root=self._root)
        try:
            entries = tuple(resolved.iterdir())
        except FileNotFoundError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExternalPathIngressError(EXTERNAL_DIRECTORY_UNREADABLE_REASON_V2) from exc

        files: list[ExternalInputFileV2] = []
        for entry in entries:
            try:
                entry_resolved = _resolve_v2(entry)
                _enforce_containment_v2(entry_resolved, root=self._root)
                mode = _stat_v2(entry_resolved).st_mode
            except ExternalPathIngressError:
                raise
            if stat.S_ISREG(mode):
                files.append(ExternalInputFileV2(entry_resolved, self._root))
        return tuple(sorted(files, key=lambda item: item.resolved_path.name))


@dataclass(frozen=True)
class ExternalOutputPathV2:
    """Caller-selected output path; target itself may not yet exist."""

    _resolved_path: Path
    _root: Path | None

    @property
    def resolved_path(self) -> Path:
        return self._resolved_path

    def open_binary_exclusive(self):
        """Open for exclusive creation under the same containment authority."""
        resolved = _resolve_v2(self._resolved_path)
        _enforce_containment_v2(resolved, root=self._root)
        parent = _resolve_v2(resolved.parent)
        _enforce_containment_v2(parent, root=self._root)
        try:
            if not stat.S_ISDIR(parent.stat().st_mode):
                raise ExternalPathIngressError(EXTERNAL_OUTPUT_PARENT_UNUSABLE_REASON_V2)
            return resolved.open("xb")
        except ExternalPathIngressError:
            raise
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ExternalPathIngressError(EXTERNAL_OUTPUT_PARENT_UNUSABLE_REASON_V2) from exc


def validate_external_input_file_v2(
    raw_path: str | os.PathLike[str] | Path,
    *,
    root: str | os.PathLike[str] | Path | None = None,
) -> ExternalInputFileV2:
    resolved_root = _resolve_root_v2(root)
    resolved = _resolve_v2(_as_path_v2(raw_path))
    _enforce_containment_v2(resolved, root=resolved_root)
    mode = _stat_v2(resolved).st_mode
    if not stat.S_ISREG(mode):
        raise ExternalPathIngressError(EXTERNAL_PATH_WRONG_TYPE_REASON_V2)
    # Opening, rather than os.access(), is the meaningful readability probe;
    # os.access() is especially misleading under privileged test runners.
    try:
        with resolved.open("rb"):
            pass
    except FileNotFoundError as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc
    return ExternalInputFileV2(resolved, resolved_root)


def validate_external_input_directory_v2(
    raw_path: str | os.PathLike[str] | Path,
    *,
    root: str | os.PathLike[str] | Path | None = None,
) -> ExternalInputDirectoryV2:
    resolved_root = _resolve_root_v2(root)
    resolved = _resolve_v2(_as_path_v2(raw_path))
    _enforce_containment_v2(resolved, root=resolved_root)
    mode = _stat_v2(resolved).st_mode
    if not stat.S_ISDIR(mode):
        raise ExternalPathIngressError(EXTERNAL_PATH_WRONG_TYPE_REASON_V2)
    try:
        # Force enumeration now; constructing iterdir() alone performs no IO.
        tuple(resolved.iterdir())
    except FileNotFoundError as exc:
        raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExternalPathIngressError(EXTERNAL_DIRECTORY_UNREADABLE_REASON_V2) from exc
    return ExternalInputDirectoryV2(resolved, resolved_root)


def validate_external_output_path_v2(
    raw_path: str | os.PathLike[str] | Path,
    *,
    root: str | os.PathLike[str] | Path | None = None,
) -> ExternalOutputPathV2:
    resolved_root = _resolve_root_v2(root)
    unresolved = _as_path_v2(raw_path)
    resolved = _resolve_v2(unresolved)
    _enforce_containment_v2(resolved, root=resolved_root)
    parent = _resolve_v2(resolved.parent)
    _enforce_containment_v2(parent, root=resolved_root)
    try:
        parent_mode = parent.stat().st_mode
        if not stat.S_ISDIR(parent_mode):
            raise ExternalPathIngressError(EXTERNAL_OUTPUT_PARENT_UNUSABLE_REASON_V2)
        if resolved.exists() and resolved.is_dir():
            raise ExternalPathIngressError(EXTERNAL_OUTPUT_ALREADY_DIRECTORY_REASON_V2)
    except ExternalPathIngressError:
        raise
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise ExternalPathIngressError(EXTERNAL_OUTPUT_PARENT_UNUSABLE_REASON_V2) from exc
    return ExternalOutputPathV2(resolved, resolved_root)
