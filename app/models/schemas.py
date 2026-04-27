"""Modelos Pydantic para requisições de API, respostas, tarefas, planos e auditoria."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --- Enumeradores ---

class TaskStatus(str, Enum):
    pending = "pending"
    planning = "planning"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    rejected = "rejected"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"
    blocked = "blocked"


class ApprovalDecision(str, Enum):
    approved = "approved"
    rejected = "rejected"


class ProviderRole(str, Enum):
    classify = "classify"
    plan = "plan"
    review = "review"
    execute = "execute"
    summarize = "summarize"


# --- Chat / Ingest ---

class ChatIngestRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    user_id: str = Field(default="webai-user")
    context: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ChatIngestResponse(BaseModel):
    task_id: str
    status: TaskStatus
    summary: str
    risk_level: RiskLevel | None = None
    requires_approval: bool = False
    message: str = ""
    findings: list[str] = Field(default_factory=list)
    recommended_action_ids: list[str] = Field(default_factory=list)


# --- Plan ---

class PlanStep(BaseModel):
    order: int
    description: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    rollback: str | None = None


class ExecutionPlan(BaseModel):
    objective: str
    context: str = ""
    assumptions: list[str] = Field(default_factory=list)
    affected_targets: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    dry_run_steps: list[PlanStep] = Field(default_factory=list)
    validation_steps: list[PlanStep] = Field(default_factory=list)
    rollback_steps: list[PlanStep] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.medium
    requires_approval: bool = True
    proposed_provider: str = "local"


# --- Task ---

class TaskCreate(BaseModel):
    message: str
    user_id: str = "webai-user"
    context: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    message: str
    risk_level: RiskLevel | None = None
    plan: ExecutionPlan | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    requires_approval: bool = False


class TaskListResponse(BaseModel):
    tasks: list[TaskResponse]
    total: int


# --- Approval ---

class ApprovalRequest(BaseModel):
    decision: ApprovalDecision
    reason: str = ""
    approved_by: str = "admin"


class ApprovalResponse(BaseModel):
    task_id: str
    decision: ApprovalDecision
    approved_by: str
    timestamp: datetime


# --- Provider ---

class ProviderStatus(BaseModel):
    name: str
    enabled: bool
    healthy: bool
    last_check: datetime | None = None
    latency_ms: float | None = None
    error: str | None = None


class ProvidersStatusResponse(BaseModel):
    providers: list[ProviderStatus]


# --- Audit ---

class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    task_id: str | None = None
    event_type: str
    actor: str = "system"
    details: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel | None = None


# --- Metrics ---

class MetricsSummary(BaseModel):
    tasks_total: int = 0
    tasks_by_status: dict[str, int] = Field(default_factory=dict)
    provider_calls_total: int = 0
    provider_failures_total: int = 0
    approvals_pending: int = 0
    blocked_actions_total: int = 0
