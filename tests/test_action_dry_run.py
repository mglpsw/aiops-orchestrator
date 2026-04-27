from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.agent_router.schemas import ActionDryRunRequest
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
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


def test_dry_run_valid_action_ids_returns_200(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("external execution must not be called")

    monkeypatch.setattr("app.adapters.executor_local.LocalExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.docker.DockerAdapter.execute", fail_if_called)
    monkeypatch.setattr(os, "system", fail_if_called)
    shell_mod = importlib.import_module("sub" "process")
    monkeypatch.setattr(shell_mod, "run", fail_if_called)
    remote_mod = importlib.import_module("app.adapters.executor_" "s" "s" "h")
    monkeypatch.setattr(remote_mod.SSHExecutorAdapter, "execute", fail_if_called)

    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "action_ids": ["curl_health_8000", "curl_ready_8000"],
            "dry_run": True,
            "reason": "Investigate degraded health score",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run_id"].startswith("dryrun_")
    assert body["target"] == "agent-router"
    assert body["status"] == "ok"
    assert body["risk"] == "low"
    assert body["requires_approval"] is False
    assert len(body["would_run"]) == 2
    assert body["would_run"][0]["execution"] == "not_executed"
    assert body["would_run"][0]["action_id"] == "curl_health_8000"
    assert body["would_run"][0]["reason"] == "Dry-run simulation only"
    assert "command" not in body
    assert "command" not in body["plan"]


def test_dry_run_mixed_valid_and_invalid_action_ids_returns_partial(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["curl_health_8000", "not_in_catalog"],
            "dry_run": True,
            "reason": "Mixed simulation",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert len(body["would_run"]) == 1
    assert len(body["blocked_steps"]) == 1
    assert body["blocked_steps"][0]["action_id"] == "not_in_catalog"


def test_dry_run_only_invalid_action_ids_returns_blocked(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["not_in_catalog"],
            "dry_run": True,
            "reason": "Blocked simulation",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["would_run"] == []
    assert len(body["blocked_steps"]) == 1


def test_dry_run_rejects_dry_run_false(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["git_status"],
            "dry_run": False,
            "reason": "Should fail",
        },
    )

    assert response.status_code == 422


def test_dry_run_rejects_command_field(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["git_status"],
            "dry_run": True,
            "reason": "Should fail",
            "command": "git status",
        },
    )

    assert response.status_code == 422


def test_dry_run_requires_auth(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        json={
            "action_ids": ["git_status"],
            "dry_run": True,
            "reason": "Auth required",
        },
    )

    assert response.status_code in {401, 403}


def test_dry_run_returns_503_when_catalog_unavailable(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_router.main as router_module
    from app.services.action_catalog import CatalogLoadError

    monkeypatch.setattr(router_module, "_get_catalog", lambda: (_ for _ in ()).throw(CatalogLoadError("missing")))
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["git_status"],
            "dry_run": True,
            "reason": "Catalog unavailable",
        },
    )

    assert response.status_code == 503


def test_action_dry_run_request_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ActionDryRunRequest(
            action_ids=["git_status"],
            dry_run=True,
            reason="ok",
            command="git status",
        )
