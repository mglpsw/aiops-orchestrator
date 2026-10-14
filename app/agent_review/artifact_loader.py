"""Profile-driven artifact loading for AgentReview intake."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import ArtifactDeclaration, ArtifactStatus, LoadedArtifact
from app.agent_review.source_boundary import (
    ARTIFACT_MAX_BYTES,
    SOURCE_STATE_ABSENT,
    SOURCE_STATE_OVERSIZE,
    SOURCE_STATE_PRESENT,
    SOURCE_STATE_UNSAFE,
    SOURCE_STATE_UNSUPPORTED,
    STRUCTURE_MAX_DEPTH,
    STRUCTURE_MAX_NODES_ARTIFACT,
    YAML_SOURCE_MAX_BYTES,
    acquire_source,
    parse_yaml_text,
    scan_structure,
)


class ArtifactLoadResult:
    def __init__(
        self,
        *,
        artifacts: dict[str, LoadedArtifact],
        artifact_status: list[ArtifactStatus],
        limitations: list[str],
    ) -> None:
        self.artifacts = artifacts
        self.artifact_status = artifact_status
        self.limitations = limitations


def load_declared_artifacts(
    *,
    agent_dir: Path | str,
    declarations: Iterable[ArtifactDeclaration],
    redaction_state: RedactionState,
) -> ArtifactLoadResult:
    root = Path(agent_dir)
    artifacts: dict[str, LoadedArtifact] = {}
    statuses: list[ArtifactStatus] = []
    limitations: list[str] = []

    for declaration in declarations:
        relative_path, path_limitations = _resolve_declared_path(declaration.path)
        if path_limitations:
            status = ArtifactStatus(
                name=declaration.name,
                path=declaration.path,
                available=False,
                valid=False,
                status="degraded",
                limitations=path_limitations,
                error_class="artifact_path_invalid",
            )
            statuses.append(status)
            limitations.extend(_artifact_limitations(declaration, path_limitations))
            continue

        # One acquisition decides presence AND reads the bytes (no `exists()` probe, no link followed, bounded,
        # regular file, single link). Only ENOENT is "missing"; a dangling link, a hard link, a FIFO, an oversize or
        # unreadable entry is a typed INVALID artifact, never absence.
        max_bytes = YAML_SOURCE_MAX_BYTES if declaration.kind == "yaml" else ARTIFACT_MAX_BYTES
        acquisition = acquire_source(root, relative_path or "", max_bytes=max_bytes)
        if acquisition.state == SOURCE_STATE_ABSENT:
            status = ArtifactStatus(
                name=declaration.name,
                path=declaration.path,
                available=False,
                valid=False,
                status="missing",
                limitations=["artifact_missing"],
            )
            statuses.append(status)
            if declaration.required:
                limitations.append(f"required_artifact_missing:{declaration.name}")
            continue

        if acquisition.state != SOURCE_STATE_PRESENT or acquisition.data is None:
            raw_content, error_class = None, _acquisition_error_class(acquisition.state, acquisition.reason)
        else:
            raw_content, error_class = _load_content(acquisition.data, declaration.kind)
        if error_class:
            status = ArtifactStatus(
                name=declaration.name,
                path=declaration.path,
                available=True,
                valid=False,
                status="invalid",
                limitations=["artifact_invalid"],
                error_class=error_class,
            )
            statuses.append(status)
            limitations.append(f"artifact_invalid:{declaration.name}")
            continue

        redaction_state.record_file()
        sanitized = redact_value(raw_content, redaction_state)
        artifacts[declaration.name] = LoadedArtifact(
            name=declaration.name,
            path=declaration.path,
            kind=declaration.kind,
            content=sanitized,
        )
        statuses.append(
            ArtifactStatus(
                name=declaration.name,
                path=declaration.path,
                available=True,
                valid=True,
                status="available",
                limitations=[],
            )
        )

    return ArtifactLoadResult(artifacts=artifacts, artifact_status=statuses, limitations=_dedupe(limitations))


def _resolve_declared_path(declared_path: str) -> tuple[str | None, list[str]]:
    """Lexical admission of a declared path; containment itself is structural (descriptor-relative, no link)."""
    candidate = Path(declared_path)
    if candidate.is_absolute():
        return None, ["artifact_path_absolute"]
    if ".." in candidate.parts or not candidate.parts:
        return None, ["artifact_path_escapes_agent_dir"]
    return candidate.as_posix(), []


def _acquisition_error_class(state: str, reason: str) -> str:
    """Typed artifact error class. `artifact_unreadable` (a directory, an unreadable file) is the historical class and
    is preserved; links, hard links and non-directory parents are `artifact_unsafe`."""
    if state == SOURCE_STATE_OVERSIZE:
        return "artifact_oversize"
    if state == SOURCE_STATE_UNSAFE and reason != "not_a_regular_file":
        return "artifact_unsafe"
    if state == SOURCE_STATE_UNSUPPORTED:
        return "artifact_unsupported"
    return "artifact_unreadable"


def _load_content(data: bytes, kind: str) -> tuple[Any, str | None]:
    """Decode and parse already-acquired bytes. The structural bound runs BEFORE redaction (which recurses and
    expands shared YAML sub-structures)."""
    try:
        # `Path.read_text` translated universal newlines; the bytes path keeps that observable behaviour.
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if kind == "json":
            content: Any = json.loads(text)
        elif kind == "yaml":
            content = parse_yaml_text(text) or {}
        else:
            return text, None
    except json.JSONDecodeError:
        return None, "json_invalid"
    except yaml.YAMLError:
        return None, "yaml_invalid"
    except UnicodeDecodeError:
        # A declared artifact with non-UTF-8 content is reported as an invalid ArtifactStatus, never a traceback.
        return None, "artifact_unreadable"
    except RecursionError:
        return None, "artifact_structure_limit"
    except Exception:  # noqa: BLE001 - ValueError/KeyError/AttributeError raised while constructing YAML tags
        return None, "yaml_invalid" if kind == "yaml" else "json_invalid"
    verdict = scan_structure(content, max_depth=STRUCTURE_MAX_DEPTH, max_nodes=STRUCTURE_MAX_NODES_ARTIFACT)
    if verdict.limit is not None:
        return None, "artifact_structure_limit"
    return content, None


def _artifact_limitations(declaration: ArtifactDeclaration, limitations: list[str]) -> list[str]:
    if declaration.required:
        return [f"required_artifact_invalid:{declaration.name}", *limitations]
    return [f"artifact_invalid:{declaration.name}", *limitations]


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped

