"""Deterministic diagnostic service for AIOps Diagnostic Engine v1.

This module is intentionally isolated from execution adapters, shells,
SSH, Docker, LLM calls, and remediation flows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.agent_router.schemas import (
    AIOpsDiagnoseRequest,
    AIOpsDiagnoseResponse,
    AIOpsFinding,
    AIOpsRecommendedAction,
    AIOpsSignal,
)
from app.agent_router.services.action_mapper import recommended_action_ids_for_check
from app.agent_router.services.health_score import calculate_health_score, classify_health_score

_READINESS_DOWN = {"not_ready", "down", "failed", "fail", "critical"}
_READINESS_DEGRADED = {"degraded", "warning", "partial"}
_BACKEND_DOWN = {"down", "offline", "unavailable", "false", "0"}
_BACKEND_DEGRADED = {"degraded", "warning", "partial"}
_OK = {"ok", "up", "ready", "healthy", "true", "1"}
_UNKNOWN = {"unknown", "n/a", "na", "none", ""}

_LATENCY_WARNING_MS = 500
_ERROR_RATE_WARNING = 0.05
_BLOCKED_TASKS_WARNING = 1
_OLLAMA_MODELS_MIN = 1


@dataclass(frozen=True)
class _Assessment:
    check: str
    status: str
    severity: str
    is_problem: bool
    emit_finding: bool
    message: str
    summary: str
    impact: str
    confidence: float
    probable_cause: str
    next_validation: str
    recommended_action_ids: list[str]


def diagnose_aiops(
    request: AIOpsDiagnoseRequest,
    signals: list[AIOpsSignal] | None = None,
    catalog_readiness: dict[str, Any] | None = None,
) -> AIOpsDiagnoseResponse:
    """Run a diagnostic pass using only supplied or synthetic signals."""
    effective_signals = _build_effective_signals(request, signals)
    catalog_signal = _build_catalog_signal(catalog_readiness)
    if catalog_signal is not None and all(signal.name.lower() != catalog_signal.name for signal in effective_signals):
        effective_signals = [*effective_signals, catalog_signal]

    findings: list[AIOpsFinding] = []
    recommended_actions: list[AIOpsRecommendedAction] = []

    assessments = [_assess_signal(signal) for signal in effective_signals]

    for signal, assessment in zip(effective_signals, assessments, strict=True):
        if assessment.emit_finding:
            findings.append(_build_finding(signal, assessment))

    problem_assessments = [assessment for assessment in assessments if assessment.is_problem]
    health = calculate_health_score(findings)
    unknown_only = bool(findings) and health.unknown_only
    status, severity = classify_health_score(health.score, unknown_only=unknown_only)

    if not effective_signals:
        status = "unknown"
        severity = "low"
        summary = "Insufficient diagnostic signals to determine system state."
        if request.dry_run:
            recommended_actions.extend(_default_unknown_actions())
        findings.append(
            AIOpsFinding(
                title="Insufficient signals",
                check="insufficient_signals",
                severity="low",
                status="unknown",
                summary="No actionable signals were provided for diagnostic evaluation.",
                description="No actionable signals were provided for diagnostic evaluation.",
                evidence=[],
                impact="The diagnostic engine cannot assess health with no signals.",
                confidence=0.2,
                probable_cause="No request checks or collected signals were available.",
                next_validation="Retry diagnose with at least one supported check.",
                recommended_action_ids=[],
            )
        )
    else:
        summary = _build_summary(status, severity, effective_signals, assessments, health.score)
        if request.dry_run and problem_assessments:
            recommended_actions.extend(_recommended_actions_for_assessments(problem_assessments))

    if request.dry_run and not recommended_actions and status != "ok":
        recommended_actions.extend(_default_safe_actions())

    return AIOpsDiagnoseResponse(
        status=status,
        severity=severity,
        health_score=health.score,
        summary=summary,
        signals=effective_signals,
        findings=findings,
        recommended_actions=recommended_actions if request.dry_run else [],
        dry_run=True,
    )


def _build_effective_signals(
    request: AIOpsDiagnoseRequest,
    signals: list[AIOpsSignal] | None,
) -> list[AIOpsSignal]:
    if signals is None:
        return [
            AIOpsSignal(
                name=check,
                status="unknown",
                value=None,
                unit=None,
                source="synthetic",
                description="Synthetic signal created because no external signals were supplied.",
            )
            for check in request.checks
        ]

    provided = list(signals)
    requested_names = {signal.name for signal in provided}
    for check in request.checks:
        if check not in requested_names:
            provided.append(
                AIOpsSignal(
                    name=check,
                    status="unknown",
                    value=None,
                    unit=None,
                    source="synthetic",
                    description="Synthetic signal created because the requested check was not supplied.",
                )
            )
    return provided


def _assess_signal(signal: AIOpsSignal) -> _Assessment:
    name = signal.name.lower().strip()
    status = signal.status.lower().strip()
    value = signal.value
    action_ids = recommended_action_ids_for_check(name)

    if name in {"readiness", "readiness_status"}:
        if status in _READINESS_DOWN:
            return _Assessment(
                check=name,
                status="critical",
                severity="high",
                is_problem=True,
                emit_finding=True,
                message="Readiness is not ready.",
                summary="Readiness failed and the runtime is not ready.",
                impact="Core dependencies are not ready, so diagnose confidence is low and operations may be impacted.",
                confidence=0.98,
                probable_cause="A critical dependency failed readiness.",
                next_validation="Check /ready and the dependency chain for the runtime.",
                recommended_action_ids=action_ids,
            )
        if status in _READINESS_DEGRADED:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="Readiness is degraded.",
                summary="Readiness is degraded and the runtime should be watched closely.",
                impact="Some required dependency is not fully ready.",
                confidence=0.9,
                probable_cause="A dependency is partially available.",
                next_validation="Re-check /ready and the dependency-specific logs.",
                recommended_action_ids=action_ids,
            )
        if status in _OK:
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Readiness is normal.",
                summary="Readiness is healthy.",
                impact="No readiness impact detected.",
                confidence=0.99,
                probable_cause="All required dependencies are healthy.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        return _Assessment(
            check=name,
            status="unknown",
            severity="low",
            is_problem=False,
            emit_finding=True,
            message="Readiness status is unknown.",
            summary="Readiness could not be classified safely.",
            impact="The runtime could not determine readiness from the available signals.",
            confidence=0.35,
            probable_cause="The signal is missing or not recognized.",
            next_validation="Re-run /ready and confirm the signal source.",
            recommended_action_ids=action_ids,
        )

    if name == "backend_up":
        if status in _BACKEND_DOWN:
            return _Assessment(
                check=name,
                status="critical",
                severity="high",
                is_problem=True,
                emit_finding=True,
                message="Required backend appears to be down.",
                summary="The main backend is unavailable.",
                impact="The runtime cannot reach a required backend dependency.",
                confidence=0.97,
                probable_cause="A required provider or router backend is offline.",
                next_validation="Check the backend health endpoint and local provider registry.",
                recommended_action_ids=action_ids,
            )
        if status in _BACKEND_DEGRADED:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="Required backend is degraded.",
                summary="The main backend is degraded.",
                impact="The runtime may still respond, but quality or latency may be affected.",
                confidence=0.9,
                probable_cause="The backend is partially available or rate limited.",
                next_validation="Inspect backend health and recent provider failures.",
                recommended_action_ids=action_ids,
            )
        if status in _OK:
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Backend availability is normal.",
                summary="Backend availability is healthy.",
                impact="No backend impact detected.",
                confidence=0.99,
                probable_cause="The backend dependency is healthy.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        return _Assessment(
            check=name,
            status="unknown",
            severity="low",
            is_problem=False,
            emit_finding=True,
            message="Backend availability is unknown.",
            summary="Backend health could not be classified safely.",
            impact="The runtime could not confirm backend availability.",
            confidence=0.35,
            probable_cause="The backend signal is missing or not recognized.",
            next_validation="Re-run the backend health check.",
            recommended_action_ids=action_ids,
        )

    if name in {"latency_p95", "latency_p95_high"}:
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment(
                    check=name,
                    status="unavailable",
                    severity="low",
                    is_problem=False,
                    emit_finding=True,
                    message="Latency signal is unavailable.",
                    summary="No safe latency sample is available.",
                    impact="The runtime cannot estimate p95 latency from available signals.",
                    confidence=0.3,
                    probable_cause="No safe latency metric is exposed in v1.",
                    next_validation="Use allowlisted metrics or the /metrics endpoint to collect latency data.",
                    recommended_action_ids=action_ids,
                )
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Latency signal is present but not numeric.",
                summary="Latency signal is present.",
                impact="Latency signal exists but does not need a finding.",
                confidence=0.5,
                probable_cause="The latency value is non-numeric but not alarming.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        if numeric >= _LATENCY_WARNING_MS:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message=f"P95 latency is elevated at {numeric}.",
                summary="P95 latency is elevated.",
                impact="User-facing responses may be slower than expected.",
                confidence=0.92,
                probable_cause="Recent requests are taking longer than the preferred threshold.",
                next_validation="Inspect the latency trend in allowlisted metrics or logs.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message=f"P95 latency is within range at {numeric}.",
            summary="P95 latency is healthy.",
            impact="No latency impact detected.",
            confidence=0.99,
            probable_cause="Current latency is below the warning threshold.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name in {"error_rate", "error_rate_high"}:
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment(
                    check=name,
                    status="unavailable",
                    severity="low",
                    is_problem=False,
                    emit_finding=True,
                    message="Error rate signal is unavailable.",
                    summary="No safe error-rate sample is available.",
                    impact="The runtime cannot estimate error rate from available signals.",
                    confidence=0.3,
                    probable_cause="No safe error-rate metric is exposed in v1.",
                    next_validation="Use the task-service metrics or /metrics endpoint to inspect error rate.",
                    recommended_action_ids=action_ids,
                )
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Error rate signal is present but not numeric.",
                summary="Error-rate signal is present.",
                impact="Error-rate signal exists but does not require a finding.",
                confidence=0.5,
                probable_cause="The error-rate value is non-numeric but not alarming.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        if numeric >= _ERROR_RATE_WARNING:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message=f"Error rate is elevated at {numeric}.",
                summary="Error rate is elevated.",
                impact="Requests or chat turns are failing more often than expected.",
                confidence=0.92,
                probable_cause="Recent operations are producing more failures than normal.",
                next_validation="Inspect allowlisted metrics and recent task failures.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message=f"Error rate is within range at {numeric}.",
            summary="Error rate is healthy.",
            impact="No error-rate impact detected.",
            confidence=0.99,
            probable_cause="Current error rate is below the warning threshold.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name in {"blocked_tasks", "route_block_spike"}:
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment(
                    check=name,
                    status="unavailable",
                    severity="low",
                    is_problem=False,
                    emit_finding=True,
                    message="Blocked task count is unavailable.",
                    summary="Blocked-task telemetry is unavailable.",
                    impact="The runtime cannot quantify blocked work safely.",
                    confidence=0.3,
                    probable_cause="Blocked-task telemetry is not exposed in this context.",
                    next_validation="Inspect local task metrics or /metrics for blocked tasks.",
                    recommended_action_ids=action_ids,
                )
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Blocked task count is present but not numeric.",
                summary="Blocked-task signal is present.",
                impact="Blocked-task signal exists but does not require a finding.",
                confidence=0.5,
                probable_cause="The blocked-task value is non-numeric but not alarming.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        if numeric >= _BLOCKED_TASKS_WARNING:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message=f"Blocked tasks are elevated at {numeric}.",
                summary="Blocked tasks are elevated.",
                impact="Work is being blocked or deferred.",
                confidence=0.9,
                probable_cause="One or more tasks are stalled or blocked by policy.",
                next_validation="Review blocked task counts and recent task events.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message=f"Blocked tasks are normal at {numeric}.",
            summary="Blocked tasks are healthy.",
            impact="No blocked-task impact detected.",
            confidence=0.99,
            probable_cause="Blocked-task counts are below the warning threshold.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name == "model_selection":
        if status in _UNKNOWN:
            return _Assessment(
                check=name,
                status="unknown",
                severity="low",
                is_problem=False,
                emit_finding=True,
                message="Model selection status is unknown.",
                summary="Model selection could not be classified safely.",
                impact="The runtime could not inspect the current model selection.",
                confidence=0.35,
                probable_cause="The model selection signal is missing or unavailable.",
                next_validation="Inspect the current planner model setting.",
                recommended_action_ids=action_ids,
            )
        if status in {"degraded", "warning", "fallback"}:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="Model selection is degraded or using fallback routing.",
                summary="Model selection is degraded.",
                impact="The runtime may be using fallback routing or a non-preferred model.",
                confidence=0.9,
                probable_cause="The configured model is falling back or not matching policy.",
                next_validation="Check the planner model selection and provider registry.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message="Model selection is normal.",
            summary="Model selection is healthy.",
            impact="No model-selection impact detected.",
            confidence=0.99,
            probable_cause="The configured model is healthy.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name == "ollama_models_count":
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment(
                    check=name,
                    status="unavailable",
                    severity="low",
                    is_problem=False,
                    emit_finding=True,
                    message="Ollama model count is unavailable.",
                    summary="Ollama model inventory is unavailable.",
                    impact="The runtime cannot inspect local Ollama model availability.",
                    confidence=0.3,
                    probable_cause="The model inventory is not exposed in this context.",
                    next_validation="Inspect the local Ollama inventory if needed.",
                    recommended_action_ids=action_ids,
                )
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Ollama model count signal is present but not numeric.",
                summary="Ollama model count is healthy.",
                impact="No Ollama inventory impact detected.",
                confidence=0.5,
                probable_cause="The inventory value is non-numeric but not alarming.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        if numeric < _OLLAMA_MODELS_MIN:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="No Ollama models are available.",
                summary="No local Ollama models are available.",
                impact="Model routing may fall back or fail for local provider paths.",
                confidence=0.9,
                probable_cause="The local Ollama inventory is empty.",
                next_validation="Verify the local Ollama inventory and configured models.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message=f"Ollama model count is {numeric}.",
            summary="Ollama model inventory is healthy.",
            impact="No Ollama inventory impact detected.",
            confidence=0.99,
            probable_cause="At least one Ollama model is available.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name == "prometheus_scrape_staleness":
        if status in _UNKNOWN:
            return _Assessment(
                check=name,
                status="skipped",
                severity="low",
                is_problem=False,
                emit_finding=True,
                message="Prometheus scrape freshness is unavailable in v1.",
                summary="Prometheus scrape freshness is not queried directly.",
                impact="The diagnostic engine cannot inspect scrape freshness without an external query.",
                confidence=0.25,
                probable_cause="Prometheus scrape freshness is not exposed through the current allowlisted signals.",
                next_validation="Use the allowlisted Prometheus query action to inspect target freshness.",
                recommended_action_ids=action_ids,
            )
        if status in _WARNING_STATUSES:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="Prometheus scrape freshness is degraded.",
                summary="Prometheus scrape freshness is degraded.",
                impact="Observability freshness may be stale.",
                confidence=0.85,
                probable_cause="A scrape target appears stale or delayed.",
                next_validation="Query Prometheus freshness and target health through the allowlisted path.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status=status or "unavailable",
            severity="low",
            is_problem=False,
            emit_finding=True,
            message="Prometheus scrape freshness is available.",
            summary="Prometheus scrape freshness signal is present.",
            impact="No scrape freshness issue detected.",
            confidence=0.7,
            probable_cause="A scrape freshness signal was supplied.",
            next_validation="No immediate validation required.",
            recommended_action_ids=action_ids if status not in _UNKNOWN else [],
        )

    if name == "rate_limit_spike":
        if status in _UNKNOWN:
            return _Assessment(
                check=name,
                status="unavailable",
                severity="low",
                is_problem=False,
                emit_finding=True,
                message="Rate-limit telemetry is unavailable in v1.",
                summary="Rate-limit telemetry is not exposed directly.",
                impact="The diagnostic engine cannot quantify rate limiting from current signals.",
                confidence=0.25,
                probable_cause="Rate-limit counters are not exposed in the current allowlisted telemetry.",
                next_validation="Use allowlisted metrics or logs if a rate-limit spike is suspected.",
                recommended_action_ids=action_ids,
            )
        if status in _WARNING_STATUSES:
            return _Assessment(
                check=name,
                status="warning",
                severity="medium",
                is_problem=True,
                emit_finding=True,
                message="Rate-limit activity is elevated.",
                summary="Rate-limit activity is elevated.",
                impact="Requests may be throttled or deferred.",
                confidence=0.85,
                probable_cause="The system is encountering more rate-limit events than expected.",
                next_validation="Inspect local rate-limit related telemetry.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message="Rate-limit telemetry is normal.",
            summary="Rate-limit activity is healthy.",
            impact="No rate-limit spike detected.",
            confidence=0.95,
            probable_cause="Current rate-limit activity is within bounds.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )

    if name == "aiops_catalog_not_ready":
        if status in _OK:
            return _Assessment(
                check=name,
                status="ok",
                severity="low",
                is_problem=False,
                emit_finding=False,
                message="Action catalog is ready.",
                summary="Action catalog is healthy.",
                impact="No catalog impact detected.",
                confidence=0.99,
                probable_cause="The action catalog loaded successfully at startup.",
                next_validation="No immediate validation required.",
                recommended_action_ids=[],
            )
        if status in _UNKNOWN:
            return _Assessment(
                check=name,
                status="unknown",
                severity="low",
                is_problem=False,
                emit_finding=True,
                message="Action catalog readiness is unknown.",
                summary="Action catalog readiness could not be classified safely.",
                impact="The planner may not be able to rely on the catalog state.",
                confidence=0.35,
                probable_cause="The startup readiness snapshot is missing.",
                next_validation="Inspect the catalog startup validation and /ready.",
                recommended_action_ids=action_ids,
            )
        return _Assessment(
            check=name,
            status="warning",
            severity="medium",
            is_problem=True,
            emit_finding=True,
            message="Action catalog is not ready.",
            summary="The action catalog failed startup validation.",
            impact="The planner may operate with reduced confidence or no plan.",
            confidence=0.95,
            probable_cause="The action catalog failed to load or validate at startup.",
            next_validation="Inspect the catalog configuration and restart after fixing it.",
            recommended_action_ids=action_ids,
        )

    if status in _UNKNOWN:
        return _Assessment(
            check=name,
            status="unknown",
            severity="low",
            is_problem=False,
            emit_finding=True,
            message=f"Signal {signal.name} is unknown.",
            summary=f"Signal {signal.name} could not be classified safely.",
            impact="The signal did not provide enough information to classify health.",
            confidence=0.25,
            probable_cause="The signal source is missing or not recognized.",
            next_validation="Re-run the check or inspect the signal source.",
            recommended_action_ids=action_ids,
        )
    if status in {"degraded", "warning"}:
        return _Assessment(
            check=name,
            status="warning",
            severity="medium",
            is_problem=True,
            emit_finding=True,
            message=f"Signal {signal.name} is degraded.",
            summary=f"Signal {signal.name} is degraded.",
            impact="The signal indicates degraded health.",
            confidence=0.8,
            probable_cause="The supplied signal reports a degraded state.",
            next_validation="Inspect the downstream dependency for the signal.",
            recommended_action_ids=action_ids,
        )
    if status in _OK:
        return _Assessment(
            check=name,
            status="ok",
            severity="low",
            is_problem=False,
            emit_finding=False,
            message=f"Signal {signal.name} is normal.",
            summary=f"Signal {signal.name} is healthy.",
            impact="No issue detected for the signal.",
            confidence=0.99,
            probable_cause="The signal reports a healthy state.",
            next_validation="No immediate validation required.",
            recommended_action_ids=[],
        )
    return _Assessment(
        check=name,
        status="unknown",
        severity="low",
        is_problem=False,
        emit_finding=True,
        message=f"Signal {signal.name} is not recognized.",
        summary=f"Signal {signal.name} is not recognized safely.",
        impact="The diagnostic engine cannot safely classify this signal.",
        confidence=0.2,
        probable_cause="The signal name or status is not recognized.",
        next_validation="Inspect the signal definition or supported checks.",
        recommended_action_ids=action_ids,
    )


def _overall_state(assessments: Iterable[_Assessment]) -> tuple[str, str]:
    assessments = list(assessments)
    if not assessments:
        return "unknown", "low"

    has_high = any(item.severity == "high" for item in assessments)
    has_medium = any(item.severity == "medium" for item in assessments)
    has_ok = any(item.status == "ok" for item in assessments)
    has_unknown = any(item.status == "unknown" for item in assessments)

    if has_high:
        return "critical", "high"
    if has_medium:
        return "warning", "medium"
    if has_ok:
        return "ok", "low"
    if has_unknown:
        return "unknown", "low"
    return "unknown", "low"


def _build_summary(
    status: str,
    severity: str,
    signals: list[AIOpsSignal],
    assessments: list[_Assessment],
    health_score: int,
) -> str:
    problem_count = sum(1 for item in assessments if item.is_problem)
    total = len(signals)
    if status == "ok":
        if health_score == 100:
            return f"System state is healthy across {total} signal(s). Health score: {health_score}."
        return (
            f"System state is healthy with warnings across {total} signal(s). "
            f"Health score: {health_score}."
        )
    if status == "warning":
        return (
            f"System state shows {problem_count} degraded signal(s) across {total} checked signal(s). "
            f"Health score: {health_score}."
        )
    if status == "critical":
        return f"System state is critical with {problem_count} high-severity issue(s). Health score: {health_score}."
    if status == "unknown":
        return f"System state could not be determined from the available signals. Health score: {health_score}."
    return "System state could not be determined from the available signals."


def _build_finding(signal: AIOpsSignal, assessment: _Assessment) -> AIOpsFinding:
    return AIOpsFinding(
        check=assessment.check,
        title=_finding_title(signal.name),
        severity=assessment.severity,
        status=assessment.status,
        summary=assessment.summary,
        description=assessment.message,
        evidence=[signal],
        impact=assessment.impact,
        confidence=assessment.confidence,
        probable_cause=assessment.probable_cause,
        next_validation=assessment.next_validation,
        recommended_action_ids=list(assessment.recommended_action_ids),
    )


def _build_catalog_signal(catalog_readiness: dict[str, Any] | None) -> AIOpsSignal | None:
    if not catalog_readiness:
        return None
    status = str(catalog_readiness.get("status", "unknown")).lower().strip()
    actions_count = catalog_readiness.get("actions_count")
    if status == "ok":
        return None
    return AIOpsSignal(
        name="aiops_catalog_not_ready",
        status=status or "error",
        value=actions_count,
        unit="count",
        source="startup",
        description="Action catalog failed startup validation and readiness is degraded.",
    )


def _recommended_actions_for_assessments(
    assessments: list[_Assessment],
) -> list[AIOpsRecommendedAction]:
    actions: list[AIOpsRecommendedAction] = []
    seen_titles: set[str] = set()

    def add_action(title: str, description: str, requires_approval: bool = False) -> None:
        if title in seen_titles:
            return
        seen_titles.add(title)
        actions.append(
            AIOpsRecommendedAction(
                title=title,
                description=description,
                requires_approval=requires_approval,
            )
        )

    has_readiness_issue = any(
        item.message.startswith("Readiness") for item in assessments if item.is_problem
    )
    has_backend_issue = any(
        "backend" in item.message.lower() for item in assessments if item.is_problem
    )
    has_latency_issue = any(
        "latency" in item.message.lower() for item in assessments if item.is_problem
    )
    has_error_issue = any(
        "error rate" in item.message.lower() for item in assessments if item.is_problem
    )
    has_blocked_issue = any(
        "blocked" in item.message.lower() for item in assessments if item.is_problem
    )
    has_catalog_issue = any(item.check == "aiops_catalog_not_ready" for item in assessments if item.is_problem)

    if has_readiness_issue:
        add_action(
            "Verificar readiness das dependências",
            "Revisar se os componentes internos estão respondendo e prontos para operação.",
        )
    if has_backend_issue:
        add_action(
            "Validar disponibilidade do backend",
            "Confirmar se o backend obrigatório está online e saudável.",
        )
    if has_latency_issue:
        add_action(
            "Consultar métricas allowlisted",
            "Analisar a tendência de latência usando apenas métricas permitidas.",
        )
    if has_error_issue:
        add_action(
            "Revisar logs da aplicação",
            "Inspecionar logs para identificar erros recorrentes sem executar ações.",
        )
    if has_blocked_issue:
        add_action(
            "Revisar tarefas bloqueadas",
            "Examinar contadores e políticas que possam estar bloqueando processamento.",
        )
    if has_catalog_issue:
        add_action(
            "Revisar catálogo de ações",
            "Validar se o catálogo allowlisted carregou corretamente no startup.",
        )

    if not actions:
        add_action(
            "Consultar métricas allowlisted",
            "Coletar mais sinais antes de qualquer decisão operacional.",
        )

    return actions


def _default_unknown_actions() -> list[AIOpsRecommendedAction]:
    return [
        AIOpsRecommendedAction(
            title="Verificar readiness das dependências",
            description="Confirmar se os componentes essenciais estão respondendo.",
        ),
        AIOpsRecommendedAction(
            title="Consultar métricas allowlisted",
            description="Coletar sinais adicionais a partir das métricas permitidas.",
        ),
    ]


def _default_safe_actions() -> list[AIOpsRecommendedAction]:
    return [
        AIOpsRecommendedAction(
            title="Revisar logs da aplicação",
            description="Examinar logs para entender o contexto do alerta.",
        ),
        AIOpsRecommendedAction(
            title="Validar disponibilidade do backend",
            description="Confirmar a saúde dos serviços dependentes sem executar comandos.",
        ),
    ]


def _finding_title(signal_name: str) -> str:
    return signal_name.replace("_", " ").strip().title()


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
