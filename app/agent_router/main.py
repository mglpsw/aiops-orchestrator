"""FastAPI router for AIOps Diagnostic Engine v1 and Action Planner v1."""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_token
from app.agent_router.metrics import record_aiops_diagnose
from app.agent_router.schemas import (
    AIOpsDiagnoseRequest,
    AIOpsDiagnoseResponse,
    ActionPlanRequest,
    ActionPlanResponse,
    CatalogActionEntry,
    CatalogResponse,
)
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.signals import collect_aiops_diagnostic_signals
from app.models.database import get_db
from app.services.action_catalog import ActionCatalog, CatalogLoadError, load_catalog
from app.services.action_planner import plan_actions

router = APIRouter(dependencies=[Depends(require_api_token)])

# Module-level catalog cache — loaded once, fail-closed.
# Tests may patch _get_catalog() to inject a fixture catalog.
_catalog_cache: ActionCatalog | None = None


def _get_catalog() -> ActionCatalog:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = load_catalog()
    return _catalog_cache


def _reset_catalog_cache() -> None:
    """Reset the module-level catalog cache. Intended for tests only."""
    global _catalog_cache
    _catalog_cache = None


# ---------------------------------------------------------------------------
# Diagnostic endpoint (existing)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Action Catalog endpoint
# ---------------------------------------------------------------------------


@router.get("/v1/aiops/actions/catalog", response_model=CatalogResponse)
async def get_catalog() -> CatalogResponse:
    """Return the full allowlisted read-only action catalog.

    Commands are intentionally omitted from the response.
    """
    try:
        catalog = _get_catalog()
    except CatalogLoadError as exc:
        raise HTTPException(status_code=503, detail=f"Action catalog unavailable: {exc}") from exc

    return CatalogResponse(
        version=catalog.version,
        count=catalog.count,
        actions=[
            CatalogActionEntry(
                action_id=entry.action_id,
                description=entry.description,
                mode=entry.mode,
                risk=entry.risk,
                timeout_seconds=entry.timeout_seconds,
                requires_approval=entry.requires_approval,
                tags=entry.tags,
            )
            for entry in catalog.all_entries()
        ],
    )


# ---------------------------------------------------------------------------
# Action Plan endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/aiops/actions/plan", response_model=ActionPlanResponse)
async def create_plan(request: ActionPlanRequest) -> ActionPlanResponse:
    """Build a deterministic, read-only action plan from the allowlisted catalog.

    - Only action_ids present in config/actions.yaml are accepted.
    - Unknown or policy-rejected action_ids go to blocked_steps.
    - No commands, no shell, no SSH, no execution in this endpoint.
    - dry_run is always True in the response.
    """
    try:
        catalog = _get_catalog()
    except CatalogLoadError as exc:
        raise HTTPException(status_code=503, detail=f"Action catalog unavailable: {exc}") from exc

    return plan_actions(request, catalog)
