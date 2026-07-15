"""Versioned schemas for AgentReview offline intake."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


TARGET_PROFILE_SCHEMA = "agent-review.target-profile.v1"
INTAKE_SCHEMA = "agent-review.intake.v1"
REDACTION_REPORT_SCHEMA = "agent-review.redaction-report.v1"
SEMANTIC_CHUNK_PLAN_SCHEMA = "agent-review.semantic-chunk-plan.v1"
CHUNK_RESULTS_SCHEMA = "agent-review.chunk-results.v1"
FINAL_REVIEW_SCHEMA = "agent-review.final-review.v1"
QUALITY_GATE_SCHEMA = "agent-review.quality-gate.v1"
TELEMETRY_SCHEMA = "agent-review.telemetry.v1"
FALSE_POSITIVE_MARKERS_SCHEMA = "agent-review.false-positive-markers.v1"
FALSE_POSITIVE_SIGNATURES_SCHEMA = "agent-review.false-positive-signatures.v1"
CONTRACT_SUGGESTIONS_SCHEMA = "agent-review.contract-suggestions.v1"

ArtifactKind = Literal["json", "yaml", "text", "markdown", "diff"]
ArtifactState = Literal["available", "missing", "invalid", "degraded"]
IntakeState = Literal["complete", "degraded", "failed"]
SemanticGroup = Literal[
    "primary_backend_logic",
    "api_schema_contract",
    "frontend_ui",
    "tests",
    "workflow_aiops",
    "docs_changelog",
    "suspicious_out_of_scope",
    "unknown",
]
ChunkCoverage = Literal["complete", "partial", "degraded"]
ChunkPlanState = Literal["complete", "partial", "degraded", "failed"]
FindingSeverity = Literal["P0", "P1", "P2", "P3"]
FindingConfidence = Literal["high", "medium", "low"]
ChunkResultState = Literal["complete", "partial", "degraded", "failed"]
FinalReviewStatus = Literal["complete", "partial", "degraded", "failed"]
FinalReviewVerdict = Literal[
    "approved",
    "approve_with_minor_notes",
    "approve_with_required_followup",
    "changes_requested",
    "manual_review_required",
    "review_unavailable",
]
ReviewQualityGateStatus = Literal["passed", "degraded", "failed", "manual_review_required"]
SecondOpinionStatus = Literal["not_required", "requested", "completed", "failed", "skipped"]
RiskSource = Literal["chunk_risk", "downgraded_finding"]
FalsePositiveReason = Literal[
    "docs_only_overseverity",
    "missing_source_artifact",
    "test_file_in_other_chunk",
    "contract_obsolete",
]
RejectedFindingReason = Literal[
    "missing_required_evidence",
    "missing_file_path",
    "file_not_in_chunk",
    "redacted_or_placeholder_only_evidence",
    "speculative_language",
    "unsupported_test_failure_source",
    "duplicate_dedupe_key",
    "invalid_finding",
]


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


class SemanticChunk(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    order_index: int
    files: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    contracts: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    coverage: ChunkCoverage
    prompt_budget_chars: int
    estimated_chars: int
    limitations: list[str] = Field(default_factory=list)


class SemanticChunkPlan(BaseModel):
    schema_version: int = 1
    schema_id: str = SEMANTIC_CHUNK_PLAN_SCHEMA
    source: str = "aiops-semantic-chunk-planner"
    target_repo: str
    max_parallel_blocks: int
    chunks: list[SemanticChunk] = Field(default_factory=list)
    files_covered: list[str] = Field(default_factory=list)
    files_partially_covered: list[str] = Field(default_factory=list)
    files_not_covered: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: ChunkPlanState
    created_at: str = Field(default_factory=utc_now_iso)


class ChunkResponseFinding(BaseModel):
    severity: str | None = None
    title: str | None = None
    file_path: str | None = None
    line_or_hunk: str | None = None
    evidence: str | None = None
    source_artifact: str | None = None
    contract_id: str | None = None
    impact: str | None = None
    confidence: str | None = None
    dedupe_key: str | None = None


class ChunkResponseRisk(BaseModel):
    title: str | None = None
    reason: str | None = None
    missing_evidence: str | None = None
    suggested_validation: str | None = None


class ChunkResponseLimitation(BaseModel):
    type: str | None = None
    detail: str | None = None


class ChunkCoverageNotes(BaseModel):
    files_reviewed: list[str] = Field(default_factory=list)
    files_partial: list[str] = Field(default_factory=list)
    files_not_reviewed: list[str] = Field(default_factory=list)


class ChunkResponse(BaseModel):
    schema_version: int
    chunk_id: str
    semantic_group: SemanticGroup
    confirmed_findings: list[ChunkResponseFinding] = Field(default_factory=list)
    risks: list[ChunkResponseRisk] = Field(default_factory=list)
    limitations: list[ChunkResponseLimitation] = Field(default_factory=list)
    coverage_notes: ChunkCoverageNotes = Field(default_factory=ChunkCoverageNotes)


class NormalizedFinding(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    severity: FindingSeverity
    title: str
    file_path: str
    line_or_hunk: str | None = None
    evidence: str
    source_artifact: str | None = None
    contract_id: str | None = None
    impact: str
    confidence: FindingConfidence | None = None
    dedupe_key: str | None = None


class NormalizedRisk(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    source: RiskSource
    title: str
    reason: str
    missing_evidence: str | None = None
    suggested_validation: str | None = None
    severity: str | None = None
    file_path: str | None = None
    evidence: str | None = None
    impact: str | None = None
    dedupe_key: str | None = None


class RejectedFinding(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    reason: RejectedFindingReason
    title: str | None = None
    severity: str | None = None
    file_path: str | None = None
    evidence: str | None = None
    dedupe_key: str | None = None


class ChunkParseFailure(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    error_class: str
    message: str


class ChunkResultsCoverage(BaseModel):
    files_reviewed: list[str] = Field(default_factory=list)
    files_partial: list[str] = Field(default_factory=list)
    files_not_reviewed: list[str] = Field(default_factory=list)


class ChunkResults(BaseModel):
    schema_version: int = 1
    schema_id: str = CHUNK_RESULTS_SCHEMA
    source: str = "aiops-review-parse-chunks"
    target_repo: str
    chunk_plan_ref: dict[str, Any]
    chunks_parsed: list[str] = Field(default_factory=list)
    chunks_failed: list[ChunkParseFailure] = Field(default_factory=list)
    confirmed_findings: list[NormalizedFinding] = Field(default_factory=list)
    risks: list[NormalizedRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    rejected_findings: list[RejectedFinding] = Field(default_factory=list)
    coverage: ChunkResultsCoverage = Field(default_factory=ChunkResultsCoverage)
    status: ChunkResultState
    created_at: str = Field(default_factory=utc_now_iso)


class FinalReviewFinding(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    severity: FindingSeverity
    title: str
    file_path: str
    line_or_hunk: str | None = None
    evidence: str
    source_artifact: str | None = None
    contract_id: str | None = None
    impact: str
    confidence: FindingConfidence | None = None
    dedupe_key: str | None = None
    source_chunks: list[str] = Field(default_factory=list)
    semantic_groups: list[SemanticGroup] = Field(default_factory=list)


class FinalReviewRisk(BaseModel):
    chunk_id: str
    semantic_group: SemanticGroup
    source: RiskSource
    title: str
    reason: str
    missing_evidence: str | None = None
    suggested_validation: str | None = None
    severity: str | None = None
    file_path: str | None = None
    evidence: str | None = None
    impact: str | None = None
    dedupe_key: str | None = None
    source_chunks: list[str] = Field(default_factory=list)
    semantic_groups: list[SemanticGroup] = Field(default_factory=list)


class FinalReviewRejectedSummary(BaseModel):
    total: int = 0
    by_reason: dict[str, int] = Field(default_factory=dict)
    sample_titles: list[str] = Field(default_factory=list)


class FinalReviewCoverage(BaseModel):
    files_reviewed: list[str] = Field(default_factory=list)
    files_partial: list[str] = Field(default_factory=list)
    files_not_reviewed: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    missing_expected_files: list[str] = Field(default_factory=list)
    extra_reported_files: list[str] = Field(default_factory=list)
    comparison_available: bool = False


class FinalReviewCounts(BaseModel):
    confirmed_findings_total: int = 0
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    risks_total: int = 0
    risks_by_source: dict[str, int] = Field(default_factory=dict)
    rejected_findings_total: int = 0
    rejected_findings_by_reason: dict[str, int] = Field(default_factory=dict)
    limitations_total: int = 0
    chunks_parsed: int = 0
    chunks_failed: int = 0


class FinalReview(BaseModel):
    schema_version: int = 1
    schema_id: str = FINAL_REVIEW_SCHEMA
    source: str = "aiops-review-synthesize"
    target_repo: str
    status: FinalReviewStatus
    verdict: FinalReviewVerdict
    summary: str
    confirmed_findings: list[FinalReviewFinding] = Field(default_factory=list)
    risks: list[FinalReviewRisk] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    rejected_summary: FinalReviewRejectedSummary = Field(default_factory=FinalReviewRejectedSummary)
    coverage: FinalReviewCoverage = Field(default_factory=FinalReviewCoverage)
    counts: FinalReviewCounts = Field(default_factory=FinalReviewCounts)
    inputs: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class ReviewQualityGate(BaseModel):
    schema_version: int = 1
    schema_id: str = QUALITY_GATE_SCHEMA
    source: Literal["aiops-review-quality-gate"] = "aiops-review-quality-gate"
    status: ReviewQualityGateStatus
    normalized_verdict: FinalReviewVerdict
    quality_score: float
    manual_review_required: bool
    second_opinion_requested: bool = False
    second_opinion_status: SecondOpinionStatus = "not_required"
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class ReviewTelemetry(BaseModel):
    schema_version: int = 1
    schema_id: str = TELEMETRY_SCHEMA
    source: Literal["aiops-review-telemetry"] = "aiops-review-telemetry"
    status: str
    target: dict[str, Any] = Field(default_factory=dict)
    pipeline: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    findings: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)
    quality_gate: dict[str, Any] = Field(default_factory=dict)
    validation_evidence: dict[str, Any] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(default_factory=dict)
    model: dict[str, Any] = Field(default_factory=dict)
    performance: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)



class FalsePositiveMarker(BaseModel):
    finding_signature: str
    reason: FalsePositiveReason
    suggested_rule: str | None = None
    contract_id: str | None = None


class FalsePositiveMarkers(BaseModel):
    schema_id: str = FALSE_POSITIVE_MARKERS_SCHEMA
    schema_version: int = 1
    source: Literal["manual"] = "manual"
    markers: list[FalsePositiveMarker] = Field(default_factory=list)


class FalsePositiveCandidate(BaseModel):
    signature: str
    basis: dict[str, Any]
    finding: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    matched_markers: list[dict[str, Any]] = Field(default_factory=list)


class FalsePositiveSignatures(BaseModel):
    schema_id: str = FALSE_POSITIVE_SIGNATURES_SCHEMA
    schema_version: int = 1
    source: Literal["aiops-review-false-positives"] = "aiops-review-false-positives"
    target: dict[str, Any] = Field(default_factory=dict)
    candidates: list[FalsePositiveCandidate] = Field(default_factory=list)
    markers: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ContractSuggestion(BaseModel):
    suggestion_id: str
    finding_signature: str
    reason: FalsePositiveReason
    contract_id: str | None = None
    suggested_rule: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ContractSuggestions(BaseModel):
    schema_id: str = CONTRACT_SUGGESTIONS_SCHEMA
    schema_version: int = 1
    source: Literal["aiops-review-false-positives"] = "aiops-review-false-positives"
    apply_mode: Literal["manual_only"] = "manual_only"
    applied: bool = False
    target: dict[str, Any] = Field(default_factory=dict)
    suggestions: list[ContractSuggestion] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
