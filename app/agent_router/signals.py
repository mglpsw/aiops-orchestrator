"""Safe signal collection for AIOps diagnostic-only requests."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsSignal
from app.core.config import get_settings
from app.services.provider_registry import get_registry
from app.services.task_service import TaskService

SUPPORTED_DIAGNOSTIC_CHECKS: tuple[str, ...] = (
    "readiness",
    "readiness_status",
    "backend_up",
    "error_rate",
    "error_rate_high",
    "chat_error_spike",
    "latency_p95",
    "latency_p95_high",
    "blocked_tasks",
    "route_block_spike",
    "rate_limit_spike",
    "backend_fallback_spike",
    "router_uptime_reset",
    "prometheus_scrape_staleness",
    "aiops_catalog_not_ready",
    "model_selection",
    "ollama_models_count",
)


async def collect_aiops_diagnostic_signals(
    request: AIOpsDiagnoseRequest,
    db: AsyncSession,
) -> list[AIOpsSignal]:
    """Collect a constrained set of local-only signals for diagnose."""
    requested_checks = request.checks or list(SUPPORTED_DIAGNOSTIC_CHECKS)
    results: list[AIOpsSignal] = []

    db_metrics = await _safe_collect_db_metrics(db)
    baseline = _baseline_snapshot(request.metadata)
    backend_up = _safe_backend_status()
    model_selection = _safe_model_selection()
    ollama_models = _safe_ollama_models_count()

    for check in requested_checks:
        if check in {"readiness", "readiness_status"}:
            results.append(_build_readiness_signal(db_metrics["database_ok"], backend_up, check))
        elif check == "backend_up":
            results.append(backend_up)
        elif check in {"error_rate", "error_rate_high"}:
            results.append(_build_error_rate_signal(db_metrics, check, baseline=_baseline_for(baseline, check, "error_rate", "error_rate_high")))
        elif check == "chat_error_spike":
            results.append(_build_chat_error_spike_signal(db_metrics, baseline=_baseline_for(baseline, check, "chat_error_spike", "error_rate", "error_rate_high")))
        elif check in {"latency_p95", "latency_p95_high"}:
            results.append(_build_latency_signal(check, baseline=_baseline_for(baseline, check, "latency_p95", "latency_p95_high")))
        elif check in {"blocked_tasks", "route_block_spike"}:
            results.append(_build_blocked_tasks_signal(db_metrics, check, baseline=_baseline_for(baseline, check, "blocked_tasks", "route_block_spike")))
        elif check == "rate_limit_spike":
            results.append(_build_rate_limit_signal(request.metadata, baseline=_baseline_for(baseline, check, "rate_limit_spike")))
        elif check == "backend_fallback_spike":
            results.append(_build_backend_fallback_signal(db_metrics, baseline=_baseline_for(baseline, check, "backend_fallback_spike")))
        elif check == "router_uptime_reset":
            results.append(_build_router_uptime_reset_signal(request.metadata, baseline=_baseline_for(baseline, check, "router_uptime_reset")))
        elif check == "prometheus_scrape_staleness":
            results.append(_build_prometheus_scrape_staleness_signal(request.metadata, baseline=_baseline_for(baseline, check, "prometheus_scrape_staleness")))
        elif check == "aiops_catalog_not_ready":
            from app.agent_router.main import get_catalog_readiness

            catalog_info = get_catalog_readiness()
            results.append(
                AIOpsSignal(
                    name=check,
                    status=str(catalog_info.get("status", "unknown")).lower(),
                    value=catalog_info.get("actions_count"),
                    unit="count",
                    source="startup",
                    description="Action catalog readiness snapshot from startup validation.",
                )
            )
        elif check == "model_selection":
            results.append(model_selection)
        elif check == "ollama_models_count":
            results.append(ollama_models)
        else:
            results.append(
                AIOpsSignal(
                    name=check,
                    status="unknown",
                    value=None,
                    unit=None,
                    source="internal",
                    description="Unsupported diagnostic check was ignored by the signal collector.",
                )
            )

    return results


async def _safe_collect_db_metrics(db: AsyncSession) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "database_ok": False,
        "tasks_total": None,
        "failed_tasks": None,
        "blocked_actions_total": None,
        "provider_calls_total": None,
        "provider_failures_total": None,
    }
    try:
        await db.execute(text("SELECT 1"))
        metrics["database_ok"] = True
        service = TaskService(db)
        aggregates = await service.get_metrics()
        metrics["tasks_total"] = aggregates.get("tasks_total")
        metrics["failed_tasks"] = aggregates.get("tasks_by_status", {}).get("failed", 0)
        metrics["blocked_actions_total"] = aggregates.get("blocked_actions_total")
        metrics["provider_calls_total"] = aggregates.get("provider_calls_total")
        metrics["provider_failures_total"] = aggregates.get("provider_failures_total")
    except Exception:
        pass
    return metrics


def _safe_backend_status() -> AIOpsSignal:
    try:
        registry = get_registry()
        enabled_llm = [provider for provider in registry.llm_providers.values() if provider.enabled]
        status = "ok" if enabled_llm else "unknown"
        value: bool | None = bool(enabled_llm) if enabled_llm else None
        description = (
            f"{len(enabled_llm)} LLM provider(s) are enabled locally."
            if enabled_llm
            else "No enabled LLM providers were detected locally."
        )
        return AIOpsSignal(
            name="backend_up",
            status=status,
            value=value,
            unit=None,
            source="provider_registry",
            description=description,
        )
    except Exception:
        return AIOpsSignal(
            name="backend_up",
            status="unknown",
            value=None,
            unit=None,
            source="provider_registry",
            description="Backend availability could not be determined safely.",
        )


def _baseline_snapshot(metadata: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(metadata, dict):
        return {}
    baseline = metadata.get("baseline")
    if not isinstance(baseline, dict):
        return {}
    snapshot: dict[str, float] = {}
    for key, value in baseline.items():
        numeric = _coerce_number(value)
        if numeric is not None:
            snapshot[str(key)] = numeric
    return snapshot


def _baseline_for(baseline: dict[str, float], *keys: str) -> float | None:
    for key in keys:
        value = baseline.get(key)
        if value is not None:
            return value
    return None


def _baseline_note(*, current: float | None, baseline: float | None, unit: str, higher_is_worse: bool) -> str:
    if current is None or baseline is None:
        return ""
    delta = current - baseline
    direction = "higher" if delta > 0 else "lower" if delta < 0 else "unchanged"
    concern = "worse" if (higher_is_worse and delta > 0) or (not higher_is_worse and delta < 0) else "better"
    return (
        f"Baseline {baseline:g}{unit} -> current {current:g}{unit} "
        f"({direction}, {concern} by {abs(delta):g}{unit})."
    )


def _build_readiness_signal(database_ok: bool, backend_up: AIOpsSignal, name: str) -> AIOpsSignal:
    if backend_up.status == "unknown":
        status = "unknown"
        value: str | None = None
        description = "Readiness could not be determined because backend health is unknown."
    else:
        ready = database_ok and bool(backend_up.value)
        status = "ready" if ready else "not_ready"
        value = "ready" if ready else "not_ready"
        description = "Database and backend are ready." if ready else "Database or backend is not ready."

    return AIOpsSignal(
        name=name,
        status=status,
        value=value,
        unit=None,
        source="internal",
        description=description,
    )


def _build_error_rate_signal(metrics: dict[str, Any], name: str, *, baseline: float | None = None) -> AIOpsSignal:
    tasks_total = metrics.get("tasks_total")
    failed_tasks = metrics.get("failed_tasks")
    if not isinstance(tasks_total, int) or tasks_total <= 0:
        return AIOpsSignal(
            name=name,
            status="unknown",
            value=None,
            unit="ratio",
            source="task_service",
            description="No task history is available to compute error rate.",
        )

    failed = int(failed_tasks or 0)
    rate = failed / tasks_total
    threshold = 0.05
    if baseline is not None and baseline >= 0:
        threshold = max(threshold, baseline * 1.25)
    status = "degraded" if rate >= threshold else "ok"
    description = f"Computed error rate from local task history: {rate:.4f}."
    if baseline is not None:
        description = f"{description} {_baseline_note(current=rate, baseline=baseline, unit='', higher_is_worse=True)}".strip()
    return AIOpsSignal(
        name=name,
        status=status,
        value=rate,
        unit="ratio",
        source="task_service",
        description=description,
    )


def _build_chat_error_spike_signal(metrics: dict[str, Any], *, baseline: float | None = None) -> AIOpsSignal:
    signal = _build_error_rate_signal(metrics, "chat_error_spike", baseline=baseline)
    if signal.status == "unknown":
        return signal
    return signal.model_copy(
        update={
            "description": f"Computed chat error spike from local task history: {signal.value:.4f}."
            + (f" {_baseline_note(current=float(signal.value or 0), baseline=baseline, unit='', higher_is_worse=True)}" if baseline is not None and signal.value is not None else ""),
        }
    )


def _build_latency_signal(name: str, *, baseline: float | None = None) -> AIOpsSignal:
    description = "No safe local latency sample is available."
    if baseline is not None:
        description = f"{description} Baseline {baseline:g}ms was provided, but no safe current sample exists."
    return AIOpsSignal(
        name=name,
        status="unknown",
        value=None,
        unit="ms",
        source="internal",
        description=description,
    )


def _build_blocked_tasks_signal(metrics: dict[str, Any], name: str, *, baseline: float | None = None) -> AIOpsSignal:
    blocked = metrics.get("blocked_actions_total")
    if not isinstance(blocked, int):
        return AIOpsSignal(
            name=name,
            status="unknown",
            value=None,
            unit="count",
            source="task_service",
            description="Blocked task count is unavailable.",
        )

    threshold = 1
    if baseline is not None and baseline >= 0:
        threshold = max(threshold, int(baseline) + 1)
    status = "degraded" if blocked >= threshold else "ok"
    description = f"Local blocked task count is {blocked}."
    if baseline is not None:
        description = f"{description} {_baseline_note(current=float(blocked), baseline=baseline, unit='', higher_is_worse=True)}".strip()
    return AIOpsSignal(
        name=name,
        status=status,
        value=blocked,
        unit="count",
        source="task_service",
        description=description,
    )


def _build_rate_limit_signal(metadata: dict[str, Any], *, baseline: float | None = None) -> AIOpsSignal:
    raw_current = metadata.get("rate_limit_spike")
    if raw_current is None:
        raw_current = metadata.get("rate_limit_events")
    current = _coerce_number(raw_current)
    if current is None:
        return AIOpsSignal(
            name="rate_limit_spike",
            status="unavailable",
            value=None,
            unit="count",
            source="internal",
            description="Rate-limit telemetry is not exposed in v1 diagnostic-only mode.",
        )
    status = "warning" if current > 0 else "ok"
    description = f"Rate-limit telemetry reports {current:g} event(s)."
    if baseline is not None:
        description = f"{description} {_baseline_note(current=current, baseline=baseline, unit='', higher_is_worse=True)}".strip()
    if baseline is not None and current >= max(1.0, baseline * 1.25):
        status = "warning"
    return AIOpsSignal(
        name="rate_limit_spike",
        status=status,
        value=current,
        unit="count",
        source="internal",
        description=description,
    )


def _build_backend_fallback_signal(metrics: dict[str, Any], *, baseline: float | None = None) -> AIOpsSignal:
    provider_calls = metrics.get("provider_calls_total")
    provider_failures = metrics.get("provider_failures_total")
    if not isinstance(provider_calls, int) or provider_calls <= 0:
        return AIOpsSignal(
            name="backend_fallback_spike",
            status="unavailable",
            value=None,
            unit="ratio",
            source="task_service",
            description="Provider fallback telemetry is unavailable.",
        )
    failures = int(provider_failures or 0)
    rate = failures / provider_calls
    threshold = 0.05
    if baseline is not None and baseline >= 0:
        threshold = max(threshold, baseline * 1.25)
    status = "degraded" if rate >= threshold else "ok"
    description = f"Provider failure rate is {rate:.4f}."
    if baseline is not None:
        description = f"{description} {_baseline_note(current=rate, baseline=baseline, unit='', higher_is_worse=True)}".strip()
    return AIOpsSignal(
        name="backend_fallback_spike",
        status=status,
        value=rate,
        unit="ratio",
        source="task_service",
        description=description,
    )


def _build_router_uptime_reset_signal(metadata: dict[str, Any], *, baseline: float | None = None) -> AIOpsSignal:
    raw_seconds = metadata.get("router_uptime_seconds")
    raw_minutes = metadata.get("router_uptime_minutes")
    current = _coerce_number(raw_seconds) if raw_seconds is not None else _coerce_number(raw_minutes)
    if raw_seconds is None and raw_minutes is not None and current is not None:
        current *= 60
    if current is None:
        return AIOpsSignal(
            name="router_uptime_reset",
            status="unavailable",
            value=None,
            unit="seconds",
            source="internal",
            description="Router uptime telemetry is not provided in this request.",
        )
    if baseline is None:
        return AIOpsSignal(
            name="router_uptime_reset",
            status="unknown",
            value=current,
            unit="seconds",
            source="internal",
            description="Router uptime telemetry is present but no baseline comparison was supplied.",
        )
    status = "degraded" if current < baseline else "ok"
    description = f"Router uptime is {current:g} seconds."
    description = f"{description} {_baseline_note(current=current, baseline=baseline, unit='s', higher_is_worse=False)}".strip()
    return AIOpsSignal(
        name="router_uptime_reset",
        status=status,
        value=current,
        unit="seconds",
        source="internal",
        description=description,
    )


def _build_prometheus_scrape_staleness_signal(metadata: dict[str, Any], *, baseline: float | None = None) -> AIOpsSignal:
    raw_current = metadata.get("prometheus_scrape_staleness_seconds")
    if raw_current is None:
        raw_current = metadata.get("prometheus_scrape_age_seconds")
    current = _coerce_number(raw_current)
    if current is None:
        return AIOpsSignal(
            name="prometheus_scrape_staleness",
            status="unavailable",
            value=None,
            unit="seconds",
            source="prometheus",
            description="Scrape freshness is not queried directly in v1 diagnostic-only mode.",
        )
    status = "warning" if current >= 300 else "ok"
    description = f"Prometheus scrape freshness is {current:g} seconds stale."
    if baseline is not None:
        description = f"{description} {_baseline_note(current=current, baseline=baseline, unit='s', higher_is_worse=True)}".strip()
        if current >= max(300, baseline * 1.25):
            status = "warning"
    return AIOpsSignal(
        name="prometheus_scrape_staleness",
        status=status,
        value=current,
        unit="seconds",
        source="prometheus",
        description=description,
    )


def _build_unavailable_signal(name: str, unit: str, source: str, description: str) -> AIOpsSignal:
    return AIOpsSignal(
        name=name,
        status="unavailable",
        value=None,
        unit=unit,
        source=source,
        description=description,
    )


def _safe_model_selection() -> AIOpsSignal:
    try:
        settings = get_settings()
        return AIOpsSignal(
            name="model_selection",
            status="ok",
            value=settings.planner_default,
            unit=None,
            source="config",
            description="Current planner model selection is loaded from local configuration.",
        )
    except Exception:
        return AIOpsSignal(
            name="model_selection",
            status="unknown",
            value=None,
            unit=None,
            source="config",
            description="Model selection could not be read from configuration.",
        )


def _safe_ollama_models_count() -> AIOpsSignal:
    return AIOpsSignal(
        name="ollama_models_count",
        status="unknown",
        value=None,
        unit="count",
        source="internal",
        description="Ollama model inventory is not queried during diagnostic-only mode.",
    )


def _coerce_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
