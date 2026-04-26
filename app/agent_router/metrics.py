"""In-memory metrics for AIOps Diagnostic v1."""

from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock

from app.agent_router.schemas import AIOpsDiagnoseResponse

_lock = Lock()
_diagnose_total: Counter[tuple[str, str]] = Counter()
_diagnose_latency_seconds: defaultdict[tuple[str, str], float] = defaultdict(float)
_findings_total: Counter[str] = Counter()

_ALLOWED_STATUSES = {"ok", "warning", "critical", "unknown"}
_ALLOWED_SEVERITIES = {"low", "medium", "high"}


def record_aiops_diagnose(response: AIOpsDiagnoseResponse, latency_seconds: float) -> None:
    """Record low-cardinality counters for diagnose requests."""
    status = _normalize_label(response.status, _ALLOWED_STATUSES, default="unknown")
    severity = _normalize_label(response.severity, _ALLOWED_SEVERITIES, default="low")
    with _lock:
        _diagnose_total[(status, severity)] += 1
        _diagnose_latency_seconds[(status, severity)] += max(latency_seconds, 0.0)
        for finding in response.findings:
            finding_severity = _normalize_label(finding.severity, _ALLOWED_SEVERITIES, default="low")
            _findings_total[finding_severity] += 1


def render_aiops_metrics_lines() -> list[str]:
    """Render diagnostic metrics in Prometheus text format."""
    lines: list[str] = [
        "# HELP agent_router_aiops_diagnose_total Total AIOps diagnose requests",
        "# TYPE agent_router_aiops_diagnose_total counter",
    ]

    with _lock:
        for (status, severity), count in sorted(_diagnose_total.items()):
            lines.append(
                f'agent_router_aiops_diagnose_total{{status="{status}",severity="{severity}"}} {count}'
            )

        lines.extend(
            [
                "",
                "# HELP agent_router_aiops_diagnose_latency_seconds Total latency spent diagnosing AIOps requests",
                "# TYPE agent_router_aiops_diagnose_latency_seconds counter",
            ]
        )
        for (status, severity), latency in sorted(_diagnose_latency_seconds.items()):
            lines.append(
                f'agent_router_aiops_diagnose_latency_seconds{{status="{status}",severity="{severity}"}} {latency:.6f}'
            )

        lines.extend(
            [
                "",
                "# HELP agent_router_aiops_findings_total Total AIOps findings emitted",
                "# TYPE agent_router_aiops_findings_total counter",
            ]
        )
        for severity, count in sorted(_findings_total.items()):
            lines.append(f'agent_router_aiops_findings_total{{severity="{severity}"}} {count}')

    lines.append("")
    return lines


def reset_aiops_metrics() -> None:
    """Reset in-memory AIOps diagnostic metrics.

    This is intended for tests.
    """
    with _lock:
        _diagnose_total.clear()
        _diagnose_latency_seconds.clear()
        _findings_total.clear()


def _normalize_label(value: str, allowed: set[str], default: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in allowed else default
