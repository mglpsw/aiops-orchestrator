from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.api.legacy_usage import reset_legacy_usage_metrics


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
    get_settings.cache_clear()

    async def noop_init_db() -> None:
        return None

    monkeypatch.setattr("app.main.init_db", noop_init_db)
    monkeypatch.setattr("app.main.get_registry", lambda: object())
    reset_legacy_usage_metrics()

    app = create_app()

    async def override_get_db():
        yield object()

    from app.models.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()
    reset_legacy_usage_metrics()


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


def test_legacy_chat_endpoint_is_marked_deprecated(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ingest(self, request):  # noqa: ANN001
        return {
            "task_id": "task-legacy-chat",
            "status": "pending",
            "summary": "queued",
            "message": "ok",
        }

    monkeypatch.setattr("app.api.routes.Orchestrator.ingest_chat", fake_ingest)

    response = client.post("/v1/chat", headers=_bearer_headers("test-token"), json={"message": "hello"})

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert "Legacy AIOps endpoint" in response.headers["Warning"]


def test_legacy_providers_status_marks_headers_and_updates_metrics(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRegistry:
        async def check_all_health(self):  # noqa: ANN001
            return [{"name": "ollama", "enabled": True, "healthy": True}]

    monkeypatch.setattr("app.api.routes.get_registry", lambda: _FakeRegistry())

    response = client.get("/v1/providers/status", headers=_bearer_headers("test-token"))

    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    assert "Legacy AIOps endpoint" in response.headers["Warning"]

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
    metrics_response = client.get("/metrics")

    assert metrics_response.status_code == 200
    assert 'aiops_legacy_endpoint_hits_total{endpoint="providers_status"} 1' in metrics_response.text


def test_legacy_error_responses_are_also_marked_deprecated(
    client: TestClient,
) -> None:
    response = client.get("/v1/providers/status")

    assert response.status_code == 401
    assert response.headers["Deprecation"] == "true"
    assert "Legacy AIOps endpoint" in response.headers["Warning"]


def test_canonical_aiops_routes_do_not_emit_legacy_headers(
    client: TestClient,
) -> None:
    response = client.get("/v1/aiops/actions/catalog", headers=_bearer_headers("test-token"))

    assert response.status_code in {200, 503}
    assert "Deprecation" not in response.headers
    assert "Warning" not in response.headers


def test_legacy_endpoint_logging_does_not_expose_sensitive_data(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that legacy endpoint logs do not expose Authorization, tokens, or body."""
    secret_token = "super-secret-bearer-token-12345"
    secret_body_value = "super-sensitive-data-payload"

    caplog.clear()
    response = client.post(
        "/v1/chat",
        headers=_bearer_headers(secret_token),
        json={"message": secret_body_value},
    )

    assert response.status_code in {401, 403}
    assert response.headers["Deprecation"] == "true"

    # Verify that secret token is NOT in caplog
    caplog_text = caplog.text.lower()
    assert secret_token.lower() not in caplog_text, (
        "Authorization token must not appear in logs"
    )
    assert secret_body_value.lower() not in caplog_text, (
        "Request body payload must not appear in logs"
    )

    # Verify that legacy_endpoint field DOES appear (without the secrets)
    assert "legacy_endpoint" in caplog_text or "legacy" in caplog_text, (
        "Log should include legacy_endpoint field to indicate deprecation usage"
    )
