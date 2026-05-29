"""Versioned schemas for AgentReview offline intake."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


TARGET_PROFILE_SCHEMA = "agent-review.target-profile.v1"
INTAKE_SCHEMA = "agent-review.intake.v1"
REDACTION_REPORT_SCHEMA = "agent-review.redaction-report.v1"

ArtifactKind = Literal["json", "yaml", "text", "markdown", "diff"]
ArtifactState = Literal["available", "missing", "invalid", "degraded"]
IntakeState = Literal["complete", "degraded", "failed"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ArtifactDeclaration(BaseModel):
    name: str
    path: str
    kind: ArtifactKind
    required: bool = False


class TargetProfile(BaseModel):
    schema_version: str = TARGET_PROFILE_SCHEMA
    source: str = "repo-profile"
    target_repo: str | None = None
    name: str | None = None
    artifacts: list[ArtifactDeclaration] = Field(default_factory=list)
    domain_contracts: dict[str, Any] | list[Any] | None = None
    review_packs: dict[str, Any] | list[Any] | None = None
    limitations: list[str] = Field(default_factory=list)


class ArtifactStatus(BaseModel):
    name: str
    path: str
    available: bool
    valid: bool
    status: ArtifactState
    limitations: list[str] = Field(default_factory=list)
    error_class: str | None = None


class LoadedArtifact(BaseModel):
    name: str
    path: str
    kind: ArtifactKind
    content: Any


class RedactionReport(BaseModel):
    schema_version: str = REDACTION_REPORT_SCHEMA
    source: str = "aiops-review-intake"
    files_processed: int = 0
    replacements_by_type: dict[str, int] = Field(default_factory=dict)
    secret_like_values_found: int = 0
    redacted_lines_present: bool = False
    redaction_is_sanitizer_artifact: bool = False
    hardcoded_secret_confirmed: bool = False
    output_safe_for_llm: bool = False
    limitations: list[str] = Field(default_factory=list)


class ReviewIntake(BaseModel):
    schema_version: str = INTAKE_SCHEMA
    source: str = "aiops-review-intake"
    target_repo: str
    target_profile: dict[str, Any]
    artifacts: dict[str, Any] = Field(default_factory=dict)
    artifact_status: list[ArtifactStatus] = Field(default_factory=list)
    redaction_summary: RedactionReport
    limitations: list[str] = Field(default_factory=list)
    completeness: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    status: IntakeState
    error_class: str | None = None

