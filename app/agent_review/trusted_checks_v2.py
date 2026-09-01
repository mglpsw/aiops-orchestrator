"""Trusted-check contracts for AgentReview v2 (#201-A, first slice of #201,
child of the distribution epic #199).

Contract only. Nothing in this module spawns a process, reads a repository
checkout, or touches a filesystem — that is `#201-B1` (offline simulator)
and `#201-B2` (isolated executor), later slices in this same DAG.

## The principle this contract exists to enforce, preserved literally

> O código do HEAD da PR é executado isoladamente como sujeito sob teste. O
> harness, a seleção de checks, os testes de autoridade e o serializador
> vêm da base confiável / host e são pinados por digest. A PR nunca pode
> controlar a evidência que a aprova.

Concretely: a `TrustedCheckPlanV2` names checks by an `AllowlistedCheckCommandV2.
command_token` — a fixed identifier the (future) harness resolves to a
real command from its OWN inventory — never a raw argv string a plan
author could smuggle arbitrary code into. `harness_digest`/`authority_suite_
digest` pin the host-owned code that will eventually run and grade a
check; this module has no field anywhere for a target- or PR-supplied
harness, reporter, or serializer.

## Why `RequiredCheckResultV2` (`contracts_v2.py:1057`) is untouched

That contract is already frozen, already published, and already consumed
by `review_readiness_emission_v2`/`readiness_decision_v2` — this DAG's
own discipline (see `#200-A`'s ADR) is additive-sidecar-first, not
silent extension. `RequiredCheckConclusionV2` (`contracts_v2.py:386`) has
exactly four values: `success`, `failure`, `pending`, `missing` — there is
no fifth value for "the runner timed out" or "the process was OOM-killed".
That is not an oversight to fix here: it is the exact reason `#201`'s own
environmental-failure taxonomy (`TrustedCheckOutcomeV2`) has to live in
THIS sidecar contract instead, and why `promote_trusted_check_to_required_v2`
below refuses to promote anything but a genuinely resolved `success`/
`failure` outcome from a `trusted`-authority result. A timeout, an OOM
kill, a cancellation, or an infra failure never becomes a
`RequiredCheckResultV2` at all — there is structurally nowhere for it to
go, which is exactly the point: environmental failure must never be
mistaken for a product regression (`#199`'s own classification rule).

## `authority`: the only field standing between a PR and its own readiness

`TrustedCheckAuthorityV2.TRUSTED` may only ever be set by a caller with
legitimate host/base authority — this module cannot enforce WHO sets it
(that is `#201-B2`/`#201-B3`'s job: an isolated executor the PR cannot
write to), but it can and does enforce WHAT trusted authority is allowed
to produce: `promote_trusted_check_to_required_v2` is the ONLY function in
this codebase that may construct a `RequiredCheckResultV2` from a
`TrustedCheckResultV2`, and it refuses outright for
`UNTRUSTED_ADVISORY` — a check the PR's own code produced (e.g. its own
`conftest.py`, its own reporter) can carry a `TrustedCheckResultV2` all
the way through this contract, but can never cross into
`RequiredCheckResultV2` and therefore can never influence
`ReviewReadinessV2`.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    PositiveInt,
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    SafeIdentifier,
    Sha256,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

TRUSTED_CHECK_PLAN_SCHEMA_V2 = "agent-review.trusted-check-plan.v2"
TRUSTED_CHECK_RESULT_SCHEMA_V2 = "agent-review.trusted-check-result.v2"

# -- binding reason codes -----------------------------------------------------

RESULT_RUN_IDENTITY_MISMATCH_REASON_V2 = "trusted_check_run_identity_mismatch"
RESULT_HEAD_SHA_MISMATCH_REASON_V2 = "trusted_check_head_sha_mismatch"
RESULT_HARNESS_DIGEST_MISMATCH_REASON_V2 = "trusted_check_harness_digest_mismatch"
RESULT_CHECK_NOT_IN_PLAN_REASON_V2 = "trusted_check_not_in_plan"
RESULT_HASH_MISMATCH_REASON_V2 = "trusted_check_result_hash_mismatch"

# -- promotion reason codes ---------------------------------------------------

PROMOTION_NOT_TRUSTED_AUTHORITY_REASON_V2 = "trusted_check_not_trusted_authority"
PROMOTION_OUTCOME_NOT_RESOLVED_REASON_V2 = "trusted_check_outcome_not_resolved"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, review_content_v2.py, and review_transport_contract_v2.py.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class TrustedCheckAuthorityV2(str, Enum):
    TRUSTED = "trusted"
    UNTRUSTED_ADVISORY = "untrusted_advisory"


TrustedCheckAuthorityValueV2 = Annotated[TrustedCheckAuthorityV2, Field(strict=False)]


class TrustedCheckAuthorityBoundaryErrorV2(ExpectedOperationalRefusalV2, ValueError):
    """Raised by `validate_trusted_check_authority_v2` when a caller-
    supplied `authority` value cannot be validated as a real
    `TrustedCheckAuthorityV2` member. Carries a stable `reason_code`
    only -- never the raw invalid value, which may not even be safe or
    meaningful to include in a message."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


TRUSTED_CHECK_AUTHORITY_BOUNDARY_INVALID_REASON_V2 = "trusted_check_authority_boundary_invalid"


def validate_trusted_check_authority_v2(value: object) -> TrustedCheckAuthorityV2:
    """The single runtime boundary for `authority` on every plain-function
    entry point in the trusted-check v2 surface (H1-C, post-merge debt
    #205, origin #212: C9).

    `TrustedCheckAuthorityValueV2` (`Annotated[TrustedCheckAuthorityV2,
    Field(strict=False)]`) only coerces a string into the real enum member
    INSIDE a validated Pydantic model field. A type annotation on a bare
    function parameter -- even this exact alias -- performs no validation
    or coercion at all: nothing runs at call time to enforce it. Every
    privileged `authority is TrustedCheckAuthorityV2.TRUSTED` decision in
    `isolated_executor_v2.py` depends on `authority` already being the
    real, canonical enum singleton, so this must run before any such
    decision is made, not deferred to whenever (if ever) the value later
    gets embedded in a Pydantic model.

    Accepts the real enum member unchanged (returned by identity -- the
    common case costs nothing extra), or a `str` that is a legitimate
    Pydantic-coercible value for this enum. Every other input -- `None`,
    an unrelated type, a malformed string, or an object whose `__eq__`/
    `__hash__`/`__class__` might otherwise satisfy a naive check -- fails
    closed. There is no default and nothing here is ever promoted to
    `TRUSTED` on failure.

    Both type checks below are deliberately `type(value) is ...`, never
    `isinstance(value, ...)` (PR #233 review rounds 1-2, P1 each):

    - `type(value) is str`, not `isinstance(value, str)`:
      `TrustedCheckAuthorityV2(value)` looks the value up via the
      object's own `__hash__`/`__eq__` -- `isinstance` alone would admit
      a `str` SUBCLASS whose overridden `__hash__`/`__eq__` make it hash
      and compare like `"trusted"` regardless of its real underlying text
      (e.g. an actual value of `"admin"` spoofed to resolve to the
      genuine `TRUSTED` member).
    - `type(value) is TrustedCheckAuthorityV2`, not
      `isinstance(value, TrustedCheckAuthorityV2)`: Python's `isinstance`
      consults an object's `__class__` attribute, which an arbitrary
      object can override with a property returning
      `TrustedCheckAuthorityV2` even though it is not a member of it --
      `isinstance` alone would return that object UNCHANGED (not the
      real enum, not a rejection), breaking this function's own "every
      other input fails closed" contract; the object would then reach a
      Pydantic model boundary downstream as a raw, uncaught
      `ValidationError` instead of this function's own typed exception.

    An exact-type check on both branches means every value this function
    returns is either the real enum singleton or was built by the enum's
    own constructor from a genuine, unsubclassed `str` -- never an
    object whose class-identity or hash/eq semantics were spoofed.
    """
    if type(value) is TrustedCheckAuthorityV2:
        return value
    if type(value) is str:
        try:
            return TrustedCheckAuthorityV2(value)
        except ValueError:
            pass
    raise TrustedCheckAuthorityBoundaryErrorV2(TRUSTED_CHECK_AUTHORITY_BOUNDARY_INVALID_REASON_V2)


class TrustedCheckOutcomeV2(str, Enum):
    """Every way a trusted check can conclude. Only SUCCESS/FAILURE are
    "resolved" (the check ran to completion and produced a real verdict);
    the other four are environmental and can never promote to a
    `RequiredCheckResultV2` -- see `promote_trusted_check_to_required_v2`."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    OOM = "oom"
    CANCELLED = "cancelled"
    INFRA_FAILURE = "infra_failure"


TrustedCheckOutcomeValueV2 = Annotated[TrustedCheckOutcomeV2, Field(strict=False)]

_RESOLVED_OUTCOMES_V2 = frozenset({TrustedCheckOutcomeV2.SUCCESS, TrustedCheckOutcomeV2.FAILURE})


class AllowlistedCheckCommandV2(ContractV2Model):
    """One check a plan authorizes. `command_token` is a fixed identifier a
    harness resolves against its OWN inventory -- never a raw argv string,
    never free text a plan author (which may be PR-influenced input feeding
    into plan construction upstream of this contract) could use to name an
    arbitrary command. `network_allowed` is pinned `False`: this contract
    cannot represent a check with network access at all."""

    check_name: SafeIdentifier
    command_token: SafeIdentifier
    timeout_seconds: PositiveInt
    max_memory_mb: PositiveInt
    max_processes: PositiveInt
    network_allowed: Literal[False]


class TrustedCheckPlanV2(ContractV2Model):
    """The host-owned plan of what will run. Never authored, appended to,
    or influenced by anything under the PR's control -- this module does
    not enforce that (it has no notion of "who called the constructor"),
    but nothing in its shape has a place for PR-supplied content to enter:
    every field here is either an identity value or a fixed resource
    limit."""

    schema_id: Literal["agent-review.trusted-check-plan.v2"]
    schema_version: Literal[2]
    run_id: Sha256
    head_sha: GitSha
    harness_digest: Sha256
    authority_suite_digest: Sha256
    checks: list[AllowlistedCheckCommandV2]

    @model_validator(mode="after")
    def validate_plan(self) -> TrustedCheckPlanV2:
        if not self.checks:
            raise ValueError("a trusted check plan must authorize at least one check")
        check_names = [check.check_name for check in self.checks]
        if len(check_names) != len(set(check_names)):
            raise ValueError("check_name must be unique within a plan")
        return self


class TrustedCheckResultMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.trusted-check-result.v2"]
    schema_version: Literal[2]
    run_id: Sha256
    head_sha: GitSha
    check_name: SafeIdentifier
    authority: TrustedCheckAuthorityValueV2
    outcome: TrustedCheckOutcomeValueV2
    harness_digest: Sha256
    # Present iff the outcome actually produced sanitized output (SUCCESS/
    # FAILURE); absent for every environmental outcome, which by
    # definition never got far enough to produce a real artifact.
    artifact_sha256: Sha256 | None

    @model_validator(mode="after")
    def validate_material(self) -> TrustedCheckResultMaterialV2:
        has_artifact = self.artifact_sha256 is not None
        is_resolved = self.outcome in _RESOLVED_OUTCOMES_V2
        if has_artifact != is_resolved:
            raise ValueError(
                "artifact_sha256 must be present if and only if the outcome is "
                "success or failure"
            )
        return self


def _trusted_check_result_preimage_v2(value: TrustedCheckResultMaterialV2) -> dict[str, object]:
    return value.model_dump(mode="json", exclude={"result_sha256"})


def compute_trusted_check_result_sha256_v2(value: TrustedCheckResultMaterialV2) -> str:
    return hashlib.sha256(_canonical_json_bytes_v2(_trusted_check_result_preimage_v2(value))).hexdigest()


class TrustedCheckResultV2(TrustedCheckResultMaterialV2):
    result_sha256: Sha256

    @model_validator(mode="after")
    def validate_hash(self) -> TrustedCheckResultV2:
        expected = compute_trusted_check_result_sha256_v2(self)
        if self.result_sha256 != expected:
            raise ValueError("result_sha256 does not match the canonical result material")
        return self


class TrustedCheckBindingError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by `bind_trusted_check_result_to_plan_v2`. Carries a stable
    `reason_code` only -- never plan or result content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def bind_trusted_check_result_to_plan_v2(result: TrustedCheckResultV2, plan: TrustedCheckPlanV2) -> None:
    """Cross-check `result` against the SPECIFIC `plan` it claims to
    answer. Raises `TrustedCheckBindingError` fail-closed on any
    divergence; returns `None` on success. Mirrors `review_content_v2.
    bind_review_content_to_manifest_v2`'s own shape and discipline."""

    if result.run_id != plan.run_id:
        raise TrustedCheckBindingError(RESULT_RUN_IDENTITY_MISMATCH_REASON_V2)
    if result.head_sha != plan.head_sha:
        raise TrustedCheckBindingError(RESULT_HEAD_SHA_MISMATCH_REASON_V2)
    if result.harness_digest != plan.harness_digest:
        raise TrustedCheckBindingError(RESULT_HARNESS_DIGEST_MISMATCH_REASON_V2)

    plan_check_names = {check.check_name for check in plan.checks}
    if result.check_name not in plan_check_names:
        raise TrustedCheckBindingError(RESULT_CHECK_NOT_IN_PLAN_REASON_V2)

    if result.result_sha256 != compute_trusted_check_result_sha256_v2(result):
        raise TrustedCheckBindingError(RESULT_HASH_MISMATCH_REASON_V2)


class TrustedCheckPromotionError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by `promote_trusted_check_to_required_v2`. Carries a stable
    `reason_code` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def promote_trusted_check_to_required_v2(result: TrustedCheckResultV2) -> RequiredCheckResultV2:
    """The ONLY function in this codebase that may construct a
    `RequiredCheckResultV2` from a `TrustedCheckResultV2`. Refuses
    fail-closed for anything but a `TRUSTED`-authority result with a
    resolved (`SUCCESS`/`FAILURE`) outcome -- an `UNTRUSTED_ADVISORY`
    result, or ANY environmental outcome (`TIMEOUT`/`OOM`/`CANCELLED`/
    `INFRA_FAILURE`) from even a trusted authority, is refused: there is
    no `RequiredCheckConclusionV2` value that honestly represents "the
    environment failed", so this function does not invent one -- it
    raises instead, leaving the caller's readiness computation to see a
    missing/absent check (already fail-closed by `readiness_decision_v2`'s
    own existing precedence), never a fabricated result."""

    if result.authority is not TrustedCheckAuthorityV2.TRUSTED:
        raise TrustedCheckPromotionError(PROMOTION_NOT_TRUSTED_AUTHORITY_REASON_V2)
    if result.outcome not in _RESOLVED_OUTCOMES_V2:
        raise TrustedCheckPromotionError(PROMOTION_OUTCOME_NOT_RESOLVED_REASON_V2)

    conclusion = (
        RequiredCheckConclusionV2.SUCCESS
        if result.outcome is TrustedCheckOutcomeV2.SUCCESS
        else RequiredCheckConclusionV2.FAILURE
    )
    return RequiredCheckResultV2(
        check_name=result.check_name, required=True, deterministic=True,
        conclusion=conclusion, head_sha=result.head_sha,
    )
