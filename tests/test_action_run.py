from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit" / "aiops_audit.jsonl"))
    monkeypatch.setenv("AIOPS_APPROVAL_STORE_PATH", str(tmp_path / "approvals" / "aiops_approvals.jsonl"))
    monkeypatch.setenv("AIOPS_RUN_STORE_PATH", str(tmp_path / "runs" / "aiops_runs.jsonl"))
    monkeypatch.setenv("AIOPS_RUN_OUTPUT_MAX_BYTES", "4000")
    get_settings.cache_clear()

    async def noop_init_db() -> None:
        return None

    monkeypatch.setattr("app.main.init_db", noop_init_db)
    monkeypatch.setattr("app.main.get_registry", lambda: object())

    app = create_app()

    async def override_get_db():
        yield object()

    from app.models.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            rows.append(json.loads(raw_line))
    return rows


def _approval_store_path(tmp_path: Path) -> Path:
    return tmp_path / "approvals" / "aiops_approvals.jsonl"


def _audit_log_path(tmp_path: Path) -> Path:
    return tmp_path / "audit" / "aiops_audit.jsonl"


def _run_store_path(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "aiops_runs.jsonl"


def _create_approved_approval(api_client: TestClient, target: str = "agent-router") -> str:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"target": target, "plan_id": "plan_run"},
    )
    assert create.status_code == 200
    approval_id = create.json()["approval_id"]
    approve = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert approve.status_code == 200
    return approval_id


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


async def _fake_get_ok(self, url: str, *args, **kwargs) -> _FakeResponse:
    if url.endswith("/health"):
        return _FakeResponse(200, '{"status":"healthy","token":"sk-test-token"}')
    if url.endswith("/ready"):
        return _FakeResponse(200, '{"ready":true,"password":"super-secret","details":"ok"}')
    return _FakeResponse(200, "ok")


async def _fake_get_partial(self, url: str, *args, **kwargs) -> _FakeResponse:
    if url.endswith("/health"):
        return _FakeResponse(200, '{"status":"healthy"}')
    raise RuntimeError("boom")


async def _fake_get_truncating(self, url: str, *args, **kwargs) -> _FakeResponse:
    return _FakeResponse(200, "Authorization: Bearer super-secret-token " + ("x" * 8000))


def _fake_subprocess_run_factory(calls: list[dict[str, object]]):
    def _fake_run(argv, **kwargs):
        call = {"argv": list(argv), **kwargs}
        calls.append(call)
        if list(argv) == ["git", "status", "--short", "--branch"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "## main\n"
                    " M config/actions.yaml\n"
                    " Authorization: Bearer super-secret-token\n"
                    " password=super-secret\n"
                    " api_key=sk-test-key\n"
                    + ("x" * 8000)
                ),
                stderr="",
            )
        if list(argv) == ["docker", "compose", "-f", "deploy/docker-compose.yml", "config", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="services:\n  app:\n    image: aiops\n", stderr="")
        if list(argv) == ["git", "diff", "--stat"]:
            return SimpleNamespace(
                returncode=0,
                stdout=" config/actions.yaml | 4 ++--\n secret=password\n" + ("y" * 7000),
                stderr="",
            )
        if list(argv) == [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "-f",
            "deploy/docker-compose.bluegreen.yml",
            "config",
            "--quiet",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if list(argv) == [
            "systemctl",
            "show",
            "aiops-orchestrator.service",
            "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,ActiveEnterTimestamp,InactiveEnterTimestamp,NRestarts",
        ]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Id=aiops-orchestrator.service\n"
                    "LoadState=loaded\n"
                    "ActiveState=active\n"
                    "SubState=running\n"
                    "Result=success\n"
                    "ExecMainStatus=0\n"
                    "MainPID=1234\n"
                    "ActiveEnterTimestamp=Mon 2026-04-27 10:00:00 UTC\n"
                    "InactiveEnterTimestamp=n/a\n"
                    "NRestarts=0\n"
                    "Authorization: Bearer super-secret-token\n"
                    "password=super-secret\n"
                    "api_key=sk-test-key\n"
                    + ("z" * 8000)
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")

    return _fake_run


def test_run_with_approved_approval_executes_read_only_actions(api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approval_id = _create_approved_approval(api_client)
    monkeypatch.setattr("httpx.AsyncClient.get", _fake_get_ok, raising=True)

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000", "curl_ready_8000"],
            "reason": "Collect read-only evidence",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["approval_id"] == approval_id
    assert len(body["results"]) == 2
    assert body["results"][0]["status"] == "ok"
    assert body["results"][0]["exit_code"] == 0
    assert "command" not in body
    assert "Authorization" not in json.dumps(body)

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    event_types = [event["event_type"] for event in audit_events]
    assert "action_run_requested" in event_types
    assert "action_run_started" in event_types
    assert "action_run_completed" in event_types

    runs = _read_jsonl(_run_store_path(tmp_path))
    assert len(runs) == 1
    assert runs[0]["run_id"] == body["run_id"]
    assert runs[0]["status"] == "ok"


def test_run_requires_approval_id(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={"target": "agent-router", "action_ids": ["curl_health_8000"]},
    )
    assert response.status_code == 422


def test_run_blocks_pending_rejected_expired_and_target_mismatch(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pending = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_pending"},
    ).json()["approval_id"]

    rejected = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_rejected"},
    ).json()["approval_id"]
    api_client.post(f"/v1/aiops/actions/approvals/{rejected}/reject", headers=_auth())

    expired = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_expired", "ttl_seconds": 1},
    ).json()["approval_id"]

    import app.agent_router.services.approval_store as approval_module

    monkeypatch.setattr(
        approval_module,
        "utcnow",
        lambda: datetime.now(timezone.utc) + timedelta(hours=2),
    )

    pending_response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": pending,
            "action_ids": ["curl_health_8000"],
        },
    )
    rejected_response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": rejected,
            "action_ids": ["curl_health_8000"],
        },
    )
    expired_response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": expired,
            "action_ids": ["curl_health_8000"],
        },
    )
    target_mismatch = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "other-target",
            "approval_id": pending,
            "action_ids": ["curl_health_8000"],
        },
    )

    assert pending_response.status_code == 200
    assert pending_response.json()["status"] == "blocked"
    assert rejected_response.status_code == 200
    assert rejected_response.json()["status"] == "blocked"
    assert expired_response.status_code == 200
    assert expired_response.json()["status"] == "blocked"
    assert target_mismatch.status_code == 200
    assert target_mismatch.json()["status"] == "blocked"

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    event_types = [event["event_type"] for event in audit_events]
    assert event_types.count("action_run_blocked") >= 4
    assert "approval_expired" in event_types


def test_run_blocks_unknown_and_non_executable_actions(api_client: TestClient) -> None:
    approval_id = _create_approved_approval(api_client)

    unknown = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["does_not_exist"],
        },
    )
    non_executable = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["journalctl_aiops_recent"],
        },
    )

    assert unknown.status_code == 200
    assert unknown.json()["status"] == "blocked"
    assert unknown.json()["blocked_steps"][0]["action_id"] == "does_not_exist"
    assert non_executable.status_code == 200
    assert non_executable.json()["status"] == "blocked"
    assert non_executable.json()["blocked_steps"][0]["action_id"] == "journalctl_aiops_recent"


def test_run_executes_systemctl_status_aiops_with_fixed_process(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approval_id = _create_approved_approval(api_client)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.agent_router.services.action_runner.subprocess.run",
        _fake_subprocess_run_factory(calls),
        raising=True,
    )
    monkeypatch.setenv("PATH", "/tmp/malicious")
    get_settings.cache_clear()

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["systemctl_status_aiops"],
            "reason": "Inspect systemd status",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["results"][0]["action_id"] == "systemctl_status_aiops"
    assert body["results"][0]["truncated"] is True
    assert "super-secret-token" not in body["results"][0]["output_preview"]
    assert "[REDACTED]" in body["results"][0]["output_preview"]
    assert len(calls) == 1
    assert calls[0]["argv"] == [
        "systemctl",
        "show",
        "aiops-orchestrator.service",
        "--no-pager",
        "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,ActiveEnterTimestamp,InactiveEnterTimestamp,NRestarts",
    ]
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == 5
    assert calls[0]["cwd"] == str(Path("/opt/aiops-orchestrator").resolve())
    env = calls[0]["env"]
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["HOME"] == str(Path("/opt/aiops-orchestrator").resolve())
    assert "AGENT_ROUTER_API_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_run_executes_git_status_and_docker_compose_config_with_fixed_process(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approval_id = _create_approved_approval(api_client)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.agent_router.services.action_runner.subprocess.run",
        _fake_subprocess_run_factory(calls),
        raising=True,
    )
    monkeypatch.setenv("PATH", "/tmp/malicious")
    get_settings.cache_clear()

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["git_status", "docker_compose_config"],
            "reason": "Inspect repository and compose config",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [result["action_id"] for result in body["results"]] == [
        "git_status",
        "docker_compose_config",
    ]
    assert body["results"][0]["output_preview"].startswith("## main")
    assert body["results"][0]["truncated"] is True
    assert "super-secret-token" not in body["results"][0]["output_preview"]
    assert "[REDACTED]" in body["results"][0]["output_preview"]
    assert body["results"][1]["output_preview"] == "docker compose config valid"
    assert "command" not in json.dumps(body)
    assert "argv" not in json.dumps(body)

    assert len(calls) == 2
    assert calls[0]["argv"] == ["git", "status", "--short", "--branch"]
    assert calls[1]["argv"] == ["docker", "compose", "-f", "deploy/docker-compose.yml", "config", "--quiet"]
    for call in calls:
        assert call["shell"] is False
        assert call["timeout"] == 5
        assert call["cwd"] == str(Path("/opt/aiops-orchestrator").resolve())
        env = call["env"]
        assert env["PATH"] == "/usr/bin:/bin"
        assert "AGENT_ROUTER_API_TOKEN" not in env
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert env["HOME"] == str(Path("/opt/aiops-orchestrator").resolve())

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    event_types = [event["event_type"] for event in audit_events]
    assert "action_run_requested" in event_types
    assert "action_run_started" in event_types
    assert "action_run_completed" in event_types


def test_run_executes_git_diff_stat_and_bluegreen_compose_config_with_fixed_process(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    approval_id = _create_approved_approval(api_client)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.agent_router.services.action_runner.subprocess.run",
        _fake_subprocess_run_factory(calls),
        raising=True,
    )
    get_settings.cache_clear()

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["git_diff_stat", "docker_compose_bluegreen_config"],
            "reason": "Validate diff and bluegreen compose",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [result["action_id"] for result in body["results"]] == [
        "git_diff_stat",
        "docker_compose_bluegreen_config",
    ]
    assert body["results"][0]["output_preview"].startswith("config/actions.yaml | 4 ++--")
    assert body["results"][0]["truncated"] is True
    assert "password" not in body["results"][0]["output_preview"].lower()
    assert "[REDACTED]" in body["results"][0]["output_preview"]
    assert body["results"][1]["output_preview"] == "docker compose bluegreen config valid"

    assert len(calls) == 2
    assert calls[0]["argv"] == ["git", "diff", "--stat"]
    assert calls[1]["argv"] == [
        "docker",
        "compose",
        "-f",
        "deploy/docker-compose.yml",
        "-f",
        "deploy/docker-compose.bluegreen.yml",
        "config",
        "--quiet",
    ]
    for call in calls:
        assert call["shell"] is False
        assert call["timeout"] == 5
        assert call["cwd"] == str(Path("/opt/aiops-orchestrator").resolve())
        env = call["env"]
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == str(Path("/opt/aiops-orchestrator").resolve())
        assert "AGENT_ROUTER_API_TOKEN" not in env
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    event_types = [event["event_type"] for event in audit_events]
    assert "action_run_requested" in event_types
    assert "action_run_started" in event_types
    assert "action_run_completed" in event_types


def test_run_rejects_command_field(api_client: TestClient) -> None:
    approval_id = _create_approved_approval(api_client)
    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000"],
            "command": "rm -rf /",
        },
    )
    assert response.status_code == 422


def test_run_rejects_extra_fields(api_client: TestClient) -> None:
    approval_id = _create_approved_approval(api_client)
    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000"],
            "argv": ["git", "status"],
        },
    )
    assert response.status_code == 422


def test_run_redacts_and_truncates_output(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOPS_RUN_OUTPUT_MAX_BYTES", "64")
    get_settings.cache_clear()
    approval_id = _create_approved_approval(api_client)
    monkeypatch.setattr("httpx.AsyncClient.get", _fake_get_truncating, raising=True)

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["results"][0]["truncated"] is True
    assert "super-secret-token" not in body["results"][0]["output_preview"]
    assert "[REDACTED]" in body["results"][0]["output_preview"]


def test_run_partial_when_one_action_fails(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    approval_id = _create_approved_approval(api_client)
    monkeypatch.setattr("httpx.AsyncClient.get", _fake_get_partial, raising=True)

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000", "curl_ready_8000"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert any(result["status"] == "failed" for result in body["results"])
    assert any(result["status"] == "ok" for result in body["results"])


def test_run_requires_bearer_auth(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/run",
        json={"target": "agent-router", "approval_id": "approval_1", "action_ids": ["curl_health_8000"]},
    )
    assert response.status_code in {401, 403}


def test_run_catalog_unavailable_returns_503(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    approval_id = _create_approved_approval(api_client)
    import app.agent_router.main as main_module
    from app.services.action_catalog import CatalogLoadError

    monkeypatch.setattr(main_module, "_get_catalog", lambda: (_ for _ in ()).throw(CatalogLoadError("boom")))

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000"],
        },
    )
    assert response.status_code == 503


def test_run_store_persists_metadata(api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    approval_id = _create_approved_approval(api_client)
    monkeypatch.setattr("httpx.AsyncClient.get", _fake_get_ok, raising=True)

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["curl_health_8000"],
        },
    )

    assert response.status_code == 200
    records = _read_jsonl(_run_store_path(tmp_path))
    assert len(records) == 1
    assert records[0]["run_id"] == response.json()["run_id"]
    assert records[0]["requested_action_ids"] == ["curl_health_8000"]
    assert "command" not in json.dumps(records[0])
    assert "argv" not in json.dumps(records[0])
    assert "Authorization" not in json.dumps(records[0])
