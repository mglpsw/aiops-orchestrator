from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent_router.services import action_runner
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit" / "aiops_audit.jsonl"))
    monkeypatch.setenv("AIOPS_APPROVAL_STORE_PATH", str(tmp_path / "approvals" / "aiops_approvals.jsonl"))
    monkeypatch.setenv("AIOPS_RUN_STORE_PATH", str(tmp_path / "runs" / "aiops_runs.jsonl"))
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


def _create_approved_approval(api_client: TestClient, target: str = "agent-router") -> str:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"target": target, "plan_id": "plan_legacy_quarantine"},
    )
    assert create.status_code == 200
    approval_id = create.json()["approval_id"]
    approve = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert approve.status_code == 200
    return approval_id


def test_action_runner_does_not_import_legacy_adapters() -> None:
    source = inspect.getsource(action_runner)
    assert "app.adapters.executor_local" not in source
    assert "app.adapters.executor_ssh" not in source
    assert "app.adapters.docker" not in source
    assert "create_subprocess_shell" not in source


def test_run_path_does_not_use_legacy_adapters(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    approval_id = _create_approved_approval(api_client)

    def fail_if_called(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("legacy adapter should not be called by /v1/aiops/actions/run")

    monkeypatch.setattr("app.adapters.executor_local.LocalExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.executor_ssh.SSHExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.docker.DockerAdapter.execute", fail_if_called)

    response = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["git_status"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "failed"}
    assert "command" not in body
    assert "argv" not in body
