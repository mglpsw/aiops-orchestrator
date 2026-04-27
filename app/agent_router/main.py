"""FastAPI router for AIOps Diagnostic Engine v1 and Action Planner v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_token
from app.agent_router.metrics import record_aiops_diagnose
from app.agent_router.schemas import (
    AIOpsDiagnoseRequest,
    AIOpsDiagnoseResponse,
    AuditRecentResponse,
    ActionDryRunRequest,
    ActionDryRunResponse,
    ActionPlanRequest,
    ActionPlanResponse,
    CatalogActionEntry,
    CatalogResponse,
)
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.services.audit_log import AuditLogError, build_audit_event, read_recent_audit_events, write_audit_event
from app.agent_router.services.action_dry_run import simulate_action_dry_run
from app.agent_router.services.action_mapper import map_findings_to_action_ids
from app.agent_router.signals import collect_aiops_diagnostic_signals
from app.models.database import get_db
from app.services.action_catalog import ActionCatalog, CatalogLoadError, load_catalog
from app.services.action_planner import plan_actions

router = APIRouter(dependencies=[Depends(require_api_token)])

# ---------------------------------------------------------------------------
# Catalog state — set once at startup, readable by /ready
# ---------------------------------------------------------------------------


@dataclass
class _CatalogState:
    status: str = "unloaded"        # "ok" | "error" | "unloaded"
    error: str | None = None        # redacted message, never contains commands
    actions_count: int = 0
    loaded_at: datetime | None = field(default=None)


# Module-level catalog cache and state.
# init_catalog_on_startup() sets both; _get_catalog() uses the cache.
# _reset_catalog_cache() resets both — intended for tests only.
_catalog_cache: ActionCatalog | None = None
_catalog_state: _CatalogState = _CatalogState()


def init_catalog_on_startup() -> None:
    """Load and cache the action catalog at application startup.

    Records status in _catalog_state for use by /ready. Always attempts a
    fresh load regardless of current cache state (startup semantics). On
    success the cache is populated; on failure the cache is cleared and state
    is marked as error so /ready degrades gracefully.
    """
    global _catalog_cache, _catalog_state
    try:
        catalog = load_catalog()
        _catalog_cache = catalog
        _catalog_state = _CatalogState(
            status="ok",
            actions_count=catalog.count,
            loaded_at=datetime.now(timezone.utc),
        )
    except CatalogLoadError as exc:
        _catalog_cache = None
        _catalog_state = _CatalogState(status="error", error=str(exc))


def get_catalog_readiness() -> dict[str, object]:
    """Return a safe, command-free snapshot of catalog state for /ready."""
    return {
        "status": _catalog_state.status,
        "actions_count": _catalog_state.actions_count,
    }


def _get_catalog() -> ActionCatalog:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = load_catalog()
    return _catalog_cache


def _reset_catalog_cache() -> None:
    """Reset catalog cache and state. Intended for tests only."""
    global _catalog_cache, _catalog_state
    _catalog_cache = None
    _catalog_state = _CatalogState()


# ---------------------------------------------------------------------------
# Diagnostic endpoint (existing)
# ---------------------------------------------------------------------------


@router.post("/v1/aiops/diagnose", response_model=AIOpsDiagnoseResponse)
async def diagnose(
    request: AIOpsDiagnoseRequest,
    db: AsyncSession = Depends(get_db),
) -> AIOpsDiagnoseResponse:
    """Diagnostic-only endpoint for AIOps state inspection.

    When problem findings are present, attaches an action_plan built from the
    allowlisted catalog. The plan is always dry_run=True and never contains
    commands. If the catalog is unavailable, action_plan is None (fail-soft).
    """
    started_at = perf_counter()
    signals = await collect_aiops_diagnostic_signals(request, db)
    response = diagnose_aiops(request, signals, catalog_readiness=get_catalog_readiness())
    record_aiops_diagnose(response, perf_counter() - started_at)

    # --- Attach action plan (fail-soft: catalog failure does not break diagnose) ---
    action_plan: ActionPlanResponse | None = None
    if response.findings:
        suggested_ids = map_findings_to_action_ids(response.findings, request.checks)
        if suggested_ids:
            try:
                catalog = _get_catalog()
                action_plan = plan_actions(
                    ActionPlanRequest(
                        target=request.target,
                        action_ids=suggested_ids,
                        context=f"diagnose status={response.status} severity={response.severity}",
                        dry_run=True,
                    ),
                    catalog,
                )
            except CatalogLoadError:
                # Catalog unavailable: return diagnose without a plan.
                action_plan = None

    response = response.model_copy(update={"action_plan": action_plan})

    if action_plan is not None:
        audit_event = build_audit_event(
            event_type="diagnose_action_plan_attached",
            target=request.target,
            source_endpoint="/v1/aiops/diagnose",
            plan=action_plan,
            correlation_id=action_plan.plan_id,
        )
        write_audit_event(audit_event, required=False)

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

    response = plan_actions(request, catalog)
    audit_event = build_audit_event(
        event_type="action_plan_created",
        target=request.target,
        source_endpoint="/v1/aiops/actions/plan",
        plan=response,
        correlation_id=response.plan_id,
    )
    try:
        write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return response


# ---------------------------------------------------------------------------
# Action Dry-Run endpoint
# ---------------------------------------------------------------------------


@router.post("/v1/aiops/actions/dry-run", response_model=ActionDryRunResponse)
async def dry_run_actions(request: ActionDryRunRequest) -> ActionDryRunResponse:
    """Simulate a catalog-backed plan without executing anything.

    The request is dry-run only, authenticated by the router dependency, and
    rejects any extra fields such as `command`.
    """
    try:
        catalog = _get_catalog()
    except CatalogLoadError as exc:
        raise HTTPException(status_code=503, detail=f"Action catalog unavailable: {exc}") from exc

    response = simulate_action_dry_run(request, catalog)
    audit_event = build_audit_event(
        event_type="action_dry_run_created",
        target=request.target,
        source_endpoint="/v1/aiops/actions/dry-run",
        plan=response.plan,
        correlation_id=response.dry_run_id,
        dry_run_id=response.dry_run_id,
    )
    try:
        write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return response


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------


@router.get("/v1/aiops/audit/recent", response_model=AuditRecentResponse)
async def recent_audit_events(limit: int = Query(default=20, ge=1)) -> AuditRecentResponse:
    return read_recent_audit_events(limit=limit)
