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
    """Validated caller-selected input file with ingress-owned reads."""

    _resolved_path: Path
    _root: Path | None
    _entry_name: str | None = None

    @property
    def resolved_path(self) -> Path:
        return self._resolved_path

    @property
    def entry_name(self) -> str:
        """The caller-visible name of this entry, BEFORE symlink
        resolution -- e.g. the directory-entry name `iter_input_files()`
        enumerated, not the name of whatever a symlink entry resolves to.

        Enumeration/matching decisions that are meant to operate on what
        the caller actually presented (e.g. "does this entry's name end in
        `.json`") must use this, not `resolved_path.name`: resolving
        symlinks is still correct and necessary for the actual read/
        containment-safety check, but it must not silently change what
        counts as a `.json` entry for enumeration purposes (#200-G4B
        post-merge Codex P2). Falls back to `resolved_path.name` for a
        capability built directly by `validate_external_input_file_v2`
        (not through directory enumeration), where there is no separate
        pre-resolution entry name to preserve.
        """

        return self._entry_name if self._entry_name is not None else self._resolved_path.name

    def read_bytes(self) -> bytes:
        # Re-resolve and re-check containment immediately before the read. Use
        # Path.read_bytes() inside THIS authority so existing independent tests
        # that inject a real read failure still exercise the exact operation;
        # the method choice changes, ownership does not.
        resolved = _resolve_v2(self._resolved_path)
        _enforce_containment_v2(resolved, root=self._root)
        try:
            return resolved.read_bytes()
        except FileNotFoundError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc

    def read_text(self, *, encoding: str = "utf-8") -> str:
        try:
            return self.read_bytes().decode(encoding)
        except UnicodeDecodeError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc

    def read_bytes_bounded(self, max_bytes: int) -> bytes:
        """Read at most ``max_bytes + 1`` bytes without ever materializing
        more of the file into memory.

        G4B (#200-G4B) changed a prior read sequence of
        ``stat size -> compare against max_bytes -> read file`` to
        ``read entire file -> len(raw_bytes) -> compare against max_bytes``,
        so a size-limited caller (e.g. a target-profile artifact) was fully
        materialized into memory BEFORE the limit meant to protect against
        exactly that. Re-introducing a pre-read ``stat()`` check would only
        trade this bug for its own TOCTOU (the file can grow between the
        ``stat`` and the ``read``). Reading a bounded ``max_bytes + 1``
        window is race-free either way: if fewer than or exactly
        ``max_bytes`` bytes come back, that IS the complete, correctly
        bounded content; if ``max_bytes + 1`` bytes come back, the caller
        refuses for being oversized, and this method never read more than
        one byte past the limit to find that out.

        The caller owns the oversized-vs-not decision (and its own typed
        reason code) -- this method's contract is purely mechanical: never
        read more than ``max_bytes + 1`` bytes.
        """

        resolved = _resolve_v2(self._resolved_path)
        _enforce_containment_v2(resolved, root=self._root)
        try:
            with resolved.open("rb") as handle:
                return handle.read(max_bytes + 1)
        except FileNotFoundError as exc:
            raise ExternalPathIngressError(EXTERNAL_PATH_MISSING_REASON_V2) from exc
        except OverflowError as exc:
            # Defense in depth (post-#200-G4B Codex review of PR #296):
            # `max_bytes` is caller-supplied with no guarantee here that it
            # was already schema-bounded (`TargetArtifactV2.max_bytes` now
            # is, but this is a general-purpose primitive, not something
            # that should rely on every future caller getting that right).
            # `.read()` converts its argument to a `Py_ssize_t`; a value at
            # or beyond `sys.maxsize` overflows that conversion and raises
            # a raw `OverflowError`. Same discipline as every other read
            # failure this method already converts: typed refusal, never a
            # raw crash.
            raise ExternalPathIngressError(EXTERNAL_PATH_UNREADABLE_REASON_V2) from exc
        except (OSError, RuntimeError, ValueError) as exc:
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
            entry_resolved = _resolve_v2(entry)
            _enforce_containment_v2(entry_resolved, root=self._root)
            mode = _stat_v2(entry_resolved).st_mode
            if stat.S_ISREG(mode):
                files.append(ExternalInputFileV2(entry_resolved, self._root, entry.name))
        return tuple(sorted(files, key=lambda item: item.entry_name))


@dataclass(frozen=True)
class ExternalOutputPathV2:
    """Caller-selected output path; target itself may not yet exist."""

    _resolved_path: Path
    _root: Path | None

    @property
    def resolved_path(self) -> Path:
        return self._resolved_path

    def open_binary_exclusive(self):
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
    resolved = _resolve_v2(_as_path_v2(raw_path))
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
