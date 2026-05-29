"""Minimal sanitized evidence references for future AgentReview phases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agent_review.schemas import ArtifactStatus, LoadedArtifact


def build_evidence_index(
    artifacts: Mapping[str, LoadedArtifact],
    artifact_status: Sequence[ArtifactStatus],
) -> list[dict[str, Any]]:
    status_by_name = {status.name: status for status in artifact_status}
    references: list[dict[str, Any]] = []

    for name, artifact in sorted(artifacts.items()):
        status = status_by_name.get(name)
        references.append(
            {
                "artifact": name,
                "kind": artifact.kind,
                "path": artifact.path,
                "present": bool(status and status.available and status.valid),
                "source_ref": f"artifact:{name}",
            }
        )
    return references

