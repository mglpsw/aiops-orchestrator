"""Rotas da API do AIOps Orchestrator."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_token
from app.models.database import get_db
from app.models.schemas import (
    ChatIngestRequest, ChatIngestResponse,
    TaskResponse, TaskListResponse,
    ApprovalRequest, ApprovalResponse, ApprovalDecision,
    ProvidersStatusResponse,
)
from app.services.orchestrator import Orchestrator
from app.services.task_service import TaskService
from app.services.provider_registry import get_registry
from app.utils.logging import get_logger

logger = get_logger("api.routes")

router = APIRouter(dependencies=[Depends(require_api_token)])


# --- Chat / Ingest ---

@router.post("/v1/chat", response_model=ChatIngestResponse)
@router.post("/v1/chat/ingest", response_model=ChatIngestResponse)
async def chat_ingest(
    request: ChatIngestRequest,
    db: AsyncSession = Depends(get_db),
):
    """Recebe uma mensagem de chat, classifica, planeja e opcionalmente executa."""
    orchestrator = Orchestrator(db)
    return await orchestrator.ingest_chat(request)


# --- Tarefas ---

@router.get("/v1/tasks", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = Query(None, description="Filtrar por status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Lista tarefas com filtro opcional por status."""
    service = TaskService(db)
    tasks, total = await service.list_tasks(status=status, limit=limit, offset=offset)
    return TaskListResponse(
        tasks=[service.task_to_response(t) for t in tasks],
        total=total,
    )


@router.get("/v1/tasks/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retorna uma tarefa específica pelo ID."""
    service = TaskService(db)
    task = await service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return service.task_to_response(task)


# --- Aprovações ---

@router.get("/v1/approvals")
async def list_pending_approvals(
    db: AsyncSession = Depends(get_db),
):
    """Lista todas as tarefas aguardando aprovação."""
    service = TaskService(db)
    tasks = await service.list_pending_approvals()
    return {
        "pending": [service.task_to_response(t) for t in tasks],
        "count": len(tasks),
    }


@router.post("/v1/approvals/{task_id}", response_model=ApprovalResponse)
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Aprova ou rejeita uma tarefa."""
    service = TaskService(db)
    orchestrator = Orchestrator(db)

    try:
        task = await service.approve_task(
            task_id=task_id,
            approved_by=request.approved_by,
            decision=request.decision,
            reason=request.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Se aprovada, dispara a execução
    if request.decision == ApprovalDecision.approved:
        try:
            await orchestrator.execute_approved_task(task_id)
        except Exception as e:
            logger.exception("Execution after approval failed for task %s", task_id)
            await service.set_error(task_id, str(e))

    return ApprovalResponse(
        task_id=task_id,
        decision=request.decision,
        approved_by=request.approved_by,
        timestamp=task.approved_at,
    )


# --- Providers ---

@router.get("/v1/providers/status", response_model=ProvidersStatusResponse)
async def providers_status():
    """Check health and status of all providers."""
    registry = get_registry()
    statuses = await registry.check_all_health()
    return ProvidersStatusResponse(providers=statuses)
