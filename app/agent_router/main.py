"""FastAPI router for AIOps Diagnostic Engine v1."""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_token
from app.agent_router.metrics import record_aiops_diagnose
from app.agent_router.schemas import AIOpsDiagnoseRequest, AIOpsDiagnoseResponse
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.signals import collect_aiops_diagnostic_signals
from app.models.database import get_db

router = APIRouter(dependencies=[Depends(require_api_token)])


@router.post("/v1/aiops/diagnose", response_model=AIOpsDiagnoseResponse)
async def diagnose(
    request: AIOpsDiagnoseRequest,
    db: AsyncSession = Depends(get_db),
) -> AIOpsDiagnoseResponse:
    """Diagnostic-only endpoint for AIOps state inspection."""
    started_at = perf_counter()
    signals = await collect_aiops_diagnostic_signals(request, db)
    response = diagnose_aiops(request, signals)
    record_aiops_diagnose(response, perf_counter() - started_at)
    return response
