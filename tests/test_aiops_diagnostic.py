from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsFinding, AIOpsSignal
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.services.health_score import calculate_health_score
from app.agent_router.signals import collect_aiops_diagnostic_signals
from app.services.action_catalog import load_catalog


def test_without_signals_returns_unknown_and_dry_run_true() -> None:
    request = AIOpsDiagnoseRequest(checks=["readiness", "backend_up"])

    response = diagnose_aiops(request, signals=None)

    assert response.status == "unknown"
    assert response.severity == "low"
    assert 0 <= response.health_score <= 100
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
    assert response.health_score < 40
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
    assert response.health_score < 40
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
    assert 60 <= response.health_score < 80
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
    assert 60 <= response.health_score < 80
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
    assert response.health_score == 100
    assert response.findings == []
    assert response.recommended_actions == []


def test_baseline_temporal_data_is_reflected_in_signal_description(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDB:
        async def execute(self, *args, **kwargs):  # noqa: ANN001
            return None

    async def fake_metrics(self):  # noqa: ANN001
        return {
            "tasks_total": 10,
            "tasks_by_status": {"failed": 3},
            "provider_calls_total": 4,
            "provider_failures_total": 2,
            "blocked_actions_total": 1,
        }

    monkeypatch.setattr("app.services.task_service.TaskService.get_metrics", fake_metrics)
    monkeypatch.setattr(
        "app.agent_router.signals.get_registry",
        lambda: SimpleNamespace(llm_providers={"ollama": SimpleNamespace(enabled=True)}, executor_providers={}),
    )

    request = AIOpsDiagnoseRequest(
        checks=["chat_error_spike"],
        metadata={"baseline": {"chat_error_spike": 0.05}},
    )

    signals = asyncio.run(collect_aiops_diagnostic_signals(request, FakeDB()))
    signal = next(item for item in signals if item.name == "chat_error_spike")

    assert signal.status == "degraded"
    assert "Baseline" in signal.description


def test_health_score_increases_with_severity() -> None:
    healthy = calculate_health_score([])
    degraded = calculate_health_score(
        [
            AIOpsFinding(
                title="Warning signal",
                severity="medium",
                status="warning",
                description="degraded",
            ),
        ]
    )
    critical = calculate_health_score(
        [
            AIOpsFinding(
                title="Critical signal",
                severity="high",
                status="critical",
                description="down",
            ),
        ]
    )

    assert healthy.score == 100
    assert degraded.score < healthy.score
    assert critical.score < degraded.score


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


def test_recommended_action_ids_exist_in_catalog() -> None:
    request = AIOpsDiagnoseRequest(checks=["router_uptime_reset"])
    signals = [
        AIOpsSignal(
            name="router_uptime_reset",
            status="warning",
            value=120,
            source="mock",
            description="Router uptime is 120 seconds. Baseline 600s -> current 120s (lower, worse by 480s).",
        )
    ]

    response = diagnose_aiops(request, signals=signals)
    catalog_ids = load_catalog().action_ids()

    assert response.findings
    assert set(response.findings[0].recommended_action_ids).issubset(catalog_ids)
    assert set(response.findings[0].recommended_action_ids) == {"systemctl_status_aiops", "journalctl_aiops_recent"}


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


def test_prometheus_scrape_staleness_returns_safe_unavailable_finding() -> None:
    request = AIOpsDiagnoseRequest(checks=["prometheus_scrape_staleness"])

    response = diagnose_aiops(request, signals=None)

    assert response.status == "unknown"
    assert response.severity == "low"
    assert response.health_score < 100
    assert response.findings
    assert response.findings[0].check == "prometheus_scrape_staleness"
    assert response.findings[0].status in {"skipped", "unavailable"}
    assert response.findings[0].recommended_action_ids == ["prometheus_query_allowlisted"]


def test_multiple_findings_accumulate_and_clamp_to_zero() -> None:
    request = AIOpsDiagnoseRequest(checks=["readiness", "backend_up"])
    signals = [
        AIOpsSignal(name="readiness", status="not_ready", value="not_ready", source="mock"),
        AIOpsSignal(name="backend_up", status="down", value="down", source="mock"),
    ]

    response = diagnose_aiops(request, signals=signals)

    assert response.health_score == 0
    assert response.status == "critical"
    assert response.severity == "high"


def test_rate_limit_spike_returns_safe_skipped_finding() -> None:
    request = AIOpsDiagnoseRequest(checks=["rate_limit_spike"])

    response = diagnose_aiops(request, signals=None)

    assert response.status == "unknown"
    assert response.severity == "low"
    assert response.findings
    assert response.findings[0].check == "rate_limit_spike"
    assert response.findings[0].status in {"unavailable", "skipped"}
    assert response.health_score < 100
