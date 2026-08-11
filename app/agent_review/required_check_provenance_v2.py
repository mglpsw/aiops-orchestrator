"""Provenance sidecar for `RequiredCheckResultV2` (`#201-C0`, C0-2).

## The gap this closes

`#201-B3` ratified a class boundary: `controls(subject, success_signal) =>
not authoritative(success_signal)`. pytest is `subject_code`, so the isolated
executor can never promote it -- `promote_trusted_check_to_required_v2`
refuses `UNTRUSTED_ADVISORY` structurally. But both live target profiles
declare `required_checks: [pytest]`, so an independent authoritative source is
required, and the project overlay already names it: deterministic CI.

Separately, `#217` records a pre-existing defect that `#201-B3` exposed rather
than created: `RequiredCheckResultV2` (`contracts_v2.py:1057`) is deliberately
provenance-agnostic -- `check_name`, `required`, `deterministic`, `conclusion`,
`head_sha`, and no producer identity at all. The quality gate matches required
checks BY NAME. So any object named `pytest` with `conclusion=success`
satisfies the gate, whoever built it.

## Why a sidecar and not a field

`RequiredCheckResultV2` is frozen and published inside
`agent-review.review-readiness.v2.schema.json`. Adding a field would be a
breaking change to a published contract. The additive-sidecar-first policy
`trusted_checks_v2` already documents applies exactly: this module carries the
proof that does not fit in the frozen contract, bound to it 1:1 by digest.

## Binding is by digest, never by name

`compute_required_check_digest_v2` hashes the whole `RequiredCheckResultV2`.
The digest is the JOIN KEY -- not the whole binding. A provenance record whose
digest matches but whose run/head/repository/source does not is still refused
(see `required_check_assembly_v2`). Matching `check_name` alone is precisely
the `#217` defect and is never sufficient anywhere in this subsystem.

## Digest family

Bare 64-hex, the AgentReview `Sha256` form (`contracts_v2.py:238`) -- NOT
CAEM's `sha256:`-prefixed `_DIGEST_RE`. Both families are already frozen in
published artifacts, so the divergence is a decision, not an oversight.

## What this module does not do

It computes no readiness, reads no filesystem, opens no socket, and decides
nothing about whether a source is trustworthy. It is a contract plus two
digests. Authority is derived by the assembler, from the base-owned policy and
the run identity -- never declared by a caller and never asserted by a document
about itself.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    PositiveInt,
    RelativePath,
    Repository,
    SafeIdentifier,
    SafeText,
    Sha256,
    RequiredCheckResultV2,
)
from app.common.strict_json import canonical_json_digest_hex

REQUIRED_CHECK_PROVENANCE_SCHEMA_V2 = "agent-review.required-check-provenance.v2"
REQUIRED_CHECK_PROVENANCE_SOURCE_V2 = "aiops-review-check-provenance"

# -- reason codes (canonical, closed set) -------------------------------------
# One family, one producing concern. The quality gate surfaces these verbatim
# through its existing `exc.reason_code` path rather than translating them into
# a second vocabulary.

PROVENANCE_MISSING_REASON_V2 = "required_check_provenance_missing"
PROVENANCE_INVALID_REASON_V2 = "required_check_provenance_invalid"
PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2 = "required_check_provenance_producer_not_allowlisted"
PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2 = "required_check_provenance_workflow_identity_mismatch"
PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2 = "required_check_provenance_run_identity_mismatch"
PROVENANCE_HEAD_MISMATCH_REASON_V2 = "required_check_provenance_head_mismatch"
PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2 = "required_check_provenance_tested_merge_mismatch"
PROVENANCE_PARENTAGE_MISMATCH_REASON_V2 = "required_check_provenance_parentage_mismatch"
PROVENANCE_REPOSITORY_MISMATCH_REASON_V2 = "required_check_provenance_repository_mismatch"
PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2 = "required_check_provenance_run_attempt_ambiguous"
PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2 = "required_check_provenance_conclusion_unresolved"
PROVENANCE_OBSERVATION_STALE_REASON_V2 = "required_check_provenance_observation_stale"
PROVENANCE_SIDECAR_HASH_MISMATCH_REASON_V2 = "required_check_provenance_sidecar_hash_mismatch"
PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2 = "required_check_provenance_origin_unsupported"
PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2 = "required_check_provenance_subject_result_not_promotable"
PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2 = "required_check_provenance_toolchain_unverified"
PROVENANCE_CONFIG_UNVERIFIED_REASON_V2 = "required_check_provenance_config_unverified"
PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2 = "required_check_provenance_policy_digest_mismatch"

ALL_PROVENANCE_REASON_CODES_V2: tuple[str, ...] = (
    PROVENANCE_MISSING_REASON_V2,
    PROVENANCE_INVALID_REASON_V2,
    PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2,
    PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2,
    PROVENANCE_HEAD_MISMATCH_REASON_V2,
    PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2,
    PROVENANCE_PARENTAGE_MISMATCH_REASON_V2,
    PROVENANCE_REPOSITORY_MISMATCH_REASON_V2,
    PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2,
    PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2,
    PROVENANCE_OBSERVATION_STALE_REASON_V2,
    PROVENANCE_SIDECAR_HASH_MISMATCH_REASON_V2,
    PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2,
    PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2,
    PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2,
    PROVENANCE_CONFIG_UNVERIFIED_REASON_V2,
    PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2,
)


class RequiredCheckProvenanceErrorV2(ValueError):
    """Carries a stable `reason_code` only -- never observation content, never
    a path, never a token. Every raise site in `#201-C0` uses this type."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class RequiredCheckSourceKindV2(str, Enum):
    """The two -- and only two -- authorised origins of a required check.

    They are never joined by `check_name`. An `AUTHORITATIVE_CI` record and a
    `TRUSTED_HOST_PROMOTION` record for the same check name describe different
    events with different trust arguments, and one is never evidence for the
    other."""

    AUTHORITATIVE_CI = "authoritative_ci"
    TRUSTED_HOST_PROMOTION = "trusted_host_promotion"


class SemanticClassV2(str, Enum):
    """What the observation IS (CAEM 3.0 vocabulary, §19.3 of the `#201-B3`
    rev. 4 spec). Borrowing the vocabulary transfers no authority: the CAEM F0
    pin declares `authority_effect: none` and that remains true."""

    AUTHORITATIVE = "authoritative"
    ADVISORY = "advisory"


class AuthorityEffectV2(str, Enum):
    """What the observation may DO. `PROMOTABLE` is a CONSEQUENCE of the
    assembler's validation, never a field a caller chooses."""

    PROMOTABLE = "promotable"
    NONE = "none"


SourceKindValueV2 = Annotated[RequiredCheckSourceKindV2, Field(strict=False)]
SemanticClassValueV2 = Annotated[SemanticClassV2, Field(strict=False)]
AuthorityEffectValueV2 = Annotated[AuthorityEffectV2, Field(strict=False)]


def compute_required_check_digest_v2(result: RequiredCheckResultV2) -> str:
    """The 1:1 join key between a `RequiredCheckResultV2` and its provenance.

    A NEW preimage over the frozen contract's own canonical JSON. It
    deliberately does not reuse, rename, or alter
    `trusted_checks_v2._canonical_json_bytes_v2` or
    `isolated_executor_v2._canonical_json_bytes_v2`: unifying those would move
    `result_sha256`/`artifact_sha256` and break the frozen schema export. That
    duplication is a recorded, deliberate non-change."""

    return canonical_json_digest_hex(result.model_dump(mode="json"))


class RequiredCheckProvenanceV2(ContractV2Model):
    """Additive sidecar proving where one `RequiredCheckResultV2` came from.

    Every cross-field rule below is structural, enforced by the contract
    itself, so an invalid combination cannot exist as an object at all -- the
    assembler never has to remember to check it."""

    schema_id: Literal["agent-review.required-check-provenance.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-check-provenance"]

    check_name: SafeText
    required_check_digest: Sha256

    source_kind: SourceKindValueV2
    semantic_class: SemanticClassValueV2
    authority_effect: AuthorityEffectValueV2
    # CAEM's own invariant. Not a knob: no provenance record may ever confer
    # its authority on anything downstream.
    authority_transfer: Literal[False]

    repository: Repository
    run_id: Sha256
    base_sha: GitSha
    # `review_subject_sha` -- what the review is about. Matches the frozen
    # `RequiredCheckResultV2.head_sha`.
    head_sha: GitSha
    # `execution_subject_sha` -- the tree actually executed. GitHub Actions
    # `pull_request` workflows run a synthetic merge commit, not the PR head,
    # so these two are routinely different and must never be conflated.
    tested_merge_sha: GitSha
    event_type: Literal["pull_request", "pull_request_target", "manual", "replay"]
    event_action: Literal["opened", "reopened", "synchronize", "ready_for_review", "manual", "replay"]

    # CAEM terms, replacing the ad-hoc `producer_identity`/`promotion_policy_digest`.
    verifier_identity: SafeIdentifier
    toolchain_digest: Sha256

    # Workflow identity, decomposed rather than opaque. A check name plus a
    # workflow filename is spoofable -- a different job in the same file, or the
    # same file resolved from a non-base ref, would both satisfy it. All three
    # are required together for `authoritative_ci`.
    workflow_path: RelativePath | None
    workflow_ref: SafeText | None
    job_name: SafeText | None

    # The CI run. Deliberately NOT named `run_id`, which above is AgentReview's
    # own run identity and a different fact entirely.
    ci_run_id: SafeIdentifier | None
    ci_run_attempt: PositiveInt | None
    observed_status: Literal["completed"] | None
    observed_conclusion: Literal["success", "failure"] | None

    observation_digest: Sha256
    policy_source_bytes_digest: Sha256
    policy_source_semantic_digest: Sha256
    provenance_digest: Sha256

    @model_validator(mode="after")
    def validate_source_discipline(self) -> RequiredCheckProvenanceV2:
        ci_fields = (
            self.workflow_path,
            self.workflow_ref,
            self.job_name,
            self.ci_run_id,
            self.ci_run_attempt,
            self.observed_status,
            self.observed_conclusion,
        )

        if self.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI:
            # Partial CI identity is worse than none: it reads as a verified
            # producer while leaving the spoofable half unchecked.
            if any(value is None for value in ci_fields):
                raise ValueError("authoritative_ci provenance requires the complete CI identity")
            if self.semantic_class is not SemanticClassV2.AUTHORITATIVE:
                raise ValueError("authoritative_ci provenance must be semantically authoritative")
        else:
            # A host promotion has no CI run behind it. Carrying CI fields here
            # would let a subject-code observation be dressed as a CI result.
            if any(value is not None for value in ci_fields):
                raise ValueError("trusted_host_promotion provenance must not carry CI identity")

        if (
            self.authority_effect is AuthorityEffectV2.PROMOTABLE
            and self.semantic_class is not SemanticClassV2.AUTHORITATIVE
        ):
            raise ValueError("advisory provenance can never be promotable")

        return self

    @model_validator(mode="after")
    def validate_provenance_digest(self) -> RequiredCheckProvenanceV2:
        if self.provenance_digest != compute_provenance_digest_v2(self):
            raise ValueError("provenance_digest does not match the canonical provenance material")
        return self


def build_required_check_provenance_v2(**fields: object) -> RequiredCheckProvenanceV2:
    """Construct a provenance record, computing `provenance_digest` here.

    Exists so no caller ever computes the self-digest by hand -- a caller that
    got the preimage subtly wrong would produce a record that validates against
    its own mistake. `provenance_digest` must NOT be supplied; passing it is a
    programming error, not a value to honour.

    This is a convenience for the assembler, not an authority shortcut: the
    object it returns still has to survive every check in
    `required_check_assembly_v2`. Building a well-formed sidecar is not the
    same as being entitled to one."""

    if "provenance_digest" in fields:
        raise TypeError("provenance_digest is computed, never supplied")

    draft = RequiredCheckProvenanceV2.model_construct(**fields, provenance_digest="0" * 64)
    return RequiredCheckProvenanceV2(**fields, provenance_digest=compute_provenance_digest_v2(draft))


def _provenance_preimage_v2(value: RequiredCheckProvenanceV2) -> dict:
    return value.model_dump(mode="json", exclude={"provenance_digest"})


def compute_provenance_digest_v2(value: RequiredCheckProvenanceV2) -> str:
    """Self-digest over every field except the digest itself.

    Detects a sidecar edited after assembly. It proves integrity only -- a
    document attesting to its own hash is not evidence of authority, which is
    why the assembler revalidates every field against the base-owned policy and
    the run identity regardless of this digest."""

    return canonical_json_digest_hex(_provenance_preimage_v2(value))
