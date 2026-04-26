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
    status: str
    severity: str
    is_problem: bool
    message: str


def diagnose_aiops(
    request: AIOpsDiagnoseRequest,
    signals: list[AIOpsSignal] | None = None,
) -> AIOpsDiagnoseResponse:
    """Run a diagnostic pass using only supplied or synthetic signals."""
    effective_signals = _build_effective_signals(request, signals)
    findings: list[AIOpsFinding] = []
    recommended_actions: list[AIOpsRecommendedAction] = []

    assessments = [_assess_signal(signal) for signal in effective_signals]
    problem_assessments = [assessment for assessment in assessments if assessment.is_problem]

    for signal, assessment in zip(effective_signals, assessments, strict=True):
        if assessment.is_problem:
            findings.append(
                AIOpsFinding(
                    title=_finding_title(signal.name),
                    severity=assessment.severity,
                    status=assessment.status,
                    description=assessment.message,
                    evidence=[signal],
                )
            )

    if not effective_signals or all(signal.status.lower() in _UNKNOWN for signal in effective_signals):
        status = "unknown"
        severity = "low"
        summary = "Insufficient diagnostic signals to determine system state."
        if request.dry_run:
            recommended_actions.extend(_default_unknown_actions())
        findings.append(
            AIOpsFinding(
                title="Insufficient signals",
                severity="low",
                status="unknown",
                description="No actionable signals were provided for diagnostic evaluation.",
                evidence=effective_signals,
            )
        )
    else:
        status, severity = _overall_state(assessments)
        summary = _build_summary(status, severity, effective_signals, assessments)
        if request.dry_run and problem_assessments:
            recommended_actions.extend(_recommended_actions_for_assessments(problem_assessments))

    if request.dry_run and not recommended_actions and status != "ok":
        recommended_actions.extend(_default_safe_actions())

    return AIOpsDiagnoseResponse(
        status=status,
        severity=severity,
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

    if name == "readiness":
        if status in _READINESS_DOWN:
            return _Assessment("critical", "high", True, "Readiness is not ready.")
        if status in _READINESS_DEGRADED:
            return _Assessment("warning", "medium", True, "Readiness is degraded.")
        if status in _OK:
            return _Assessment("ok", "low", False, "Readiness is normal.")
        return _Assessment("unknown", "low", False, "Readiness status is unknown.")

    if name == "backend_up":
        if status in _BACKEND_DOWN:
            return _Assessment("critical", "high", True, "Required backend appears to be down.")
        if status in _BACKEND_DEGRADED:
            return _Assessment("warning", "medium", True, "Required backend is degraded.")
        if status in _OK:
            return _Assessment("ok", "low", False, "Backend availability is normal.")
        return _Assessment("unknown", "low", False, "Backend availability is unknown.")

    if name == "latency_p95":
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment("unknown", "low", False, "Latency signal is unavailable.")
            return _Assessment("ok", "low", False, "Latency signal is present but not numeric.")
        if numeric >= _LATENCY_WARNING_MS:
            return _Assessment("warning", "medium", True, f"P95 latency is elevated at {numeric}.")
        return _Assessment("ok", "low", False, f"P95 latency is within range at {numeric}.")

    if name == "error_rate":
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment("unknown", "low", False, "Error rate signal is unavailable.")
            return _Assessment("ok", "low", False, "Error rate signal is present but not numeric.")
        if numeric >= _ERROR_RATE_WARNING:
            return _Assessment("warning", "medium", True, f"Error rate is elevated at {numeric}.")
        return _Assessment("ok", "low", False, f"Error rate is within range at {numeric}.")

    if name == "blocked_tasks":
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment("unknown", "low", False, "Blocked task count is unavailable.")
            return _Assessment("ok", "low", False, "Blocked task count is present but not numeric.")
        if numeric >= _BLOCKED_TASKS_WARNING:
            return _Assessment("warning", "medium", True, f"Blocked tasks are elevated at {numeric}.")
        return _Assessment("ok", "low", False, f"Blocked tasks are normal at {numeric}.")

    if name == "model_selection":
        if status in _UNKNOWN:
            return _Assessment("unknown", "low", False, "Model selection status is unknown.")
        if status in {"degraded", "warning", "fallback"}:
            return _Assessment("warning", "medium", True, "Model selection is degraded or using fallback routing.")
        return _Assessment("ok", "low", False, "Model selection is normal.")

    if name == "ollama_models_count":
        numeric = _coerce_number(value)
        if numeric is None:
            if status in _UNKNOWN:
                return _Assessment("unknown", "low", False, "Ollama model count is unavailable.")
            return _Assessment("ok", "low", False, "Ollama model count signal is present but not numeric.")
        if numeric < _OLLAMA_MODELS_MIN:
            return _Assessment("warning", "medium", True, "No Ollama models are available.")
        return _Assessment("ok", "low", False, f"Ollama model count is {numeric}.")

    if status in _UNKNOWN:
        return _Assessment("unknown", "low", False, f"Signal {signal.name} is unknown.")
    if status in {"degraded", "warning"}:
        return _Assessment("warning", "medium", True, f"Signal {signal.name} is degraded.")
    if status in _OK:
        return _Assessment("ok", "low", False, f"Signal {signal.name} is normal.")
    return _Assessment("unknown", "low", False, f"Signal {signal.name} is not recognized.")


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
) -> str:
    problem_count = sum(1 for item in assessments if item.is_problem)
    total = len(signals)
    if status == "ok":
        return f"System state is healthy across {total} signal(s)."
    if status == "warning":
        return f"System state shows {problem_count} warning signal(s) across {total} checked signal(s)."
    if status == "critical":
        return f"System state is critical with {problem_count} high-severity issue(s)."
    return "System state could not be determined from the available signals."


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
