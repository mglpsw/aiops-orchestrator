"""Prometheus metrics endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_router.metrics import render_aiops_metrics_lines
from app.api.legacy_usage import render_legacy_usage_metrics_lines
from app.models.database import get_db
from app.services.task_service import TaskService

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics(db: AsyncSession = Depends(get_db)):
    """Expose metrics in Prometheus text format."""
    service = TaskService(db)
    m = await service.get_metrics()

    lines = [
        "# HELP aiops_tasks_total Total number of tasks",
        "# TYPE aiops_tasks_total gauge",
        f'aiops_tasks_total {m["tasks_total"]}',
        "",
        "# HELP aiops_tasks_by_status Tasks grouped by status",
        "# TYPE aiops_tasks_by_status gauge",
    ]
    for status, count in m["tasks_by_status"].items():
        lines.append(f'aiops_tasks_by_status{{status="{status}"}} {count}')

    lines.extend([
        "",
        "# HELP aiops_provider_calls_total Total provider API calls",
        "# TYPE aiops_provider_calls_total counter",
        f'aiops_provider_calls_total {m["provider_calls_total"]}',
        "",
        "# HELP aiops_provider_failures_total Total provider failures",
        "# TYPE aiops_provider_failures_total counter",
        f'aiops_provider_failures_total {m["provider_failures_total"]}',
        "",
        "# HELP aiops_approvals_pending Current pending approvals",
        "# TYPE aiops_approvals_pending gauge",
        f'aiops_approvals_pending {m["approvals_pending"]}',
        "",
        "# HELP aiops_blocked_actions_total Total blocked actions",
        "# TYPE aiops_blocked_actions_total counter",
        f'aiops_blocked_actions_total {m["blocked_actions_total"]}',
        "",
    ])

    lines.extend(render_aiops_metrics_lines())
    lines.extend(render_legacy_usage_metrics_lines())

    return "\n".join(lines)
