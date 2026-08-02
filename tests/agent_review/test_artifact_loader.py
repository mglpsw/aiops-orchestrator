from __future__ import annotations

from pathlib import Path

from app.agent_review.artifact_loader import load_declared_artifacts
from app.agent_review.redaction import RedactionState
from app.agent_review.schemas import ArtifactDeclaration


def test_artifact_loader_marks_missing_required_artifact_as_limitation(tmp_path: Path) -> None:
    result = load_declared_artifacts(
        agent_dir=tmp_path,
        declarations=[ArtifactDeclaration(name="checks", path="checks.json", kind="json", required=True)],
        redaction_state=RedactionState(),
    )

    assert result.artifact_status[0].status == "missing"
    assert "required_artifact_missing:checks" in result.limitations


def test_artifact_loader_marks_invalid_json_as_invalid_without_process_crash(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")

    result = load_declared_artifacts(
        agent_dir=tmp_path,
        declarations=[ArtifactDeclaration(name="bad", path="bad.json", kind="json", required=True)],
        redaction_state=RedactionState(),
    )

    assert result.artifact_status[0].status == "invalid"
    assert result.artifact_status[0].error_class == "json_invalid"
    assert result.artifacts == {}


def test_artifact_loader_marks_unreadable_artifact_as_invalid_without_process_crash(tmp_path: Path) -> None:
    """Codex review of PR #49: a declared path resolving to a directory
    (or an unreadable file, or non-UTF-8 content) raised an uncaught
    OSError/UnicodeDecodeError from path.read_text(), crashing the intake
    CLI with a traceback instead of reporting a degraded/invalid
    ArtifactStatus like every other malformed-artifact case."""

    (tmp_path / "bad_dir").mkdir()

    result = load_declared_artifacts(
        agent_dir=tmp_path,
        declarations=[ArtifactDeclaration(name="bad", path="bad_dir", kind="json", required=True)],
        redaction_state=RedactionState(),
    )

    assert result.artifact_status[0].status == "invalid"
    assert result.artifact_status[0].error_class == "artifact_unreadable"
    assert result.artifacts == {}
    assert "artifact_invalid:bad" in result.limitations


def test_artifact_loader_rejects_absolute_artifact_path(tmp_path: Path) -> None:
    result = load_declared_artifacts(
        agent_dir=tmp_path,
        declarations=[ArtifactDeclaration(name="bad", path="/tmp/bad.json", kind="json", required=True)],
        redaction_state=RedactionState(),
    )

    assert result.artifact_status[0].status == "degraded"
    assert result.artifact_status[0].error_class == "artifact_path_invalid"
    assert "artifact_path_absolute" in result.artifact_status[0].limitations


def test_artifact_loader_rejects_escaping_artifact_path(tmp_path: Path) -> None:
    result = load_declared_artifacts(
        agent_dir=tmp_path,
        declarations=[ArtifactDeclaration(name="bad", path="../bad.json", kind="json", required=True)],
        redaction_state=RedactionState(),
    )

    assert result.artifact_status[0].status == "degraded"
    assert result.artifact_status[0].error_class == "artifact_path_invalid"
    assert "artifact_path_escapes_agent_dir" in result.artifact_status[0].limitations

