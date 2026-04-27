from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent_router.schemas import ActionPlanBlockedStep, ActionPlanResponse, ActionPlanStep
from app.core.config import get_settings
from app.main import create_app
from app.agent_router.services.audit_log import AuditLogError, build_audit_event, write_audit_event


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


def _make_plan(
    *,
    plan_id: str = "plan-test",
    status: str = "ready",
    action_ids: list[str] | None = None,
    blocked_action_ids: list[str] | None = None,
) -> ActionPlanResponse:
    action_ids = action_ids or ["curl_health_8000"]
    blocked_action_ids = blocked_action_ids or []
    steps = [
        ActionPlanStep(
            action_id=action_id,
            title=f"Title for {action_id}",
            risk="low",
            mode="readonly",
            requires_approval=False,
            reason="selected",
        )
        for action_id in action_ids
    ]
    blocked_steps = [
        ActionPlanBlockedStep(action_id=action_id, reason="blocked")
        for action_id in blocked_action_ids
    ]
    return ActionPlanResponse(
        plan_id=plan_id,
        target="agent-router",
        status=status,
        risk="low",
        requires_approval=False,
        steps=steps,
        blocked_steps=blocked_steps,
        warnings=[],
        dry_run=True,
    )


def _configure_audit_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, required: bool = True, max_bytes: int = 5_000_000, backup_count: int = 5, rotation_enabled: bool = True) -> Path:
    audit_path = tmp_path / "audit" / "aiops_audit.jsonl"
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(audit_path))
    monkeypatch.setenv("AIOPS_AUDIT_LOG_REQUIRED", "true" if required else "false")
    monkeypatch.setenv("AIOPS_AUDIT_LOG_MAX_BYTES", str(max_bytes))
    monkeypatch.setenv("AIOPS_AUDIT_LOG_BACKUP_COUNT", str(backup_count))
    monkeypatch.setenv("AIOPS_AUDIT_LOG_ROTATION_ENABLED", "true" if rotation_enabled else "false")
    get_settings.cache_clear()
    return audit_path


def _audit_payload(plan: ActionPlanResponse, event_type: str = "action_plan_created"):
    return build_audit_event(
        event_type=event_type,  # type: ignore[arg-type]
        target=plan.target,
        source_endpoint="/v1/aiops/actions/plan",
        plan=plan,
        correlation_id=plan.plan_id,
    )


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


def test_audit_recent_returns_empty_list_when_file_missing(api_client: TestClient) -> None:
    response = api_client.get("/v1/aiops/audit/recent", headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"events": []}


def test_audit_write_without_rotation_does_not_rotate(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path, max_bytes=10_000, backup_count=3, rotation_enabled=True)
    plan = _make_plan(plan_id="plan-no-rotate")
    event = _audit_payload(plan)

    assert write_audit_event(event) is True
    assert audit_path.exists()
    assert not audit_path.with_name(f"{audit_path.name}.1").exists()


def test_audit_write_that_exceeds_limit_rotates(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=3, rotation_enabled=True)
    plan1 = _make_plan(plan_id="plan-old")
    plan2 = _make_plan(plan_id="plan-new")
    event1 = _audit_payload(plan1)
    event2 = _audit_payload(plan2)

    assert write_audit_event(event1) is True
    assert write_audit_event(event2) is True

    active_lines = _read_audit_lines(audit_path)
    rotated_lines = _read_audit_lines(audit_path.with_name(f"{audit_path.name}.1"))
    assert len(active_lines) == 1
    assert len(rotated_lines) == 1
    assert active_lines[0]["plan_id"] == "plan-new"
    assert rotated_lines[0]["plan_id"] == "plan-old"


def test_audit_backup_count_is_respected(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=2, rotation_enabled=True)
    for idx in range(4):
        assert write_audit_event(_audit_payload(_make_plan(plan_id=f"plan-{idx}"))) is True

    assert audit_path.exists()
    assert audit_path.with_name(f"{audit_path.name}.1").exists()
    assert audit_path.with_name(f"{audit_path.name}.2").exists()
    assert not audit_path.with_name(f"{audit_path.name}.3").exists()


def test_audit_backup_count_zero_discards_old_backups(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=0, rotation_enabled=True)
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-1"))) is True
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-2"))) is True

    assert audit_path.exists()
    assert not audit_path.with_name(f"{audit_path.name}.1").exists()
    active_lines = _read_audit_lines(audit_path)
    assert len(active_lines) == 1
    assert active_lines[0]["plan_id"] == "plan-2"


def test_audit_rotation_disabled_keeps_single_active_file(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=3, rotation_enabled=False)
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-1"))) is True
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-2"))) is True

    assert audit_path.exists()
    assert not audit_path.with_name(f"{audit_path.name}.1").exists()
    active_lines = _read_audit_lines(audit_path)
    assert len(active_lines) == 2


def test_audit_directory_is_created(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    audit_path = _configure_audit_log(monkeypatch, tmp_path / "nested" / "path", max_bytes=10_000, backup_count=2, rotation_enabled=True)
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-dir"))) is True
    assert audit_path.parent.exists()


def test_recent_reads_active_file_after_rotation(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=2, rotation_enabled=True)
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-1"))) is True
    assert write_audit_event(_audit_payload(_make_plan(plan_id="plan-2"))) is True

    response = api_client.get("/v1/aiops/audit/recent?limit=1", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["plan_id"] == "plan-2"


def test_rotation_failure_returns_controlled_error_when_required_true(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=2, rotation_enabled=True, required=True)

    import app.agent_router.services.audit_log as audit_module

    monkeypatch.setattr(audit_module, "should_rotate_audit_log", lambda *args, **kwargs: True)
    monkeypatch.setattr(audit_module, "rotate_audit_log", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))

    response = api_client.post(
        "/v1/aiops/actions/plan",
        headers=_auth(),
        json={"action_ids": ["curl_health_8000"], "dry_run": True},
    )

    assert response.status_code == 500


def test_rotation_failure_with_required_false_returns_warning(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_audit_log(monkeypatch, tmp_path, max_bytes=1, backup_count=2, rotation_enabled=True, required=False)

    import app.agent_router.services.audit_log as audit_module

    monkeypatch.setattr(audit_module, "should_rotate_audit_log", lambda *args, **kwargs: True)
    monkeypatch.setattr(audit_module, "rotate_audit_log", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))

    response = api_client.post(
        "/v1/aiops/actions/dry-run",
        headers=_auth(),
        json={"action_ids": ["curl_health_8000"], "dry_run": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert any("Audit log unavailable" in warning for warning in body["warnings"])
