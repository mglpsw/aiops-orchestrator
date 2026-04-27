from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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


def _run_store_path(tmp_path: Path) -> Path:
    return tmp_path / "runs" / "aiops_runs.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.strip():
            rows.append(json.loads(raw_line))
    return rows


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


async def _fake_get_ok(self, url: str, *args, **kwargs) -> _FakeResponse:
    if url.endswith("/health"):
        return _FakeResponse(200, '{"status":"healthy","token":"sk-test-token","password":"secret"}')
    return _FakeResponse(200, '{"ready":true,"api_key":"sk-test-key"}')


def _fake_subprocess_run(argv, **kwargs):
    if list(argv) == ["git", "status", "--short", "--branch"]:
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "## main\n"
                " M config/actions.yaml\n"
                " Authorization: Bearer super-secret-token\n"
                " password=super-secret\n"
                " api_key=sk-test-key\n"
            ),
            stderr="",
        )
    if list(argv) == ["git", "diff", "--stat"]:
        return SimpleNamespace(
            returncode=0,
            stdout=" config/actions.yaml | 4 ++--\n secret=password\n",
            stderr="",
        )
    if list(argv) == ["docker", "compose", "-f", "deploy/docker-compose.yml", "config", "--quiet"]:
        return SimpleNamespace(returncode=0, stdout="services:\n  app:\n    image: aiops\n", stderr="")
    if list(argv) == [
        "docker",
        "compose",
        "-f",
        "deploy/docker-compose.yml",
        "-f",
        "deploy/docker-compose.bluegreen.yml",
        "config",
        "--quiet",
    ]:
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    return SimpleNamespace(returncode=1, stdout="", stderr="unexpected argv")


def _create_approved_run(api_client: TestClient, monkeypatch: pytest.MonkeyPatch, action_id: str = "curl_health_8000", target: str = "agent-router") -> str:
    if action_id.startswith("curl_"):
        monkeypatch.setattr("httpx.AsyncClient.get", _fake_get_ok, raising=True)
    else:
        monkeypatch.setattr("app.agent_router.services.action_runner.subprocess.run", _fake_subprocess_run, raising=True)
    approval = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"target": target, "plan_id": "plan_run_history"},
    )
    approval_id = approval.json()["approval_id"]
    api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    run = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": target,
            "approval_id": approval_id,
            "action_ids": [action_id],
        },
    )
    assert run.status_code == 200
    return run.json()["run_id"]


def test_recent_requires_bearer_auth(api_client: TestClient) -> None:
    response = api_client.get("/v1/aiops/runs/recent")
    assert response.status_code in {401, 403}


def test_get_run_requires_bearer_auth(api_client: TestClient) -> None:
    response = api_client.get("/v1/aiops/runs/run_missing")
    assert response.status_code in {401, 403}


def test_recent_lists_persisted_runs_and_respects_limit(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    run_ids = [_create_approved_run(api_client, monkeypatch) for _ in range(3)]

    response = api_client.get("/v1/aiops/runs/recent?limit=2", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["runs"]) == 2
    assert body["runs"][0]["run_id"] == run_ids[-1]
    assert body["runs"][1]["run_id"] == run_ids[-2]
    assert "command" not in json.dumps(body)
    assert "Authorization" not in json.dumps(body)


def test_recent_filters_by_target_and_status(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    run_target_a = _create_approved_run(api_client, monkeypatch, target="agent-router")
    run_target_b = _create_approved_run(api_client, monkeypatch, target="other-target")

    response_target = api_client.get("/v1/aiops/runs/recent?target=agent-router", headers=_auth())
    response_status = api_client.get("/v1/aiops/runs/recent?status=ok", headers=_auth())

    assert response_target.status_code == 200
    assert response_status.status_code == 200
    assert [run["run_id"] for run in response_target.json()["runs"]] == [run_target_a]
    assert {run["run_id"] for run in response_status.json()["runs"]} == {run_target_a, run_target_b}


def test_run_history_includes_local_inspection_runs(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _create_approved_run(api_client, monkeypatch, action_id="git_status")

    recent = api_client.get("/v1/aiops/runs/recent?limit=20", headers=_auth())
    detail = api_client.get(f"/v1/aiops/runs/{run_id}", headers=_auth())

    assert recent.status_code == 200
    assert detail.status_code == 200
    assert recent.json()["runs"][0]["run_id"] == run_id
    assert recent.json()["runs"][0]["result_count"] == 1
    assert detail.json()["results"][0]["action_id"] == "git_status"
    detail_body = detail.json()
    assert "command" not in json.dumps(detail_body)
    assert "argv" not in json.dumps(detail_body)
    assert "super-secret-token" not in json.dumps(detail_body)
    assert "password" not in json.dumps(detail_body).lower()


def test_run_history_includes_git_diff_and_bluegreen_compose_runs(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _create_approved_run(api_client, monkeypatch, action_id="git_diff_stat")
    monkeypatch.setattr("app.agent_router.services.action_runner.subprocess.run", _fake_subprocess_run, raising=True)
    approval = api_client.post(
        "/v1/aiops/actions/approvals",
        headers=_auth(),
        json={"target": "agent-router", "plan_id": "plan_run_history_bluegreen"},
    )
    approval_id = approval.json()["approval_id"]
    api_client.post(f"/v1/aiops/actions/approvals/{approval_id}/approve", headers=_auth())
    bluegreen_run = api_client.post(
        "/v1/aiops/actions/run",
        headers=_auth(),
        json={
            "target": "agent-router",
            "approval_id": approval_id,
            "action_ids": ["docker_compose_bluegreen_config"],
        },
    )
    assert bluegreen_run.status_code == 200

    recent = api_client.get("/v1/aiops/runs/recent?limit=20", headers=_auth())
    detail = api_client.get(f"/v1/aiops/runs/{run_id}", headers=_auth())
    bluegreen_detail = api_client.get(f"/v1/aiops/runs/{bluegreen_run.json()['run_id']}", headers=_auth())

    assert recent.status_code == 200
    assert detail.status_code == 200
    assert bluegreen_detail.status_code == 200
    recent_ids = {run["run_id"] for run in recent.json()["runs"]}
    assert run_id in recent_ids
    assert bluegreen_run.json()["run_id"] in recent_ids
    assert detail.json()["results"][0]["action_id"] == "git_diff_stat"
    assert bluegreen_detail.json()["results"][0]["action_id"] == "docker_compose_bluegreen_config"
    bluegreen_body = bluegreen_detail.json()
    assert "command" not in json.dumps(bluegreen_body)
    assert "argv" not in json.dumps(bluegreen_body)


def test_get_run_returns_detail_and_missing_returns_404(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id = _create_approved_run(api_client, monkeypatch)

    response = api_client.get(f"/v1/aiops/runs/{run_id}", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["requested_action_ids"] == ["curl_health_8000"]
    assert body["results"][0]["action_id"] == "curl_health_8000"
    assert "command" not in json.dumps(body)
    assert "Authorization" not in json.dumps(body)
    assert "password" not in json.dumps(body).lower()
    assert "api_key" not in json.dumps(body).lower()
    assert "token" not in json.dumps(body).lower()
    assert "secret" not in json.dumps(body).lower()

    missing = api_client.get("/v1/aiops/runs/run_missing", headers=_auth())
    assert missing.status_code == 404


def test_empty_store_returns_empty_list(api_client: TestClient) -> None:
    response = api_client.get("/v1/aiops/runs/recent", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["runs"] == []


def test_invalid_jsonl_is_ignored_with_safe_warning(api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _run_store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"run_id":"run_bad","password":"secret"\n', encoding="utf-8")
    run_id = _create_approved_run(api_client, monkeypatch)

    response = api_client.get("/v1/aiops/runs/recent", headers=_auth())
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["runs"][0]["run_id"] == run_id
    assert body["warnings"]
    assert "secret" not in json.dumps(body).lower()
    assert "password" not in json.dumps(body).lower()


def test_run_store_compaction_keeps_recent_records(api_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOPS_RUN_STORE_MAX_RECORDS", "2")
    get_settings.cache_clear()
    first = _create_approved_run(api_client, monkeypatch)
    second = _create_approved_run(api_client, monkeypatch)
    third = _create_approved_run(api_client, monkeypatch)

    records = _read_jsonl(_run_store_path(tmp_path))
    assert len(records) <= 2
    run_ids = [record["run_id"] for record in records]
    assert third in run_ids
    assert second in run_ids or first in run_ids
    assert "command" not in json.dumps(records)
