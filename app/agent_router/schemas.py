"""Schemas do AIOps Diagnostic Engine v1.

Estes modelos representam somente diagnostico e saida em dry-run.
Nao carregam comando executavel nem habilitam remediacao.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field, model_validator


class AIOpsSignal(BaseModel):
    name: str
    status: str
    value: float | int | str | None = None
    unit: str | None = None
    source: str
    description: str | None = None


class AIOpsFinding(BaseModel):
    title: str
    severity: str
    status: str
    description: str
    evidence: list[AIOpsSignal] = Field(default_factory=list)


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
        "backend_up",
        "error_rate",
        "latency_p95",
        "blocked_tasks",
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
    summary: str
    signals: list[AIOpsSignal] = Field(default_factory=list)
    findings: list[AIOpsFinding] = Field(default_factory=list)
    recommended_actions: list[AIOpsRecommendedAction] = Field(default_factory=list)
    dry_run: bool = True


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
