"""The semantic-independence axis, and the axis it must not be confused with.

`#331`, SGAQ-CI1, extending `#201-C0`'s round-7 correction.

WHAT THIS SLICE CLOSES, AND WHAT IT DOES NOT

It closes exactly one proposition:

    C0_CAN_REPRESENT_AN_INDEPENDENT_SEMANTIC_JUDGE

It does NOT establish that any judge exists, that SGAQ is one, or that any
current workflow can promote. `verify_independent_semantic_judge_v2` refuses
unconditionally today because, in its own words, "a producer_kind representing
an actually independent judge -- one whose verdict does not derive from
executing or trusting the subject's own code -- does not exist yet." This adds
the vocabulary to say it, and nothing else.

THE TWO AXES, WHICH LIVE CODE ALREADY SEPARATES

`ProducerKindV2` answers *who owns the producer*. `CheckExecutionModeV2`
answers *how the verdict was obtained*. Round 7's correction is that the first
does not imply the second: re-running the pull request's own test suite inside
a base-owned `workflow_run` relocates the execution without changing who
authored the value being measured, so `controls(subject, success_signal)` still
holds.

The separation is structural, not stylistic: `check_execution_mode` is a field
of `ProducerAttestationV2`, while `producer_kind` belongs to the producer
identity half. That is why the gate's signature takes only the attestation, and
why widening it to accept `producer_kind` would be undoing the distinction
rather than implementing it.

Test H below is the guard on that: the discriminant must be execution mode, and
varying every identity-side field while holding a subject-controlled mode must
never buy independence.

WHY THE NEW MODE IS NOT SELF-AUTHORIZING

The string cannot grant authority by itself. The positive path exists only
inside the already-verified chain -- base-owned producer, correct workflow
identity, supported trigger, first-hand attestation, exact executed-tree
binding -- and the judge gate is deliberately last. Calling the gate on a
detached attestation proves one predicate, not authority, and tests C and G
keep that distinction visible.
"""

from __future__ import annotations

from typing import get_args

import pytest

from app.agent_review.authoritative_producer_evidence_v2 import (
    FIRST_HAND_EXECUTION_MODES_V2,
    INDEPENDENT_JUDGE_EXECUTION_MODE_V2,
    INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2,
    UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2,
    CheckExecutionModeV2,
    ProducerAttestationV2,
    RequiredCheckProvenanceErrorV2,
    compute_producer_attestation_digest_v2,
    verify_independent_semantic_judge_v2,
    verify_producer_execution_is_first_hand_v2,
)

#: The mode this slice introduces. Bound to the module's OWN constant, not to a
#: copy of the string: a test that spells the literal independently would keep
#: passing if production were repointed at a different value.
INDEPENDENT_MODE = INDEPENDENT_JUDGE_EXECUTION_MODE_V2

#: The execution-mode universe as it existed BEFORE this slice, DERIVED from the
#: live Literal rather than hardcoded. Test A loops over this exhaustively, so a
#: future mode added without a decision here cannot silently escape the refusal
#: check -- which a hardcoded tuple would have allowed, since it would simply
#: not have grown.
ALL_EXECUTION_MODES = get_args(CheckExecutionModeV2)
PRE_CI1_EXECUTION_MODES = tuple(m for m in ALL_EXECUTION_MODES if m != INDEPENDENT_MODE)

REPO = "mglpsw/aiops-orchestrator"
BASE = "1" * 40
HEAD = "2" * 40
MERGE = "3" * 40


def _attestation(**overrides: object) -> ProducerAttestationV2:
    fields: dict[str, object] = {
        "schema_id": "agent-review.producer-attestation.v2",
        "schema_version": 2,
        "source": "aiops-authoritative-check-producer",
        "repository": REPO,
        "pr_number": 7,
        "base_sha": BASE,
        "head_sha": HEAD,
        "executed_sha": MERGE,
        "workflow_run_id": "900",
        "run_attempt": 1,
        "test_outcome": "success",
        "check_execution_mode": "reexecuted_in_producer_run",
        "executed_sha_derivation": "verified_checkout_rev_parse",
        "policy_digest": "5" * 64,
        "toolchain_digest": "6" * 64,
    }
    fields.update(overrides)
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**fields, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**fields, attestation_digest=digest)


# --------------------------------------------------------------------------
# A, D, E -- every pre-existing mode still refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", PRE_CI1_EXECUTION_MODES)
def test_a_every_pre_ci1_execution_mode_still_fails_the_judge_gate(mode: str) -> None:
    """Exhaustive over the prior universe, not a hand-picked pair.

    `upstream_artifact_republished` is refused earlier by the first-hand gate in
    the real assembly path; asserted here at the judge gate directly so that
    removing the earlier gate could never silently make it independent.
    """
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        verify_independent_semantic_judge_v2(attestation=_attestation(check_execution_mode=mode))
    assert str(raised.value) == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2


def test_d_base_owned_plus_reexecuted_still_refuses() -> None:
    """The round-7 case in one line: base-ownership does not launder a
    subject-authored success signal."""
    attestation = _attestation(check_execution_mode="reexecuted_in_producer_run")
    verify_producer_execution_is_first_hand_v2(attestation=attestation)  # passes here
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        verify_independent_semantic_judge_v2(attestation=attestation)


def test_e_base_owned_plus_republished_still_refuses_at_both_gates() -> None:
    attestation = _attestation(check_execution_mode="upstream_artifact_republished")
    with pytest.raises(RequiredCheckProvenanceErrorV2) as first_hand:
        verify_producer_execution_is_first_hand_v2(attestation=attestation)
    assert str(first_hand.value) == UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        verify_independent_semantic_judge_v2(attestation=attestation)


# --------------------------------------------------------------------------
# B -- the new mode, and only at the gate it is for
# --------------------------------------------------------------------------


def test_b_the_independent_mode_passes_the_semantic_judge_gate() -> None:
    verify_independent_semantic_judge_v2(
        attestation=_attestation(check_execution_mode=INDEPENDENT_MODE)
    )


def test_b_the_independent_mode_is_also_first_hand() -> None:
    """A host tool that decided in the producer run forwarded nothing.

    Without this the new mode would be refused by the first-hand gate before
    ever reaching the judge gate, and the extension point would be unreachable
    in the real assembly path -- true in isolation, dead in practice.
    """
    verify_producer_execution_is_first_hand_v2(
        attestation=_attestation(check_execution_mode=INDEPENDENT_MODE)
    )


def test_b_the_independent_mode_does_not_bypass_executed_tree_observation() -> None:
    """The new mode buys semantic independence and nothing else."""
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        verify_producer_execution_is_first_hand_v2(
            attestation=_attestation(
                check_execution_mode=INDEPENDENT_MODE,
                executed_sha_derivation="caller_supplied",
            )
        )


# --------------------------------------------------------------------------
# C, H -- the discriminant is the execution mode, never the identity axis
# --------------------------------------------------------------------------


def test_c_producer_kind_is_not_on_the_attestation_at_all() -> None:
    """Structural, not behavioural: the axes cannot be confused because the
    gate cannot even see the identity axis."""
    assert "producer_kind" not in ProducerAttestationV2.model_fields
    assert "check_execution_mode" in ProducerAttestationV2.model_fields


def test_h_no_identity_side_field_can_buy_semantic_independence() -> None:
    """H, as a property over every attestation field the gate CAN see.

    Vary each one in turn while holding a subject-controlled execution mode.
    Every variation must still refuse. If any field other than the execution
    mode changes the verdict, the discriminant is not the axis this slice
    claims it is.
    """
    variations: dict[str, object] = {
        "repository": "mglpsw/some-other-repo",
        "pr_number": 99,
        "base_sha": "9" * 40,
        "head_sha": "8" * 40,
        "executed_sha": "7" * 40,
        "workflow_run_id": "424242",
        "run_attempt": 5,
        "test_outcome": "failure",
        "executed_sha_derivation": "caller_supplied",
        "policy_digest": "a" * 64,
        "toolchain_digest": "b" * 64,
    }
    for field, value in variations.items():
        attestation = _attestation(
            check_execution_mode="reexecuted_in_producer_run", **{field: value}
        )
        with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
            verify_independent_semantic_judge_v2(attestation=attestation)
        assert str(raised.value) == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2, field


# --------------------------------------------------------------------------
# F -- the new mode weakens nothing above it
# --------------------------------------------------------------------------


def test_f_the_new_mode_is_confined_to_the_two_gates_that_read_it() -> None:
    """`check_execution_mode` must not become an input to identity, base
    ownership, tree binding or the attestation digest.

    Asserted by walking the module's AST: any function that branches on the
    mode is one that could weaken a gate above it, so the set is pinned.

    The first version of this test built the set from `vars(module)` with
    `not isinstance(function, type)`, which silently excluded EVERY method,
    because that exclusion drops the whole class object -- `ProducerAttestation
    V2.validate_digest` among them. An adversarial lane reproduced the
    consequence: a mutant returning early from `validate_digest` for the new
    mode, so that a forged digest promotes, left all twelve tests in this file
    GREEN while claiming in this very docstring to cover the digest.

    The AST walk sees function definitions wherever they are nested. The
    EXPECTED set is unchanged -- on the clean subject the digest is whole-model
    (`model_dump(exclude={"attestation_digest"})`) and names no field, so only
    the two gates mention it. What changed is that a new reader inside a class
    now makes this test FAIL instead of being invisible to it. The guard below
    pins that reach, so the mechanism cannot silently regress to method-blind.
    """
    import ast
    import inspect

    import app.agent_review.authoritative_producer_evidence_v2 as evidence

    source = inspect.getsource(evidence)
    module = ast.parse(source)
    defined = {
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "validate_digest" in defined, (
        "the walk must reach methods, or a mode-reader added inside a model class "
        "would be invisible to this test -- the exact defect this replaced"
    )

    readers = {
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "check_execution_mode" in (ast.get_source_segment(source, node) or "")
    }
    assert readers == {
        "verify_producer_execution_is_first_hand_v2",
        "verify_independent_semantic_judge_v2",
    }, readers


def test_f_the_judge_gate_matches_the_mode_exactly_not_by_containment() -> None:
    """A near-miss mode must not pass the judge gate.

    An adversarial lane mutated the gate's `!=` to `not in`, turning an
    equality test into a substring test, and the whole 138-test neighbourhood
    stayed green. Under that mutant `'independent_data_only_host_too'`,
    `'host_tool'`, `'t'` and `''` all promote. Nothing in production reaches
    the gate with an unvalidated attestation today -- pydantic refuses these at
    construction -- so the exposure is latent, not live; `model_construct`
    reaches the gate directly, exactly as the digest test below does."""

    honest = _attestation()
    for near_miss in ("independent_data_only_host_too", "host_tool", "t", ""):
        attestation = ProducerAttestationV2.model_construct(
            **{
                **{key: getattr(honest, key) for key in ProducerAttestationV2.model_fields},
                "check_execution_mode": near_miss,
            }
        )
        with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
            verify_independent_semantic_judge_v2(attestation=attestation)
        assert str(raised.value) == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2, near_miss


def test_a_the_two_gates_partition_the_mode_universe_with_no_mode_left_over() -> None:
    """The derived universe is a partition, not a sample.

    `PRE_CI1_EXECUTION_MODES` is computed from the live Literal, so this states
    the invariant that keeps test A exhaustive: every declared mode is either
    the one independent mode or a pre-CI1 mode, and the first-hand set is a
    subset of the universe that contains the independent mode."""

    assert INDEPENDENT_MODE in ALL_EXECUTION_MODES
    assert set(ALL_EXECUTION_MODES) == {INDEPENDENT_MODE, *PRE_CI1_EXECUTION_MODES}
    assert FIRST_HAND_EXECUTION_MODES_V2 <= set(ALL_EXECUTION_MODES)
    assert INDEPENDENT_MODE in FIRST_HAND_EXECUTION_MODES_V2


def test_f_the_attestation_digest_still_covers_the_execution_mode() -> None:
    """Weakening check: the mode must remain inside the hash-bound material, so
    it cannot be swapped after the producer emitted it."""
    honest = _attestation(check_execution_mode=INDEPENDENT_MODE)
    forged = ProducerAttestationV2.model_construct(
        **{
            **{k: getattr(honest, k) for k in ProducerAttestationV2.model_fields},
            "check_execution_mode": "reexecuted_in_producer_run",
        }
    )
    assert compute_producer_attestation_digest_v2(forged) != honest.attestation_digest


# --------------------------------------------------------------------------
# G -- a PR-writable producer is refused before the judge gate is reached
# --------------------------------------------------------------------------


def test_g_the_judge_gate_is_last_and_is_not_an_authority_proof_on_its_own() -> None:
    """Calling the gate on a detached attestation proves one predicate.

    The assembler runs identity, base-ownership, tree binding and first-hand
    checks before this one; a PR-writable producer is refused by those, and no
    execution mode it declares can reach here. This test states that the
    positive result above is a predicate, not a promotion.
    """
    import inspect

    import app.agent_review.required_check_assembly_v2 as assembly

    source = inspect.getsource(assembly)
    judge = source.index("verify_independent_semantic_judge_v2(attestation=")
    for earlier in (
        "verify_producer_attestation_v2(",
        "verify_producer_execution_is_first_hand_v2(attestation=",
    ):
        assert source.index(earlier) < judge, f"{earlier} must run before the judge gate"
