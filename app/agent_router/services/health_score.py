"""Deterministic health score calculation for AIOps diagnostics.

The score is derived only from findings/checks. No LLM, no execution, and
no external side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.agent_router.schemas import AIOpsFinding

_CRITICAL_STATUSES = {"critical", "down", "not_ready", "failed", "fail"}
_WARNING_STATUSES = {"warning", "degraded", "partial"}
_UNKNOWN_STATUSES = {"unknown", "unavailable", "skipped", "n/a", "na", "none", ""}


@dataclass(frozen=True)
class HealthScoreResult:
    score: int
    status: str
    severity: str
    total_penalty: int
    unknown_only: bool


def calculate_health_score(findings: Sequence[AIOpsFinding]) -> HealthScoreResult:
    """Return a deterministic health score and score-derived band."""
    total_penalty = sum(_finding_penalty(finding) for finding in findings)
    score = max(0, min(100, 100 - total_penalty))
    unknown_only = bool(findings) and all(_is_unknown_like(finding) for finding in findings)
    status, severity = classify_health_score(score, unknown_only=unknown_only)
    return HealthScoreResult(
        score=score,
        status=status,
        severity=severity,
        total_penalty=total_penalty,
        unknown_only=unknown_only,
    )


def classify_health_score(score: int, *, unknown_only: bool = False) -> tuple[str, str]:
    """Map a numeric score into the current response contract."""
    bounded = max(0, min(100, int(score)))
    if unknown_only:
        return "unknown", "low"
    if bounded >= 100:
        return "ok", "low"
    if bounded >= 80:
        return "ok", "medium"
    if bounded >= 60:
        return "warning", "medium"
    if bounded >= 40:
        return "warning", "high"
    return "critical", "high"


def _finding_penalty(finding: AIOpsFinding) -> int:
    check = _normalize_token(finding.check or finding.title)
    status = _normalize_token(finding.status)

    if check in {"readiness", "readiness_status"}:
        if status in _CRITICAL_STATUSES:
            return 70
        if status in _WARNING_STATUSES:
            return 35
        if status in _UNKNOWN_STATUSES:
            return 10
        return 5

    if check == "backend_up":
        if status in _CRITICAL_STATUSES:
            return 65
        if status in _WARNING_STATUSES:
            return 30
        if status in _UNKNOWN_STATUSES:
            return 10
        return 5

    if check in {"error_rate", "error_rate_high", "latency_p95", "latency_p95_high"}:
        if status in _CRITICAL_STATUSES:
            return 40
        if status in _WARNING_STATUSES:
            return 25
        if status in _UNKNOWN_STATUSES:
            return 10
        return 5

    if check in {"blocked_tasks", "route_block_spike"}:
        if status in _CRITICAL_STATUSES:
            return 15
        if status in _WARNING_STATUSES:
            return 10
        if status in _UNKNOWN_STATUSES:
            return 5
        return 3

    if check == "rate_limit_spike":
        if status in _CRITICAL_STATUSES:
            return 10
        if status in _WARNING_STATUSES:
            return 5
        if status in _UNKNOWN_STATUSES:
            return 5
        return 3

    if check == "prometheus_scrape_staleness":
        if status in _CRITICAL_STATUSES:
            return 20
        if status in _WARNING_STATUSES:
            return 15
        if status in _UNKNOWN_STATUSES:
            return 10
        return 5

    if check == "aiops_catalog_not_ready":
        if status in _CRITICAL_STATUSES:
            return 20
        if status in _WARNING_STATUSES:
            return 10
        if status in _UNKNOWN_STATUSES:
            return 5
        return 5

    if status in _CRITICAL_STATUSES:
        return 50
    if status in _WARNING_STATUSES:
        return 20
    if status in _UNKNOWN_STATUSES:
        return 5
    return 0


def _is_unknown_like(finding: AIOpsFinding) -> bool:
    status = _normalize_token(finding.status)
    return status in _UNKNOWN_STATUSES


def _normalize_token(value: str | None) -> str:
    return (value or "").strip().lower()
