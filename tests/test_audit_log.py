from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


@pytest.fixture()
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")
    monkeypatch.setenv("AIOPS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/aiops.db")
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit" / "aiops_audit.jsonl"))
    monkeypatch.setenv("AIOPS_AUDIT_LOG_REQUIRED", "true")
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


def _read_audit_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    lines = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            lines.append(json.loads(raw_line))
    return lines


def test_plan_writes_audit_event(api_client: TestClient, tmp_path: Path) -> None:
    response = api_client.post(
        "/v1/aiops/actions/plan",
        headers=_auth(),
        json={
            "action_ids": ["curl_health_8000", "not_in_catalog"],
            "dry_run": True,
            "context": "audit plan test",
        },
    )

    assert response.status_code == 200
    audit_events = _read_audit_lines(tmp_path / "audit" / "aiops_audit.jsonl")
    assert len(audit_events) == 1
    event = audit_events[0]
    assert event["event_id"].startswith("audit_")
    assert event["event_type"] == "action_plan_created"
    assert event["target"] == "agent-router"
    assert event["source_endpoint"] == "/v1/aiops/actions/plan"
    assert event["status"] == "ready"
    assert event["risk"] == "low"
    assert event["action_ids"] == ["curl_health_8000"]
    assert event["blocked_action_ids"] == ["not_in_catalog"]
    assert event["warnings_count"] >= 1
    assert event["blocked_steps_count"] == 1
    assert "command" not in event
    assert "Authorization" not in event
    assert "test-token" not in json.dumps(event)


def test_dry_run_writes_audit_event(api_client: TestClient, tmp_path: Path) -> None:
    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={
            "action_ids": ["curl_health_8000"],
            "dry_run": True,
            "reason": "audit dry-run test",
        },
    )

    assert response.status_code == 200
    audit_events = _read_audit_lines(tmp_path / "audit" / "aiops_audit.jsonl")
    assert len(audit_events) == 1
    event = audit_events[0]
    assert event["event_type"] == "action_dry_run_created"
    assert event["dry_run_id"].startswith("dryrun_")
    assert event["plan_id"]
    assert event["status"] == "ready"
    assert event["action_ids"] == ["curl_health_8000"]
    assert event["blocked_action_ids"] == []


def test_audit_recent_returns_latest_events(api_client: TestClient, tmp_path: Path) -> None:
    api_client.post(
        "/v1/aiops/actions/plan",
        headers=_auth(),
        json={"action_ids": ["curl_health_8000"], "dry_run": True},
    )
    api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={"action_ids": ["curl_ready_8000"], "dry_run": True},
    )

    response = api_client.get("/v1/aiops/audit/recent?limit=1", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["event_type"] == "action_dry_run_created"


def test_audit_recent_clamps_limit(api_client: TestClient, tmp_path: Path) -> None:
    for _ in range(3):
        api_client.post(
            "/v1/aiops/actions/dry-run",
            headers=_auth(),
            json={"action_ids": ["curl_health_8000"], "dry_run": True},
        )

    response = api_client.get("/v1/aiops/audit/recent?limit=1000", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 3


def test_audit_write_failure_returns_500(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_router.main as router_module
    from app.agent_router.services.audit_log import AuditLogError

    monkeypatch.setattr(router_module, "write_audit_event", lambda *args, **kwargs: (_ for _ in ()).throw(AuditLogError("boom")))

    response = api_client.post(
        "/v1/aiops/actions/plan",
        headers=_auth(),
        json={"action_ids": ["curl_health_8000"], "dry_run": True},
    )

    assert response.status_code == 500
