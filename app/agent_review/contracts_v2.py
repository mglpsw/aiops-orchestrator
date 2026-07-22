"""Strict, offline-only AgentReview v2 contract foundation.

This module deliberately has no integration with the v1 planner, payload builder,
parser, synthesizer, or quality gate.  It freezes the data contracts that those
consumers may adopt through explicit future migrations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
)

from app.agent_review.redaction import sanitize_artifact_value


RUN_SCHEMA_V2 = "agent-review.run.v2"
CHUNK_PAYLOAD_SCHEMA_V2 = "agent-review.chunk-payload.v2"
CHUNK_RESPONSE_ENVELOPE_SCHEMA_V2 = "agent-review.chunk-response-envelope.v2"
CHUNK_RESPONSE_SCHEMA_V2 = "agent-review.chunk-response.v2"
TARGET_PROFILE_SCHEMA_V2 = "agent-review.target-profile.v2"
REVIEW_READINESS_SCHEMA_V2 = "agent-review.review-readiness.v2"
RESPONSE_CONTRACT_INVALID_REASON_V2 = "response_contract_invalid"
PAYLOAD_CONTRACT_INVALID_REASON_V2 = "payload_contract_invalid"
RESPONSE_SCOPE_MISMATCH_REASON_V2 = "response_scope_mismatch"

_RUN_IDENTITY_FIELDS = (
    "repo",
    "pr_number",
    "base_sha",
    "head_sha",
    "tested_merge_sha",
    "toolrepo_sha",
    "profile_hash",
    "policy_hash",
    "manifest_hash",
    "evidence_hash",
)
_REPOSITORY_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
_RFC3339_SECONDS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_GIT_BRANCH_FORBIDDEN_CHARACTERS = frozenset(" ~^:?*[\\")
_GIT_BRANCH_SCHEMA_PATTERN = r"^[^\u0000-\u0020\u007f~^:?*\\\[]+$"
_HTTP_ROUTE_LITERAL_RE = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+/[^\s\"'<>?#;]*"
)
_VERSIONED_ROUTE_LITERAL_RE = re.compile(
    r"(?P<prefix>^|[\s(\[{,:\"'])(?P<route>/(?:api|v[0-9]+)"
    r"(?=/|[?#;\s\"'<>]|$)[^\s\"'<>?#;]*)"
)


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


def _validate_normalized_relative_posix(value: str, *, kind: str) -> str:
    if value != value.strip() or not value or "\\" in value:
        raise ValueError(f"{kind} must be a non-empty normalized POSIX relative value")
    if value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError("absolute and home-relative values are forbidden")
    raw_parts = value.split("/")
    if ".." in raw_parts:
        raise ValueError("parent traversal is forbidden")
    if any(part in {"", "."} for part in raw_parts):
        raise ValueError(f"{kind} contains an empty or dot path component")
    if PurePosixPath(value).as_posix() != value:
        raise ValueError(f"{kind} must use its normalized POSIX spelling")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("control characters are forbidden")
    _reject_sensitive_value(value)
    return value


def _validate_relative_path(value: str) -> str:
    return _validate_normalized_relative_posix(value, kind="path")


def _validate_relative_pattern(value: str) -> str:
    return _validate_normalized_relative_posix(value, kind="pattern")


def _validate_safe_identifier(value: str) -> str:
    if value != value.strip() or not _SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError("identifier contains unsupported characters")
    _reject_sensitive_value(value)
    return value


def _validate_branch_name(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("branch name must be non-empty with no surrounding whitespace")
    if value.startswith(("/", "-")) or value.endswith(("/", ".")) or "//" in value:
        raise ValueError("branch name has an invalid boundary or empty component")
    if value in {"@", "HEAD"} or ".." in value or "@{" in value:
        raise ValueError("branch name contains an ambiguous revision expression")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or character in _GIT_BRANCH_FORBIDDEN_CHARACTERS
        for character in value
    ):
        raise ValueError("branch name contains a character forbidden by Git")
    components = value.split("/")
    if any(component.startswith(".") or component.endswith(".lock") for component in components):
        raise ValueError("branch components cannot start with dot or end with .lock")
    _reject_sensitive_value(value)
    return value


def _validate_safe_text(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("text must be non-empty and have no surrounding whitespace")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("text must be valid UTF-8") from exc
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ValueError("text contains control or non-printing characters")
    _reject_sensitive_material_v2(value)
    return value


def _validate_timestamp(value: str) -> str:
    if not _RFC3339_SECONDS_RE.fullmatch(value):
        raise ValueError("timestamp must be canonical RFC 3339 UTC seconds")
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    return value


def _reject_sensitive_value(value: str) -> None:
    if sanitize_artifact_value(value) != value:
        raise ValueError("value contains a secret-like token or local path")


def _neutralize_route_literals_v2(value: object) -> object:
    if isinstance(value, dict):
        return {key: _neutralize_route_literals_v2(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_neutralize_route_literals_v2(item) for item in value]
    if isinstance(value, tuple):
        return [_neutralize_route_literals_v2(item) for item in value]
    if not isinstance(value, str):
        return value

    def neutralize_route(route: str) -> str:
        return route.replace("/", " route-separator ")

    def neutralize_http_route(match: re.Match[str]) -> str:
        return neutralize_route(match.group(0))

    def neutralize_versioned_route(match: re.Match[str]) -> str:
        return match.group("prefix") + neutralize_route(match.group("route"))

    material = _HTTP_ROUTE_LITERAL_RE.sub(neutralize_http_route, value)
    return _VERSIONED_ROUTE_LITERAL_RE.sub(neutralize_versioned_route, material)


def _reject_sensitive_material_v2(value: object) -> None:
    route_neutralized = _neutralize_route_literals_v2(value)
    if sanitize_artifact_value(route_neutralized) != route_neutralized:
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
    AfterValidator(_validate_relative_pattern),
]
SafeIdentifier = Annotated[
    StrictStr,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
    AfterValidator(_validate_safe_identifier),
]
BranchName = Annotated[
    StrictStr,
    Field(
        min_length=1,
        description="Git branch name validated with the git-check-ref-format --branch rules.",
        json_schema_extra={
            "not": {"const": "HEAD"},
            "pattern": _GIT_BRANCH_SCHEMA_PATTERN,
            "x-git-ref-format": "--branch",
        },
    ),
    AfterValidator(_validate_branch_name),
]
SafeText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=512),
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


def _require_json_value(value: object, *, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _require_json_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string object key")
            _require_json_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"{path} contains a non-JSON value of type {type(value).__name__}")


def _canonical_json_bytes(value: object) -> bytes:
    """Serialize a JSON value into the canonical UTF-8 form used by v2 hashes."""

    _require_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _validate_manifest_object_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("manifest contains a non-string object key")
            _reject_sensitive_value(key)
            _validate_manifest_object_keys(item)
    elif isinstance(value, list):
        for item in value:
            _validate_manifest_object_keys(item)


def canonical_manifest_bytes_v2(manifest: Mapping[str, object]) -> bytes:
    """Return canonical bytes for an independently material v2 manifest.

    The manifest contract itself belongs to the later multi-chunk delivery.  Its
    identity is already frozen here: the complete sanitized JSON object is
    encoded with the same deterministic JSON rules as every other v2 hash.
    """

    material = dict(manifest)
    _validate_manifest_object_keys(material)
    _reject_sensitive_material_v2(material)
    return _canonical_json_bytes(material)


def compute_manifest_hash_v2(manifest: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_manifest_bytes_v2(manifest)).hexdigest()


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


class CoverageDegradationReasonV2(str, Enum):
    ARTIFACT_MISSING = "artifact_missing"
    BUDGET_EXHAUSTED = "budget_exhausted"
    TRANSPORT_FAILURE = "transport_failure"
    SCHEMA_FAILURE = "schema_failure"
    MODEL_UNCERTAINTY = "model_uncertainty"


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


class PullRequestStateV2(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class RequiredCheckConclusionV2(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"
    MISSING = "missing"


class ReadinessReasonV2(str, Enum):
    SCHEMA_FAILURE = "schema_failure"
    TRANSPORT_FAILURE = "transport_failure"
    COVERAGE_FAILURE = "coverage_failure"
    POLICY_FAILURE = "policy_failure"
    MODEL_UNCERTAINTY = "model_uncertainty"
    FINDING_CONFIRMATION_REQUIRED = "finding_confirmation_required"
    CONFIRMED_CODE_FINDING = "confirmed_code_finding"
    HEAD_MISMATCH = "head_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"


SemanticGroupValue = Annotated[SemanticGroupV2, Field(strict=False)]
CoverageStateValue = Annotated[CoverageStateV2, Field(strict=False)]
CoverageDegradationReasonValue = Annotated[CoverageDegradationReasonV2, Field(strict=False)]
FindingSeverityValue = Annotated[FindingSeverityV2, Field(strict=False)]
FindingConfidenceValue = Annotated[FindingConfidenceV2, Field(strict=False)]
FindingDispositionValue = Annotated[FindingDispositionV2, Field(strict=False)]
ReadinessStateValue = Annotated[ReadinessStateV2, Field(strict=False)]
ReadinessReasonValue = Annotated[ReadinessReasonV2, Field(strict=False)]
PullRequestStateValue = Annotated[PullRequestStateV2, Field(strict=False)]
RequiredCheckConclusionValue = Annotated[RequiredCheckConclusionV2, Field(strict=False)]


class RunIdentityV2(ContractV2Model):
    repo: Repository
    pr_number: PositiveInt
    base_sha: GitSha
    head_sha: GitSha
    tested_merge_sha: GitSha
    toolrepo_sha: GitSha
    profile_hash: Sha256
    policy_hash: Sha256
    manifest_hash: Sha256
    evidence_hash: Sha256


def canonical_run_identity_bytes(identity: RunIdentityV2) -> bytes:
    """Return the exact bytes hashed for an AgentReview v2 ``run_id``.

    The bytes are UTF-8 encoding of the ten-field JSON object returned by
    ``RunIdentityV2.model_dump(mode="json")`` with Unicode left unescaped,
    lexicographically sorted keys, separators ``(',', ':')``, and non-finite
    numbers rejected.  No delimiter concatenation, timestamp, local path,
    dictionary insertion order, clock, or random value participates.
    """

    payload = identity.model_dump(mode="json")
    if set(payload) != set(_RUN_IDENTITY_FIELDS) or len(payload) != len(_RUN_IDENTITY_FIELDS):
        raise RuntimeError("run identity fields diverged from the frozen v2 contract")
    return _canonical_json_bytes(payload)


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


class CoverageDegradationV2(ContractV2Model):
    reason_code: CoverageDegradationReasonValue
    affected_files: list[RelativePath]
    detail: SafeText

    @model_validator(mode="after")
    def validate_affected_files(self) -> CoverageDegradationV2:
        if not self.affected_files:
            raise ValueError("a degradation cause must identify at least one affected file")
        if len(self.affected_files) != len(set(self.affected_files)):
            raise ValueError("degradation affected_files must be unique")
        return self


class ChunkCoverageV2(ContractV2Model):
    status: CoverageStateValue
    expected_files: list[RelativePath]
    reviewed_files: list[RelativePath]
    partially_reviewed_files: list[RelativePath]
    missing_files: list[RelativePath]
    must_review_files: list[RelativePath]
    missing_must_review_files: list[RelativePath]
    degradation_causes: list[CoverageDegradationV2]

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
        if not (reviewed | partial | missing) <= expected:
            raise ValueError("coverage partitions must be subsets of expected_files")
        if reviewed & partial or reviewed & missing or partial & missing:
            raise ValueError("coverage partitions must be disjoint")
        partition = reviewed | partial | missing
        if partition != expected:
            omitted = sorted(expected - partition)
            raise ValueError(f"coverage partitions omit expected_files: {omitted}")
        if not must_review <= expected or not missing_must_review <= must_review:
            raise ValueError("must-review coverage must be a subset of expected coverage")
        if missing_must_review != must_review & (partial | missing):
            raise ValueError("missing_must_review_files contradict the coverage partitions")
        if self.status is CoverageStateV2.COMPLETE:
            if reviewed != expected or partial or missing or missing_must_review or self.degradation_causes:
                raise ValueError("complete coverage requires every expected file to be reviewed")
        elif self.status is CoverageStateV2.PARTIAL:
            if not (partial or missing):
                raise ValueError("partial coverage requires a partial or missing file")
            if self.degradation_causes:
                raise ValueError("partial coverage cannot carry degraded-state causes")
        elif self.status is CoverageStateV2.DEGRADED:
            affected = partial | missing
            if not affected or not self.degradation_causes:
                raise ValueError("degraded coverage requires affected files and structured causes")
            caused_files: set[str] = set()
            cause_keys: set[tuple[CoverageDegradationReasonV2, tuple[str, ...]]] = set()
            for cause in self.degradation_causes:
                files = set(cause.affected_files)
                if not files <= affected:
                    raise ValueError("degradation causes may reference only partial or missing files")
                caused_files.update(files)
                key = (cause.reason_code, tuple(sorted(files)))
                if key in cause_keys:
                    raise ValueError("degradation causes must be unique")
                cause_keys.add(key)
            if caused_files != affected:
                raise ValueError("degradation causes must account for every partial or missing file")
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


class ChunkPayloadMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.chunk-payload.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-build-payloads"]
    run_id: Sha256
    identity: RunIdentityV2
    chunk_id: SafeIdentifier
    semantic_group: SemanticGroupValue
    coverage: ChunkCoverageV2
    artifact_references: list[PayloadArtifactReferenceV2]
    contract_references: list[PayloadContractReferenceV2]

    @model_validator(mode="after")
    def validate_identity_and_references(self) -> ChunkPayloadMaterialV2:
        if self.run_id != compute_run_id(self.identity):
            raise ValueError("run_id does not match the canonical run identity")
        if len({item.artifact_id for item in self.artifact_references}) != len(self.artifact_references):
            raise ValueError("artifact references must be unique")
        contract_keys = {(item.contract_id, item.contract_version) for item in self.contract_references}
        if len(contract_keys) != len(self.contract_references):
            raise ValueError("contract references must be unique")
        return self


class ChunkPayloadV2(ChunkPayloadMaterialV2):
    payload_sha256: Sha256

    @model_validator(mode="after")
    def validate_payload_hash(self) -> ChunkPayloadV2:
        expected = compute_payload_sha256_v2(self)
        if self.payload_sha256 != expected:
            raise ValueError("payload_sha256 does not match the canonical payload material")
        return self


def _chunk_payload_material(value: ChunkPayloadMaterialV2 | Mapping[str, object]) -> dict[str, Any]:
    if isinstance(value, ChunkPayloadMaterialV2):
        return value.model_dump(mode="json", exclude={"payload_sha256"})
    if not isinstance(value, Mapping):
        raise TypeError("chunk payload must be a model or mapping")
    raw = dict(value)
    raw.pop("payload_sha256", None)
    material = ChunkPayloadMaterialV2.model_validate(raw)
    return material.model_dump(mode="json")


def canonical_chunk_payload_bytes_v2(
    value: ChunkPayloadMaterialV2 | Mapping[str, object],
) -> bytes:
    """Hash preimage: every validated payload field except ``payload_sha256``."""

    return _canonical_json_bytes(_chunk_payload_material(value))


def compute_payload_sha256_v2(value: ChunkPayloadMaterialV2 | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_chunk_payload_bytes_v2(value)).hexdigest()


def verify_payload_sha256_v2(payload: ChunkPayloadV2) -> None:
    encoded = _canonical_json_bytes(payload.model_dump(mode="json"))
    ChunkPayloadV2.model_validate_json(encoded, strict=True)


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
    response_received: StrictBool
    response_sha256: Sha256 | None


class ChunkResponseSuccessEnvelopeV2(_ChunkResponseEnvelopeBaseV2):
    status: Literal["success"]
    result: ChunkReviewResultV2

    @model_validator(mode="after")
    def validate_success_finish_reason(self) -> ChunkResponseSuccessEnvelopeV2:
        if self.finish_reason is not FinishReasonV2.STOP:
            raise ValueError("success requires the conclusive finish_reason=stop")
        if not self.response_received or self.response_sha256 is None:
            raise ValueError("success requires a received response and its canonical hash")
        if self.response_sha256 != compute_response_sha256_v2(self):
            raise ValueError("response_sha256 does not match the canonical sanitized response")
        return self


class ChunkResponseErrorEnvelopeV2(_ChunkResponseEnvelopeBaseV2):
    status: Literal["error"]
    error: SanitizedResponseErrorV2

    @model_validator(mode="after")
    def validate_error_finish_reason(self) -> ChunkResponseErrorEnvelopeV2:
        if self.finish_reason is FinishReasonV2.STOP:
            raise ValueError("an error envelope cannot have finish_reason=stop")
        if not self.response_received:
            if self.finish_reason is not FinishReasonV2.ERROR:
                raise ValueError("no-response failures require finish_reason=error")
            if self.error.reason_code is not ResponseErrorReasonV2.TRANSPORT_FAILURE:
                raise ValueError("no-response failures require transport_failure")
            if self.response_sha256 is not None:
                raise ValueError("a missing response cannot have response_sha256")
            return self
        if self.response_sha256 is None:
            raise ValueError("a received error response requires response_sha256")
        if self.response_sha256 != compute_response_sha256_v2(self):
            raise ValueError("response_sha256 does not match the canonical sanitized response")
        return self


ChunkResponseEnvelopeValueV2: TypeAlias = Annotated[
    ChunkResponseSuccessEnvelopeV2 | ChunkResponseErrorEnvelopeV2,
    Field(discriminator="status"),
]


class ChunkResponseEnvelopeV2(RootModel[ChunkResponseEnvelopeValueV2]):
    """Named root model used for stable validation and JSON Schema refs."""

    model_config = ConfigDict(frozen=True)


def canonical_response_envelope_bytes_v2(
    value: ChunkResponseEnvelopeValueV2 | Mapping[str, object],
) -> bytes:
    """Hash the sanitized envelope fields, excluding only ``response_sha256``.

    This is deliberately not a hash of a raw provider body.  Raw responses,
    prompts, headers, credentials, and local paths are outside this contract.
    """

    if isinstance(value, (ChunkResponseSuccessEnvelopeV2, ChunkResponseErrorEnvelopeV2)):
        material = value.model_dump(mode="json", exclude={"response_sha256"})
    elif isinstance(value, Mapping):
        material = dict(value)
        material.pop("response_sha256", None)
    else:
        raise TypeError("response envelope must be a validated model or mapping")
    _reject_sensitive_material_v2(material)
    return _canonical_json_bytes(material)


def compute_response_sha256_v2(
    value: ChunkResponseEnvelopeValueV2 | Mapping[str, object],
) -> str:
    return hashlib.sha256(canonical_response_envelope_bytes_v2(value)).hexdigest()


def validate_chunk_response_envelope_v2(value: object) -> ChunkResponseEnvelopeValueV2:
    """Validate a JSON-compatible response envelope with strict JSON semantics."""

    if isinstance(value, (ChunkResponseSuccessEnvelopeV2, ChunkResponseErrorEnvelopeV2)):
        value = value.model_dump(mode="json")
    if isinstance(value, (str, bytes, bytearray)):
        return ChunkResponseEnvelopeV2.model_validate_json(value, strict=True).root
    encoded = _canonical_json_bytes(value)
    return ChunkResponseEnvelopeV2.model_validate_json(encoded, strict=True).root


class ResponseBindingV2(ContractV2Model):
    payload: ChunkPayloadV2


class ResponseBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_response_binding_v2(
    envelope: ChunkResponseEnvelopeValueV2,
    expected: ResponseBindingV2 | ChunkPayloadV2,
) -> None:
    """Revalidate both hashes, then bind a response before findings are read."""

    try:
        envelope = validate_chunk_response_envelope_v2(envelope)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ResponseBindingError(RESPONSE_CONTRACT_INVALID_REASON_V2) from exc

    payload = expected.payload if isinstance(expected, ResponseBindingV2) else expected
    try:
        verify_payload_sha256_v2(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ResponseBindingError(PAYLOAD_CONTRACT_INVALID_REASON_V2) from exc

    comparisons = (
        (envelope.run_id, payload.run_id, "run_id_mismatch"),
        (envelope.chunk_id, payload.chunk_id, "chunk_id_mismatch"),
        (envelope.payload_sha256, payload.payload_sha256, "payload_sha256_mismatch"),
        (envelope.head_sha, payload.identity.head_sha, "head_sha_mismatch"),
    )
    for observed, wanted, reason_code in comparisons:
        if observed != wanted:
            raise ResponseBindingError(reason_code)

    if isinstance(envelope, ChunkResponseSuccessEnvelopeV2):
        payload_files = set(payload.coverage.expected_files)
        response_files = set(envelope.result.coverage.expected_files)
        finding_files = {finding.file_path for finding in envelope.result.findings}
        if response_files != payload_files or not finding_files <= payload_files:
            raise ResponseBindingError(RESPONSE_SCOPE_MISMATCH_REASON_V2)


class TargetIdentityV2(ContractV2Model):
    repo: Repository
    default_branch: BranchName


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
    fail_closed: Literal[True]
    redaction_required: Literal[True]
    allow_partial_coverage: Literal[False]
    required_checks: list[SafeText]
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
    head_sha: GitSha

    @model_validator(mode="after")
    def validate_reference_kind(self) -> DispositionEvidenceV2:
        if (
            self.kind is DispositionEvidenceKindV2.COMMIT
            and not _GIT_SHA_RE.fullmatch(self.reference)
        ):
            raise ValueError("commit evidence reference must be a canonical Git SHA")
        return self


class FindingLifecycleRecordV2(ContractV2Model):
    finding_id: SafeIdentifier
    severity: FindingSeverityValue
    observed_at_head_sha: GitSha
    disposition: FindingDispositionValue
    actionable: StrictBool
    justification: SafeText | None
    decided_by: SafeIdentifier | None
    decided_at_head_sha: GitSha | None
    evidence: list[DispositionEvidenceV2]
    superseded_by: SafeIdentifier | None

    @model_validator(mode="after")
    def validate_disposition_metadata(self) -> FindingLifecycleRecordV2:
        active = self.disposition in {FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED}
        if self.actionable is not active:
            raise ValueError("new and confirmed findings are actionable; terminal findings are not")
        if self.disposition is FindingDispositionV2.NEW:
            if (
                self.justification is not None
                or self.decided_by is not None
                or self.decided_at_head_sha is not None
                or self.evidence
                or self.superseded_by is not None
            ):
                raise ValueError("new findings cannot carry disposition decision metadata")
            return self

        if self.decided_by is None or self.decided_at_head_sha is None:
            raise ValueError("disposition decisions require an owner and decision HEAD")
        if self.disposition is FindingDispositionV2.DISMISSED:
            if self.justification is None or not self.evidence:
                raise ValueError("dismissed findings require justification and typed evidence")
        elif self.disposition is FindingDispositionV2.FIXED and not self.evidence:
            raise ValueError("fixed findings require commit or test evidence")
        if self.disposition is FindingDispositionV2.SUPERSEDED:
            if self.superseded_by is None or self.superseded_by == self.finding_id:
                raise ValueError("superseded findings require a different successor")
        elif self.superseded_by is not None:
            raise ValueError("superseded_by is valid only for superseded findings")
        evidence_keys = [(item.kind, item.reference, item.head_sha) for item in self.evidence]
        if len(evidence_keys) != len(set(evidence_keys)):
            raise ValueError("disposition evidence must be unique")
        return self


class ReadinessBlockerV2(ContractV2Model):
    blocker_id: SafeIdentifier
    reason_code: ReadinessReasonValue
    active: StrictBool
    finding_id: SafeIdentifier | None


class RequiredCheckResultV2(ContractV2Model):
    check_name: SafeText
    required: Literal[True]
    deterministic: Literal[True]
    conclusion: RequiredCheckConclusionValue
    head_sha: GitSha


class PipelineDegradationCauseV2(ContractV2Model):
    reason_code: ReadinessReasonValue
    component: SafeIdentifier
    detail: SafeText

    @model_validator(mode="after")
    def validate_reason(self) -> PipelineDegradationCauseV2:
        allowed = {
            ReadinessReasonV2.SCHEMA_FAILURE,
            ReadinessReasonV2.TRANSPORT_FAILURE,
            ReadinessReasonV2.COVERAGE_FAILURE,
            ReadinessReasonV2.POLICY_FAILURE,
            ReadinessReasonV2.MODEL_UNCERTAINTY,
        }
        if self.reason_code not in allowed:
            raise ValueError("pipeline degradation requires a pipeline or uncertainty reason")
        return self


class PipelineAssessmentV2(ContractV2Model):
    degraded: StrictBool
    causes: list[PipelineDegradationCauseV2]

    @model_validator(mode="after")
    def validate_degradation(self) -> PipelineAssessmentV2:
        if self.degraded != bool(self.causes):
            raise ValueError("pipeline degraded must be exactly represented by structured causes")
        keys = [(cause.reason_code, cause.component) for cause in self.causes]
        if len(keys) != len(set(keys)):
            raise ValueError("pipeline degradation causes must be unique")
        return self


class ReviewReadinessV2(ContractV2Model):
    schema_id: Literal["agent-review.review-readiness.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-quality-gate"]
    run_id: Sha256
    identity: RunIdentityV2
    evaluated_run_id: Sha256
    evaluated_identity: RunIdentityV2
    head_sha: GitSha
    evaluated_head_sha: GitSha
    pr_state: PullRequestStateValue
    checks: list[RequiredCheckResultV2]
    coverage: ChunkCoverageV2
    pipeline: PipelineAssessmentV2
    state: ReadinessStateValue
    reason_codes: list[ReadinessReasonValue]
    blockers: list[ReadinessBlockerV2]
    findings: list[FindingLifecycleRecordV2]

    @model_validator(mode="after")
    def validate_state_invariants(self) -> ReviewReadinessV2:
        if self.run_id != compute_run_id(self.identity):
            raise ValueError("run_id does not match the canonical readiness identity")
        if self.evaluated_run_id != compute_run_id(self.evaluated_identity):
            raise ValueError("evaluated_run_id does not match evaluated_identity")
        if self.head_sha != self.identity.head_sha:
            raise ValueError("readiness head_sha must match the expected run identity HEAD")
        if self.evaluated_head_sha != self.evaluated_identity.head_sha:
            raise ValueError("evaluated_head_sha must match evaluated_identity")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason_codes must be unique")
        blocker_ids = [blocker.blocker_id for blocker in self.blockers]
        finding_ids = [finding.finding_id for finding in self.findings]
        check_names = [check.check_name for check in self.checks]
        if len(blocker_ids) != len(set(blocker_ids)):
            raise ValueError("blocker IDs must be unique")
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding IDs must be unique")
        if len(check_names) != len(set(check_names)):
            raise ValueError("required check names must be unique")
        if any(check.head_sha != self.evaluated_head_sha for check in self.checks):
            raise ValueError("check results must be bound to the evaluated HEAD")
        for finding in self.findings:
            if finding.observed_at_head_sha != self.evaluated_head_sha:
                raise ValueError("findings must be observed on the evaluated HEAD")
            if (
                finding.disposition is not FindingDispositionV2.NEW
                and finding.decided_at_head_sha != self.evaluated_head_sha
            ):
                raise ValueError("finding decisions must be revalidated on the evaluated HEAD")
            if any(item.head_sha != self.evaluated_head_sha for item in finding.evidence):
                raise ValueError("disposition evidence must be revalidated on the evaluated HEAD")

        findings_by_id = {finding.finding_id: finding for finding in self.findings}
        for blocker in self.blockers:
            if blocker.reason_code in {
                ReadinessReasonV2.CONFIRMED_CODE_FINDING,
                ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED,
            }:
                if blocker.finding_id is None or blocker.finding_id not in findings_by_id:
                    raise ValueError("finding blockers require a valid finding_id")
                finding = findings_by_id[blocker.finding_id]
                if blocker.reason_code is ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED and not (
                    finding.disposition is FindingDispositionV2.NEW
                    and finding.actionable
                    and finding.severity
                    in {FindingSeverityV2.P0, FindingSeverityV2.P1, FindingSeverityV2.P2}
                ):
                    raise ValueError(
                        "finding_confirmation_required requires an actionable new P0/P1/P2 finding"
                    )
            elif blocker.finding_id is not None:
                raise ValueError("pipeline, manual, and stale blockers cannot point to findings")

        active_blockers = [blocker for blocker in self.blockers if blocker.active]
        active_reasons = {blocker.reason_code for blocker in active_blockers}
        reasons = set(self.reason_codes)
        heads_differ = self.head_sha != self.evaluated_head_sha
        expected_context = self.identity.model_dump(mode="json", exclude={"head_sha"})
        evaluated_context = self.evaluated_identity.model_dump(mode="json", exclude={"head_sha"})
        identities_differ = expected_context != evaluated_context

        if self.state is ReadinessStateV2.STALE:
            expected_reasons: set[ReadinessReasonV2] = set()
            if heads_differ:
                expected_reasons.add(ReadinessReasonV2.HEAD_MISMATCH)
            if identities_differ:
                expected_reasons.add(ReadinessReasonV2.IDENTITY_MISMATCH)
            if not expected_reasons or reasons != expected_reasons or active_blockers:
                raise ValueError("stale requires explicit HEAD or identity divergence reasons")
            return self
        if heads_differ or identities_differ:
            raise ValueError("only stale may refer to a different evaluated HEAD or run identity")

        blocking_findings = [
            finding
            for finding in self.findings
            if finding.actionable
            and finding.disposition in {FindingDispositionV2.NEW, FindingDispositionV2.CONFIRMED}
            and finding.severity in {FindingSeverityV2.P0, FindingSeverityV2.P1, FindingSeverityV2.P2}
        ]
        if self.state is ReadinessStateV2.READY:
            if self.pr_state is not PullRequestStateV2.OPEN:
                raise ValueError("ready requires an open, non-merged pull request")
            if not self.checks or any(
                check.conclusion is not RequiredCheckConclusionV2.SUCCESS for check in self.checks
            ):
                raise ValueError("ready requires every deterministic required check to be green")
            if self.coverage.status is not CoverageStateV2.COMPLETE or self.coverage.missing_must_review_files:
                raise ValueError("ready requires complete total and must-review coverage")
            if self.pipeline.degraded:
                raise ValueError("ready cannot use a degraded pipeline result")
            if reasons or active_blockers or blocking_findings:
                raise ValueError("ready cannot contain reasons, active blockers, or blocking findings")
            return self

        if not reasons or reasons != active_reasons:
            raise ValueError("blocked and manual states require matching active reason codes")

        if self.state is ReadinessStateV2.BLOCKED_CODE:
            allowed_pipeline_reasons = {
                ReadinessReasonV2.SCHEMA_FAILURE,
                ReadinessReasonV2.TRANSPORT_FAILURE,
                ReadinessReasonV2.COVERAGE_FAILURE,
                ReadinessReasonV2.POLICY_FAILURE,
                ReadinessReasonV2.MODEL_UNCERTAINTY,
            }
            pipeline_reasons = reasons - {ReadinessReasonV2.CONFIRMED_CODE_FINDING}
            cause_reasons = {cause.reason_code for cause in self.pipeline.causes}
            if (
                ReadinessReasonV2.CONFIRMED_CODE_FINDING not in reasons
                or not pipeline_reasons <= allowed_pipeline_reasons
                or cause_reasons != pipeline_reasons
                or self.pipeline.degraded != bool(pipeline_reasons)
            ):
                raise ValueError(
                    "blocked_code requires confirmed findings and matching structured pipeline causes"
                )
            confirmed = {
                finding.finding_id
                for finding in blocking_findings
                if finding.disposition is FindingDispositionV2.CONFIRMED
            }
            blocker_findings = [
                blocker.finding_id
                for blocker in active_blockers
                if blocker.reason_code is ReadinessReasonV2.CONFIRMED_CODE_FINDING
            ]
            blocker_finding_set = set(blocker_findings)
            if (
                not confirmed
                or None in blocker_finding_set
                or blocker_finding_set != confirmed
                or len(blocker_findings) != len(blocker_finding_set)
            ):
                raise ValueError(
                    "blocked_code requires one active blocker per confirmed actionable P0/P1/P2 finding"
                )
        elif self.state is ReadinessStateV2.BLOCKED_PIPELINE:
            allowed = {
                ReadinessReasonV2.SCHEMA_FAILURE,
                ReadinessReasonV2.TRANSPORT_FAILURE,
                ReadinessReasonV2.COVERAGE_FAILURE,
                ReadinessReasonV2.POLICY_FAILURE,
            }
            cause_reasons = {cause.reason_code for cause in self.pipeline.causes}
            if not reasons <= allowed or not self.pipeline.degraded or cause_reasons != reasons:
                raise ValueError("blocked_pipeline accepts pipeline failures only")
            if any(
                finding.disposition is FindingDispositionV2.CONFIRMED
                for finding in blocking_findings
            ):
                raise ValueError("blocked_pipeline cannot mask a confirmed code-blocking finding")
        elif self.state is ReadinessStateV2.MANUAL_REQUIRED:
            allowed = {
                ReadinessReasonV2.COVERAGE_FAILURE,
                ReadinessReasonV2.POLICY_FAILURE,
                ReadinessReasonV2.MODEL_UNCERTAINTY,
                ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED,
            }
            cause_reasons = {cause.reason_code for cause in self.pipeline.causes}
            pipeline_reasons = reasons - {ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED}
            if (
                not reasons <= allowed
                or cause_reasons != pipeline_reasons
                or self.pipeline.degraded != bool(pipeline_reasons)
            ):
                raise ValueError(
                    "manual_required requires confirmation and/or matching structured pipeline causes"
                )
            if any(
                finding.disposition is FindingDispositionV2.CONFIRMED
                for finding in blocking_findings
            ):
                raise ValueError("manual_required cannot mask a confirmed code-blocking finding")
            new_findings = {
                finding.finding_id
                for finding in blocking_findings
                if finding.disposition is FindingDispositionV2.NEW
            }
            confirmation_blockers = [
                blocker.finding_id
                for blocker in active_blockers
                if blocker.reason_code is ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED
            ]
            confirmation_finding_ids = set(confirmation_blockers)
            if ReadinessReasonV2.FINDING_CONFIRMATION_REQUIRED in reasons:
                if (
                    not new_findings
                    or None in confirmation_finding_ids
                    or confirmation_finding_ids != new_findings
                    or len(confirmation_blockers) != len(confirmation_finding_ids)
                ):
                    raise ValueError(
                        "manual confirmation blockers must identify every pending new P0/P1/P2 finding"
                    )
            elif new_findings:
                raise ValueError("manual_required must represent pending finding confirmation")
        return self
