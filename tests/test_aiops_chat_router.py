from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.agent_router.schemas import (
    AIOpsDiagnoseResponse,
    AIOpsFinding,
    AIOpsSignal,
    ActionRunResponse,
    ActionRunResult,
    ApprovalCreateRequest,
)
from app.agent_router.services.approval_store import create_approval
from app.agent_router.services.run_store import write_run_record
from app.core.config import get_settings
from app.main import create_app
from app.models.schemas import RiskLevel, TaskStatus
from app.services.aiops_chat_router import detect_aiops_chat_intent, route_aiops_chat


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
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
    monkeypatch.setattr("app.services.orchestrator.get_registry", lambda: object())

    app = create_app()

    async def override_get_db():
        yield object()

    from app.models.database import get_db

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture()
def chat_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOPS_APPROVAL_STORE_PATH", str(tmp_path / "approvals" / "aiops_approvals.jsonl"))
    monkeypatch.setenv("AIOPS_RUN_STORE_PATH", str(tmp_path / "runs" / "aiops_runs.jsonl"))
    monkeypatch.setenv("AIOPS_AUDIT_LOG_PATH", str(tmp_path / "audit" / "aiops_audit.jsonl"))
    get_settings.cache_clear()


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _make_diag_response() -> AIOpsDiagnoseResponse:
    finding = AIOpsFinding(
        check="readiness",
        title="Readiness",
        severity="medium",
        status="warning",
        summary="Readiness is degraded.",
        description="Readiness is degraded.",
        evidence=[AIOpsSignal(name="readiness", status="warning", value="warning", source="mock")],
        impact="Some dependency is not fully ready.",
        confidence=0.9,
        probable_cause="A dependency is partially available.",
        next_validation="Re-check readiness.",
        recommended_action_ids=["curl_ready_8000"],
    )
    return AIOpsDiagnoseResponse(
        status="warning",
        severity="medium",
        health_score=72,
        summary="System state shows 1 degraded signal(s). Health score: 72.",
        signals=[AIOpsSignal(name="readiness", status="warning", value="warning", source="mock")],
        findings=[finding],
        recommended_actions=[],
        dry_run=True,
    )


def _seed_pending_approvals(count: int) -> None:
    for idx in range(count):
        create_approval(
            ApprovalCreateRequest(
                target="agent-router",
                dry_run_id=f"dry_{idx}",
                reason=f"api_key=sk-test-token-{idx}",
            )
        )


def _seed_runs() -> None:
    failed_run = ActionRunResponse(
        run_id="run_failed_123456",
        target="agent-router",
        approval_id="approval_failed_123456",
        status="failed",
        started_at="2026-04-27T03:00:00+00:00",
        finished_at="2026-04-27T03:00:10+00:00",
        results=[
            ActionRunResult(
                action_id="git_diff_stat",
                status="failed",
                exit_code=1,
                duration_ms=120,
                output_preview="Authorization: Bearer sk-test-token secret=hidden",
                truncated=False,
            )
        ],
        blocked_steps=[],
        warnings=[],
    )
    ok_run = ActionRunResponse(
        run_id="run_ok_654321",
        target="agent-router",
        approval_id="approval_ok_654321",
        status="ok",
        started_at="2026-04-27T02:59:00+00:00",
        finished_at="2026-04-27T02:59:05+00:00",
        results=[
            ActionRunResult(
                action_id="curl_health_8000",
                status="ok",
                exit_code=0,
                duration_ms=60,
                output_preview="healthy",
                truncated=False,
            )
        ],
        blocked_steps=[],
        warnings=[],
    )
    write_run_record(failed_run, requested_action_ids=["git_diff_stat"])
    write_run_record(ok_run, requested_action_ids=["curl_health_8000"])


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("diagnostique o agent router", "diagnose_agent_router"),
        ("diagnostique o aiops", "diagnose_aiops"),
        ("o agent router está saudável?", "status_agent_router"),
        ("por que o ollama está lento?", "ollama_slow"),
        ("o blue/green está consistente?", "status_bluegreen"),
        ("quais approvals estão pendentes?", "approvals_pending"),
        ("resuma os últimos runs", "runs_recent"),
        ("o último run falhou por quê?", "last_run_failed"),
    ],
)
def test_detect_aiops_chat_intent_for_supported_phrases(message: str, intent: str) -> None:
    assert detect_aiops_chat_intent(message) == intent


@pytest.mark.parametrize(
    "message",
    [
        "olá, tudo bem?",
        "me conte uma piada",
        "este texto fala de runs de treino e aprovação de café",
    ],
)
def test_detect_aiops_chat_intent_ignores_regular_messages(message: str) -> None:
    assert detect_aiops_chat_intent(message) is None


def test_diagnostic_route_returns_pt_br_and_recommended_action_ids(
    chat_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_collect(request, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="warning", value="warning", source="mock")]

    def fake_diagnose(request, signals, catalog_readiness=None):  # noqa: ANN001
        assert request.dry_run is True
        return _make_diag_response()

    monkeypatch.setattr("app.services.aiops_chat_router.collect_aiops_diagnostic_signals", fake_collect)
    monkeypatch.setattr("app.services.aiops_chat_router.diagnose_aiops", fake_diagnose)

    response = asyncio.run(
        route_aiops_chat(
            "diagnostique o agent router",
            db=SimpleNamespace(),
            catalog_readiness={"status": "ok", "actions_count": 13},
        )
    )

    assert response is not None
    assert response.status == TaskStatus.completed
    assert response.risk_level == RiskLevel.medium
    assert response.summary.startswith("Diagnóstico do Agent Router")
    assert "Saúde 72/100" in response.message
    assert "Prontidão: com alerta." in response.findings
    assert response.recommended_action_ids == ["curl_ready_8000"]
    assert "command" not in json.dumps(response.model_dump(mode="json"))
    assert "argv" not in json.dumps(response.model_dump(mode="json"))
    assert "Diagnóstico" in response.message


def test_pending_approvals_are_limited_and_redacted(chat_env: None) -> None:
    _seed_pending_approvals(7)

    response = asyncio.run(route_aiops_chat("quais approvals estão pendentes?", db=SimpleNamespace()))

    assert response is not None
    assert response.status == TaskStatus.completed
    assert response.summary == "5 aprovação(ões) pendente(s)."
    assert len(response.findings) == 5
    assert "sk-test-token" not in json.dumps(response.model_dump(mode="json"))
    assert "command" not in json.dumps(response.model_dump(mode="json"))
    assert "sk-test-token" not in response.findings[0]
    assert "Próximo passo" not in response.message


def test_recent_runs_are_limited_and_redacted(chat_env: None) -> None:
    _seed_runs()

    response = asyncio.run(route_aiops_chat("resuma os últimos runs", db=SimpleNamespace()))

    assert response is not None
    assert response.status == TaskStatus.completed
    assert response.summary.startswith("2 execuções recentes")
    assert len(response.findings) == 2
    assert "sk-test-token" not in json.dumps(response.model_dump(mode="json"))
    assert "command" not in json.dumps(response.model_dump(mode="json"))
    assert "argv" not in json.dumps(response.model_dump(mode="json"))
    assert response.recommended_action_ids == ["journalctl_aiops_recent"]


def test_last_failed_run_redacts_output_and_avoids_command_fields(chat_env: None) -> None:
    _seed_runs()
    write_run_record(
        ActionRunResponse(
            run_id="run_failed_latest",
            target="agent-router",
            approval_id="approval_failed_latest",
            status="failed",
            started_at="2026-04-27T03:10:00+00:00",
            finished_at="2026-04-27T03:10:10+00:00",
            results=[
                ActionRunResult(
                    action_id="journalctl_aiops_recent",
                    status="failed",
                    exit_code=1,
                    duration_ms=90,
                    output_preview="Authorization: Bearer sk-test-token-latest",
                    truncated=False,
                )
            ],
            blocked_steps=[],
            warnings=[],
        ),
        requested_action_ids=["journalctl_aiops_recent"],
    )

    response = asyncio.run(route_aiops_chat("o último run falhou por quê?", db=SimpleNamespace()))

    assert response is not None
    assert response.status == TaskStatus.completed
    assert response.summary.startswith("Último run falhou")
    assert any("journalctl_aiops_recent" in finding for finding in response.findings)
    assert "sk-test-token" not in json.dumps(response.model_dump(mode="json"))
    assert "command" not in json.dumps(response.model_dump(mode="json"))
    assert "argv" not in json.dumps(response.model_dump(mode="json"))
    assert response.recommended_action_ids == ["journalctl_aiops_recent"]


def test_internal_failure_returns_safe_message(chat_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_collect(request, db):  # noqa: ANN001
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.aiops_chat_router.collect_aiops_diagnostic_signals", fake_collect)

    response = asyncio.run(
        route_aiops_chat(
            "diagnostique o agent router",
            db=SimpleNamespace(),
            catalog_readiness={"status": "ok", "actions_count": 13},
        )
    )

    assert response is not None
    assert response.status == TaskStatus.failed
    assert "Não consegui interpretar a resposta com segurança" in response.message
    assert "boom" not in response.message
    assert "traceback" not in response.message.lower()


def test_chat_endpoint_routes_aiops_intent_before_classification_and_without_execution(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create_task(self, message, user_id="webai-user", context=None):  # noqa: ANN001
        return SimpleNamespace(id="task-aiops-1")

    async def fake_set_result(self, task_id, result, status=TaskStatus.completed):  # noqa: ANN001
        return SimpleNamespace(id=task_id)

    async def fail_if_called(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("LLM/classic execution path should not run for AIOps chat intents")

    async def fake_collect(request, db):  # noqa: ANN001
        return [AIOpsSignal(name="readiness", status="warning", value="warning", source="mock")]

    def fake_diagnose(request, signals, catalog_readiness=None):  # noqa: ANN001
        return _make_diag_response()

    monkeypatch.setattr("app.services.orchestrator.TaskService.create_task", fake_create_task)
    monkeypatch.setattr("app.services.orchestrator.TaskService.set_result", fake_set_result)
    monkeypatch.setattr("app.services.orchestrator.Orchestrator._classify", fail_if_called)
    monkeypatch.setattr("app.services.orchestrator.Orchestrator._create_plan", fail_if_called)
    monkeypatch.setattr("app.services.orchestrator.Orchestrator._execute_plan", fail_if_called)
    monkeypatch.setattr("app.services.orchestrator.get_catalog_readiness", lambda: {"status": "ok", "actions_count": 13})
    monkeypatch.setattr("app.services.aiops_chat_router.collect_aiops_diagnostic_signals", fake_collect)
    monkeypatch.setattr("app.services.aiops_chat_router.diagnose_aiops", fake_diagnose)

    response = client.post("/v1/chat", headers=_auth(), json={"message": "diagnostique o agent router"})

    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == "task-aiops-1"
    assert body["status"] == "completed"
    assert body["summary"].startswith("Diagnóstico do Agent Router")
    assert "Saúde 72/100" in body["message"]
    assert "command" not in json.dumps(body)
    assert "argv" not in json.dumps(body)
