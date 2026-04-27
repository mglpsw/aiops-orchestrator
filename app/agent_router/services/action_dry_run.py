"""Safe dry-run simulation for allowlisted action plans.

This layer reuses the validated Action Catalog and the deterministic Action
Planner. It never executes commands, shells, SSH, Docker, or external
processes.
"""

from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid5

from app.agent_router.schemas import (
    ActionDryRunRequest,
    ActionDryRunResponse,
    ActionDryRunStep,
    ActionPlanRequest,
    ActionPlanResponse,
)
from app.services.action_catalog import ActionCatalog
from app.services.action_planner import plan_actions

_DRY_RUN_NAMESPACE = UUID("4b5f5f76-96c7-4a3d-8b2d-5b4ddf8fd0c2")


def simulate_action_dry_run(
    request: ActionDryRunRequest,
    catalog: ActionCatalog,
) -> ActionDryRunResponse:
    """Simulate an action plan without executing anything."""
    plan = plan_actions(
        ActionPlanRequest(
            target=request.target,
            action_ids=request.action_ids,
            context=request.reason,
            dry_run=True,
        ),
        catalog,
    )
    return _build_response(request, plan)


def _build_response(
    request: ActionDryRunRequest,
    plan: ActionPlanResponse,
) -> ActionDryRunResponse:
    dry_run_id = _build_dry_run_id(request)
    would_run = [
        ActionDryRunStep(
            action_id=step.action_id,
            title=step.title,
            mode=step.mode,
            risk=step.risk,
            requires_approval=step.requires_approval,
            execution="not_executed",
            reason="Dry-run simulation only",
        )
        for step in plan.steps
    ]

    if plan.status == "ready" and not plan.blocked_steps:
        status = "ok"
    elif plan.steps and plan.blocked_steps:
        status = "partial"
    else:
        status = "blocked"

    warnings = [
        "Dry-run simulation only; no commands were executed.",
        *plan.warnings,
    ]

    return ActionDryRunResponse(
        dry_run_id=dry_run_id,
        target=request.target,
        status=status,
        risk=plan.risk,
        requires_approval=plan.requires_approval,
        plan=plan,
        would_run=would_run,
        blocked_steps=plan.blocked_steps,
        warnings=warnings,
    )


def _build_dry_run_id(request: ActionDryRunRequest) -> str:
    material = "|".join(
        [
            request.target.strip(),
            ",".join(request.action_ids),
            request.reason.strip(),
            str(request.dry_run).lower(),
        ]
    )
    digest = sha256(material.encode("utf-8")).hexdigest()
    return f"dryrun_{uuid5(_DRY_RUN_NAMESPACE, digest).hex[:16]}"
