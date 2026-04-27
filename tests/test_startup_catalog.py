"""Tests for action catalog startup validation and /ready integration.

Covers:
  1.  Startup with valid catalog → state ok, 13 actions
  2.  Startup with valid catalog → actions_count matches real catalog
  3.  Startup with broken catalog → state error, actions_count 0
  4.  /ready returns not_ready when catalog invalid
  5.  /ready returns catalog ok when catalog valid
  6.  /v1/aiops/actions/catalog works with valid catalog
  7.  /v1/aiops/actions/plan fail-closed with invalid catalog (503)
  8.  /v1/aiops/diagnose safe when catalog invalid (no command, no execution)
  9.  Existing tests unaffected (smoke: init then reset leaves unloaded state)
  10. No command field in any /ready, /diagnose, or catalog response
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.agent_router.main as router_module
from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsSignal
from app.core.config import get_settings
from app.main import create_app
from app.models.database import get_db
from app.services.action_catalog import CatalogLoadError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _make_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Build a TestClient with minimal production-like startup.

    Patches DB and provider registry so the test doesn't need real infrastructure.
    The action catalog is loaded for real from config/actions.yaml unless the
    caller patches load_catalog before entering the context.
    """
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

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Unit tests for init_catalog_on_startup (no TestClient needed)
# ---------------------------------------------------------------------------


def test_startup_valid_catalog_sets_state_ok() -> None:
    """init_catalog_on_startup with real catalog → status ok."""
    router_module._reset_catalog_cache()
    try:
        router_module.init_catalog_on_startup()
        info = router_module.get_catalog_readiness()
        assert info["status"] == "ok"
    finally:
        router_module._reset_catalog_cache()


def test_startup_valid_catalog_loads_thirteen_actions() -> None:
    """init_catalog_on_startup loads exactly 13 actions from config/actions.yaml."""
    router_module._reset_catalog_cache()
    try:
        router_module.init_catalog_on_startup()
        info = router_module.get_catalog_readiness()
        assert info["actions_count"] == 13
    finally:
        router_module._reset_catalog_cache()


def test_startup_broken_catalog_sets_state_error() -> None:
    """init_catalog_on_startup with bad catalog → status error, actions_count 0."""
    router_module._reset_catalog_cache()
    try:
        with patch.object(router_module, "load_catalog", side_effect=CatalogLoadError("bad yaml")):
            router_module.init_catalog_on_startup()
        info = router_module.get_catalog_readiness()
        assert info["status"] == "error"
        assert info["actions_count"] == 0
    finally:
        router_module._reset_catalog_cache()


def test_reset_after_init_returns_unloaded() -> None:
    """_reset_catalog_cache() after init leaves state as unloaded."""
    router_module.init_catalog_on_startup()
    router_module._reset_catalog_cache()
    info = router_module.get_catalog_readiness()
    assert info["status"] == "unloaded"
    assert info["actions_count"] == 0


# ---------------------------------------------------------------------------
# /ready endpoint tests
# ---------------------------------------------------------------------------


def test_ready_includes_action_catalog_when_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When catalog loads ok, /ready has action_catalog status=ok."""
    router_module._reset_catalog_cache()
    with _make_client(monkeypatch, tmp_path) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert "dependencies" in body
    assert body["dependencies"]["action_catalog"]["status"] == "ok"
    assert body["dependencies"]["action_catalog"]["actions_count"] == 13
    assert body["checks"]["action_catalog"] is True
    router_module._reset_catalog_cache()


def test_ready_not_ready_when_catalog_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/ready returns not_ready and action_catalog error when catalog fails to load."""
    router_module._reset_catalog_cache()
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

    app.dependency_overrides[get_db] = override_get_db

    with patch.object(router_module, "load_catalog", side_effect=CatalogLoadError("missing")):
        with TestClient(app) as client:
            response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["action_catalog"] is False
    assert body["dependencies"]["action_catalog"]["status"] == "error"
    router_module._reset_catalog_cache()


def test_ready_response_has_no_command_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/ready response never contains command-like content."""
    router_module._reset_catalog_cache()
    with _make_client(monkeypatch, tmp_path) as client:
        response = client.get("/ready")
    body = response.json()
    # /ready must not expose any catalog command or shell string
    assert "command" not in body
    assert "command" not in str(body.get("dependencies", {}))
    router_module._reset_catalog_cache()


# ---------------------------------------------------------------------------
# Existing endpoints still work after startup
# ---------------------------------------------------------------------------


def test_catalog_endpoint_returns_catalog_after_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/aiops/actions/catalog returns 200 with actions after valid startup."""
    router_module._reset_catalog_cache()
    with _make_client(monkeypatch, tmp_path) as client:
        response = client.get("/v1/aiops/actions/catalog", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 13
    assert len(body["actions"]) == 13
    for entry in body["actions"]:
        assert "command" not in entry
    router_module._reset_catalog_cache()


def test_plan_endpoint_503_when_catalog_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/aiops/actions/plan returns 503 when catalog failed to load at startup."""
    router_module._reset_catalog_cache()
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

    app.dependency_overrides[get_db] = override_get_db

    with patch.object(router_module, "load_catalog", side_effect=CatalogLoadError("gone")):
        with TestClient(app) as client:
            response = client.post(
                "/v1/aiops/actions/plan",
                headers=_auth(),
                json={"action_ids": ["git_status"], "dry_run": True},
            )

    assert response.status_code == 503
    router_module._reset_catalog_cache()


def test_diagnose_safe_when_catalog_broken_no_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/aiops/diagnose returns 200 without command even when catalog is broken."""
    router_module._reset_catalog_cache()
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

    app.dependency_overrides[get_db] = override_get_db

    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    with patch.object(router_module, "load_catalog", side_effect=CatalogLoadError("gone")):
        with TestClient(app) as client:
            response = client.post(
                "/v1/aiops/diagnose",
                headers=_auth(),
                json={"checks": ["readiness"], "dry_run": True},
            )

    assert response.status_code == 200
    body = response.json()
    # Diagnose still works (fail-soft for action_plan)
    assert body["status"] == "critical"
    assert body["action_plan"] is None
    # No executable command in recommended_actions (command must be null)
    for action in body["recommended_actions"]:
        assert action["command"] is None
    router_module._reset_catalog_cache()


def test_diagnose_no_command_in_response_with_valid_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/aiops/diagnose with valid catalog: action_plan present, no command."""
    router_module._reset_catalog_cache()

    async def fake_collect(request: AIOpsDiagnoseRequest, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock")]

    monkeypatch.setattr("app.agent_router.main.collect_aiops_diagnostic_signals", fake_collect)

    with _make_client(monkeypatch, tmp_path) as client:
        response = client.post(
            "/v1/aiops/diagnose",
            headers=_auth(),
            json={"checks": ["readiness"], "dry_run": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["action_plan"] is not None
    assert body["action_plan"]["dry_run"] is True
    # No command in action_plan steps or blocked_steps
    for step in body["action_plan"].get("steps", []):
        assert "command" not in step
    for blocked in body["action_plan"].get("blocked_steps", []):
        assert "command" not in blocked
    # No executable command in recommended_actions (field must be null)
    for action in body["recommended_actions"]:
        assert action["command"] is None
    router_module._reset_catalog_cache()
