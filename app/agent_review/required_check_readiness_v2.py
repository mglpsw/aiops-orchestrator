"""Required-check readiness assessment -- the C0-to-readiness choke point.

`#201-C`, connecting `#201-C0`'s provenance boundary
(`required_check_assembly_v2.reassemble_and_verify_required_checks_v2`) to
`ReviewReadinessV2`.

## The one rule this module exists to enforce

```text
#201-C MAY CONNECT AUTHORITY.
#201-C MUST NEVER CREATE AUTHORITY.
```

Everything below is a consequence of that rule, not a design choice made for
its own sake.

## Completeness never comes from the caller

`required_check_names` is not a parameter anywhere in this module. It is
derived, every call, from a `TargetProfileV2` loaded fresh from a trusted
base/default checkout (`target_profile_root`) and bound to `identity.
profile_hash` before anything else runs. A caller that could pass its own
`required_check_names` -- or a `TargetPoliciesV2`, or an already-loaded
`LoadedAuthoritativeCheckPolicyV2` -- could submit a real, C0-legitimated
green `pytest` alongside a required set trimmed down to `["pytest"]`, and
walk straight past the exact defect `#145` fixed, through a boundary that
was never actually fooled. `#201-C0`'s verifier only ever answers "is this
specific check legitimate"; nothing upstream of this module has ever
answered "is the required set complete", and this module is deliberately
the only place that now does.

`validate_policy_against_profile_v2` (already existing, `#201-C0`) proves the
policy's producer entries and the profile's `required_checks` name exactly
the same set, in both directions, before this module ever reads either --
so `required_check_names` here is provably the profile's own set, never a
policy artifact that could drift from it.

## The submitted arrays are claims, never evidence

`checks`/`provenance` arriving at `_verify_and_assess_required_checks_v2`
are exactly as trustworthy as any other caller-supplied bytes: none. They
become eligible for readiness only after
`reassemble_and_verify_required_checks_v2` -- `#201-C0`'s own re-derivation
against the acquired snapshot -- accepts them without raising. This module
adds no second opinion about authority and re-implements none of that
verifier's logic; it only asks, of an already-legitimated set, "is every
required name present, and is it green".

`RequiredCheckProvenanceErrorV2` is never caught here. A submission the
verifier refuses is an attack or a mistake, and turning that refusal into a
routine `manual_required` artifact would be exactly the kind of laundering
`#201-C0` was built to prevent one layer down. It propagates.

The two submitted sequences are frozen into tuples in the first two lines of
`_verify_and_assess_required_checks_v2`, and only those tuples are used from
then on -- verification and assessment never re-read a caller-owned
`Sequence`, which could otherwise be mutated between the two steps.

## `RequiredCheckReadinessAssessmentV2` is not a capability token

It is plain, freely constructible, internal, non-wire state -- like
`ReadinessDecisionV2` in `readiness_decision_v2.py`, not a published
contract. No production function anywhere in this codebase accepts one as a
parameter; the only production path that produces one
(`_verify_and_assess_required_checks_v2`, called exclusively from
`review_readiness_emission_v2.produce_review_readiness_v2`) also consumes it
immediately, in the same call. A caller cannot build an assessment by hand
and hand it to anything that treats it as proof: there is nothing downstream
of this module willing to accept it as input. See
`tests/agent_review/test_required_check_readiness_arch_v2.py` for the
AST-level proof that this stays true.

## Three outcomes, never conflated

- `SATISFIED` -- every required name has a verified, green check.
- `FAILED` -- every required name has a verified check, at least one red.
  The red check is never dropped: it survives in `assessment.checks` and
  therefore in the final `ReviewReadinessV2.checks`, unchanged.
- `AUTHORITY_NOT_ESTABLISHED` -- at least one required name has no verified
  check at all. This name is deliberately imprecise in one direction only:
  it means "no authoritative result was established for this evaluation",
  never "it is proven none exists". Nothing in this module is positioned to
  make the stronger claim.

None of the three is `READY` by itself; connecting that to a
`ReadinessStateV2` is `readiness_decision_v2.
_apply_required_check_assessment_v2`'s job, not this module's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from app.agent_review.authoritative_check_policy_v2 import (
    LoadedAuthoritativeCheckPolicyV2,
    load_authoritative_check_policy_v2,
    validate_policy_against_profile_v2,
)
from app.agent_review.authoritative_ci_snapshot_v2 import AuthoritativeCheckSnapshotV2
from app.agent_review.contracts_v2 import (
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    RunIdentityV2,
    RunOriginV2,
)
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.required_check_assembly_v2 import reassemble_and_verify_required_checks_v2
from app.agent_review.required_check_provenance_v2 import RequiredCheckProvenanceV2

ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2 = (
    "readiness_required_check_assessment_profile_identity_mismatch"
)
ASSESSMENT_CONCLUSION_UNRESOLVED_REASON_V2 = "readiness_required_check_assessment_conclusion_unresolved"

_RESOLVED_CONCLUSIONS_V2 = frozenset({RequiredCheckConclusionV2.SUCCESS, RequiredCheckConclusionV2.FAILURE})


class RequiredCheckReadinessErrorV2(ValueError):
    """Raised for a failure this module itself detects -- never a translation
    of `RequiredCheckProvenanceErrorV2`, which always propagates verbatim.
    Carries a stable `reason_code` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class RequiredCheckStatusV2(str, Enum):
    SATISFIED = "satisfied"
    FAILED = "failed"
    AUTHORITY_NOT_ESTABLISHED = "authority_not_established"


@dataclass(frozen=True)
class RequiredCheckReadinessAssessmentV2:
    """Derived, internal, non-wire result of one verified submission against
    one trusted required-check set. Not a capability token -- see the module
    docstring."""

    status: RequiredCheckStatusV2
    checks: tuple[RequiredCheckResultV2, ...]
    failed_check_names: tuple[str, ...]
    missing_check_names: tuple[str, ...]


def _assess_required_checks_v2(
    *, verified_checks: tuple[RequiredCheckResultV2, ...], required_check_names: tuple[str, ...]
) -> RequiredCheckReadinessAssessmentV2:
    """Pure and total over already-legitimated input. Reads no policy, no
    snapshot, no filesystem, and decides nothing about authority -- that is
    entirely `_verify_and_assess_required_checks_v2`'s job, upstream of this
    call. Calling this directly with a hand-built `RequiredCheckResultV2` is
    not a bypass of anything: this function was never the boundary."""

    for check in verified_checks:
        if check.conclusion not in _RESOLVED_CONCLUSIONS_V2:
            # Defensive, not reachable through the real assembler today: both
            # promotion paths (`promote_trusted_check_to_required_v2`,
            # `assemble_authoritative_ci_promotion_v2`) only ever construct a
            # `RequiredCheckResultV2` with a resolved SUCCESS/FAILURE
            # conclusion. Kept as a named refusal rather than an uncaught
            # `KeyError`-shaped surprise if that ever changes.
            raise RequiredCheckReadinessErrorV2(ASSESSMENT_CONCLUSION_UNRESOLVED_REASON_V2)

    # Two verified checks sharing a `check_name` but differing elsewhere would
    # differ in `compute_required_check_digest_v2` too, and re-deriving the
    # SAME check_name from the SAME snapshot/policy/identity/origin is
    # deterministic -- so no two verified checks can share a name without
    # being byte-identical, which `verify_required_check_provenance_set_v2`
    # already refuses as a duplicate digest. A dict keyed by check_name is
    # therefore lossless here, not merely convenient.
    by_name = {check.check_name: check for check in verified_checks}

    missing_check_names = tuple(sorted(name for name in required_check_names if name not in by_name))
    failed_check_names = tuple(
        sorted(
            name
            for name in required_check_names
            if name in by_name and by_name[name].conclusion is RequiredCheckConclusionV2.FAILURE
        )
    )

    if missing_check_names:
        status = RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    elif failed_check_names:
        status = RequiredCheckStatusV2.FAILED
    else:
        status = RequiredCheckStatusV2.SATISFIED

    return RequiredCheckReadinessAssessmentV2(
        status=status,
        checks=verified_checks,
        failed_check_names=failed_check_names,
        missing_check_names=missing_check_names,
    )


def _verify_and_assess_required_checks_v2(
    *,
    checks: Sequence[RequiredCheckResultV2],
    provenance: Sequence[RequiredCheckProvenanceV2],
    identity: RunIdentityV2,
    origin: RunOriginV2,
    snapshot: AuthoritativeCheckSnapshotV2,
    toolchain_digest: str,
    target_profile_root: str,
) -> RequiredCheckReadinessAssessmentV2:
    """The ONLY production path that may call
    `reassemble_and_verify_required_checks_v2` on behalf of readiness. Derives
    the required-check set itself from a trusted checkout, verifies the
    submission against the C0 boundary, and only then assesses it. Never
    catches `RequiredCheckProvenanceErrorV2` -- see the module docstring."""

    submitted_checks = tuple(checks)
    submitted_provenance = tuple(provenance)

    profile = load_target_profile_v2(target_profile_root)
    if compute_profile_hash_v2(profile) != identity.profile_hash:
        raise RequiredCheckReadinessErrorV2(ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2)

    loaded_policy: LoadedAuthoritativeCheckPolicyV2 = load_authoritative_check_policy_v2(target_profile_root)
    validate_policy_against_profile_v2(policy=loaded_policy.policy, profile=profile)

    required_check_names = tuple(profile.policies.required_checks)

    # THE FRONTIER. No `except RequiredCheckProvenanceErrorV2` around this
    # call, anywhere in this module.
    reassemble_and_verify_required_checks_v2(
        checks=submitted_checks,
        provenance=submitted_provenance,
        identity=identity,
        origin=origin,
        loaded_policy=loaded_policy,
        snapshot=snapshot,
        toolchain_digest=toolchain_digest,
    )

    return _assess_required_checks_v2(
        verified_checks=submitted_checks, required_check_names=required_check_names
    )
