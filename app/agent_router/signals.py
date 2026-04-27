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
    "readiness_status",
    "backend_up",
    "error_rate_high",
    "latency_p95_high",
    "blocked_tasks",
    "route_block_spike",
    "rate_limit_spike",
    "prometheus_scrape_staleness",
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
    backend_up = _safe_backend_status()
    model_selection = _safe_model_selection()
    ollama_models = _safe_ollama_models_count()

    for check in requested_checks:
        if check in {"readiness", "readiness_status"}:
            results.append(_build_readiness_signal(db_metrics["database_ok"], backend_up, check))
        elif check == "backend_up":
            results.append(backend_up)
        elif check in {"error_rate", "error_rate_high"}:
            results.append(_build_error_rate_signal(db_metrics, check))
        elif check in {"latency_p95", "latency_p95_high"}:
            results.append(_build_latency_signal(check))
        elif check in {"blocked_tasks", "route_block_spike"}:
            results.append(_build_blocked_tasks_signal(db_metrics, check))
        elif check == "rate_limit_spike":
            results.append(_build_unavailable_signal(
                name=check,
                unit="count",
                source="internal",
                description="Rate-limit telemetry is not exposed in v1 diagnostic-only mode.",
            ))
        elif check == "prometheus_scrape_staleness":
            results.append(_build_unavailable_signal(
                name=check,
                unit="seconds",
                source="prometheus",
                description="Scrape freshness is not queried directly in v1 diagnostic-only mode.",
            ))
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
    }
    try:
        await db.execute(text("SELECT 1"))
        metrics["database_ok"] = True
        service = TaskService(db)
        aggregates = await service.get_metrics()
        metrics["tasks_total"] = aggregates.get("tasks_total")
        metrics["failed_tasks"] = aggregates.get("tasks_by_status", {}).get("failed", 0)
        metrics["blocked_actions_total"] = aggregates.get("blocked_actions_total")
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


def _build_error_rate_signal(metrics: dict[str, Any], name: str) -> AIOpsSignal:
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
    status = "degraded" if rate >= 0.05 else "ok"
    description = f"Computed error rate from local task history: {rate:.4f}."
    return AIOpsSignal(
        name=name,
        status=status,
        value=rate,
        unit="ratio",
        source="task_service",
        description=description,
    )


def _build_latency_signal(name: str) -> AIOpsSignal:
    return AIOpsSignal(
        name=name,
        status="unknown",
        value=None,
        unit="ms",
        source="internal",
        description="No safe local latency sample is available.",
    )


def _build_blocked_tasks_signal(metrics: dict[str, Any], name: str) -> AIOpsSignal:
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

    status = "degraded" if blocked >= 1 else "ok"
    description = f"Local blocked task count is {blocked}."
    return AIOpsSignal(
        name=name,
        status=status,
        value=blocked,
        unit="count",
        source="task_service",
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
