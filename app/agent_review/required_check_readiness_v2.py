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

- `SATISFIED` -- every required name has a verified, green check, AND every
  OTHER verified check the caller submitted (if any) is green too.
- `FAILED` -- at least one verified check is red -- a required name's own
  check, or any other submitted-and-verified check alongside it. The red
  check is never dropped: it survives in `assessment.checks` and therefore
  in the final `ReviewReadinessV2.checks`, unchanged.
- `AUTHORITY_NOT_ESTABLISHED` -- at least one required name has no verified
  check at all. This name is deliberately imprecise in one direction only:
  it means "no authoritative result was established for this evaluation",
  never "it is proven none exists". Nothing in this module is positioned to
  make the stronger claim.

Adversarial review finding, confirmed and fixed (round 8): `assessment.
checks` is `verified_checks` UNFILTERED -- every check the caller submitted
and C0 verified, not only the required-named ones (`#201-C0` verifies
whatever it is asked to verify; nothing restricts a submission to the
required set). That full, unfiltered tuple becomes `ReviewReadinessV2.
checks` one layer up. `ReviewReadinessV2.validate_state_invariants`'s
`READY` branch (`contracts_v2.py`) requires EVERY entry of `self.checks` to
be green, not only the required-named ones. `status` therefore cannot be
decided from required-name membership alone: a submission with every
required check green but ONE additional, non-required, legitimately
verified check red would previously report `SATISFIED` -- unchanged in
`_apply_required_check_assessment_v2`'s `SATISFIED` short-circuit -- and
then crash several calls later with an uncaught `pydantic.ValidationError`
from `ReviewReadinessV2.__init__`, for the adjacent, non-required-check
case of exactly the "crash instead of a representable state" defect class
this module exists to eliminate for required checks. Not reachable through
the real C0 boundary for a target that has not authorised an independent
judge -- since `#331` SGAQ-CI1R the path is gated by policy authorization
rather than categorically closed, and whether any particular target has
opted in is not observable from this repository (see the plan's own Class
A/B/C split) -- so this was found and fixed at the pure-composition (Class B)
layer, prospectively, rather than waiting for it to become reachable. `failed_check_names`/`status` now account for every
verified check's conclusion, not only required-named ones.

## Canonical check order

`assessment.checks` is sorted by `check_name`, never left in the order the
caller happened to submit. Post-merge review finding on PR #220
(`#220 (comment) discussion_r3773499142`), confirmed and fixed: the derived
name lists (`failed_check_names`/`missing_check_names`) were already sorted,
but `checks` itself preserved submission order, and
`produce_review_readiness_v2` copies that tuple verbatim into
`ReviewReadinessV2.checks`. Two semantically identical runs -- the same
legitimated checks, verified against the same snapshot, differing only in
the sequence the caller listed them -- therefore serialized to different
artifact bytes. Reproduced directly: identical status and identical name
lists, but `a.checks != b.checks` and different serialized bytes.

Sorting by `check_name` alone is a TOTAL order here, not a partial one that
would need a tie-break -- but only within the already-legitimated domain
this function receives, and the reason is two steps, not one:
`compute_required_check_digest_v2` hashes the entire canonical
`RequiredCheckResultV2`, so two checks sharing only a `check_name` do NOT
thereby share a digest -- differing in `conclusion`/`head_sha`/etc. would
still differ in digest, so a bare name match is not by itself grounds for
anything. What closes the gap is the caller,
`_verify_and_assess_required_checks_v2`, which always calls
`reassemble_and_verify_required_checks_v2` before assessment, and that
verifier re-derives each check from a fixed identity/snapshot/policy/origin
-- a deterministic process that authorizes at most ONE digest per
`check_name` (a CI snapshot records one result per check, not several). So
if two entries in a VERIFIED submission share a `check_name`, they were
both re-derived against that same single authorized digest and are
therefore byte-identical -- and `verify_required_check_provenance_set_v2`
already refuses outright any submission whose digests collide
(`len(set(digests)) != len(digests)`), before assessment ever runs. Two
verified checks can therefore never legitimately share a name at all (the
same reasoning the existing `by_name` comment in
`_assess_required_checks_v2` records), which is what makes `check_name` a
sufficient, tie-break-free sort key for `verified_checks` specifically --
not a general property of `RequiredCheckResultV2`.

This is ordering normalization only. Every check is preserved -- no filter,
no dedup, no substitution -- so nothing about `status`, precedence, or the
"a red check is never dropped" guarantee above changes.

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
    # Every verified check with a FAILURE conclusion, not only required-named
    # ones -- see the module docstring (round 8): `assessment.checks` carries
    # every verified check unfiltered, and the frozen `ReviewReadinessV2`
    # contract's READY branch holds all of them, not only the required-named
    # ones, to the same green-only bar.
    failed_check_names = tuple(
        sorted({check.check_name for check in verified_checks if check.conclusion is RequiredCheckConclusionV2.FAILURE})
    )

    if missing_check_names:
        status = RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    elif failed_check_names:
        status = RequiredCheckStatusV2.FAILED
    else:
        status = RequiredCheckStatusV2.SATISFIED

    # Canonicalized by `check_name` -- see the module docstring's
    # "Canonical check order" section. Every check is preserved; only the
    # order is normalized.
    canonical_checks = tuple(sorted(verified_checks, key=lambda check: check.check_name))

    return RequiredCheckReadinessAssessmentV2(
        status=status,
        checks=canonical_checks,
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
    catches `RequiredCheckProvenanceErrorV2` -- see the module docstring.

    Diagnostic-ordering note (round-8 adversarial review): completeness
    (`_assess_required_checks_v2`, missing/failed names) is only ever
    computed AFTER the C0 boundary call returns without raising. If a
    submission is simultaneously missing a required check entirely AND
    carries invalid/corrupted provenance on a DIFFERENT submitted check,
    the boundary's provenance error is what surfaces -- not a "missing
    required check" diagnosis, even though the missing check is, in a
    sense, the simpler underlying problem. Deliberately not reordered:
    running completeness first would mean asserting something about the
    required set before the submission has been verified at all, and the
    fail-closed OUTCOME (no artifact, nonzero exit) is identical either
    way. This is the same precedent already accepted for the narrower
    "extra check does not substitute for a missing one" case (see
    `test_cli_extra_check_does_not_substitute_a_missing_required_check`),
    generalized to the mixed case."""

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
