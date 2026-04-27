"""Structured audit log for safe AIOps planning events.

The audit log records only allowlisted metadata about plans and dry-runs.
It never stores commands, secrets, headers, or raw request payloads.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Literal

from app.agent_router.schemas import AuditEvent, AuditRecentResponse, ActionPlanResponse
from app.core.config import BASE_DIR, get_settings

_AUDIT_LOCK = threading.Lock()
_MAX_RECENT_LIMIT = 100


class AuditLogError(RuntimeError):
    """Raised when a required audit event cannot be persisted."""


AuditEventType = Literal[
    "action_plan_created",
    "action_dry_run_created",
    "diagnose_action_plan_attached",
]


def resolve_audit_log_path() -> Path:
    settings = get_settings()
    path = Path(settings.audit_log_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def build_audit_event(
    *,
    event_type: AuditEventType,
    target: str,
    source_endpoint: str,
    plan: ActionPlanResponse,
    actor: str = "authenticated_user",
    correlation_id: str | None = None,
    dry_run_id: str | None = None,
    run_id: str | None = None,
) -> AuditEvent:
    action_ids = [step.action_id for step in plan.steps]
    blocked_action_ids = [step.action_id for step in plan.blocked_steps]
    event_id = f"audit_{uuid4().hex}"
    timestamp = datetime.now(timezone.utc).isoformat()
    return AuditEvent(
        event_id=event_id,
        timestamp=timestamp,
        event_type=event_type,
        actor=actor,
        target=target,
        source_endpoint=source_endpoint,
        correlation_id=correlation_id,
        plan_id=plan.plan_id,
        dry_run_id=dry_run_id,
        run_id=run_id,
        risk=plan.risk,
        requires_approval=plan.requires_approval,
        status=plan.status,
        action_ids=action_ids,
        blocked_action_ids=blocked_action_ids,
        warnings_count=len(plan.warnings),
        blocked_steps_count=len(plan.blocked_steps),
    )


def write_audit_event(event: AuditEvent, *, required: bool | None = None) -> bool:
    path = resolve_audit_log_path()
    if required is None:
        required = get_settings().audit_log_required
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        with _AUDIT_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
        return True
    except Exception as exc:  # pragma: no cover - exercised through endpoint tests
        if required:
            raise AuditLogError(f"Failed to write audit log at {path}") from exc
        return False


def read_recent_audit_events(limit: int = 20) -> AuditRecentResponse:
    path = resolve_audit_log_path()
    if limit < 1:
        limit = 1
    if limit > _MAX_RECENT_LIMIT:
        limit = _MAX_RECENT_LIMIT
    if not path.exists():
        return AuditRecentResponse(events=[])

    events: list[AuditEvent] = []
    with _AUDIT_LOCK:
        try:
            with path.open("r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            return AuditRecentResponse(events=[])

    for raw_line in lines[-limit:]:
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = AuditEvent.model_validate_json(line)
        except Exception:
            continue
        events.append(event)

    return AuditRecentResponse(events=events)
