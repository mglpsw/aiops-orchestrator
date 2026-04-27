"""Schemas do AIOps Diagnostic Engine v1.

Estes modelos representam somente diagnostico e saida em dry-run.
Nao carregam comando executavel nem habilitam remediacao.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AIOpsSignal(BaseModel):
    name: str
    status: str
    value: float | int | str | None = None
    unit: str | None = None
    source: str
    description: str | None = None


class AIOpsFinding(BaseModel):
    check: str | None = None
    title: str
    severity: str
    status: str
    summary: str | None = None
    description: str
    evidence: list[AIOpsSignal] = Field(default_factory=list)
    impact: str | None = None
    confidence: float | None = None
    probable_cause: str | None = None
    next_validation: str | None = None
    recommended_action_ids: list[str] = Field(default_factory=list)


class AIOpsRecommendedAction(BaseModel):
    title: str
    action_type: str = "dry_run"
    description: str
    requires_approval: bool = False
    command: str | None = None

    @model_validator(mode="after")
    def validate_recommended_action(self) -> "AIOpsRecommendedAction":
        if self.action_type != "dry_run":
            raise ValueError("action_type must be dry_run in AIOps Diagnostic Engine v1")
        if self.command is not None:
            raise ValueError("command must be None in AIOps Diagnostic Engine v1")
        return self


class AIOpsDiagnoseRequest(BaseModel):
    allowed_checks: ClassVar[set[str]] = {
        "readiness",
        "readiness_status",
        "backend_up",
        "error_rate",
        "error_rate_high",
        "latency_p95",
        "latency_p95_high",
        "blocked_tasks",
        "route_block_spike",
        "rate_limit_spike",
        "prometheus_scrape_staleness",
        "aiops_catalog_not_ready",
        "model_selection",
        "ollama_models_count",
    }

    target: str = "agent-router"
    scope: str = "self"
    checks: list[str] = Field(default_factory=list)
    dry_run: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_request(self) -> "AIOpsDiagnoseRequest":
        if self.dry_run is not True:
            raise ValueError("dry_run must be True in AIOps Diagnostic Engine v1")
        unknown = [check for check in self.checks if check not in self.allowed_checks]
        if unknown:
            raise ValueError(f"Unknown check(s): {', '.join(sorted(unknown))}")
        return self


class AIOpsDiagnoseResponse(BaseModel):
    status: str
    severity: str
    health_score: int = 100
    summary: str
    signals: list[AIOpsSignal] = Field(default_factory=list)
    findings: list[AIOpsFinding] = Field(default_factory=list)
    recommended_actions: list[AIOpsRecommendedAction] = Field(default_factory=list)
    dry_run: bool = True
    # Attached by the endpoint after the diagnostic pass; None when no problems found
    # or when the catalog is unavailable (fail-soft). Never contains command fields.
    action_plan: "ActionPlanResponse | None" = None


# ---------------------------------------------------------------------------
# Action Catalog schemas
# ---------------------------------------------------------------------------


class CatalogActionEntry(BaseModel):
    """API representation of one allowlisted action. Command is intentionally omitted."""

    action_id: str
    description: str
    mode: str
    risk: str
    timeout_seconds: int
    requires_approval: bool
    tags: list[str] = Field(default_factory=list)


class CatalogResponse(BaseModel):
    version: str
    count: int
    actions: list[CatalogActionEntry]


# ---------------------------------------------------------------------------
# Action Planner schemas
# ---------------------------------------------------------------------------

_ALLOWED_RISKS = {"low", "medium", "high"}
_ALLOWED_MODES = {"readonly", "readwrite"}


class ActionPlanRequest(BaseModel):
    """Request to build a safe, deterministic action plan from the catalog."""

    target: str = "agent-router"
    action_ids: list[str] = Field(default_factory=list)
    context: str = ""
    dry_run: bool = True

    @model_validator(mode="after")
    def validate_plan_request(self) -> "ActionPlanRequest":
        if self.dry_run is not True:
            raise ValueError("dry_run must be True in AIOps Action Planner v1")
        return self


class ActionPlanStep(BaseModel):
    """A single planned step resolved from the action catalog."""

    action_id: str
    title: str
    risk: str
    mode: str
    requires_approval: bool
    reason: str
    evidence_source: str | None = None
    finding_id: str | None = None


class ActionPlanBlockedStep(BaseModel):
    """A step that could not be planned because it is unknown or policy-rejected."""

    action_id: str
    reason: str


class ActionPlanResponse(BaseModel):
    """Structured, dry-run-only plan produced by the Action Planner."""

    plan_id: str
    target: str
    status: str  # ready | blocked | empty
    risk: str
    requires_approval: bool
    steps: list[ActionPlanStep] = Field(default_factory=list)
    blocked_steps: list[ActionPlanBlockedStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = True


class ActionDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = "agent-router"
    action_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    dry_run: bool = True

    @model_validator(mode="after")
    def validate_dry_run_request(self) -> "ActionDryRunRequest":
        if self.dry_run is not True:
            raise ValueError("dry_run must be True in AIOps Action Dry-Run v1")
        return self


class ActionDryRunStep(BaseModel):
    action_id: str
    title: str
    mode: str
    risk: str
    requires_approval: bool
    execution: Literal["not_executed"] = "not_executed"
    reason: str


# ActionPlanResponse is defined after AIOpsDiagnoseResponse in this file,
# so rebuild is required to resolve the forward reference on action_plan.
AIOpsDiagnoseResponse.model_rebuild()
ActionDryRunRequest.model_rebuild()


class ActionDryRunResponse(BaseModel):
    dry_run_id: str
    target: str
    status: str  # ok | blocked | partial
    risk: str
    requires_approval: bool
    plan: ActionPlanResponse
    would_run: list[ActionDryRunStep] = Field(default_factory=list)
    blocked_steps: list[ActionPlanBlockedStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ActionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = "agent-router"
    approval_id: str
    action_ids: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="after")
    def validate_action_run_request(self) -> "ActionRunRequest":
        if not self.action_ids:
            raise ValueError("action_ids must not be empty")
        return self


class ActionRunResult(BaseModel):
    action_id: str
    status: Literal["ok", "failed"]
    exit_code: int
    duration_ms: int
    output_preview: str
    truncated: bool = False


class ActionRunResponse(BaseModel):
    run_id: str
    target: str
    approval_id: str
    status: Literal["ok", "partial", "failed", "blocked"]
    started_at: str
    finished_at: str
    results: list[ActionRunResult] = Field(default_factory=list)
    blocked_steps: list[ActionPlanBlockedStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    target: str
    approval_id: str
    status: Literal["ok", "partial", "failed", "blocked"]
    started_at: str
    finished_at: str
    requested_action_ids: list[str] = Field(default_factory=list)
    result_count: int = 0
    blocked_count: int = 0
    warning_count: int = 0


class RunDetailResponse(BaseModel):
    run_id: str
    target: str
    approval_id: str
    status: Literal["ok", "partial", "failed", "blocked"]
    started_at: str
    finished_at: str
    requested_action_ids: list[str] = Field(default_factory=list)
    results: list[ActionRunResult] = Field(default_factory=list)
    blocked_steps: list[ActionPlanBlockedStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RunRecentResponse(BaseModel):
    runs: list[RunSummary] = Field(default_factory=list)
    count: int = 0
    warnings: list[str] = Field(default_factory=list)


class AuditEvent(BaseModel):
    event_id: str
    timestamp: str
    event_type: Literal[
        "action_plan_created",
        "action_dry_run_created",
        "action_run_requested",
        "action_run_started",
        "action_run_completed",
        "action_run_blocked",
        "action_run_failed",
        "diagnose_action_plan_attached",
        "approval_requested",
        "approval_approved",
        "approval_rejected",
        "approval_expired",
    ]
    actor: str
    target: str
    source_endpoint: str
    approval_id: str | None = None
    correlation_id: str | None = None
    plan_id: str | None = None
    dry_run_id: str | None = None
    run_id: str | None = None
    risk: str
    requires_approval: bool
    status: str
    action_ids: list[str] = Field(default_factory=list)
    blocked_action_ids: list[str] = Field(default_factory=list)
    warnings_count: int = 0
    blocked_steps_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=lambda: {"schema_version": "audit.v1"})


class AuditRecentResponse(BaseModel):
    events: list[AuditEvent] = Field(default_factory=list)


ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]


class ApprovalCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = "agent-router"
    plan_id: str | None = None
    dry_run_id: str | None = None
    reason: str = ""
    ttl_seconds: int = 900

    @model_validator(mode="after")
    def validate_approval_create_request(self) -> "ApprovalCreateRequest":
        if not self.plan_id and not self.dry_run_id:
            raise ValueError("plan_id or dry_run_id is required")
        if self.plan_id and self.dry_run_id:
            return self
        return self


class ApprovalResponse(BaseModel):
    approval_id: str
    target: str
    plan_id: str | None = None
    dry_run_id: str | None = None
    status: ApprovalStatus
    risk: str
    requires_approval: bool
    created_at: str
    expires_at: str
    approved_at: str | None = None
    rejected_at: str | None = None
    actor: str
    approved_by: str | None = None
    rejected_by: str | None = None
    reason: str = ""


class ApprovalDecisionResponse(ApprovalResponse):
    pass


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalResponse] = Field(default_factory=list)


ActionDryRunResponse.model_rebuild()
