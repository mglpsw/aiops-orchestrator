"""Shared fail-closed chunk ID rules for payload and response artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from app.agent_review.redaction import sanitize_artifact_value


CHUNK_PLAN_CHUNK_ID_INVALID = "chunk_plan_chunk_id_invalid"
_ALLOWED_FILENAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


class ChunkArtifactIdError(ValueError):
    def __init__(self) -> None:
        message = "chunk plan contains a chunk_id that is incompatible with artifact filenames"
        super().__init__(message)
        self.error_class = CHUNK_PLAN_CHUNK_ID_INVALID
        self.message = message


def validate_chunk_id(chunk_id: object, *, artifact_root: Path | None = None) -> str:
    if not isinstance(chunk_id, str) or not chunk_id or chunk_id.strip() != chunk_id:
        raise ChunkArtifactIdError()
    if chunk_id in {".", ".."} or "/" in chunk_id or "\\" in chunk_id:
        raise ChunkArtifactIdError()
    if any(ord(character) < 32 or ord(character) == 127 for character in chunk_id):
        raise ChunkArtifactIdError()
    if any(character not in _ALLOWED_FILENAME_CHARS for character in chunk_id):
        raise ChunkArtifactIdError()
    if sanitize_artifact_value(chunk_id) != chunk_id:
        raise ChunkArtifactIdError()

    filename = f"{chunk_id}.json"
    root = (artifact_root or Path("/chunk-artifacts")).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root or candidate.name != filename:
        raise ChunkArtifactIdError()
    return chunk_id


def chunk_artifact_filename(chunk_id: object, *, artifact_root: Path | None = None) -> str:
    validated = validate_chunk_id(chunk_id, artifact_root=artifact_root)
    return f"{validated}.json"


def validate_chunk_ids(chunk_ids: Iterable[object]) -> None:
    for chunk_id in chunk_ids:
        validate_chunk_id(chunk_id)
