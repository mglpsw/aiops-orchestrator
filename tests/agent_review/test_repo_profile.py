from __future__ import annotations

from pathlib import Path

from app.agent_review.repo_profile import load_repo_profile


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "agentescala_minimal"


def test_repo_profile_loads_valid_agentescala_profile() -> None:
    result = load_repo_profile(FIXTURE_ROOT, target_repo="mglpsw/AgentEscala")

    assert result.status == "complete"
    assert result.error_class is None
    assert result.profile.schema_version == "agent-review.target-profile.v1"
    assert result.profile.target_repo == "mglpsw/AgentEscala"
    assert [artifact.name for artifact in result.profile.artifacts] == [
        "checks.json",
        "test-intelligence.json",
        "file-diff-context.json",
        "local-code-intelligence.json",
        "validation-evidence-result.json",
    ]
    assert result.profile.domain_contracts
    assert result.profile.review_packs


def test_repo_profile_missing_becomes_degraded_limitation(tmp_path: Path) -> None:
    result = load_repo_profile(tmp_path, target_repo="mglpsw/AgentEscala")

    assert result.status == "degraded"
    assert result.error_class is None
    assert "repo_profile_missing" in result.limitations
    assert "repo_profile_missing" in result.profile.limitations


def test_repo_profile_invalid_yaml_becomes_failed_yaml_invalid(tmp_path: Path) -> None:
    aiops_dir = tmp_path / ".aiops"
    aiops_dir.mkdir()
    (aiops_dir / "repo-profile.yaml").write_text("artifacts: [", encoding="utf-8")

    result = load_repo_profile(tmp_path, target_repo="mglpsw/AgentEscala")

    assert result.status == "failed"
    assert result.error_class == "yaml_invalid"
    assert "repo_profile_yaml_invalid" in result.limitations

