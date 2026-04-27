"""Legacy AIOps surface usage tracking and deprecation helpers."""

from __future__ import annotations

from collections import Counter
from threading import Lock

LEGACY_DEPRECATION_WARNING = '299 - "Legacy AIOps endpoint; use canonical /v1/aiops/* APIs"'

_lock = Lock()
_legacy_hits: Counter[str] = Counter()


def legacy_endpoint_label(path: str) -> str | None:
    """Map a legacy path to a stable low-cardinality metric label."""
    if path in {"/v1/chat", "/v1/chat/ingest"}:
        return "chat_ingest"
    if path == "/v1/tasks":
        return "tasks_collection"
    if path.startswith("/v1/tasks/"):
        return "tasks_item"
    if path == "/v1/approvals":
        return "approvals_collection"
    if path.startswith("/v1/approvals/"):
        return "approvals_item"
    if path == "/v1/providers/status":
        return "providers_status"
    return None


def record_legacy_endpoint_use(label: str) -> None:
    """Record a single hit for a deprecated legacy surface."""
    with _lock:
        _legacy_hits[label] += 1


def render_legacy_usage_metrics_lines() -> list[str]:
    """Render legacy usage counters in Prometheus text format."""
    lines: list[str] = [
        "# HELP aiops_legacy_endpoint_hits_total Total requests to legacy AIOps endpoints",
        "# TYPE aiops_legacy_endpoint_hits_total counter",
    ]

    with _lock:
        for endpoint, count in sorted(_legacy_hits.items()):
            lines.append(f'aiops_legacy_endpoint_hits_total{{endpoint="{endpoint}"}} {count}')

    lines.append("")
    return lines


def reset_legacy_usage_metrics() -> None:
    """Reset legacy usage counters. Intended for tests."""
    with _lock:
        _legacy_hits.clear()
