"""FastAPI router for AIOps Diagnostic Engine v1 and Action Planner v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_token
from app.agent_router.metrics import record_aiops_diagnose
from app.agent_router.schemas import (
    AIOpsDiagnoseRequest,
    AIOpsDiagnoseResponse,
    ApprovalCreateRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
    ApprovalResponse,
    ApprovalStatus,
    ActionPlanBlockedStep,
    ActionRunRequest,
    ActionRunResponse,
    ActionRunResult,
    AuditRecentResponse,
    ActionDryRunRequest,
    ActionDryRunResponse,
    ActionPlanRequest,
    ActionPlanResponse,
    RunDetailResponse,
    RunRecentResponse,
    CatalogActionEntry,
    CatalogResponse,
)
from app.agent_router.services.aiops_diagnostic import diagnose_aiops
from app.agent_router.services.audit_log import (
    AuditLogError,
    build_approval_audit_event,
    build_audit_event,
    build_run_audit_event,
    read_recent_audit_events,
    write_audit_event,
)
from app.agent_router.services.approval_store import (
    ApprovalExpiredError,
    ApprovalStoreError,
    create_approval,
    expire_pending_approvals,
    decide_approval,
    list_approvals,
    resolve_approval,
)
from app.agent_router.services.action_dry_run import simulate_action_dry_run
from app.agent_router.services.action_runner import allowed_action_ids, execute_action
from app.agent_router.services.run_store import get_run, list_recent_runs, write_run_record
from app.core.config import get_settings
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
        wrote = write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    if not wrote and not get_settings().audit_log_required:
        response = response.model_copy(
            update={
                "warnings": [
                    *response.warnings,
                    "Audit log unavailable; event not persisted.",
                ]
            }
        )
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
        wrote = write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    if not wrote and not get_settings().audit_log_required:
        response = response.model_copy(
            update={
                "warnings": [
                    *response.warnings,
                    "Audit log unavailable; event not persisted.",
                ]
            }
        )
    return response


# ---------------------------------------------------------------------------
# Approval endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/aiops/actions/approvals", response_model=ApprovalResponse)
async def request_approval(request: ApprovalCreateRequest) -> ApprovalResponse:
    try:
        approval = create_approval(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=500, detail="Approval store unavailable") from exc

    audit_event = build_approval_audit_event(
        event_type="approval_requested",
        approval=approval,
        source_endpoint="/v1/aiops/actions/approvals",
    )
    try:
        write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return approval


@router.get("/v1/aiops/actions/approvals", response_model=ApprovalListResponse)
async def list_approvals_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    status: ApprovalStatus | None = Query(default=None),
) -> ApprovalListResponse:
    expired = expire_pending_approvals()
    for approval in expired:
        audit_event = build_approval_audit_event(
            event_type="approval_expired",
            approval=approval,
            source_endpoint="/v1/aiops/actions/approvals",
        )
        try:
            write_audit_event(audit_event)
        except AuditLogError as exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from exc

    approvals = list_approvals(limit=limit, status=status)
    return ApprovalListResponse(approvals=approvals)


@router.get("/v1/aiops/actions/approvals/{approval_id}", response_model=ApprovalResponse)
async def get_approval_by_id(approval_id: str) -> ApprovalResponse:
    approval, expired = resolve_approval(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if expired:
        audit_event = build_approval_audit_event(
            event_type="approval_expired",
            approval=approval,
            source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}",
        )
        try:
            write_audit_event(audit_event)
        except AuditLogError as exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return approval


@router.post("/v1/aiops/actions/approvals/{approval_id}/approve", response_model=ApprovalDecisionResponse)
async def approve_request(approval_id: str) -> ApprovalDecisionResponse:
    try:
        approval, expired = resolve_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if expired:
            audit_event = build_approval_audit_event(
                event_type="approval_expired",
                approval=approval,
                source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/approve",
            )
            try:
                write_audit_event(audit_event)
            except AuditLogError as audit_exc:
                raise HTTPException(status_code=500, detail="Audit log unavailable") from audit_exc
            raise HTTPException(status_code=409, detail="Approval expired")
        approval = decide_approval(approval_id, decision="approve")
    except ApprovalExpiredError as exc:
        approval = exc.approval
        audit_event = build_approval_audit_event(
            event_type="approval_expired",
            approval=approval,
            source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/approve",
        )
        try:
            write_audit_event(audit_event)
        except AuditLogError as audit_exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from audit_exc
        raise HTTPException(status_code=409, detail="Approval expired")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=500, detail="Approval store unavailable") from exc

    audit_event = build_approval_audit_event(
        event_type="approval_approved",
        approval=approval,
        source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/approve",
    )
    try:
        write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return approval


@router.post("/v1/aiops/actions/approvals/{approval_id}/reject", response_model=ApprovalDecisionResponse)
async def reject_request(approval_id: str) -> ApprovalDecisionResponse:
    try:
        approval, expired = resolve_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        if expired:
            audit_event = build_approval_audit_event(
                event_type="approval_expired",
                approval=approval,
                source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/reject",
            )
            try:
                write_audit_event(audit_event)
            except AuditLogError as audit_exc:
                raise HTTPException(status_code=500, detail="Audit log unavailable") from audit_exc
            raise HTTPException(status_code=409, detail="Approval expired")
        approval = decide_approval(approval_id, decision="reject")
    except ApprovalExpiredError as exc:
        approval = exc.approval
        audit_event = build_approval_audit_event(
            event_type="approval_expired",
            approval=approval,
            source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/reject",
        )
        try:
            write_audit_event(audit_event)
        except AuditLogError as audit_exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from audit_exc
        raise HTTPException(status_code=409, detail="Approval expired")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Approval not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ApprovalStoreError as exc:
        raise HTTPException(status_code=500, detail="Approval store unavailable") from exc

    audit_event = build_approval_audit_event(
        event_type="approval_rejected",
        approval=approval,
        source_endpoint=f"/v1/aiops/actions/approvals/{approval_id}/reject",
    )
    try:
        write_audit_event(audit_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    return approval


def _dedupe_action_ids(action_ids: list[str]) -> tuple[list[str], list[str]]:
    normalized: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw_id in action_ids:
        action_id = str(raw_id).strip()
        if action_id in seen:
            warnings.append(f"Duplicate action_id '{action_id}' ignored.")
            continue
        seen.add(action_id)
        normalized.append(action_id)
    return normalized, warnings


def _build_run_response(
    *,
    run_id: str,
    target: str,
    approval_id: str,
    status: str,
    started_at: str,
    finished_at: str,
    results: list[ActionRunResult],
    blocked_steps: list[ActionPlanBlockedStep],
    warnings: list[str],
) -> ActionRunResponse:
    return ActionRunResponse(
        run_id=run_id,
        target=target,
        approval_id=approval_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        blocked_steps=blocked_steps,
        warnings=warnings,
    )


@router.post("/v1/aiops/actions/run", response_model=ActionRunResponse)
async def run_actions(request: ActionRunRequest) -> ActionRunResponse:
    try:
        catalog = _get_catalog()
    except CatalogLoadError as exc:
        raise HTTPException(status_code=503, detail=f"Action catalog unavailable: {exc}") from exc

    approval, expired = resolve_approval(request.approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")

    run_id = f"run_{uuid4().hex}"
    started_at = datetime.now(timezone.utc).isoformat()
    finished_at = started_at
    requested_action_ids, request_warnings = _dedupe_action_ids(request.action_ids)
    blocking_reasons: list[str] = []
    audit_warnings: list[str] = []

    requested_event = build_run_audit_event(
        event_type="action_run_requested",
        approval=approval,
        run_id=run_id,
        target=request.target,
        source_endpoint="/v1/aiops/actions/run",
        status="requested",
        requested_action_ids=requested_action_ids,
        warnings_count=len(request_warnings),
    )
    try:
        wrote = write_audit_event(requested_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    if not wrote and not get_settings().audit_log_required:
        audit_warnings.append("Audit log unavailable; event not persisted.")

    if approval.target != request.target:
        blocking_reasons.append("Approval target does not match requested target.")

    if expired or approval.status == "expired":
        expired_event = build_approval_audit_event(
            event_type="approval_expired",
            approval=approval,
            source_endpoint="/v1/aiops/actions/run",
        )
        try:
            write_audit_event(expired_event)
        except AuditLogError as exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
        blocking_reasons.append("Approval is expired.")
    elif approval.status != "approved":
        blocking_reasons.append(f"Approval status is '{approval.status}' and must be 'approved'.")

    blocked_steps: list[ActionPlanBlockedStep] = []
    executable_ids: list[str] = []
    allowed_ids = allowed_action_ids()
    for action_id in requested_action_ids:
        entry = catalog.get(action_id)
        if entry is None:
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason=f"action_id '{action_id}' is not in the allowlisted catalog",
                )
            )
            continue
        if action_id not in allowed_ids:
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason="action_id is not executable in run v1",
                )
            )
            continue
        if entry.mode != "readonly" or entry.risk != "low":
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason="action_id is not allowed by the run v1 policy gate",
                )
            )
            continue
        executable_ids.append(action_id)

    if blocking_reasons or blocked_steps:
        blocked_response = _build_run_response(
            run_id=run_id,
            target=request.target,
            approval_id=request.approval_id,
            status="blocked",
            started_at=started_at,
            finished_at=finished_at,
            results=[],
            blocked_steps=blocked_steps,
            warnings=[*request_warnings, *blocking_reasons, *audit_warnings],
        )
        blocked_event = build_run_audit_event(
            event_type="action_run_blocked",
            approval=approval,
            run_id=run_id,
            target=request.target,
            source_endpoint="/v1/aiops/actions/run",
            status="blocked",
            requested_action_ids=requested_action_ids,
            blocked_action_ids=[step.action_id for step in blocked_steps],
            warnings_count=len(blocked_response.warnings),
            blocked_steps_count=len(blocked_steps),
        )
        try:
            wrote = write_audit_event(blocked_event)
        except AuditLogError as exc:
            raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
        if not wrote and not get_settings().audit_log_required:
            blocked_response = blocked_response.model_copy(
                update={
                    "warnings": [
                        *blocked_response.warnings,
                        "Audit log unavailable; event not persisted.",
                    ]
                }
            )
        persisted = write_run_record(blocked_response, requested_action_ids=requested_action_ids)
        if not persisted:
            blocked_response = blocked_response.model_copy(
                update={
                    "warnings": [
                        *blocked_response.warnings,
                        "Run metadata unavailable; event not persisted.",
                    ]
                }
            )
        return blocked_response

    started_event = build_run_audit_event(
        event_type="action_run_started",
        approval=approval,
        run_id=run_id,
        target=request.target,
        source_endpoint="/v1/aiops/actions/run",
        status="started",
        requested_action_ids=requested_action_ids,
        warnings_count=len(request_warnings),
    )
    try:
        wrote = write_audit_event(started_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    if not wrote and not get_settings().audit_log_required:
        audit_warnings.append("Audit log unavailable; event not persisted.")

    results: list[ActionRunResult] = []
    for action_id in executable_ids:
        execution = await execute_action(action_id)
        results.append(
            ActionRunResult(
                action_id=execution.action_id,
                status=execution.status,  # type: ignore[arg-type]
                exit_code=execution.exit_code,
                duration_ms=execution.duration_ms,
                output_preview=execution.output_preview,
                truncated=execution.truncated,
            )
        )

    if results and all(result.status == "ok" for result in results):
        final_status = "ok"
        final_event_type = "action_run_completed"
    elif any(result.status == "ok" for result in results):
        final_status = "partial"
        final_event_type = "action_run_completed"
    else:
        final_status = "failed"
        final_event_type = "action_run_failed"

    finished_at = datetime.now(timezone.utc).isoformat()
    response = _build_run_response(
        run_id=run_id,
        target=request.target,
        approval_id=request.approval_id,
        status=final_status,
        started_at=started_at,
        finished_at=finished_at,
        results=results,
        blocked_steps=[],
        warnings=[*request_warnings, *audit_warnings],
    )

    final_event = build_run_audit_event(
        event_type=final_event_type,
        approval=approval,
        run_id=run_id,
        target=request.target,
        source_endpoint="/v1/aiops/actions/run",
        status=final_status,
        requested_action_ids=requested_action_ids,
        warnings_count=len(response.warnings),
    )
    try:
        wrote = write_audit_event(final_event)
    except AuditLogError as exc:
        raise HTTPException(status_code=500, detail="Audit log unavailable") from exc
    if not wrote and not get_settings().audit_log_required:
        response = response.model_copy(
            update={
                "warnings": [
                    *response.warnings,
                    "Audit log unavailable; event not persisted.",
                ]
            }
        )

    persisted = write_run_record(response, requested_action_ids=requested_action_ids)
    if not persisted:
        response = response.model_copy(
            update={
                "warnings": [
                    *response.warnings,
                    "Run metadata unavailable; event not persisted.",
                ]
            }
        )

    return response


@router.get("/v1/aiops/runs/recent", response_model=RunRecentResponse)
async def recent_runs(
    limit: int = Query(default=20, ge=1, le=100),
    target: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> RunRecentResponse:
    response, warnings = list_recent_runs(limit=limit, target=target, status=status)
    if warnings:
        return response.model_copy(update={"warnings": warnings})
    return response


@router.get("/v1/aiops/runs/{run_id}", response_model=RunDetailResponse)
async def get_run_by_id(run_id: str) -> RunDetailResponse:
    response, warnings = get_run(run_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if warnings:
        response = response.model_copy(update={"warnings": warnings})
    return response


# ---------------------------------------------------------------------------
# Audit log endpoint
# ---------------------------------------------------------------------------


@router.get("/v1/aiops/audit/recent", response_model=AuditRecentResponse)
async def recent_audit_events(limit: int = Query(default=20, ge=1)) -> AuditRecentResponse:
    return read_recent_audit_events(limit=limit)
