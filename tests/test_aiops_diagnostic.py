from __future__ import annotations

import pytest

from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsSignal
from app.agent_router.services.aiops_diagnostic import diagnose_aiops


def test_without_signals_returns_unknown_and_dry_run_true() -> None:
    request = AIOpsDiagnoseRequest(checks=["readiness", "backend_up"])

    response = diagnose_aiops(request, signals=None)

    assert response.status == "unknown"
    assert response.severity == "low"
    assert response.dry_run is True
    assert response.signals
    assert all(signal.status == "unknown" for signal in response.signals)
    assert response.recommended_actions
    assert all(action.command is None for action in response.recommended_actions)
    assert all(action.action_type == "dry_run" for action in response.recommended_actions)


def test_readiness_not_ready_generates_critical_high() -> None:
    request = AIOpsDiagnoseRequest(checks=["readiness"])
    signals = [
        AIOpsSignal(
            name="readiness",
            status="not_ready",
            value="not_ready",
            source="mock",
        )
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "critical"
    assert response.severity == "high"
    assert any(finding.status == "critical" for finding in response.findings)


def test_backend_down_generates_critical_high() -> None:
    request = AIOpsDiagnoseRequest(checks=["backend_up"])
    signals = [
        AIOpsSignal(
            name="backend_up",
            status="down",
            value="down",
            source="mock",
        )
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "critical"
    assert response.severity == "high"
    assert any("backend" in finding.description.lower() for finding in response.findings)


def test_latency_high_generates_warning_medium() -> None:
    request = AIOpsDiagnoseRequest(checks=["latency_p95"])
    signals = [
        AIOpsSignal(
            name="latency_p95",
            status="degraded",
            value=750,
            unit="ms",
            source="mock",
        )
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "warning"
    assert response.severity == "medium"
    assert any("latency" in finding.description.lower() for finding in response.findings)


def test_error_rate_high_generates_warning_medium() -> None:
    request = AIOpsDiagnoseRequest(checks=["error_rate"])
    signals = [
        AIOpsSignal(
            name="error_rate",
            status="degraded",
            value=0.12,
            unit="ratio",
            source="mock",
        )
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "warning"
    assert response.severity == "medium"
    assert any("error rate" in finding.description.lower() for finding in response.findings)


def test_normal_signals_generate_ok_low() -> None:
    request = AIOpsDiagnoseRequest(
        checks=[
            "readiness",
            "backend_up",
            "error_rate",
            "latency_p95",
            "blocked_tasks",
            "model_selection",
            "ollama_models_count",
        ]
    )
    signals = [
        AIOpsSignal(name="readiness", status="ready", value="ready", source="mock"),
        AIOpsSignal(name="backend_up", status="up", value="up", source="mock"),
        AIOpsSignal(name="error_rate", status="ok", value=0.0, unit="ratio", source="mock"),
        AIOpsSignal(name="latency_p95", status="ok", value=120, unit="ms", source="mock"),
        AIOpsSignal(name="blocked_tasks", status="ok", value=0, source="mock"),
        AIOpsSignal(name="model_selection", status="ok", value="claude", source="mock"),
        AIOpsSignal(name="ollama_models_count", status="ok", value=3, source="mock"),
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "ok"
    assert response.severity == "low"
    assert response.findings == []
    assert response.recommended_actions == []


def test_recommended_actions_never_have_command() -> None:
    request = AIOpsDiagnoseRequest(checks=["readiness", "backend_up"])
    signals = [
        AIOpsSignal(name="readiness", status="degraded", value="degraded", source="mock"),
        AIOpsSignal(name="backend_up", status="down", value="down", source="mock"),
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.recommended_actions
    assert all(action.command is None for action in response.recommended_actions)
    assert all(action.action_type == "dry_run" for action in response.recommended_actions)


def test_diagnostic_service_does_not_call_legacy_executors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_if_called(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("legacy executor should not be called")

    monkeypatch.setattr("app.services.provider_registry.get_registry", fail_if_called)
    monkeypatch.setattr("app.adapters.executor_local.LocalExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.executor_ssh.SSHExecutorAdapter.execute", fail_if_called)
    monkeypatch.setattr("app.adapters.docker.DockerAdapter.execute", fail_if_called)

    request = AIOpsDiagnoseRequest(checks=["readiness"])
    signals = [AIOpsSignal(name="readiness", status="ready", value="ready", source="mock")]

    response = diagnose_aiops(request, signals=signals)

    assert response.status == "ok"
    assert response.severity == "low"
