"""Profile-driven artifact loading for AgentReview intake."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.schemas import ArtifactDeclaration, ArtifactStatus, LoadedArtifact


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
    root = Path(agent_dir).resolve()
    artifacts: dict[str, LoadedArtifact] = {}
    statuses: list[ArtifactStatus] = []
    limitations: list[str] = []

    for declaration in declarations:
        safe_path, path_limitations = _resolve_declared_path(root, declaration.path)
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

        if safe_path is None or not safe_path.exists():
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

        raw_content, error_class = _load_content(safe_path, declaration.kind)
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


def _resolve_declared_path(root: Path, declared_path: str) -> tuple[Path | None, list[str]]:
    candidate = Path(declared_path)
    if candidate.is_absolute():
        return None, ["artifact_path_absolute"]
    if ".." in candidate.parts:
        return None, ["artifact_path_escapes_agent_dir"]

    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None, ["artifact_path_escapes_agent_dir"]
    return resolved, []


def _load_content(path: Path, kind: str) -> tuple[Any, str | None]:
    try:
        if kind == "json":
            return json.loads(path.read_text(encoding="utf-8")), None
        if kind == "yaml":
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}, None
        return path.read_text(encoding="utf-8"), None
    except json.JSONDecodeError:
        return None, "json_invalid"
    except yaml.YAMLError:
        return None, "yaml_invalid"


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

