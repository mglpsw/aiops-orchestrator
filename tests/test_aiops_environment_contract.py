from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_INFO = ROOT / "scripts" / "aiops-env-info.py"
GUARD = ROOT / "scripts" / "guard-aiops-environment.py"


def _clean_env(**updates: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(updates)
    return env


def _run_script(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=ROOT,
        env=env if env is not None else _clean_env(),
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def _json_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def test_env_info_empty_env_returns_unknown_and_limitation() -> None:
    result = _run_script(ENV_INFO)

    assert result.returncode == 0
    payload = _json_stdout(result)
    assert payload == {
        "schema_version": 1,
        "source": "aiops-env-info",
        "environment": "unknown",
        "node_role": "unknown",
        "repo_mode": "unknown",
        "production_runtime": False,
        "agent_review_tooling_allowed": False,
        "limitations": ["environment_not_declared"],
    }


def test_env_info_ct102_prod_runtime_disallows_agent_review_tooling() -> None:
    result = _run_script(
        ENV_INFO,
        env=_clean_env(
            AIOPS_ENVIRONMENT="prod",
            AIOPS_NODE_ROLE="runtime",
            AIOPS_REPO_MODE="aiops_runtime",
            AIOPS_PRODUCTION_RUNTIME="true",
        ),
    )

    assert result.returncode == 0
    payload = _json_stdout(result)
    assert payload["environment"] == "prod"
    assert payload["node_role"] == "runtime"
    assert payload["repo_mode"] == "aiops_runtime"
    assert payload["production_runtime"] is True
    assert payload["agent_review_tooling_allowed"] is False
    assert payload["limitations"] == []


def test_env_info_ct104_dev_toolrepo_allows_agent_review_tooling() -> None:
    result = _run_script(
        ENV_INFO,
        env=_clean_env(
            AIOPS_ENVIRONMENT="dev",
            AIOPS_NODE_ROLE="toolrepo",
            AIOPS_REPO_MODE="agent_review_tooling",
            AIOPS_PRODUCTION_RUNTIME="false",
        ),
    )

    assert result.returncode == 0
    payload = _json_stdout(result)
    assert payload["environment"] == "dev"
    assert payload["node_role"] == "toolrepo"
    assert payload["repo_mode"] == "agent_review_tooling"
    assert payload["production_runtime"] is False
    assert payload["agent_review_tooling_allowed"] is True
    assert payload["limitations"] == []


def test_guard_agent_review_tooling_fails_in_prod_runtime() -> None:
    result = _run_script(
        GUARD,
        "--require-mode",
        "agent_review_tooling",
        env=_clean_env(
            AIOPS_ENVIRONMENT="prod",
            AIOPS_NODE_ROLE="runtime",
            AIOPS_REPO_MODE="aiops_runtime",
            AIOPS_PRODUCTION_RUNTIME="true",
        ),
    )

    assert result.returncode == 1
    assert result.stdout.strip() == "Blocked: agent_review_tooling is not allowed on production runtime."


def test_guard_agent_review_tooling_passes_in_dev_toolrepo() -> None:
    result = _run_script(
        GUARD,
        "--require-mode",
        "agent_review_tooling",
        env=_clean_env(
            AIOPS_ENVIRONMENT="dev",
            AIOPS_NODE_ROLE="toolrepo",
            AIOPS_REPO_MODE="agent_review_tooling",
            AIOPS_PRODUCTION_RUNTIME="false",
        ),
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "Allowed: agent_review_tooling environment confirmed."


def test_guard_agent_review_tooling_fails_closed_on_invalid_production_runtime() -> None:
    result = _run_script(
        GUARD,
        "--require-mode",
        "agent_review_tooling",
        "--json",
        env=_clean_env(
            AIOPS_ENVIRONMENT="dev",
            AIOPS_NODE_ROLE="toolrepo",
            AIOPS_REPO_MODE="agent_review_tooling",
            AIOPS_PRODUCTION_RUNTIME="definitely",
        ),
    )

    assert result.returncode == 1
    payload = _json_stdout(result)
    assert payload["ok"] is False
    assert payload["message"] == "Blocked: production runtime flag is invalid."
    assert payload["context"]["production_runtime"] is False
    assert payload["context"]["agent_review_tooling_allowed"] is False
    assert "invalid_production_runtime" in payload["context"]["limitations"]


def test_guard_deny_production_runtime_fails_when_true() -> None:
    result = _run_script(
        GUARD,
        "--deny-production-runtime",
        env=_clean_env(AIOPS_PRODUCTION_RUNTIME="true"),
    )

    assert result.returncode == 1
    assert result.stdout.strip() == "Blocked: production runtime is denied for this operation."


def test_env_info_missing_config_path_is_non_fatal(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.yaml"

    result = _run_script(
        ENV_INFO,
        env=_clean_env(AIOPS_ENVIRONMENT_CONFIG=str(missing_config)),
    )

    assert result.returncode == 0
    payload = _json_stdout(result)
    assert "environment_config_not_found" in payload["limitations"]
    assert "environment_not_declared" in payload["limitations"]


def test_env_info_invalid_config_is_non_fatal(tmp_path: Path) -> None:
    invalid_config = tmp_path / "environment.yaml"
    invalid_config.write_text("environment: [", encoding="utf-8")

    result = _run_script(
        ENV_INFO,
        env=_clean_env(AIOPS_ENVIRONMENT_CONFIG=str(invalid_config)),
    )

    assert result.returncode == 0
    payload = _json_stdout(result)
    assert "environment_config_invalid" in payload["limitations"]
    assert "environment_not_declared" in payload["limitations"]
    assert payload["error_class"]


def test_guard_invalid_config_fails_closed_unless_complete_env_overrides(tmp_path: Path) -> None:
    invalid_config = tmp_path / "environment.yaml"
    invalid_config.write_text("environment: [", encoding="utf-8")

    failed = _run_script(
        GUARD,
        "--require-mode",
        "agent_review_tooling",
        "--json",
        env=_clean_env(AIOPS_ENVIRONMENT_CONFIG=str(invalid_config)),
    )

    assert failed.returncode == 1
    failed_payload = _json_stdout(failed)
    assert failed_payload["ok"] is False
    assert failed_payload["message"] == (
        "Blocked: environment config is invalid and no complete environment override was provided."
    )

    passed = _run_script(
        GUARD,
        "--require-mode",
        "agent_review_tooling",
        "--json",
        env=_clean_env(
            AIOPS_ENVIRONMENT_CONFIG=str(invalid_config),
            AIOPS_ENVIRONMENT="dev",
            AIOPS_NODE_ROLE="toolrepo",
            AIOPS_REPO_MODE="agent_review_tooling",
            AIOPS_PRODUCTION_RUNTIME="false",
        ),
    )

    assert passed.returncode == 0
    passed_payload = _json_stdout(passed)
    assert passed_payload["ok"] is True


def test_env_info_output_does_not_include_secrets() -> None:
    secret = "super-secret-aiops-token"

    result = _run_script(
        ENV_INFO,
        env=_clean_env(
            AIOPS_ENVIRONMENT="dev",
            AIOPS_NODE_ROLE="toolrepo",
            AIOPS_REPO_MODE="agent_review_tooling",
            AIOPS_PRODUCTION_RUNTIME="false",
            AIOPS_FAKE_SECRET=secret,
        ),
    )

    assert result.returncode == 0
    assert secret not in result.stdout
