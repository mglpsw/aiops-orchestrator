from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
    monkeypatch.setenv("AIOPS_APPROVAL_STORE_PATH", str(tmp_path / "approvals" / "aiops_approvals.jsonl"))
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


def test_create_approval_returns_pending_and_persists(api_client: TestClient, tmp_path: Path) -> None:
    response = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={
            "target": "agent-router",
            "dry_run_id": "dryrun_123",
            "reason": "Approve future read-only collection",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["approval_id"].startswith("approval_")
    assert body["status"] == "pending"
    assert body["target"] == "agent-router"
    assert body["dry_run_id"] == "dryrun_123"
    assert body["plan_id"] is None
    assert body["requires_approval"] is True
    assert body["actor"] == "authenticated_user"
    assert body["approved_at"] is None
    assert body["rejected_at"] is None
    assert "command" not in body
    assert "Authorization" not in body
    assert "test-token" not in json.dumps(body)

    approvals = _read_jsonl(_approval_store_path(tmp_path))
    assert len(approvals) == 1
    assert approvals[0]["approval_id"] == body["approval_id"]

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    assert audit_events[0]["event_type"] == "approval_requested"
    assert audit_events[0]["approval_id"] == body["approval_id"]
    assert "command" not in audit_events[0]
    assert "Authorization" not in json.dumps(audit_events[0])


def test_default_ttl_and_lookup_returns_persisted_data(api_client: TestClient, tmp_path: Path) -> None:
    response = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_123", "reason": "TTL check"},
    )

    assert response.status_code == 200
    body = response.json()
    created_at = datetime.fromisoformat(body["created_at"])
    expires_at = datetime.fromisoformat(body["expires_at"])
    delta = expires_at - created_at
    assert 899 <= delta.total_seconds() <= 901

    lookup = api_client.get(f"/v1/aiops/actions/approvals/{body['approval_id']}", headers=_auth())
    assert lookup.status_code == 200
    lookup_body = lookup.json()
    assert lookup_body["approval_id"] == body["approval_id"]
    assert lookup_body["status"] == "pending"


def test_ttl_validation_rejects_zero_and_over_max(api_client: TestClient) -> None:
    response_zero = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_1", "ttl_seconds": 0},
    )
    response_over = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_1", "ttl_seconds": 3601},
    )

    assert response_zero.status_code == 422
    assert response_over.status_code == 422


def test_approval_requires_auth(api_client: TestClient) -> None:
    response = api_client.post(
        "/v1/aiops/actions/approvals",
        json={"plan_id": "plan_1"},
    )

    assert response.status_code in {401, 403}


def test_approve_pending_changes_status_and_audits(api_client: TestClient, tmp_path: Path) -> None:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_approve"},
    )
    approval_id = create.json()["approval_id"]

    response = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["approved_at"] is not None
    assert body["approved_by"] == "authenticated_user"

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    assert audit_events[-1]["event_type"] == "approval_approved"
    assert audit_events[-1]["approval_id"] == approval_id


def test_reject_pending_changes_status_and_audits(api_client: TestClient, tmp_path: Path) -> None:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_reject"},
    )
    approval_id = create.json()["approval_id"]

    response = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/reject", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["rejected_at"] is not None
    assert body["rejected_by"] == "authenticated_user"

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    assert audit_events[-1]["event_type"] == "approval_rejected"
    assert audit_events[-1]["approval_id"] == approval_id


def test_approved_cannot_be_rejected(api_client: TestClient) -> None:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_lock"},
    )
    approval_id = create.json()["approval_id"]
    approve = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert approve.status_code == 200

    reject = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/reject", headers=_auth())
    assert reject.status_code == 409


def test_rejected_cannot_be_approved(api_client: TestClient) -> None:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_lock_2"},
    )
    approval_id = create.json()["approval_id"]
    reject = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/reject", headers=_auth())
    assert reject.status_code == 200

    approve = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert approve.status_code == 409


def test_expired_cannot_be_approved(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    create = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_expire", "ttl_seconds": 1},
    )
    approval_id = create.json()["approval_id"]

    import app.agent_router.services.approval_store as approval_module

    monkeypatch.setattr(
        approval_module,
        "utcnow",
        lambda: datetime.now(timezone.utc) + timedelta(hours=2),
    )

    approve = api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    assert approve.status_code == 409

    audit_events = _read_jsonl(_audit_log_path(tmp_path))
    assert audit_events[-1]["event_type"] == "approval_expired"
    assert audit_events[-1]["approval_id"] == approval_id



def test_store_unavailable_returns_controlled_error(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent_router.services.approval_store as approval_module

    monkeypatch.setattr(approval_module, "_persist_snapshot", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("boom")))

    response = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"plan_id": "plan_store_fail"},
    )

    assert response.status_code == 500


def test_approval_lookup_missing_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/v1/aiops/actions/approvals/approval_missing", headers=_auth())
    assert response.status_code == 404
