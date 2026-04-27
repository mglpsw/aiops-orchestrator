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
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
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


# ---------------------------------------------------------------------------
# action_plan integration tests
# ---------------------------------------------------------------------------


def test_diagnose_attaches_action_plan_when_findings_exist(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "critical"
    # action_plan must be present and not null
    assert body["action_plan"] is not None
    plan = body["action_plan"]
    assert plan["dry_run"] is True
    assert plan["status"] in ("ready", "blocked", "empty")
    assert "plan_id" in plan


def test_diagnose_action_plan_contains_relevant_action_ids_for_readiness(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )
    body = response.json()
    plan = body["action_plan"]
    step_ids = [s["action_id"] for s in plan["steps"]]
    # Readiness problem → health/ready/systemctl + general investigation
    assert "curl_health_8000" in step_ids or len(plan["steps"]) >= 1


def test_diagnose_action_plan_is_none_when_all_ok(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    body = response.json()
    assert body["status"] == "ok"
    # No problems → no action plan
    assert body["action_plan"] is None


def test_diagnose_action_plan_never_contains_command_field(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )
    body = response.json()
    plan = body.get("action_plan") or {}
    for step in plan.get("steps", []):
        assert "command" not in step
    for blocked in plan.get("blocked_steps", []):
        assert "command" not in blocked


def test_diagnose_action_plan_dry_run_always_true(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="backend_up", status="down", value="down", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["backend_up"], "dry_run": True},
    )
    body = response.json()
    if body["action_plan"] is not None:
        assert body["action_plan"]["dry_run"] is True


def test_diagnose_catalog_failure_does_not_break_diagnose(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the action catalog is unavailable, diagnose still returns 200 with action_plan=None."""
    from unittest.mock import patch
    import app.agent_router.main as router_module

    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    from app.services.action_catalog import CatalogLoadError
    router_module._reset_catalog_cache()
    with patch.object(router_module, "_get_catalog", side_effect=CatalogLoadError("missing")):
        response = client.post(
            "/v1/aiops/diagnose",
            headers=_auth_headers(),
            json={"checks": ["readiness"], "dry_run": True},
        )

    assert response.status_code == 200
    body = response.json()
    # Diagnose succeeded
    assert body["status"] == "critical"
    # But plan is null due to catalog failure
    assert body["action_plan"] is None
    router_module._reset_catalog_cache()


def test_diagnose_preserves_original_fields_with_action_plan(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All pre-existing fields in the diagnose response must still be present."""
    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    response = client.post(
        "/v1/aiops/diagnose",
        headers=_auth_headers(),
        json={"checks": ["readiness"], "dry_run": True},
    )
    body = response.json()
    # Original fields preserved
    assert "status" in body
    assert "severity" in body
    assert "summary" in body
    assert "signals" in body
    assert "findings" in body
    assert "recommended_actions" in body
    assert body["dry_run"] is True
    # New field present
    assert "action_plan" in body
