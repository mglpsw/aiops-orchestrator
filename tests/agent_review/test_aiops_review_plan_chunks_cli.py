from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-plan-chunks.py"
FIXTURE_SECRET = "AGENTESCALA_PHASE2_FIXTURE_SECRET"


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


def _intake() -> dict[str, object]:
    return {
        "schema_version": "agent-review.intake.v1",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {"domain_contracts": {"rules": []}},
        "artifacts": {
            "file-diff-context.json": {
                "name": "file-diff-context.json",
                "path": "file-diff-context.json",
                "kind": "json",
                "content": {
                    "files": [
                        {"path": "backend/api/notification_admin.py"},
                        {"path": "frontend/src/pages/admin_notifications_page.jsx"},
                        {"path": f"backend/services/token={FIXTURE_SECRET}.py"},
                    ]
                },
            }
        },
        "artifact_status": [
            {"name": "file-diff-context.json", "available": True, "valid": True, "status": "available"}
        ],
        "status": "complete",
        "limitations": [],
    }


def _write_intake(tmp_path: Path, intake: dict[str, object] | None = None) -> Path:
    path = tmp_path / "aiops-intake.json"
    path.write_text(json.dumps(intake if intake is not None else _intake()), encoding="utf-8")
    return path


def _run_cli(intake: Path, output: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--intake",
            str(intake),
            "--output",
            str(output),
            "--max-blocks",
            "6",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def test_plan_chunks_cli_generates_semantic_chunk_plan(tmp_path: Path) -> None:
    intake = _write_intake(tmp_path)
    output = tmp_path / "semantic-chunk-plan.json"

    result = _run_cli(intake, output, env=_dev_env())

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert payload == {"ok": True, "output_written": True, "status": "partial"}
    assert plan["schema_id"] == "agent-review.semantic-chunk-plan.v1"
    assert plan["target_repo"] == "mglpsw/AgentEscala"
    assert plan["chunks"]


def test_plan_chunks_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    intake = _write_intake(tmp_path)
    output = tmp_path / "semantic-chunk-plan.json"

    result = _run_cli(intake, output, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output.exists()


def test_plan_chunks_cli_passes_on_dev_toolrepo_env(tmp_path: Path) -> None:
    intake = _write_intake(tmp_path)
    output = tmp_path / "semantic-chunk-plan.json"

    result = _run_cli(intake, output, env=_dev_env())

    assert result.returncode == 0
    assert output.exists()


def test_plan_chunks_cli_rejects_invalid_intake(tmp_path: Path) -> None:
    intake = _write_intake(tmp_path, {"schema_version": 1})
    output = tmp_path / "semantic-chunk-plan.json"

    result = _run_cli(intake, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "intake_invalid"
    assert not output.exists()


def test_plan_chunks_cli_output_does_not_contain_fixture_secret(tmp_path: Path) -> None:
    intake = _write_intake(tmp_path)
    output = tmp_path / "semantic-chunk-plan.json"

    result = _run_cli(intake, output, env=_dev_env())

    assert result.returncode == 0
    rendered = output.read_text(encoding="utf-8") + result.stdout
    assert FIXTURE_SECRET not in rendered


def test_plan_chunks_cli_does_not_call_network_or_provider(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setenv("AIOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("AIOPS_NODE_ROLE", "toolrepo")
    monkeypatch.setenv("AIOPS_REPO_MODE", "agent_review_tooling")
    monkeypatch.setenv("AIOPS_PRODUCTION_RUNTIME", "false")
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    intake = _write_intake(tmp_path)
    output = tmp_path / "semantic-chunk-plan.json"
    module = _load_script_module()

    assert module.main(["--intake", str(intake), "--output", str(output), "--max-blocks", "6"]) == 0
    assert output.exists()


def _load_script_module():
    spec = importlib.util.spec_from_file_location("aiops_review_plan_chunks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
