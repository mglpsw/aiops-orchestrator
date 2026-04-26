from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _router_headers(token: str) -> dict[str, str]:
    return {"X-Agent-Router-Token": token}


def test_chat_rejects_missing_token(client: TestClient) -> None:
    response = client.post("/v1/chat", json={"message": "hello"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_chat_rejects_invalid_token_without_leaking_it(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = "super-secret-token"
    caplog.set_level("WARNING")

    response = client.post("/v1/chat", headers=_bearer_headers(token), json={"message": "hello"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid token"
    assert token not in response.text
    assert token not in caplog.text


def test_chat_accepts_bearer_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ingest(self, request):  # noqa: ANN001
        return {
            "task_id": "task-1",
            "status": "pending",
            "summary": "queued",
            "message": "ok",
        }

    monkeypatch.setattr("app.api.routes.Orchestrator.ingest_chat", fake_ingest)

    response = client.post("/v1/chat", headers=_bearer_headers("test-token"), json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-1"
    assert body["message"] == "ok"


def test_chat_accepts_x_agent_router_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ingest(self, request):  # noqa: ANN001
        return {
            "task_id": "task-2",
            "status": "pending",
            "summary": "queued",
            "message": "ok",
        }

    monkeypatch.setattr("app.api.routes.Orchestrator.ingest_chat", fake_ingest)

    response = client.post("/v1/chat", headers=_router_headers("test-token"), json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-2"
    assert body["message"] == "ok"


def test_health_ready_and_metrics_remain_public(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_metrics(self):  # noqa: ANN001
        return {
            "tasks_total": 0,
            "tasks_by_status": {},
            "provider_calls_total": 0,
            "provider_failures_total": 0,
            "approvals_pending": 0,
            "blocked_actions_total": 0,
        }

    monkeypatch.setattr("app.api.metrics.TaskService.get_metrics", fake_get_metrics)

    health_response = client.get("/health")
    healthz_response = client.get("/healthz")
    ready_response = client.get("/readyz")
    metrics_response = client.get("/metrics")

    assert health_response.status_code == 200
    assert healthz_response.status_code == 200
    assert ready_response.status_code == 200
    assert metrics_response.status_code == 200
