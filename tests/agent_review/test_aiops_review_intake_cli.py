from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-intake.py"
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "agentescala_minimal"
FIXTURE_SECRETS = [
    "AGENTESCALA_FIXTURE_AUTH_SECRET_123456",
    "AGENTESCALA_FIXTURE_TOKEN_SECRET",
    "AGENTESCALA_FIXTURE_DIFF_SECRET",
    "AGENTESCALA_FIXTURE_CLIENT_SECRET",
    "AGENTESCALA_FIXTURE_DB_SECRET",
    "AGENTESCALA_FIXTURE_URL_SECRET",
]


def _dev_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "dev",
            "AIOPS_NODE_ROLE": "toolrepo",
            "AIOPS_REPO_MODE": "agent_review_tooling",
            "AIOPS_PRODUCTION_RUNTIME": "false",
        }
    )
    return env


def _prod_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "prod",
            "AIOPS_NODE_ROLE": "runtime",
            "AIOPS_REPO_MODE": "aiops_runtime",
            "AIOPS_PRODUCTION_RUNTIME": "true",
        }
    )
    return env


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "AgentEscala"
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _run_cli(repo_root: Path, output: Path, report: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--target-repo",
            "mglpsw/AgentEscala",
            "--repo-root",
            str(repo_root),
            "--agent-dir",
            str(repo_root / "artifacts"),
            "--output",
            str(output),
            "--redaction-report",
            str(report),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def test_cli_generates_intake_and_redaction_report(tmp_path: Path) -> None:
    repo_root = _copy_fixture(tmp_path)
    output = tmp_path / "out" / "aiops-intake.json"
    report = tmp_path / "out" / "redaction-report.json"

    result = _run_cli(repo_root, output, report, env=_dev_env())

    assert result.returncode == 0
    intake = json.loads(output.read_text(encoding="utf-8"))
    redaction_report = json.loads(report.read_text(encoding="utf-8"))
    assert intake["schema_version"] == "agent-review.intake.v1"
    assert intake["status"] == "complete"
    assert intake["target_repo"] == "mglpsw/AgentEscala"
    assert redaction_report["schema_version"] == "agent-review.redaction-report.v1"
    assert redaction_report["secret_like_values_found"] >= len(FIXTURE_SECRETS)
    assert redaction_report["output_safe_for_llm"] is True


def test_cli_output_does_not_contain_fixture_secret(tmp_path: Path) -> None:
    repo_root = _copy_fixture(tmp_path)
    output = tmp_path / "aiops-intake.json"
    report = tmp_path / "redaction-report.json"

    result = _run_cli(repo_root, output, report, env=_dev_env())

    assert result.returncode == 0
    combined_output = output.read_text(encoding="utf-8") + report.read_text(encoding="utf-8")
    for secret in FIXTURE_SECRETS:
        assert secret not in combined_output
    assert str(repo_root.resolve()) not in combined_output


def test_cli_rejects_output_inside_repo_root(tmp_path: Path) -> None:
    repo_root = _copy_fixture(tmp_path)
    output = repo_root / "aiops-intake.json"
    report = tmp_path / "redaction-report.json"

    result = _run_cli(repo_root, output, report, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "target_repo_write_blocked"
    assert "target repo must not be modified" in payload["message"]
    assert not output.exists()


def test_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    repo_root = _copy_fixture(tmp_path)
    output = tmp_path / "aiops-intake.json"
    report = tmp_path / "redaction-report.json"

    result = _run_cli(repo_root, output, report, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output.exists()
    assert not report.exists()


def test_cli_passes_on_dev_toolrepo_env(tmp_path: Path) -> None:
    repo_root = _copy_fixture(tmp_path)
    output = tmp_path / "aiops-intake.json"
    report = tmp_path / "redaction-report.json"

    result = _run_cli(repo_root, output, report, env=_dev_env())

    assert result.returncode == 0
    assert output.exists()
    assert report.exists()
