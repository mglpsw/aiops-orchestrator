from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent_router.metrics import reset_aiops_metrics
from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsSignal
from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AIOPS_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
    get_settings.cache_clear()
    reset_aiops_metrics()

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


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_aiops_diagnose_valid_request_returns_200(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [
            AIOpsSignal(name="readiness", status="ready", value="ready", source="mock"),
            AIOpsSignal(name="backend_up", status="up", value="up", source="mock"),
        ]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness", "backend_up"], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["status"] == "ok"
    assert body["severity"] == "low"


def test_aiops_diagnose_rejects_dry_run_false(client: TestClient) -> None:
    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": False},
    )

    assert response.status_code == 422


def test_aiops_diagnose_rejects_unknown_check(client: TestClient) -> None:
    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness", "unknown_check"], "dry_run": True},
    )

    assert response.status_code == 422


def test_aiops_diagnose_response_always_has_dry_run_true_and_no_commands(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [
            AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock"),
            AIOpsSignal(name="backend_up", status="down", value="down", source="mock"),
        ]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness", "backend_up"], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert all(action["command"] is None for action in body["recommended_actions"])


def test_aiops_diagnose_unknown_scenario_returns_unknown(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return []

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness", "backend_up"], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert body["severity"] == "low"


def test_aiops_diagnose_readiness_not_ready_generates_critical_high(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [
            AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock"),
        ]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "critical"
    assert body["severity"] == "high"


def test_aiops_diagnose_does_not_call_legacy_executors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="ready", value="ready", source="mock")]

    def fail_if_called(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("legacy executor should not be called")

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)
    monkeypatch.setattr("app.adapters.executor_local.LocalExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.executor_ssh.SSHExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.docker.DockerAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.codex.CodexAdapter.generate", fail_if_called)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )

    assert response.status_code == 200


def test_aiops_metrics_appear_in_prometheus_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [
            AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock"),
        ]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

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

    diagnose_response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )
    assert diagnose_response.status_code == 200

    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    metrics_text = metrics_response.text
    assert "agent_router_aiops_diagnose_total" in metrics_text
    assert 'agent_router_aiops_diagnose_total{status="critical",severity="high"} 1' in metrics_text
    assert "agent_router_aiops_diagnose_latency_seconds" in metrics_text
    assert "agent_router_aiops_findings_total" in metrics_text
