"""Strict, offline-only AgentReview v2 contract foundation.

This module deliberately has no integration with the v1 planner, payload builder,
parser, synthesizer, or quality gate.  It freezes the data contracts that those
consumers may adopt through explicit future migrations.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    model_validator,
)

from app.agent_review.redaction import sanitize_artifact_value


RUN_SCHEMA_V2 = "agent-review.run.v2"
CHUNK_PAYLOAD_SCHEMA_V2 = "agent-review.chunk-payload.v2"
CHUNK_RESPONSE_ENVELOPE_SCHEMA_V2 = "agent-review.chunk-response-envelope.v2"
CHUNK_RESPONSE_SCHEMA_V2 = "agent-review.chunk-response.v2"
TARGET_PROFILE_SCHEMA_V2 = "agent-review.target-profile.v2"
REVIEW_READINESS_SCHEMA_V2 = "agent-review.review-readiness.v2"

_RUN_IDENTITY_FIELDS = (
    "repo",
    "pr_number",
    "base_sha",
    "head_sha",
    "tested_merge_sha",
    "toolrepo_sha",
    "profile_hash",
    "policy_hash",
    "evidence_hash",
)
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SAFE_TEXT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,;:_()#+-]{0,511}")
_RFC3339_SECONDS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class ContractV2Model(BaseModel):
    """Base configuration shared by every v2 contractual object."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        protected_namespaces=(),
        validate_default=True,
    )


def _validate_repository(value: str) -> str:
    if value != value.strip() or not _REPOSITORY_RE.fullmatch(value):
        raise ValueError("repository must use the owner/name form")
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("repository contains an invalid segment")
    _reject_sensitive_value(value)
    return value


def _validate_relative_path(value: str) -> str:
    if value != value.strip() or not value or "\\" in value:
        raise ValueError("path must be a non-empty normalized POSIX relative path")
    if value.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError("absolute and home-relative paths are forbidden")
    if ".." in PurePosixPath(value).parts:
        raise ValueError("parent traversal is forbidden")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("control characters are forbidden")
    _reject_sensitive_value(value)
    return value


def _validate_safe_identifier(value: str) -> str:
    if value != value.strip() or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier contains unsupported characters")
    lowered = value.lower()
    if any(marker in lowered for marker in ("authorization", "bearer", "password", "secret", "cookie")):
        raise ValueError("identifier contains a sensitive marker")
    _reject_sensitive_value(value)
    return value


def _validate_safe_text(value: str) -> str:
    if value != value.strip() or not _SAFE_TEXT_RE.fullmatch(value):
        raise ValueError("text is not in the sanitized contract subset")
    lowered = value.lower()
    if any(marker in lowered for marker in ("authorization", "bearer", "password", "secret", "cookie")):
        raise ValueError("text contains a sensitive marker")
    _reject_sensitive_value(value)
    return value


def _validate_timestamp(value: str) -> str:
    if not _RFC3339_SECONDS_RE.fullmatch(value):
        raise ValueError("timestamp must be canonical RFC 3339 UTC seconds")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def _reject_sensitive_value(value: str) -> None:
    if sanitize_artifact_value(value) != value:
        raise ValueError("value contains a secret-like token or local path")


Repository = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
    AfterValidator(_validate_repository),
]
RelativePath = Annotated[
    StrictStr,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_relative_path),
]
RelativePattern = Annotated[
    StrictStr,
    Field(min_length=1, max_length=512),
    AfterValidator(_validate_relative_path),
]
SafeIdentifier = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
    AfterValidator(_validate_safe_identifier),
]
SafeText = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 .,;:_()#+-]{0,511}$"),
    AfterValidator(_validate_safe_text),
]
GitSha = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Rfc3339Timestamp = Annotated[
    StrictStr,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
    AfterValidator(_validate_timestamp),
]
PositiveInt = Annotated[StrictInt, Field(gt=0)]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]


class SemanticGroupV2(str, Enum):
    PRIMARY_BACKEND_LOGIC = "primary_backend_logic"
    API_SCHEMA_CONTRACT = "api_schema_contract"
    FRONTEND_UI = "frontend_ui"
    TESTS = "tests"
    WORKFLOW_AIOPS = "workflow_aiops"
    DOCS_CHANGELOG = "docs_changelog"
    SUSPICIOUS_OUT_OF_SCOPE = "suspicious_out_of_scope"
    UNKNOWN = "unknown"


class CoverageStateV2(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    DEGRADED = "degraded"


class FindingSeverityV2(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class FindingConfidenceV2(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingDispositionV2(str, Enum):
    NEW = "new"
    CONFIRMED = "confirmed"
    FIXED = "fixed"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"
    STALE = "stale"


class ReadinessStateV2(str, Enum):
    READY = "ready"
    BLOCKED_CODE = "blocked_code"
    BLOCKED_PIPELINE = "blocked_pipeline"
    MANUAL_REQUIRED = "manual_required"
    STALE = "stale"


class ReadinessReasonV2(str, Enum):
    SCHEMA_FAILURE = "schema_failure"
    TRANSPORT_FAILURE = "transport_failure"
    COVERAGE_FAILURE = "coverage_failure"
    POLICY_FAILURE = "policy_failure"
    MODEL_UNCERTAINTY = "model_uncertainty"
    CONFIRMED_CODE_FINDING = "confirmed_code_finding"


SemanticGroupValue = Annotated[SemanticGroupV2, Field(strict=False)]
CoverageStateValue = Annotated[CoverageStateV2, Field(strict=False)]
FindingSeverityValue = Annotated[FindingSeverityV2, Field(strict=False)]
FindingConfidenceValue = Annotated[FindingConfidenceV2, Field(strict=False)]
FindingDispositionValue = Annotated[FindingDispositionV2, Field(strict=False)]
ReadinessStateValue = Annotated[ReadinessStateV2, Field(strict=False)]
ReadinessReasonValue = Annotated[ReadinessReasonV2, Field(strict=False)]


class RunIdentityV2(ContractV2Model):
    repo: Repository
    pr_number: PositiveInt
    base_sha: GitSha
    head_sha: GitSha
    tested_merge_sha: GitSha
    toolrepo_sha: GitSha
    profile_hash: Sha256
    policy_hash: Sha256
    evidence_hash: Sha256


def canonical_run_identity_bytes(identity: RunIdentityV2) -> bytes:
    """Return the exact bytes hashed for an AgentReview v2 ``run_id``.

    The bytes are UTF-8 encoding of the nine-field JSON object returned by
    ``RunIdentityV2.model_dump(mode="json")`` with Unicode left unescaped,
    lexicographically sorted keys, separators ``(',', ':')``, and non-finite
    numbers rejected.  No delimiter concatenation, timestamp, local path,
    dictionary insertion order, clock, or random value participates.
    """

    payload = identity.model_dump(mode="json")
    if set(payload) != set(_RUN_IDENTITY_FIELDS) or len(payload) != len(_RUN_IDENTITY_FIELDS):
        raise RuntimeError("run identity fields diverged from the frozen v2 contract")
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return rendered.encode("utf-8")


def compute_run_id(identity: RunIdentityV2) -> str:
    return hashlib.sha256(canonical_run_identity_bytes(identity)).hexdigest()


class RunOriginV2(ContractV2Model):
    event_type: Literal["pull_request", "pull_request_target", "manual", "replay"]
    event_action: Literal["opened", "reopened", "synchronize", "ready_for_review", "manual", "replay"]
    delivery_id: SafeIdentifier

    @model_validator(mode="after")
    def validate_event_pair(self) -> RunOriginV2:
        pull_request_actions = {"opened", "reopened", "synchronize", "ready_for_review"}
        if self.event_type in {"pull_request", "pull_request_target"}:
            if self.event_action not in pull_request_actions:
                raise ValueError("pull request events require a pull request action")
        elif self.event_action != self.event_type:
            raise ValueError("manual and replay event types require their matching action")
        return self


class AgentReviewRunV2(ContractV2Model):
    schema_id: Literal["agent-review.run.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-run"]
    run_id: Sha256
    identity: RunIdentityV2
    origin: RunOriginV2
    created_at: Rfc3339Timestamp
    expires_at: Rfc3339Timestamp | None

    @model_validator(mode="after")
    def validate_identity(self) -> AgentReviewRunV2:
        if self.run_id != compute_run_id(self.identity):
            raise ValueError("run_id does not match the canonical run identity")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class ChunkCoverageV2(ContractV2Model):
    status: CoverageStateValue
    expected_files: list[RelativePath]
    reviewed_files: list[RelativePath]
    partially_reviewed_files: list[RelativePath]
    missing_files: list[RelativePath]
    must_review_files: list[RelativePath]
    missing_must_review_files: list[RelativePath]

    @model_validator(mode="after")
    def validate_partition(self) -> ChunkCoverageV2:
        collections = {
            "expected_files": self.expected_files,
            "reviewed_files": self.reviewed_files,
            "partially_reviewed_files": self.partially_reviewed_files,
            "missing_files": self.missing_files,
            "must_review_files": self.must_review_files,
            "missing_must_review_files": self.missing_must_review_files,
        }
        for name, values in collections.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")

        expected = set(self.expected_files)
        reviewed = set(self.reviewed_files)
        partial = set(self.partially_reviewed_files)
        missing = set(self.missing_files)
        must_review = set(self.must_review_files)
        missing_must_review = set(self.missing_must_review_files)
        if not reviewed | partial | missing <= expected:
            raise ValueError("coverage partitions must be subsets of expected_files")
        if reviewed & partial or reviewed & missing or partial & missing:
            raise ValueError("coverage partitions must be disjoint")
        if not must_review <= expected or not missing_must_review <= must_review:
            raise ValueError("must-review coverage must be a subset of expected coverage")
        if missing_must_review != must_review & (partial | missing):
            raise ValueError("missing_must_review_files contradict the coverage partitions")
        if self.status is CoverageStateV2.COMPLETE:
            if reviewed != expected or partial or missing or missing_must_review:
                raise ValueError("complete coverage requires every expected file to be reviewed")
        elif self.status is CoverageStateV2.PARTIAL and not (partial or missing):
            raise ValueError("partial coverage requires a partial or missing file")
        return self


class PayloadArtifactReferenceV2(ContractV2Model):
    artifact_id: SafeIdentifier
    kind: Literal["json", "yaml", "text", "markdown", "diff"]
    sha256: Sha256
    role: Literal["primary", "supporting", "validation", "coverage"]


class PayloadContractReferenceV2(ContractV2Model):
    contract_id: SafeIdentifier
    contract_version: SafeIdentifier
    sha256: Sha256
    scope: Literal["repository", "semantic_group", "chunk", "file"]
    paths: list[RelativePath]


class ChunkPayloadV2(ContractV2Model):
    schema_id: Literal["agent-review.chunk-payload.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-build-payloads"]
    run_id: Sha256
    identity: RunIdentityV2
    chunk_id: SafeIdentifier
    semantic_group: SemanticGroupValue
    payload_sha256: Sha256
    coverage: ChunkCoverageV2
    artifact_references: list[PayloadArtifactReferenceV2]
    contract_references: list[PayloadContractReferenceV2]

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> ChunkPayloadV2:
        if self.run_id != compute_run_id(self.identity):
            raise ValueError("run_id does not match the canonical run identity")
        if len({item.artifact_id for item in self.artifact_references}) != len(self.artifact_references):
            raise ValueError("artifact references must be unique")
        contract_keys = {(item.contract_id, item.contract_version) for item in self.contract_references}
        if len(contract_keys) != len(self.contract_references):
            raise ValueError("contract references must be unique")
        return self


class ChunkFindingV2(ContractV2Model):
    finding_id: SafeIdentifier
    severity: FindingSeverityValue
    title: SafeText
    file_path: RelativePath
    line_start: PositiveInt | None
    line_end: PositiveInt | None
    evidence: SafeText
    impact: SafeText
    confidence: FindingConfidenceValue
    contract_ids: list[SafeIdentifier]
    disposition: FindingDispositionValue

    @model_validator(mode="after")
    def validate_finding(self) -> ChunkFindingV2:
        if self.disposition is not FindingDispositionV2.NEW:
            raise ValueError("provider findings must enter the lifecycle as new")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must both be present or absent")
        if self.line_start is not None and self.line_end is not None and self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        if len(self.contract_ids) != len(set(self.contract_ids)):
            raise ValueError("contract_ids must be unique")
        return self


class ChunkReviewResultV2(ContractV2Model):
    schema_id: Literal["agent-review.chunk-response.v2"]
    schema_version: Literal[2]
    summary: SafeText
    findings: list[ChunkFindingV2]
    coverage: ChunkCoverageV2
    limitations: list[SafeIdentifier]

    @model_validator(mode="after")
    def validate_unique_findings(self) -> ChunkReviewResultV2:
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding IDs must be unique")
        if len(self.limitations) != len(set(self.limitations)):
            raise ValueError("limitations must be unique")
        return self


class FinishReasonV2(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALL = "tool_call"
    ERROR = "error"
    UNKNOWN = "unknown"


class ResponseErrorReasonV2(str, Enum):
    SCHEMA_FAILURE = "schema_failure"
    TRANSPORT_FAILURE = "transport_failure"
    POLICY_FAILURE = "policy_failure"
    MODEL_UNCERTAINTY = "model_uncertainty"


FinishReasonValue = Annotated[FinishReasonV2, Field(strict=False)]
ResponseErrorReasonValue = Annotated[ResponseErrorReasonV2, Field(strict=False)]


class SanitizedResponseErrorV2(ContractV2Model):
    reason_code: ResponseErrorReasonValue
    retryable: StrictBool


class _ChunkResponseEnvelopeBaseV2(ContractV2Model):
    schema_id: Literal["agent-review.chunk-response-envelope.v2"]
    schema_version: Literal[2]
    source: Literal["agent-review-provider-response"]
    run_id: Sha256
    chunk_id: SafeIdentifier
    payload_sha256: Sha256
    head_sha: GitSha
    provider: SafeIdentifier
    model: SafeIdentifier
    attempt: Annotated[StrictInt, Field(ge=1, le=100)]
    request_id: SafeIdentifier
    finish_reason: FinishReasonValue
    response_sha256: Sha256


class ChunkResponseSuccessEnvelopeV2(_ChunkResponseEnvelopeBaseV2):
    status: Literal["success"]
    result: ChunkReviewResultV2

    @model_validator(mode="after")
    def validate_success_finish_reason(self) -> ChunkResponseSuccessEnvelopeV2:
        if self.finish_reason is FinishReasonV2.ERROR:
            raise ValueError("a successful envelope cannot have finish_reason=error")
        return self


class ChunkResponseErrorEnvelopeV2(_ChunkResponseEnvelopeBaseV2):
    status: Literal["error"]
    error: SanitizedResponseErrorV2

    @model_validator(mode="after")
    def validate_error_finish_reason(self) -> ChunkResponseErrorEnvelopeV2:
        if self.finish_reason in {FinishReasonV2.STOP, FinishReasonV2.TOOL_CALL}:
            raise ValueError("an error envelope cannot have a successful finish reason")
        return self


ChunkResponseEnvelopeV2: TypeAlias = Annotated[
    ChunkResponseSuccessEnvelopeV2 | ChunkResponseErrorEnvelopeV2,
    Field(discriminator="status"),
]
CHUNK_RESPONSE_ENVELOPE_V2_ADAPTER = TypeAdapter(ChunkResponseEnvelopeV2)


def validate_chunk_response_envelope_v2(value: object) -> ChunkResponseEnvelopeV2:
    """Validate a JSON-compatible response envelope with strict JSON semantics."""

    if isinstance(value, (ChunkResponseSuccessEnvelopeV2, ChunkResponseErrorEnvelopeV2)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return CHUNK_RESPONSE_ENVELOPE_V2_ADAPTER.validate_json(value, strict=True)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return CHUNK_RESPONSE_ENVELOPE_V2_ADAPTER.validate_json(encoded, strict=True)


class ResponseBindingV2(ContractV2Model):
    run_id: Sha256
    chunk_id: SafeIdentifier
    payload_sha256: Sha256
    head_sha: GitSha


class ResponseBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_response_binding_v2(
    envelope: ChunkResponseEnvelopeV2,
    expected: ResponseBindingV2,
) -> None:
    """Detect identity divergence before a future parser reads result findings."""

    comparisons = (
        ("run_id", "run_id_mismatch"),
        ("chunk_id", "chunk_id_mismatch"),
        ("payload_sha256", "payload_sha256_mismatch"),
        ("head_sha", "head_sha_mismatch"),
    )
    for field_name, reason_code in comparisons:
        if getattr(envelope, field_name) != getattr(expected, field_name):
            raise ResponseBindingError(reason_code)


class TargetIdentityV2(ContractV2Model):
    repo: Repository
    default_branch: SafeIdentifier


class TargetArtifactV2(ContractV2Model):
    artifact_id: SafeIdentifier
    path: RelativePath
    kind: Literal["json", "yaml", "text", "markdown", "diff"]
    required: StrictBool
    max_bytes: PositiveInt


class TargetBudgetsV2(ContractV2Model):
    max_chunks: PositiveInt
    total_prompt_chars: PositiveInt
    max_chars_per_chunk: PositiveInt
    max_files_per_chunk: PositiveInt
    max_contracts_per_chunk: PositiveInt


class TargetMustReviewV2(ContractV2Model):
    paths: list[RelativePath]
    patterns: list[RelativePattern]
    artifact_ids: list[SafeIdentifier]
    minimum_coverage: Literal["complete"]


class TargetPoliciesV2(ContractV2Model):
    network_policy: Literal["forbidden"]
    fail_closed: StrictBool
    redaction_required: StrictBool
    allow_partial_coverage: StrictBool
    required_checks: list[SafeIdentifier]
    allowed_semantic_groups: list[SemanticGroupValue]
    coverage_failure_state: Literal["blocked_pipeline", "manual_required"]
    model_uncertainty_state: Literal["manual_required"]


class TargetContractV2(ContractV2Model):
    contract_id: SafeIdentifier
    contract_version: SafeIdentifier
    path: RelativePath
    sha256: Sha256
    scope: Literal["repository", "semantic_group", "file"]
    required: StrictBool


class TargetProfileV2(ContractV2Model):
    schema_id: Literal["agent-review.target-profile.v2"]
    schema_version: Literal[2]
    source: Literal["repo-profile"]
    identity: TargetIdentityV2
    artifacts: list[TargetArtifactV2]
    budgets: TargetBudgetsV2
    must_review: TargetMustReviewV2
    policies: TargetPoliciesV2
    contracts: list[TargetContractV2]
    limitations: list[SafeIdentifier]

    @model_validator(mode="after")
    def validate_profile_references(self) -> TargetProfileV2:
        artifact_ids = [artifact.artifact_id for artifact in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("artifact IDs must be unique")
        if not set(self.must_review.artifact_ids) <= set(artifact_ids):
            raise ValueError("must_review references an unknown artifact")
        contract_keys = [(contract.contract_id, contract.contract_version) for contract in self.contracts]
        if len(contract_keys) != len(set(contract_keys)):
            raise ValueError("contract IDs and versions must be unique")
        for values, name in (
            (self.must_review.paths, "must_review.paths"),
            (self.must_review.patterns, "must_review.patterns"),
            (self.must_review.artifact_ids, "must_review.artifact_ids"),
            (self.policies.required_checks, "policies.required_checks"),
            (self.policies.allowed_semantic_groups, "policies.allowed_semantic_groups"),
            (self.limitations, "limitations"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class DispositionEvidenceKindV2(str, Enum):
    COMMIT = "commit"
    TEST = "test"


DispositionEvidenceKindValue = Annotated[DispositionEvidenceKindV2, Field(strict=False)]


class DispositionEvidenceV2(ContractV2Model):
    kind: DispositionEvidenceKindValue
    reference: SafeIdentifier


class FindingLifecycleRecordV2(ContractV2Model):
    finding_id: SafeIdentifier
    severity: FindingSeverityValue
    disposition: FindingDispositionValue
    actionable: StrictBool
    justification: SafeText | None
    decided_by: SafeIdentifier | None
    evidence: list[DispositionEvidenceV2]
    superseded_by: SafeIdentifier | None

    @model_validator(mode="after")
    def validate_disposition_metadata(self) -> FindingLifecycleRecordV2:
        if self.disposition is FindingDispositionV2.DISMISSED:
            if self.justification is None or self.decided_by is None or not self.evidence:
                raise ValueError("dismissed findings require justification, owner, and evidence")
        elif self.disposition is FindingDispositionV2.FIXED and not self.evidence:
            raise ValueError("fixed findings require commit or test evidence")
        elif self.disposition is FindingDispositionV2.SUPERSEDED and self.superseded_by is None:
            raise ValueError("superseded findings require a successor")
        elif self.superseded_by is not None:
            raise ValueError("superseded_by is valid only for superseded findings")
        return self


class ReadinessBlockerV2(ContractV2Model):
    blocker_id: SafeIdentifier
    reason_code: ReadinessReasonValue
    active: StrictBool
    finding_id: SafeIdentifier | None


class ReviewReadinessV2(ContractV2Model):
    schema_id: Literal["agent-review.review-readiness.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-quality-gate"]
    run_id: Sha256
    head_sha: GitSha
    evaluated_head_sha: GitSha
    state: ReadinessStateValue
    reason_codes: list[ReadinessReasonValue]
    blockers: list[ReadinessBlockerV2]
    findings: list[FindingLifecycleRecordV2]

    @model_validator(mode="after")
    def validate_state_invariants(self) -> ReviewReadinessV2:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be unique")
        blocker_ids = [blocker.blocker_id for blocker in self.blockers]
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(blocker_ids) != len(set(blocker_ids)):
            raise ValueError("blocker IDs must be unique")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding IDs must be unique")

        active_blockers = [blocker for blocker in self.blockers if blocker.active]
        active_reasons = {blocker.reason_code for blocker in active_blockers}
        reasons = set(self.reason_codes)
        heads_differ = self.head_sha != self.evaluated_head_sha

        if self.state is ReadinessStateV2.STALE:
            if not heads_differ or reasons or active_blockers:
                raise ValueError("stale requires a divergent HEAD and no active blockers or reason codes")
            return self
        if heads_differ:
            raise ValueError("only stale may refer to a different evaluated HEAD")

        active_findings = [
            finding
            for finding in self.findings
            if finding.actionable
            and finding.disposition in {FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED}
        ]
        if self.state is ReadinessStateV2.READY:
            if reasons or active_blockers or active_findings:
                raise ValueError("ready cannot contain reasons, active blockers, or actionable findings")
            return self

        if not reasons or reasons != active_reasons:
            raise ValueError("blocked and manual states require matching active reason codes")

        if self.state is ReadinessStateV2.BLOCKED_CODE:
            if reasons != {ReadinessReasonV2.CONFIRMED_CODE_FINDING}:
                raise ValueError("blocked_code accepts only confirmed_code_finding")
            confirmed = {
                finding.finding_id
                for finding in active_findings
                if finding.disposition is FindingDispositionV2.CONFIRMED
            }
            blocker_findings = {
                blocker.finding_id
                for blocker in active_blockers
                if blocker.reason_code is ReadinessReasonV2.CONFIRMED_CODE_FINDING
            }
            if not confirmed or None in blocker_findings or not blocker_findings <= confirmed:
                raise ValueError("blocked_code requires a confirmed actionable finding")
        elif self.state is ReadinessStateV2.BLOCKED_PIPELINE:
            allowed = {
                ReadinessReasonV2.SCHEMA_FAILURE,
                ReadinessReasonV2.TRANSPORT_FAILURE,
                ReadinessReasonV2.COVERAGE_FAILURE,
                ReadinessReasonV2.POLICY_FAILURE,
            }
            if not reasons <= allowed or active_findings:
                raise ValueError("blocked_pipeline accepts pipeline failures only")
        elif self.state is ReadinessStateV2.MANUAL_REQUIRED:
            allowed = {
                ReadinessReasonV2.COVERAGE_FAILURE,
                ReadinessReasonV2.POLICY_FAILURE,
                ReadinessReasonV2.MODEL_UNCERTAINTY,
            }
            if not reasons <= allowed or active_findings:
                raise ValueError("manual_required accepts uncertainty or incomplete evidence only")
        return self
