"""Structured audit log for safe AIOps planning events.

The audit log records only allowlisted metadata about plans and dry-runs.
It never stores commands, secrets, headers, or raw request payloads.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.agent_router.schemas import ApprovalResponse, AuditEvent, AuditRecentResponse, ActionPlanResponse
from app.core.config import BASE_DIR, get_settings

_AUDIT_LOCK = threading.Lock()
_MAX_RECENT_LIMIT = 100


class AuditLogError(RuntimeError):
    """Raised when a required audit event cannot be persisted."""


AuditEventType = Literal[
    "action_plan_created",
    "action_dry_run_created",
    "diagnose_action_plan_attached",
    "approval_requested",
    "approval_approved",
    "approval_rejected",
    "approval_expired",
]


def resolve_audit_log_path() -> Path:
    settings = get_settings()
    path = Path(settings.audit_log_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def should_rotate_audit_log(path: Path, incoming_bytes: int, max_bytes: int) -> bool:
    if max_bytes <= 0:
        return False
    if incoming_bytes <= 0:
        return False
    if not path.exists():
        return False
    try:
        current_size = path.stat().st_size
    except FileNotFoundError:
        return False
    return current_size + incoming_bytes > max_bytes


def rotate_audit_log(path: Path, backup_count: int) -> None:
    backup_count = max(0, backup_count)
    if backup_count == 0:
        if path.exists():
            path.unlink()
        return

    oldest = path.with_name(f"{path.name}.{backup_count}")
    if oldest.exists():
        oldest.unlink()

    for index in range(backup_count - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))

    if path.exists():
        path.replace(path.with_name(f"{path.name}.1"))


def enforce_audit_retention(path: Path, backup_count: int) -> None:
    backup_count = max(0, backup_count)
    if backup_count == 0:
        for candidate in path.parent.glob(f"{path.name}.*"):
            if candidate.is_file():
                candidate.unlink()
        return

    for candidate in path.parent.glob(f"{path.name}.*"):
        suffix = candidate.name.removeprefix(f"{path.name}.")
        if not suffix.isdigit():
            continue
        if int(suffix) > backup_count:
            candidate.unlink()


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


def build_approval_audit_event(
    *,
    event_type: Literal["approval_requested", "approval_approved", "approval_rejected", "approval_expired"],
    approval: ApprovalResponse,
    source_endpoint: str,
    actor: str = "authenticated_user",
) -> AuditEvent:
    event_id = f"audit_{uuid4().hex}"
    timestamp = datetime.now(timezone.utc).isoformat()
    return AuditEvent(
        event_id=event_id,
        timestamp=timestamp,
        event_type=event_type,
        actor=actor,
        target=approval.target,
        source_endpoint=source_endpoint,
        approval_id=approval.approval_id,
        correlation_id=approval.approval_id,
        plan_id=approval.plan_id,
        dry_run_id=approval.dry_run_id,
        risk=approval.risk,
        requires_approval=approval.requires_approval,
        status=approval.status,
        action_ids=[],
        blocked_action_ids=[],
        warnings_count=0,
        blocked_steps_count=0,
    )


def write_audit_event(event: AuditEvent, *, required: bool | None = None) -> bool:
    path = resolve_audit_log_path()
    if required is None:
        required = get_settings().audit_log_required
    try:
        settings = get_settings()
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        payload_bytes = payload.encode("utf-8") + b"\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            if settings.audit_log_rotation_enabled and settings.audit_log_max_bytes > 0:
                if settings.audit_log_backup_count == 0:
                    enforce_audit_retention(path, 0)
                if should_rotate_audit_log(path, len(payload_bytes), settings.audit_log_max_bytes):
                    rotate_audit_log(path, settings.audit_log_backup_count)
                    enforce_audit_retention(path, settings.audit_log_backup_count)

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
