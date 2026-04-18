"""Task lifecycle management: create, plan, approve, execute, complete."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import TaskRecord, AuditRecord, ProviderCallRecord, ExecutionRecord
from app.models.schemas import (
    TaskStatus, RiskLevel, ApprovalDecision,
    TaskResponse, ExecutionPlan, ChatIngestResponse,
)
from app.utils.logging import get_logger
from app.utils.secrets import mask_secrets

logger = get_logger("services.task")


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_task(
        self, message: str, user_id: str = "webai-user", context: dict | None = None
    ) -> TaskRecord:
        task = TaskRecord(
            id=str(uuid.uuid4()),
            message=message,
            user_id=user_id,
            status=TaskStatus.pending.value,
            context_json=context or {},
        )
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        await self._audit("task_created", task.id, {"message": message[:200], "user_id": user_id})
        return task

    async def update_status(self, task_id: str, status: TaskStatus, **kwargs) -> TaskRecord:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        task.status = status.value
        task.updated_at = datetime.utcnow()
        for key, val in kwargs.items():
            if hasattr(task, key):
                setattr(task, key, val)
        await self.db.commit()
        await self.db.refresh(task)
        await self._audit("task_status_changed", task_id, {"new_status": status.value, **{k: str(v)[:200] for k, v in kwargs.items()}})
        return task

    async def set_plan(self, task_id: str, plan: dict[str, Any], risk_level: RiskLevel, requires_approval: bool) -> TaskRecord:
        return await self.update_status(
            task_id,
            TaskStatus.awaiting_approval if requires_approval else TaskStatus.approved,
            plan_json=plan,
            risk_level=risk_level.value,
            requires_approval=requires_approval,
        )

    async def approve_task(self, task_id: str, approved_by: str, decision: ApprovalDecision, reason: str = "") -> TaskRecord:
        task = await self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        if task.status != TaskStatus.awaiting_approval.value:
            raise ValueError(f"Task {task_id} is not awaiting approval (status: {task.status})")

        new_status = TaskStatus.approved if decision == ApprovalDecision.approved else TaskStatus.rejected
        task.status = new_status.value
        task.approved_by = approved_by
        task.approved_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(task)
        await self._audit(
            "task_approval",
            task_id,
            {"decision": decision.value, "approved_by": approved_by, "reason": reason},
        )
        return task

    async def set_result(self, task_id: str, result: dict[str, Any], status: TaskStatus = TaskStatus.completed) -> TaskRecord:
        return await self.update_status(task_id, status, result_json=result)

    async def set_error(self, task_id: str, error: str) -> TaskRecord:
        return await self.update_status(task_id, TaskStatus.failed, error=error)

    async def get_task(self, task_id: str) -> TaskRecord | None:
        result = await self.db.execute(select(TaskRecord).where(TaskRecord.id == task_id))
        return result.scalar_one_or_none()

    async def list_tasks(self, status: str | None = None, limit: int = 50, offset: int = 0) -> tuple[list[TaskRecord], int]:
        query = select(TaskRecord).order_by(TaskRecord.created_at.desc())
        count_query = select(func.count(TaskRecord.id))
        if status:
            query = query.where(TaskRecord.status == status)
            count_query = count_query.where(TaskRecord.status == status)
        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        count_result = await self.db.execute(count_query)
        return list(result.scalars().all()), count_result.scalar_one()

    async def list_pending_approvals(self) -> list[TaskRecord]:
        result = await self.db.execute(
            select(TaskRecord)
            .where(TaskRecord.status == TaskStatus.awaiting_approval.value)
            .order_by(TaskRecord.created_at.asc())
        )
        return list(result.scalars().all())

    async def record_provider_call(self, task_id: str, provider: str, role: str, **kwargs) -> None:
        record = ProviderCallRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            provider=provider,
            role=role,
            **kwargs,
        )
        self.db.add(record)
        await self.db.commit()

    async def record_execution(self, task_id: str, step_order: int, **kwargs) -> None:
        record = ExecutionRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            step_order=step_order,
            **kwargs,
        )
        self.db.add(record)
        await self.db.commit()

    async def get_metrics(self) -> dict[str, Any]:
        """Get aggregate metrics for Prometheus."""
        # Tasks by status
        result = await self.db.execute(
            select(TaskRecord.status, func.count(TaskRecord.id)).group_by(TaskRecord.status)
        )
        by_status = {row[0]: row[1] for row in result.all()}

        # Total tasks
        total = sum(by_status.values())

        # Provider calls
        provider_total = await self.db.execute(select(func.count(ProviderCallRecord.id)))
        provider_failures = await self.db.execute(
            select(func.count(ProviderCallRecord.id)).where(ProviderCallRecord.success == False)
        )

        # Pending approvals
        pending = by_status.get(TaskStatus.awaiting_approval.value, 0)

        # Blocked
        blocked = by_status.get(TaskStatus.blocked.value, 0)

        return {
            "tasks_total": total,
            "tasks_by_status": by_status,
            "provider_calls_total": provider_total.scalar_one(),
            "provider_failures_total": provider_failures.scalar_one(),
            "approvals_pending": pending,
            "blocked_actions_total": blocked,
        }

    async def _audit(self, event_type: str, task_id: str | None, details: dict[str, Any]) -> None:
        record = AuditRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            event_type=event_type,
            details_json={k: mask_secrets(str(v)) for k, v in details.items()},
        )
        self.db.add(record)
        await self.db.commit()

    @staticmethod
    def task_to_response(task: TaskRecord) -> TaskResponse:
        plan = None
        if task.plan_json:
            try:
                plan = ExecutionPlan(**task.plan_json)
            except Exception:
                plan = None
        return TaskResponse(
            id=task.id,
            status=TaskStatus(task.status),
            created_at=task.created_at,
            updated_at=task.updated_at,
            message=task.message,
            risk_level=RiskLevel(task.risk_level) if task.risk_level else None,
            plan=plan,
            result=task.result_json,
            error=task.error,
            requires_approval=task.requires_approval or False,
        )
