"""Action Planner — maps explicit action_ids to a structured, safe plan.

Design constraints (v1):
  - Deterministic: no LLM, no free-text command generation.
  - Fail-closed: unknown or policy-rejected action_ids go to blocked_steps.
  - Commands are NEVER included in the plan output.
  - Only actions present in the validated ActionCatalog are accepted.
  - Policy gate: v1 allows only mode=readonly and risk=low.
  - dry_run is always True in the output.
"""

from __future__ import annotations

import uuid

from app.agent_router.schemas import (
    ActionPlanBlockedStep,
    ActionPlanRequest,
    ActionPlanResponse,
    ActionPlanStep,
)
from app.services.action_catalog import ActionCatalog

# v1 policy: only these values are permitted
_V1_ALLOWED_MODES = frozenset({"readonly"})
_V1_ALLOWED_RISKS = frozenset({"low"})
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def plan_actions(
    request: ActionPlanRequest,
    catalog: ActionCatalog,
) -> ActionPlanResponse:
    """Build a read-only, dry-run action plan from *request.action_ids*.

    Every action_id that is unknown, not in the catalog, or rejected by the
    v1 policy gate is placed in ``blocked_steps``. The plan is fail-closed:
    if the catalog is empty or all steps are blocked, ``status`` reflects that.
    """
    plan_id = str(uuid.uuid4())
    steps: list[ActionPlanStep] = []
    blocked_steps: list[ActionPlanBlockedStep] = []
    warnings: list[str] = []

    if not request.action_ids:
        return ActionPlanResponse(
            plan_id=plan_id,
            target=request.target,
            status="empty",
            risk="low",
            requires_approval=False,
            steps=[],
            blocked_steps=[],
            warnings=["No action_ids were requested."],
            dry_run=True,
        )

    seen: set[str] = set()
    for raw_id in request.action_ids:
        action_id = str(raw_id).strip()

        # Duplicate within the same request
        if action_id in seen:
            warnings.append(f"Duplicate action_id '{action_id}' ignored.")
            continue
        seen.add(action_id)

        entry = catalog.get(action_id)

        # Unknown action_id — fail-closed
        if entry is None:
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason=f"action_id '{action_id}' is not in the allowlisted catalog",
                )
            )
            continue

        # Policy gate: mode
        if entry.mode not in _V1_ALLOWED_MODES:
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason=(
                        f"mode '{entry.mode}' is not permitted in v1 "
                        f"(allowed: {sorted(_V1_ALLOWED_MODES)})"
                    ),
                )
            )
            continue

        # Policy gate: risk
        if entry.risk not in _V1_ALLOWED_RISKS:
            blocked_steps.append(
                ActionPlanBlockedStep(
                    action_id=action_id,
                    reason=(
                        f"risk '{entry.risk}' is not permitted in v1 "
                        f"(allowed: {sorted(_V1_ALLOWED_RISKS)})"
                    ),
                )
            )
            continue

        steps.append(
            ActionPlanStep(
                action_id=entry.action_id,
                title=entry.description,
                risk=entry.risk,
                mode=entry.mode,
                requires_approval=entry.requires_approval,
                reason="Selected from validated read-only action catalog",
                evidence_source=request.context or None,
                finding_id=None,
            )
        )

    # Overall plan attributes
    if blocked_steps:
        warnings.append(
            f"{len(blocked_steps)} action(s) were blocked and excluded from the plan."
        )

    if not steps and not blocked_steps:
        status = "empty"
        overall_risk = "low"
        requires_approval = False
    elif not steps:
        status = "blocked"
        overall_risk = "low"
        requires_approval = False
    else:
        status = "ready"
        max_risk_index = max(_RISK_ORDER.get(s.risk, 0) for s in steps)
        overall_risk = next(k for k, v in _RISK_ORDER.items() if v == max_risk_index)
        requires_approval = any(s.requires_approval for s in steps)

    return ActionPlanResponse(
        plan_id=plan_id,
        target=request.target,
        status=status,
        risk=overall_risk,
        requires_approval=requires_approval,
        steps=steps,
        blocked_steps=blocked_steps,
        warnings=warnings,
        dry_run=True,
    )
