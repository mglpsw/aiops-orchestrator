"""Unit tests for the C0-to-readiness choke point (`#201-C`).

Two tiers, matching the module's own two functions:

- `_assess_required_checks_v2` -- pure, total, no C0 involved. These are
  "Class B" in the `#201-C` plan's own vocabulary: they prove the
  SATISFIED/FAILED/AUTHORITY_NOT_ESTABLISHED logic in isolation, using
  hand-built `RequiredCheckResultV2` objects that stand in for "whatever C0
  already legitimated" -- never described as, and never usable as, proof of
  authority.
- `_verify_and_assess_required_checks_v2` -- the real choke point, always
  exercised against the REAL, unpatched `reassemble_and_verify_required_
  checks_v2`. Every test in this tier is reachable in production today:
  empty/incomplete submissions (`AUTHORITY_NOT_ESTABLISHED`), a hand-built
  attacker submission (refused, uncaught, no artifact), a profile-identity
  mismatch (refused), and -- the core proof of `#201-C`'s R1 amendment --
  that swapping the trusted `target_profile_root` changes which names are
  demanded, with no other input capable of doing so, because no other input
  for required-check names exists on this function's signature at all.

No test in this file patches `verify_independent_semantic_judge_v2`,
`reassemble_and_verify_required_checks_v2`, or
`_verify_and_assess_required_checks_v2` itself. See
`test_required_check_readiness_arch_v2.py` for the AST-level check that no
test in `tests/agent_review/test_*.py` does either in a function that also
calls the production readiness path. That scan is non-recursive and covers
that directory only, and its reach is narrower than "anywhere in this
codebase" -- read its own reach note before citing it as a guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent_review.authoritative_check_policy_v2 import load_authoritative_check_policy_v2
from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.authoritative_producer_evidence_v2 import (
    INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2,
)
from app.agent_review.contracts_v2 import (
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    RunIdentityV2,
    RunOriginV2,
)
from app.agent_review.profile_loader_v2 import (
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_v2,
)
from app.agent_review.required_check_provenance_v2 import (
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
)
from app.agent_review.required_check_readiness_v2 import (
    ASSESSMENT_CONCLUSION_UNRESOLVED_REASON_V2,
    ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2,
    RequiredCheckReadinessErrorV2,
    RequiredCheckStatusV2,
    _assess_required_checks_v2,
    _verify_and_assess_required_checks_v2,
)
from tests.agent_review.test_aiops_review_quality_gate_v2_cli import (
    TOOLCHAIN_DIGEST,
    _hand_built_ci_pair,
    _identity_dict,
    _snapshot_dict,
    _write_target_profile,
)

ORIGIN = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")


def _check(name: str, conclusion: RequiredCheckConclusionV2, head_sha: str = "2" * 40) -> RequiredCheckResultV2:
    return RequiredCheckResultV2(
        check_name=name, required=True, deterministic=True, conclusion=conclusion, head_sha=head_sha
    )


# -- `_assess_required_checks_v2` -- pure composition, Class B --------------


def test_all_required_present_and_green_is_satisfied() -> None:
    checks = (_check("pytest", RequiredCheckConclusionV2.SUCCESS),)

    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))

    assert assessment.status is RequiredCheckStatusV2.SATISFIED
    assert assessment.checks == checks
    assert assessment.failed_check_names == ()
    assert assessment.missing_check_names == ()


def test_a_required_name_missing_from_verified_checks_is_authority_not_established() -> None:
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    assert assessment.status is RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    assert assessment.missing_check_names == ("pytest",)
    assert assessment.failed_check_names == ()
    assert assessment.checks == ()


def test_a_required_check_with_failure_conclusion_is_failed() -> None:
    checks = (_check("pytest", RequiredCheckConclusionV2.FAILURE),)

    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))

    assert assessment.status is RequiredCheckStatusV2.FAILED
    assert assessment.failed_check_names == ("pytest",)
    assert assessment.missing_check_names == ()
    # The red check is never dropped -- it survives byte-for-byte.
    assert assessment.checks == checks


def test_missing_takes_precedence_over_failed_when_both_are_present() -> None:
    """One required name failed, a DIFFERENT required name never showed up at
    all. `AUTHORITY_NOT_ESTABLISHED` is the more conservative of the two
    non-satisfied statuses -- reported here as the overall status -- but
    neither name's own fact is lost: both lists are populated independently."""

    checks = (_check("pytest", RequiredCheckConclusionV2.FAILURE),)

    assessment = _assess_required_checks_v2(
        verified_checks=checks, required_check_names=("pytest", "mypy")
    )

    assert assessment.status is RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    assert assessment.missing_check_names == ("mypy",)
    assert assessment.failed_check_names == ("pytest",)


def test_an_extra_non_required_check_does_not_substitute_a_missing_required_one() -> None:
    """C-T7. A green check present under a name the profile never required
    must never be read as satisfying a DIFFERENT, genuinely missing,
    required name."""

    checks = (_check("lint", RequiredCheckConclusionV2.SUCCESS),)

    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))

    assert assessment.status is RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    assert assessment.missing_check_names == ("pytest",)
    # The extra check still rides along in the artifact, unfiltered.
    assert assessment.checks == checks


def test_a_red_non_required_check_never_produces_satisfied() -> None:
    """Adversarial review finding, confirmed and fixed (round 8). Every
    required check ("pytest") is green, but a legitimately-verified,
    NON-required check ("lint") is red. Before the fix, `status` was
    decided from required-name membership alone, so this reported
    `SATISFIED` -- unchanged by `_apply_required_check_assessment_v2`'s
    `SATISFIED` short-circuit -- even though `assessment.checks` (which
    becomes `ReviewReadinessV2.checks` unfiltered) still carried the red
    "lint" check. `ReviewReadinessV2.validate_state_invariants`'s READY
    branch requires EVERY entry of `self.checks` to be green, not only the
    required-named ones, so this combination previously crashed
    `ReviewReadinessV2.__init__` with an uncaught `pydantic.ValidationError`
    several calls later -- not reachable through the real C0 boundary for a
    target that has not authorised an independent judge (since `#331`
    SGAQ-CI1R that is an authorization fact, not a categorical one), but a
    real latent defect at the pure-composition layer this module is exercised
    at directly. `status` must now be `FAILED`, and "lint" must appear in
    `failed_check_names`, so `_apply_required_check_assessment_v2` never
    lets this combination reach construction unchanged."""

    checks = (
        _check("pytest", RequiredCheckConclusionV2.SUCCESS),
        _check("lint", RequiredCheckConclusionV2.FAILURE),
    )

    assessment = _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))

    assert assessment.status is RequiredCheckStatusV2.FAILED
    assert assessment.status is not RequiredCheckStatusV2.SATISFIED
    assert "lint" in assessment.failed_check_names
    assert assessment.missing_check_names == ()
    # Unfiltered: every submitted-and-verified check survives, the red
    # non-required one included -- which is what this test is about. Compared
    # as a set, not positionally: `assessment.checks` is canonicalized by
    # `check_name` (see the module docstring's "Canonical check order"), so
    # positional equality with the SUBMISSION order would assert the very
    # caller-order dependence that PR #220's post-merge review removed.
    assert set(assessment.checks) == set(checks)
    assert len(assessment.checks) == len(checks)


def test_missing_and_failed_names_are_sorted_deterministically() -> None:
    checks = (
        _check("zeta", RequiredCheckConclusionV2.FAILURE),
        _check("alpha", RequiredCheckConclusionV2.FAILURE),
    )

    assessment = _assess_required_checks_v2(
        verified_checks=checks, required_check_names=("zeta", "alpha", "omega", "beta")
    )

    assert assessment.failed_check_names == ("alpha", "zeta")
    assert assessment.missing_check_names == ("beta", "omega")


def test_check_order_does_not_change_the_assessment() -> None:
    """C-T19 at this layer: which order `checks`/`required_check_names`
    arrive in must never change the derived status or name lists.

    Post-merge review finding on PR #220 (`discussion_r3773499142`),
    confirmed and fixed: this test used to assert ONLY the status and the
    two name lists, and passed while `assessment.checks` still carried the
    caller's submission order verbatim -- which
    `produce_review_readiness_v2` then copies into
    `ReviewReadinessV2.checks`, making semantically identical runs
    serialize to different artifact bytes. The `checks` assertion below is
    the one that was missing."""

    checks_a = (
        _check("alpha", RequiredCheckConclusionV2.SUCCESS),
        _check("beta", RequiredCheckConclusionV2.FAILURE),
    )
    checks_b = tuple(reversed(checks_a))

    a = _assess_required_checks_v2(verified_checks=checks_a, required_check_names=("beta", "alpha"))
    b = _assess_required_checks_v2(verified_checks=checks_b, required_check_names=("alpha", "beta"))

    assert a.status is b.status
    assert a.failed_check_names == b.failed_check_names
    assert a.missing_check_names == b.missing_check_names
    assert a.checks == b.checks
    assert tuple(check.check_name for check in a.checks) == ("alpha", "beta")


def test_canonical_check_order_preserves_every_check_including_a_red_one() -> None:
    """Ordering normalization only: no filter, no dedup, no substitution.
    The red check in particular must still be present -- the "a verified
    failure is never dropped" guarantee is independent of ordering."""

    checks = (
        _check("zeta", RequiredCheckConclusionV2.SUCCESS),
        _check("alpha", RequiredCheckConclusionV2.FAILURE),
        _check("mid", RequiredCheckConclusionV2.SUCCESS),
    )

    assessment = _assess_required_checks_v2(
        verified_checks=checks, required_check_names=("alpha", "mid", "zeta")
    )

    assert tuple(check.check_name for check in assessment.checks) == ("alpha", "mid", "zeta")
    assert len(assessment.checks) == len(checks)
    assert set(assessment.checks) == set(checks)
    assert assessment.status is RequiredCheckStatusV2.FAILED
    red = next(check for check in assessment.checks if check.check_name == "alpha")
    assert red.conclusion is RequiredCheckConclusionV2.FAILURE


@pytest.mark.parametrize("conclusion", [RequiredCheckConclusionV2.PENDING, RequiredCheckConclusionV2.MISSING])
def test_an_unresolved_conclusion_in_a_verified_set_is_refused(conclusion: RequiredCheckConclusionV2) -> None:
    """C-T22, defensive. Neither production promotion path ever constructs a
    `RequiredCheckResultV2` with `pending`/`missing` -- this is a refusal for
    a case this module cannot itself rule out structurally, not a case that
    is reachable through the real assembler today."""

    checks = (_check("pytest", conclusion),)

    with pytest.raises(RequiredCheckReadinessErrorV2) as exc_info:
        _assess_required_checks_v2(verified_checks=checks, required_check_names=("pytest",))

    assert exc_info.value.reason_code == ASSESSMENT_CONCLUSION_UNRESOLVED_REASON_V2


def test_assessment_is_frozen() -> None:
    assessment = _assess_required_checks_v2(verified_checks=(), required_check_names=("pytest",))

    with pytest.raises(Exception):
        assessment.status = RequiredCheckStatusV2.SATISFIED  # type: ignore[misc]


# -- `_verify_and_assess_required_checks_v2` -- real C0, Class A ------------


def _identity(tmp_path: Path, *, required_checks: list[str] | None = None, **overrides: object) -> RunIdentityV2:
    profile_root = _write_target_profile(tmp_path, required_checks=required_checks)
    profile = load_target_profile_v2(profile_root)
    raw = _identity_dict(profile_hash=compute_profile_hash_v2(profile))
    raw.update(overrides)
    return profile_root, RunIdentityV2.model_validate(raw)


def test_empty_submission_yields_authority_not_established_for_every_required_name(tmp_path: Path) -> None:
    profile_root, identity = _identity(tmp_path, required_checks=["pytest"])
    snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))

    assessment = _verify_and_assess_required_checks_v2(
        checks=[],
        provenance=[],
        identity=identity,
        origin=ORIGIN,
        snapshot=snapshot,
        toolchain_digest=TOOLCHAIN_DIGEST,
        target_profile_root=str(profile_root),
    )

    assert assessment.status is RequiredCheckStatusV2.AUTHORITY_NOT_ESTABLISHED
    assert assessment.missing_check_names == ("pytest",)
    assert assessment.checks == ()


def test_required_names_come_only_from_the_trusted_profile(tmp_path: Path) -> None:
    """R1 / C-T23. Two DIFFERENT trusted roots, each with its own required
    set, each with its own matching `profile_hash`. Nothing else on this
    function's signature can name a required check -- there is no
    `required_check_names`, no `TargetPoliciesV2`, no
    `LoadedAuthoritativeCheckPolicyV2` parameter to smuggle a reduced set
    through. Swapping the root is the ONLY way the demanded set changes."""

    root_a, identity_a = _identity(tmp_path / "a", required_checks=["pytest"])
    root_b, identity_b = _identity(tmp_path / "b", required_checks=["pytest", "mypy"])
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))

    assessment_a = _verify_and_assess_required_checks_v2(
        checks=[], provenance=[], identity=identity_a, origin=ORIGIN, snapshot=empty_snapshot,
        toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(root_a),
    )
    assessment_b = _verify_and_assess_required_checks_v2(
        checks=[], provenance=[], identity=identity_b, origin=ORIGIN, snapshot=empty_snapshot,
        toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(root_b),
    )

    assert assessment_a.missing_check_names == ("pytest",)
    assert assessment_b.missing_check_names == ("mypy", "pytest")


def test_a_target_profile_cannot_declare_zero_required_checks(tmp_path: Path) -> None:
    """The trivial `checks=[] provenance=[] required_check_names=[]` reading
    of the reduced-required-names attack is closed one layer below this
    module, structurally: `TargetPoliciesV2.required_checks` is
    `Field(min_length=1)` in the frozen contract. A target profile with an
    empty required set cannot be loaded at all."""

    profile_root = _write_target_profile(tmp_path, required_checks=[])

    with pytest.raises(TargetProfileLoadErrorV2):
        load_target_profile_v2(profile_root)


def test_profile_hash_mismatch_is_refused(tmp_path: Path) -> None:
    profile_root, identity = _identity(tmp_path, required_checks=["pytest"], profile_hash="9" * 64)
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))

    with pytest.raises(RequiredCheckReadinessErrorV2) as exc_info:
        _verify_and_assess_required_checks_v2(
            checks=[], provenance=[], identity=identity, origin=ORIGIN, snapshot=empty_snapshot,
            toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(profile_root),
        )

    assert exc_info.value.reason_code == ASSESSMENT_PROFILE_IDENTITY_MISMATCH_REASON_V2


def test_a_missing_target_profile_root_propagates_the_loaders_own_error(tmp_path: Path) -> None:
    _, identity = _identity(tmp_path / "real", required_checks=["pytest"])
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))

    with pytest.raises(TargetProfileLoadErrorV2):
        _verify_and_assess_required_checks_v2(
            checks=[], provenance=[], identity=identity, origin=ORIGIN, snapshot=empty_snapshot,
            toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(tmp_path / "nonexistent"),
        )


def test_a_hand_built_attacker_submission_is_refused_uncaught_by_the_real_boundary(tmp_path: Path) -> None:
    """The forged/invalid case (I-10, C-T21): a self-consistent, well-formed
    submission that was never legitimately promoted must propagate the C0
    verifier's own exception -- never be converted into an assessment, and
    never silently absorbed into `AUTHORITY_NOT_ESTABLISHED`."""

    profile_root, identity = _identity(tmp_path, required_checks=["pytest"])
    loaded_policy = load_authoritative_check_policy_v2(profile_root)
    # `_snapshot_dict`/`_observation`'s defaults already match `_identity_dict`'s
    # default repo/head_sha -- no override needed for them to bind.
    snapshot_dict = _snapshot_dict(["pytest"])
    snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(snapshot_dict))

    result, provenance_dict = _hand_built_ci_pair(
        check_name="pytest", snapshot=snapshot, loaded_policy=loaded_policy,
        identity=identity, origin=ORIGIN, toolchain_digest=TOOLCHAIN_DIGEST,
    )
    provenance = RequiredCheckProvenanceV2.model_validate(provenance_dict)

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc_info:
        _verify_and_assess_required_checks_v2(
            checks=[result], provenance=[provenance], identity=identity, origin=ORIGIN,
            snapshot=snapshot, toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(profile_root),
        )

    assert exc_info.value.reason_code == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2


def test_a_forged_submission_still_refuses_cleanly_even_when_a_different_required_check_is_also_missing(
    tmp_path: Path,
) -> None:
    """Adversarial review finding (round 8, removed-behavior audit): the old
    CLI ran completeness (`missing required check`) before provenance, so a
    submission that was simultaneously missing "mypy" entirely AND carried
    forged provenance on the "pytest" it DID submit would have been
    diagnosed as `gate_required_check_missing`. `#201-C` always runs the C0
    boundary first, so this same mixed submission now surfaces the
    provenance error instead -- a diagnostic-ordering change, documented in
    `_verify_and_assess_required_checks_v2`'s own docstring, not a
    regression in the property that actually matters: the submission is
    still refused, uncaught, with no assessment/artifact produced either
    way. This test exists to make sure that property survives even in the
    mixed case, which no other test exercised."""

    profile_root, identity = _identity(tmp_path, required_checks=["pytest", "mypy"])
    loaded_policy = load_authoritative_check_policy_v2(profile_root)
    snapshot_dict = _snapshot_dict(["pytest", "mypy"])
    snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(snapshot_dict))

    result, provenance_dict = _hand_built_ci_pair(
        check_name="pytest", snapshot=snapshot, loaded_policy=loaded_policy,
        identity=identity, origin=ORIGIN, toolchain_digest=TOOLCHAIN_DIGEST,
    )
    provenance = RequiredCheckProvenanceV2.model_validate(provenance_dict)

    # "mypy" is not submitted at all -- missing -- while "pytest" IS
    # submitted but with forged/invalid provenance.
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        _verify_and_assess_required_checks_v2(
            checks=[result], provenance=[provenance], identity=identity, origin=ORIGIN,
            snapshot=snapshot, toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(profile_root),
        )


def test_the_submitted_sequences_are_frozen_before_verification(tmp_path: Path) -> None:
    """R3 / C-T25. A caller-owned mutable list, mutated AFTER the call
    returns, must never retroactively change the returned assessment --
    proving the choke point copies into its own tuple rather than aliasing
    the caller's `Sequence`."""

    profile_root, identity = _identity(tmp_path, required_checks=["pytest"])
    empty_snapshot = parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict([])))
    caller_checks: list[RequiredCheckResultV2] = []
    caller_provenance: list = []

    assessment = _verify_and_assess_required_checks_v2(
        checks=caller_checks, provenance=caller_provenance, identity=identity, origin=ORIGIN,
        snapshot=empty_snapshot, toolchain_digest=TOOLCHAIN_DIGEST, target_profile_root=str(profile_root),
    )

    caller_checks.append(_check("pytest", RequiredCheckConclusionV2.SUCCESS))

    assert assessment.checks == ()
    assert assessment.missing_check_names == ("pytest",)
