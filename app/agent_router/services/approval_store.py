"""Persistent approval store for AIOps planning workflows.

This layer stores approval metadata only. It never executes actions, never
stores commands, and never persists secrets or raw authorization headers.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.agent_router.schemas import ApprovalCreateRequest, ApprovalResponse
from app.core.config import BASE_DIR, get_settings

_APPROVAL_LOCK = threading.RLock()
_MAX_TTL_SECONDS = 3600


class ApprovalStoreError(RuntimeError):
    """Raised when the approval store cannot be read or written."""


class ApprovalExpiredError(ValueError):
    """Raised when an approval is expired and cannot transition further."""

    def __init__(self, approval: ApprovalResponse) -> None:
        super().__init__("approval expired")
        self.approval = approval


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def resolve_approval_store_path() -> Path:
    settings = get_settings()
    path = Path(settings.approval_store_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _validate_ttl(ttl_seconds: int) -> int:
    settings = get_settings()
    max_ttl = max(1, min(settings.approval_ttl_max_seconds, _MAX_TTL_SECONDS))
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than 0")
    if ttl_seconds > max_ttl:
        raise ValueError(f"ttl_seconds must be <= {max_ttl}")
    return ttl_seconds


def _serialize(approval: ApprovalResponse) -> str:
    return json.dumps(approval.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _parse(line: str) -> ApprovalResponse | None:
    try:
        return ApprovalResponse.model_validate_json(line)
    except Exception:
        return None


def _load_latest(path: Path) -> dict[str, ApprovalResponse]:
    if not path.exists():
        return {}

    latest: dict[str, ApprovalResponse] = {}
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            approval = _parse(line)
            if approval is None:
                continue
            latest[approval.approval_id] = approval
    return latest


def _read_all_records(path: Path | None = None) -> list[ApprovalResponse]:
    path = path or resolve_approval_store_path()
    if not path.exists():
        return []

    records: list[ApprovalResponse] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            approval = _parse(line)
            if approval is None:
                continue
            records.append(approval)
    return records


def _persist_snapshot(approval: ApprovalResponse) -> None:
    path = resolve_approval_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize(approval)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")

    settings = get_settings()
    if settings.approval_store_max_records > 0 and len(_read_all_records(path)) > settings.approval_store_max_records:
        compact_approval_store(settings.approval_store_max_records)


def create_approval(request: ApprovalCreateRequest, *, actor: str = "authenticated_user") -> ApprovalResponse:
    ttl_seconds = _validate_ttl(request.ttl_seconds)
    created_at = utcnow()
    expires_at = created_at + timedelta(seconds=ttl_seconds)
    approval = ApprovalResponse(
        approval_id=f"approval_{uuid4().hex}",
        target=request.target,
        plan_id=request.plan_id,
        dry_run_id=request.dry_run_id,
        status="pending",
        risk="low",
        requires_approval=True,
        created_at=created_at.isoformat(),
        expires_at=expires_at.isoformat(),
        approved_at=None,
        rejected_at=None,
        actor=actor,
        approved_by=None,
        rejected_by=None,
        reason=request.reason,
    )
    with _APPROVAL_LOCK:
        try:
            _persist_snapshot(approval)
        except Exception as exc:
            raise ApprovalStoreError("Failed to persist approval request") from exc
    return approval


def get_approval(approval_id: str) -> ApprovalResponse | None:
    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        latest = _load_latest(path)
    approval = latest.get(approval_id)
    if approval is None:
        return None
    return approval


def resolve_approval(approval_id: str, now: datetime | None = None) -> tuple[ApprovalResponse | None, bool]:
    now = now or utcnow()
    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        latest = _load_latest(path)
        approval = latest.get(approval_id)
        if approval is None:
            return None, False
        if approval.status != "pending":
            return approval, False
        expires_at = datetime.fromisoformat(approval.expires_at)
        if now <= expires_at:
            return approval, False
        expired_approval = approval.model_copy(update={"status": "expired"})
        try:
            _persist_snapshot(expired_approval)
        except Exception as exc:
            raise ApprovalStoreError("Failed to persist expired approval state") from exc
        return expired_approval, True


def _apply_expiration(approval: ApprovalResponse) -> ApprovalResponse:
    if approval.status != "pending":
        return approval
    expires_at = datetime.fromisoformat(approval.expires_at)
    if utcnow() <= expires_at:
        return approval
    return approval.model_copy(update={"status": "expired"})


def _sort_key(approval: ApprovalResponse) -> datetime:
    try:
        return datetime.fromisoformat(approval.created_at)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def compact_approval_store(max_records: int) -> int:
    if max_records <= 0:
        return 0

    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        records = _read_all_records(path)
        if not records:
            return 0

        latest_by_id: dict[str, ApprovalResponse] = {}
        for approval in records:
            latest_by_id[approval.approval_id] = approval

        ordered = sorted(latest_by_id.values(), key=_sort_key, reverse=True)
        if len(ordered) > max_records:
            ordered = ordered[:max_records]

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for approval in ordered:
                handle.write(_serialize(approval))
                handle.write("\n")
        tmp_path.replace(path)
        return len(ordered)


def expire_pending_approvals(now: datetime | None = None) -> list[ApprovalResponse]:
    now = now or utcnow()
    path = resolve_approval_store_path()
    expired: list[ApprovalResponse] = []

    with _APPROVAL_LOCK:
        latest = _load_latest(path)
        for approval in latest.values():
            if approval.status != "pending":
                continue
            expires_at = datetime.fromisoformat(approval.expires_at)
            if now <= expires_at:
                continue
            expired_approval = approval.model_copy(update={"status": "expired"})
            try:
                _persist_snapshot(expired_approval)
            except Exception as exc:
                raise ApprovalStoreError("Failed to persist expired approval state") from exc
            expired.append(expired_approval)

    return expired


def decide_approval(approval_id: str, *, decision: str, actor: str = "authenticated_user") -> ApprovalResponse:
    if decision not in {"approve", "reject"}:
        raise ValueError("decision must be approve or reject")

    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        latest = _load_latest(path)
        current = latest.get(approval_id)
        if current is None:
            raise KeyError(approval_id)

        current = _apply_expiration(current)
        now = utcnow().isoformat()
        if current.status == "expired":
            expired = current.model_copy(update={"status": "expired"})
            try:
                _persist_snapshot(expired)
            except Exception as exc:
                raise ApprovalStoreError("Failed to persist expired approval state") from exc
            raise ApprovalExpiredError(expired)

        if current.status != "pending":
            raise ValueError(f"approval is already {current.status}")

        if decision == "approve":
            updated = current.model_copy(
                update={
                    "status": "approved",
                    "approved_at": now,
                    "approved_by": actor,
                }
            )
        else:
            updated = current.model_copy(
                update={
                    "status": "rejected",
                    "rejected_at": now,
                    "rejected_by": actor,
                }
            )

        try:
            _persist_snapshot(updated)
        except Exception as exc:
            raise ApprovalStoreError("Failed to persist approval decision") from exc
        return updated


def list_latest_approvals() -> list[ApprovalResponse]:
    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        latest = _load_latest(path)
    return list(latest.values())


def list_approvals(
    *,
    limit: int = 20,
    status: str | None = None,
    now: datetime | None = None,
) -> list[ApprovalResponse]:
    path = resolve_approval_store_path()
    with _APPROVAL_LOCK:
        latest = _load_latest(path)

    approvals = list(latest.values())
    if status is not None:
        approvals = [approval for approval in approvals if approval.status == status]

    approvals = sorted(approvals, key=_sort_key, reverse=True)
    if limit > 0:
        approvals = approvals[:limit]
    return approvals
