"""Deterministic AIOps chat router for OpenWebUI-compatible chat requests.

This module only classifies safe operational intents. It never executes
actions, shells out, uses SSH, or performs deploys.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_router.schemas import AIOpsDiagnoseRequest
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.services.approval_store import list_approvals
from app.agent_router.services.run_store import get_run, list_recent_runs
from app.agent_router.signals import collect_aiops_diagnostic_signals
from app.models.schemas import ChatIngestResponse, RiskLevel, TaskStatus
from app.utils.logging import get_logger
from app.utils.secrets import mask_secrets, truncate

logger = get_logger("services.aiops_chat_router")

_INTENT_DIAGNOSE_AGENT_ROUTER = "diagnose_agent_router"
_INTENT_DIAGNOSE_AIOPS = "diagnose_aiops"
_INTENT_STATUS_AGENT_ROUTER = "status_agent_router"
_INTENT_STATUS_BLUEGREEN = "status_bluegreen"
_INTENT_APPROVALS_PENDING = "approvals_pending"
_INTENT_RUNS_RECENT = "runs_recent"
_INTENT_LAST_RUN_FAILED = "last_run_failed"
_INTENT_OLLAMA_SLOW = "ollama_slow"

_ROUTER_DIAGNOSTIC_CHECKS = [
    "readiness",
    "backend_up",
    "error_rate",
    "latency_p95",
    "blocked_tasks",
    "router_uptime_reset",
    "prometheus_scrape_staleness",
    "aiops_catalog_not_ready",
]

_AIOPS_DIAGNOSTIC_CHECKS = [
    "readiness",
    "backend_up",
    "error_rate",
    "latency_p95",
    "blocked_tasks",
    "model_selection",
    "ollama_models_count",
    "aiops_catalog_not_ready",
]

_OLLAMA_DIAGNOSTIC_CHECKS = [
    "backend_up",
    "latency_p95",
    "model_selection",
    "ollama_models_count",
    "error_rate",
]

_STATUS_DIAGNOSTIC_CHECKS = [
    "readiness",
    "backend_up",
    "aiops_catalog_not_ready",
]

_STATUS_LABELS = {
    "ok": "saudável",
    "warning": "com alerta",
    "critical": "crítico",
    "unknown": "indefinido",
}

_SEVERITY_TO_RISK = {
    "ok": RiskLevel.low,
    "warning": RiskLevel.medium,
    "critical": RiskLevel.critical,
    "unknown": RiskLevel.low,
}

_CHECK_LABELS_PT = {
    "readiness": "Prontidão",
    "readiness_status": "Prontidão",
    "backend_up": "Backend",
    "error_rate": "Taxa de erro",
    "error_rate_high": "Taxa de erro",
    "chat_error_spike": "Erros no chat",
    "latency_p95": "Latência p95",
    "latency_p95_high": "Latência p95",
    "blocked_tasks": "Tarefas bloqueadas",
    "route_block_spike": "Bloqueios de rota",
    "rate_limit_spike": "Rate limit",
    "backend_fallback_spike": "Fallback do backend",
    "router_uptime_reset": "Uptime do router",
    "prometheus_scrape_staleness": "Coleta do Prometheus",
    "aiops_catalog_not_ready": "Catálogo de ações",
    "model_selection": "Seleção de modelo",
    "ollama_models_count": "Inventário do Ollama",
}

def detect_aiops_chat_intent(message: str) -> str | None:
    """Return the deterministic intent name for a supported AIOps chat prompt."""
    normalized = _normalize(message)
    if not normalized:
        return None

    if _looks_like_last_run_failure(normalized):
        return _INTENT_LAST_RUN_FAILED
    if _looks_like_pending_approvals(normalized):
        return _INTENT_APPROVALS_PENDING
    if _looks_like_recent_runs(normalized):
        return _INTENT_RUNS_RECENT
    if _looks_like_ollama_slow(normalized):
        return _INTENT_OLLAMA_SLOW
    if _looks_like_bluegreen_status(normalized):
        return _INTENT_STATUS_BLUEGREEN
    if _looks_like_router_status(normalized):
        return _INTENT_STATUS_AGENT_ROUTER
    if _looks_like_aiops_diagnose(normalized):
        return _INTENT_DIAGNOSE_AIOPS
    if _looks_like_router_diagnose(normalized):
        return _INTENT_DIAGNOSE_AGENT_ROUTER
    return None


async def route_aiops_chat(
    message: str,
    *,
    db: AsyncSession,
    catalog_readiness: dict[str, object] | None = None,
) -> ChatIngestResponse | None:
    """Return a safe AIOps response when the message is an operational intent."""
    intent = detect_aiops_chat_intent(message)
    if intent is None:
        return None

    try:
        if intent == _INTENT_APPROVALS_PENDING:
            return await _route_pending_approvals()
        if intent == _INTENT_RUNS_RECENT:
            return await _route_recent_runs()
        if intent == _INTENT_LAST_RUN_FAILED:
            return await _route_last_failed_run()
        if intent == _INTENT_OLLAMA_SLOW:
            return await _route_diagnostic(
                db=db,
                catalog_readiness=catalog_readiness,
                checks=_OLLAMA_DIAGNOSTIC_CHECKS,
                title="Diagnóstico do Ollama",
                target="ollama",
            )
        if intent == _INTENT_DIAGNOSE_AIOPS:
            return await _route_diagnostic(
                db=db,
                catalog_readiness=catalog_readiness,
                checks=_AIOPS_DIAGNOSTIC_CHECKS,
                title="Diagnóstico do AIOps",
                target="aiops",
            )
        if intent == _INTENT_STATUS_BLUEGREEN:
            return await _route_diagnostic(
                db=db,
                catalog_readiness=catalog_readiness,
                checks=_STATUS_DIAGNOSTIC_CHECKS,
                title="Status do blue/green",
                target="bluegreen",
            )
        if intent == _INTENT_STATUS_AGENT_ROUTER:
            return await _route_diagnostic(
                db=db,
                catalog_readiness=catalog_readiness,
                checks=_STATUS_DIAGNOSTIC_CHECKS + ["router_uptime_reset", "prometheus_scrape_staleness"],
                title="Status do Agent Router",
                target="agent-router",
            )
        if intent == _INTENT_DIAGNOSE_AGENT_ROUTER:
            return await _route_diagnostic(
                db=db,
                catalog_readiness=catalog_readiness,
                checks=_ROUTER_DIAGNOSTIC_CHECKS,
                title="Diagnóstico do Agent Router",
                target="agent-router",
            )
    except Exception as exc:
        logger.exception("AIOps chat routing failed for intent %s", intent)
        return _safe_failure_response(str(exc))

    return None


def _safe_failure_response(_error: str) -> ChatIngestResponse:
    return ChatIngestResponse(
        task_id="aiops-chat",
        status=TaskStatus.failed,
        summary="Falha ao responder ao chat AIOps.",
        risk_level=RiskLevel.medium,
        requires_approval=False,
        message="Não consegui interpretar a resposta com segurança; tente novamente.",
        findings=[
            "A resposta do serviço interno não pôde ser processada com segurança.",
        ],
        recommended_action_ids=[],
    )


async def _route_diagnostic(
    *,
    db: AsyncSession,
    catalog_readiness: dict[str, object] | None,
    checks: list[str],
    title: str,
    target: str,
) -> ChatIngestResponse:
    request = AIOpsDiagnoseRequest(target=target, checks=checks, dry_run=True)
    signals = await collect_aiops_diagnostic_signals(request, db)
    response = diagnose_aiops(request, signals, catalog_readiness=catalog_readiness)
    findings = _render_findings(response.findings)
    recommended = _collect_action_ids(response.findings)
    if not findings:
        findings = ["Nenhum achado relevante nos sinais consultados."]
    summary = f"{title}: {_STATUS_LABELS.get(response.status, 'indefinido')}."
    if response.health_score is not None:
        summary = f"{summary} Saúde {response.health_score}/100."
    message = _compose_message(summary, findings, recommended)
    return ChatIngestResponse(
        task_id="aiops-chat",
        status=TaskStatus.completed,
        summary=summary,
        risk_level=_SEVERITY_TO_RISK.get(response.status, RiskLevel.low),
        requires_approval=False,
        message=message,
        findings=findings,
        recommended_action_ids=recommended,
    )


async def _route_pending_approvals() -> ChatIngestResponse:
    approvals = list_approvals(limit=5, status="pending")
    findings: list[str] = []
    for approval in approvals[:5]:
        findings.append(
            mask_secrets(
                truncate(
                    f"{approval.approval_id[:8]} para {approval.target}: "
                    f"expira em {approval.expires_at}. "
                    f"Motivo: {mask_secrets(approval.reason or 'sem motivo informado')}",
                    160,
                )
            )
        )
    summary = (
        "Nenhuma aprovação pendente encontrada."
        if not approvals
        else f"{len(approvals)} aprovação(ões) pendente(s)."
    )
    message = _compose_message(summary, findings, [])
    return ChatIngestResponse(
        task_id="aiops-chat",
        status=TaskStatus.completed,
        summary=summary,
        risk_level=RiskLevel.medium if approvals else RiskLevel.low,
        requires_approval=False,
        message=message,
        findings=findings or ["Não há approvals pendentes no momento."],
        recommended_action_ids=[],
    )


async def _route_recent_runs() -> ChatIngestResponse:
    response, warnings = list_recent_runs(limit=5)
    findings: list[str] = []
    status_counts = {"ok": 0, "partial": 0, "failed": 0, "blocked": 0}
    for run in response.runs[:5]:
        status_counts[run.status] = status_counts.get(run.status, 0) + 1
        findings.append(
            f"{run.run_id[:8]} em {run.target}: {_run_status_pt(run.status)} "
            f"({run.result_count} resultado(s), {run.blocked_count} bloqueio(s))."
        )
    if warnings:
        findings.append("Alguns registros inválidos foram ignorados.")
    total = response.count
    if total == 0:
        summary = "Nenhum run recente encontrado."
        risk = RiskLevel.low
    else:
        summary = (
            f"{total} execuções recentes: {status_counts['ok']} ok, "
            f"{status_counts['partial']} parciais, {status_counts['failed']} com falha, "
            f"{status_counts['blocked']} bloqueadas."
        )
        risk = RiskLevel.medium if status_counts["failed"] or status_counts["blocked"] else RiskLevel.low
    recommended = ["journalctl_aiops_recent"] if status_counts["failed"] or status_counts["blocked"] else []
    message = _compose_message(summary, findings, recommended)
    return ChatIngestResponse(
        task_id="aiops-chat",
        status=TaskStatus.completed,
        summary=summary,
        risk_level=risk,
        requires_approval=False,
        message=message,
        findings=findings or ["Nenhum run recente disponível."],
        recommended_action_ids=recommended,
    )


async def _route_last_failed_run() -> ChatIngestResponse:
    recent, _warnings = list_recent_runs(limit=1)
    if not recent.runs:
        return ChatIngestResponse(
            task_id="aiops-chat",
            status=TaskStatus.completed,
            summary="Nenhum run encontrado.",
            risk_level=RiskLevel.low,
            requires_approval=False,
            message="Ainda não encontrei nenhum run para resumir.",
            findings=["O histórico de runs ainda está vazio."],
            recommended_action_ids=[],
        )

    last_run = recent.runs[0]
    if last_run.status != "failed":
        summary = f"O último run não falhou; status {_run_status_pt(last_run.status)}."
        return ChatIngestResponse(
            task_id="aiops-chat",
            status=TaskStatus.completed,
            summary=summary,
            risk_level=RiskLevel.low,
            requires_approval=False,
            message=summary,
            findings=[f"Último run em {last_run.target}: {_run_status_pt(last_run.status)}."],
            recommended_action_ids=[],
        )

    detail, detail_warnings = get_run(last_run.run_id)
    findings: list[str] = []
    if detail is not None:
        failed_results = [result for result in detail.results if result.status == "failed"]
        if failed_results:
            first_failed = failed_results[0]
            findings.append(
                f"Falhou em {first_failed.action_id} com exit_code {first_failed.exit_code}."
            )
            if first_failed.output_preview:
                findings.append(
                    f"Saída resumida: {truncate(mask_secrets(first_failed.output_preview), 180)}"
                )
        elif detail.blocked_steps:
            blocked = detail.blocked_steps[0]
            findings.append(f"Run bloqueado em {blocked.action_id}: {blocked.reason}.")
        else:
            findings.append("Run falhou sem detalhe adicional disponível.")
        if detail_warnings:
            findings.extend("Aviso: " + truncate(mask_secrets(warning), 120) for warning in detail_warnings[:2])
    else:
        findings.append("Não consegui abrir o detalhe do último run com falha.")

    summary = f"Último run falhou: {last_run.run_id[:8]}."
    message = _compose_message(summary, findings, ["journalctl_aiops_recent"])
    return ChatIngestResponse(
        task_id="aiops-chat",
        status=TaskStatus.completed,
        summary=summary,
        risk_level=RiskLevel.critical,
        requires_approval=False,
        message=message,
        findings=findings,
        recommended_action_ids=["journalctl_aiops_recent"],
    )


def _compose_message(summary: str, findings: list[str], recommended: list[str]) -> str:
    parts = [summary]
    if findings:
        parts.append("Achados: " + "; ".join(findings[:3]))
    if recommended:
        parts.append("Próximo passo: " + ", ".join(recommended[:3]))
    return " ".join(parts)


def _collect_action_ids(findings: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for finding in findings:
        for action_id in getattr(finding, "recommended_action_ids", []) or []:
            if action_id and action_id not in seen:
                seen.add(action_id)
                result.append(action_id)
    return result


def _render_findings(findings: Iterable[Any]) -> list[str]:
    rendered: list[str] = []
    for finding in findings:
        label = _CHECK_LABELS_PT.get(getattr(finding, "check", None) or "", _slug_to_label(getattr(finding, "check", None) or getattr(finding, "title", "sinal")))
        status = str(getattr(finding, "status", "unknown")).lower()
        rendered.append(f"{label}: {_STATUS_LABELS.get(status, 'indefinido')}.")
    return rendered


def _run_status_pt(status: str) -> str:
    status = status.lower().strip()
    if status == "ok":
        return "ok"
    if status == "partial":
        return "parcial"
    if status == "failed":
        return "com falha"
    if status == "blocked":
        return "bloqueado"
    return status or "indefinido"


def _slug_to_label(value: str) -> str:
    value = value.replace("_", " ").replace("-", " ").strip()
    if not value:
        return "Sinal"
    return " ".join(part.capitalize() for part in value.split())


def _normalize(message: str) -> str:
    normalized = unicodedata.normalize("NFKD", message)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = normalized.replace("blue/green", "blue green")
    normalized = normalized.replace("blue-green", "blue green")
    normalized = normalized.replace("agent-router", "agent router")
    normalized = re.sub(r"[^a-z0-9\s+/]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _looks_like_router_diagnose(text: str) -> bool:
    return _has_any(text, ("diagnostique", "diagnosticar", "diagnostico", "diagnostic")) and _has_any(
        text, ("agent router", "router", "orchestrator")
    )


def _looks_like_aiops_diagnose(text: str) -> bool:
    return _has_any(text, ("diagnostique", "diagnosticar", "diagnostico", "diagnostic")) and _has_any(
        text, ("aiops", "orchestrator", "operacional")
    )


def _looks_like_router_status(text: str) -> bool:
    return _has_any(text, ("agent router", "router", "orchestrator")) and _has_any(
        text, ("saudavel", "saudavel?", "saudavel ", "saudavel?", "saudavel")
    )


def _looks_like_bluegreen_status(text: str) -> bool:
    return _has_any(text, ("blue green", "bluegreen")) and _has_any(
        text, ("consistente", "consistencia", "coerente", "ok", "saudavel")
    )


def _looks_like_pending_approvals(text: str) -> bool:
    return _has_any(text, ("approval", "approvals", "aprov", "aprovacoes", "aprovacoes")) and _has_any(
        text, ("pendente", "pendentes", "aguardando", "abertas")
    )


def _looks_like_recent_runs(text: str) -> bool:
    return _has_any(text, ("runs", "run", "execucao", "execucoes")) and _has_any(
        text, ("resuma", "resumo", "ultimos", "recentes", "listar")
    )


def _looks_like_last_run_failure(text: str) -> bool:
    return (
        _has_any(text, ("ultimo run falhou", "último run falhou", "last run failed", "falhou por que", "falhou por quê"))
        or (_has_any(text, ("ultimo run", "último run")) and _has_any(text, ("falhou", "erro", "porque", "por que")))
    )


def _looks_like_ollama_slow(text: str) -> bool:
    return _has_any(text, ("ollama",)) and _has_any(text, ("lento", "lent", "demor", "latencia", "slow", "devagar"))


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)
