"""An independent judge is eligible only if the base-owned policy authorized it.

`#331`, SGAQ-CI1R. Forensic predecessor: PR #339 at `6d94238`, which added the
execution-mode vocabulary WITHOUT the policy authorization and was stopped for
exactly that -- `AGENTS.md` requires fail-closed authorization, and representing
a capability without a way for the trusted base policy to authorize or refuse it
leaves the boundary fail-open. No qualification transfers from that PR.

THE PROPOSITION

    C0_INDEPENDENT_JUDGE_REQUIRES_EXPLICIT_BASE_POLICY_OPT_IN

A producer declaration that its verdict came from an independent data-only host
tool is NEVER sufficient by itself. Positive eligibility requires BOTH a
verified independent execution mode AND explicit authorization of that mode by
the trusted base-owned `AuthoritativeCheckEntryV2`.

THREE AXES, DELIBERATELY NOT COLLAPSED

    ProducerKindV2                -> WHO owns the producer
    CheckExecutionModeV2          -> HOW the verdict was obtained
    permitted_execution_modes     -> WHICH ways this target authorizes

The first two already existed and are unchanged in meaning. Only the third is
new, and it is the one that makes the capability fail-closed: a producer says
"I am independent", and the trusted base policy must previously have said "I
accept independent". Neither alone promotes.

THE BACKWARD-COMPATIBLE DEFAULT IS THE SECURITY PROPERTY

An existing policy has no `permitted_execution_modes`. Its effective set is
exactly the pre-CI1R universe, so `independent_data_only_host_tool` is NOT
authorized anywhere merely because the engine learned the vocabulary. No target
gains authority by an engine upgrade. That is asserted here against the REAL
shipped fixture policies loaded through the REAL loader, not against a
hand-built model.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import get_args

import pytest

from app.agent_review.authoritative_check_policy_v2 import (
    AuthoritativeCheckPolicyErrorV2,
    compute_policy_semantic_digest_v2,
    load_authoritative_check_policy_v2,
)
from app.agent_review.authoritative_producer_evidence_v2 import (
    EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2,
    INDEPENDENT_JUDGE_EXECUTION_MODE_V2,
    INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2,
    LEGACY_PERMITTED_EXECUTION_MODES_V2,
    UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2,
    CheckExecutionModeV2,
    RequiredCheckProvenanceErrorV2,
    verify_execution_mode_is_policy_authorized_v2,
)
from app.agent_review.required_check_assembly_v2 import (
    assemble_authoritative_ci_promotion_v2,
)
from app.agent_review.required_check_provenance_v2 import RequiredCheckSourceKindV2

from tests.agent_review.test_required_check_assembly_v2 import (
    BASE,
    HEAD,
    IDENTITY,
    MERGE,
    ORIGIN,
    PRODUCER_WORKFLOW_PATH,
    PRODUCER_WORKFLOW_SHA,
    REPO,
    TOOLCHAIN,
    _attestation,
    _obs,
    _pr_triggered_obs,
    _snapshot,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "v2"

#: The exact `policy_source_semantic_digest` each shipped fixture policy had on
#: master (`4e334ab4`), captured before this slice existed. Pinned as literals
#: so the compatibility claim is a cross-head invariant rather than a
#: self-consistent recomputation inside one tree.
MASTER_SEMANTIC_DIGESTS = {
    "agent_escala": "2839c9c6b0e9b37ed9d8db01af93b9f74007ab08e7dbadc137e0e067c4d043cd",
    "interleitos": "ddc1fc3ed6dddaff66d624483af7668b71a0fbd09e08981d97b3d37d4070ebcc",
}

LEGACY_MODES = ("reexecuted_in_producer_run", "upstream_artifact_republished")
INDEPENDENT = "independent_data_only_host_tool"

#: The semantic digest of `_policy_yaml` opting all three modes in. Pinned as a
#: literal because the legacy digests above exercise only the branch that DROPS
#: the key, leaving the sorted-projection branch with no pinned expectation at
#: all. Two review lanes independently showed the consequence: replacing
#: `sorted(effective)` with `list(effective)` makes the digest process-dependent
#: (6 distinct values over 40 fresh processes) and reversing the sort changes it
#: outright, and the whole suite stayed green for both. A same-process
#: comparison of two frozensets cannot see either, because both sides get the
#: same iteration order. Verified stable across hash seeds 0/1/42/12345/99999.
OPTED_IN_SEMANTIC_DIGEST = "7e391125b581bb7583fa948ff40a51b13107894188386f2e913c3ba6fe6b1254"


# --------------------------------------------------------------------------
# policy construction through the REAL loader, never model_copy
# --------------------------------------------------------------------------


def _policy_yaml(*, permitted: list[str] | None) -> str:
    """The agent_escala fixture, optionally with an opt-in line.

    Built as TEXT and loaded by `load_authoritative_check_policy_v2` so every
    assertion below travels the real YAML ingress, the real strict validation
    and the real digest computation. `model_copy(update=...)` would bypass all
    three and prove nothing about what a target can actually write."""

    opt_in = ""
    if permitted is not None:
        rendered = "\n".join(f"      - {mode}" for mode in permitted)
        opt_in = f"    permitted_execution_modes:\n{rendered}\n" if permitted else "    permitted_execution_modes: []\n"
    return f"""schema_id: agent-review.authoritative-check-policy.v2
schema_version: 2
source: repo-policy
identity:
  repo: {REPO}
authoritative_checks:
  - check_name: pytest
    workflow_path: {PRODUCER_WORKFLOW_PATH}
    job_name: authoritative-pytest
    verifier_identity: github-actions
    producer_kind: base_owned_workflow_run
    producer_workflow:
      repository: {REPO}
      path: {PRODUCER_WORKFLOW_PATH}
      sha: "{PRODUCER_WORKFLOW_SHA}"
    producer_workflow_ref: refs/heads/master
{opt_in}    permitted_conclusions:
      - success
      - failure
    origin_rules:
      pull_request: synthetic_merge_parentage
"""


def _load(tmp_path: Path, *, permitted: list[str] | None):
    aiops = tmp_path / ".aiops"
    aiops.mkdir(parents=True, exist_ok=True)
    (aiops / "authoritative-checks.v2.yaml").write_text(
        _policy_yaml(permitted=permitted), encoding="utf-8"
    )
    return load_authoritative_check_policy_v2(tmp_path)


def _assemble(loaded_policy, *, observation: dict | None = None):
    return assemble_authoritative_ci_promotion_v2(
        check_name="pytest",
        snapshot=_snapshot(observations=[observation if observation is not None else _obs()]),
        loaded_policy=loaded_policy,
        identity=IDENTITY,
        origin=ORIGIN,
        toolchain_digest=TOOLCHAIN,
    )


def _independent_obs(**overrides: object) -> dict:
    """A fully coherent observation whose producer declares the independent
    mode. Everything else is the promotable base-owned shape."""

    record = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1, check_execution_mode=INDEPENDENT
        )
    )
    record.update(overrides)
    return record


# --------------------------------------------------------------------------
# vocabulary and set semantics
# --------------------------------------------------------------------------


def test_the_three_axes_stay_separate() -> None:
    """`permitted_execution_modes` ranges over the HOW axis, never over WHO."""

    from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckEntryV2

    assert "permitted_execution_modes" in AuthoritativeCheckEntryV2.model_fields
    assert INDEPENDENT_JUDGE_EXECUTION_MODE_V2 in get_args(CheckExecutionModeV2)
    assert set(LEGACY_PERMITTED_EXECUTION_MODES_V2) == set(LEGACY_MODES)
    assert INDEPENDENT_JUDGE_EXECUTION_MODE_V2 not in LEGACY_PERMITTED_EXECUTION_MODES_V2


def test_the_legacy_default_is_exactly_the_pre_ci1r_universe(tmp_path: Path) -> None:
    loaded = _load(tmp_path, permitted=None)
    entry = loaded.policy.entry_for("pytest")
    assert entry.permitted_execution_modes is None
    assert entry.effective_permitted_execution_modes == frozenset(LEGACY_MODES)


def test_duplicate_modes_are_refused_at_load(tmp_path: Path) -> None:
    """A set with a repeated member is a malformed set, not a set."""

    with pytest.raises(AuthoritativeCheckPolicyErrorV2):
        _load(tmp_path, permitted=[*LEGACY_MODES, "reexecuted_in_producer_run"])


def test_an_empty_permitted_set_is_refused_at_load(tmp_path: Path) -> None:
    """Consistent with `origin_rules`: a target learns its policy cannot work
    when it writes the policy, not when a review silently never goes ready."""

    with pytest.raises(AuthoritativeCheckPolicyErrorV2):
        _load(tmp_path, permitted=[])


def test_an_unknown_mode_is_refused_at_load(tmp_path: Path) -> None:
    with pytest.raises(AuthoritativeCheckPolicyErrorV2):
        _load(tmp_path, permitted=["reexecuted_in_producer_run", "definitely_not_a_mode"])


def test_ordering_is_semantically_irrelevant(tmp_path: Path) -> None:
    """Set semantics: declaration order must not move the semantic digest."""

    forward = _load(tmp_path / "a", permitted=[*LEGACY_MODES, INDEPENDENT])
    reversed_ = _load(tmp_path / "b", permitted=[INDEPENDENT, *reversed(LEGACY_MODES)])
    assert (
        forward.policy_source_semantic_digest == reversed_.policy_source_semantic_digest
    )
    assert forward.policy_source_bytes_digest != reversed_.policy_source_bytes_digest


# --------------------------------------------------------------------------
# P1-F / section 5 -- semantic digest compatibility
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", sorted(MASTER_SEMANTIC_DIGESTS))
def test_shipped_policies_keep_their_master_semantic_digest(target: str) -> None:
    """A policy that did not change meaning must not change semantic identity.

    Adding an optional field with a default would otherwise move every existing
    policy's semantic digest and churn every snapshot and provenance sidecar
    that records it, for no change in what is authorized."""

    loaded = load_authoritative_check_policy_v2(FIXTURES / target)
    assert loaded.policy_source_semantic_digest == MASTER_SEMANTIC_DIGESTS[target]


def test_omitted_and_explicit_legacy_are_semantically_identical(tmp_path: Path) -> None:
    """They authorize the same set, so they are the same policy semantically --
    while the raw bytes differ, because the bytes really did change."""

    omitted = _load(tmp_path / "omitted", permitted=None)
    explicit = _load(tmp_path / "explicit", permitted=list(LEGACY_MODES))
    assert omitted.policy_source_semantic_digest == explicit.policy_source_semantic_digest
    assert omitted.policy_source_bytes_digest != explicit.policy_source_bytes_digest


def test_opting_in_moves_the_semantic_digest(tmp_path: Path) -> None:
    """P1-F. Authorizing a new judge class is a change in MEANING."""

    legacy = _load(tmp_path / "legacy", permitted=None)
    opted_in = _load(tmp_path / "optin", permitted=[*LEGACY_MODES, INDEPENDENT])
    assert legacy.policy_source_semantic_digest != opted_in.policy_source_semantic_digest


def test_an_opted_in_policy_has_a_pinned_canonical_semantic_digest(tmp_path: Path) -> None:
    """The projected branch has a fixed expectation, not just a relative one.

    `sorted(effective)` must produce ONE canonical form. Asserting only that
    two digests differ, or that two same-process frozensets agree, cannot
    detect a projection that is unordered or reverse-ordered -- both lanes
    demonstrated exactly that. A literal can."""

    loaded = _load(tmp_path, permitted=[*LEGACY_MODES, INDEPENDENT])
    assert loaded.policy_source_semantic_digest == OPTED_IN_SEMANTIC_DIGEST


def test_the_projected_digest_is_stable_across_processes(tmp_path: Path) -> None:
    """Same policy, fresh interpreters, randomized hash seeds -- one digest.

    The digest travels into provenance sidecars and is compared across runs, so
    a value that depends on this process's set-iteration order would be a real
    identity defect. `PYTHONHASHSEED` is set explicitly per child rather than
    inherited, because a parent and child sharing a seed would hide it."""

    import subprocess
    import sys
    import textwrap

    aiops = tmp_path / ".aiops"
    aiops.mkdir(parents=True, exist_ok=True)
    (aiops / "authoritative-checks.v2.yaml").write_text(
        _policy_yaml(permitted=[*LEGACY_MODES, INDEPENDENT]), encoding="utf-8"
    )
    program = textwrap.dedent(
        """
        import sys
        from app.agent_review.authoritative_check_policy_v2 import (
            load_authoritative_check_policy_v2,
        )
        print(load_authoritative_check_policy_v2(sys.argv[1]).policy_source_semantic_digest)
        """
    )
    seen = set()
    for seed in ("0", "1", "42", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", program, str(tmp_path)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        seen.add(result.stdout.strip())
    assert seen == {OPTED_IN_SEMANTIC_DIGEST}, seen


# --------------------------------------------------------------------------
# P1-A .. P1-E -- the authorization proposition, end to end
# --------------------------------------------------------------------------


def test_p1_a_independent_mode_with_legacy_policy_field_omitted_refuses(tmp_path: Path) -> None:
    """The headline. The producer says it is independent; no trusted base
    policy ever said that was acceptable; the promotion is refused."""

    loaded = _load(tmp_path, permitted=None)
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded, observation=_independent_obs())
    assert str(raised.value) == EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2


def test_p1_b_independent_mode_with_explicit_legacy_only_refuses(tmp_path: Path) -> None:
    """Explicitly declining is as fail-closed as never mentioning it."""

    loaded = _load(tmp_path, permitted=list(LEGACY_MODES))
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded, observation=_independent_obs())
    assert str(raised.value) == EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2


def test_p1_c_independent_mode_with_explicit_opt_in_promotes(tmp_path: Path) -> None:
    """The positive path, and the only one in this repository."""

    loaded = _load(tmp_path, permitted=[*LEGACY_MODES, INDEPENDENT])
    promoted = _assemble(loaded, observation=_independent_obs())
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI
    assert promoted.result.conclusion.value == "success"
    assert promoted.result.deterministic is True


def test_p1_d_the_producer_declaration_alone_is_not_authority(tmp_path: Path) -> None:
    """P1-D stated as the difference between two runs of IDENTICAL evidence.

    Same snapshot, same attestation, same identity, same origin. The ONLY
    difference is which policy was loaded. If the declaration were sufficient,
    both would promote."""

    observation = _independent_obs()
    legacy = _load(tmp_path / "legacy", permitted=None)
    opted_in = _load(tmp_path / "optin", permitted=[*LEGACY_MODES, INDEPENDENT])

    with pytest.raises(RequiredCheckProvenanceErrorV2):
        _assemble(legacy, observation=observation)

    promoted = _assemble(opted_in, observation=observation)
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI


@pytest.mark.parametrize("target", sorted(MASTER_SEMANTIC_DIGESTS))
def test_p1_e_no_shipped_policy_authorizes_the_new_mode(target: str) -> None:
    """The engine learning the vocabulary must not hand any existing target a
    promotion path. Asserted through the real loader on the real files."""

    loaded = load_authoritative_check_policy_v2(FIXTURES / target)
    for entry in loaded.policy.authoritative_checks:
        assert entry.permitted_execution_modes is None
        assert INDEPENDENT not in entry.effective_permitted_execution_modes


def test_a_policy_may_authorize_LESS_than_the_legacy_universe(tmp_path: Path) -> None:
    """The narrowing half of the field, which nothing else exercised.

    Every other test here opts in ADDITIVELY, so an implementation that OR-ed
    the legacy set back in, or that consulted the policy only for the
    independent mode, would satisfy all of them. Two lanes found exactly those
    two weakenings surviving the entire suite.

    Here a target authorizes ONLY the independent judge. `reexecuted_in_
    producer_run` must then be refused for that target -- by the authorization
    gate, naming the authorization, even though it is a mode the engine has
    always understood and every other policy permits."""

    loaded = _load(tmp_path, permitted=[INDEPENDENT])
    entry = loaded.policy.entry_for("pytest")
    assert entry.effective_permitted_execution_modes == frozenset({INDEPENDENT})

    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded)
    assert str(raised.value) == EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2

    promoted = _assemble(loaded, observation=_independent_obs())
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI


def test_legacy_mode_under_legacy_policy_still_refuses_at_the_judge_gate(tmp_path: Path) -> None:
    """Master's behaviour, unchanged. `reexecuted_in_producer_run` is
    authorized by the legacy default, so authorization PASSES and the refusal
    comes from the judge gate with master's own reason code. If this slice had
    moved that refusal to the authorization gate, every existing target would
    see a different reason code for an unchanged situation."""

    loaded = _load(tmp_path, permitted=None)
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded)
    assert str(raised.value) == INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2


# --------------------------------------------------------------------------
# the gate as a unit, independent of the assembler
# --------------------------------------------------------------------------


def test_the_authorization_gate_reads_the_policy_set_not_the_attestation(tmp_path: Path) -> None:
    attestation_independent = _attestation(
        REPO, 7, BASE, HEAD, MERGE, "900", 1, check_execution_mode=INDEPENDENT
    )
    from app.agent_review.authoritative_producer_evidence_v2 import ProducerAttestationV2

    parsed = ProducerAttestationV2.model_validate(attestation_independent)

    verify_execution_mode_is_policy_authorized_v2(
        attestation=parsed,
        permitted_execution_modes=frozenset({*LEGACY_MODES, INDEPENDENT}),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        verify_execution_mode_is_policy_authorized_v2(
            attestation=parsed, permitted_execution_modes=frozenset(LEGACY_MODES)
        )
    assert str(raised.value) == EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2


def test_the_gate_is_fail_closed_on_an_empty_permitted_set() -> None:
    """The public gate does not depend on a validator it cannot see.

    No policy can reach here with an empty set -- `min_length=1` refuses that
    at load. So this guard is unreachable through the assembler, and would be
    a decorative guard if nothing witnessed it. It is not decorative: this
    function is public, takes a raw `frozenset`, and a future caller resolving
    the set some other way must not be silently granted everything by an empty
    one. Asserted directly, because that is the only way to assert it."""

    from app.agent_review.authoritative_producer_evidence_v2 import ProducerAttestationV2

    for mode in (INDEPENDENT, *LEGACY_MODES):
        parsed = ProducerAttestationV2.model_validate(
            _attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1, check_execution_mode=mode)
        )
        with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
            verify_execution_mode_is_policy_authorized_v2(
                attestation=parsed, permitted_execution_modes=frozenset()
            )
        assert str(raised.value) == EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2, mode


# --------------------------------------------------------------------------
# P2a -- the upstream authority chain, behaviourally, WITH opt-in present
# --------------------------------------------------------------------------


def _optin(tmp_path: Path):
    return _load(tmp_path, permitted=[*LEGACY_MODES, INDEPENDENT])


def test_p2a_pr_writable_producer_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_PR_WRITABLE_REASON_V2,
    )

    observation = _pr_triggered_obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1, check_execution_mode=INDEPENDENT
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(_optin(tmp_path), observation=observation)
    assert str(raised.value) == PRODUCER_PR_WRITABLE_REASON_V2


def test_p2a_wrong_producer_workflow_sha_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    )

    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(_optin(tmp_path), observation=_independent_obs(workflow_sha="0" * 40))
    assert str(raised.value) == PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2


def test_p2a_wrong_producer_workflow_ref_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    )

    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(
            _optin(tmp_path),
            observation=_independent_obs(workflow_execution_ref="refs/pull/7/merge"),
        )
    assert str(raised.value) == PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2


def test_p2a_wrong_producer_workflow_repository_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    )

    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(
            _optin(tmp_path),
            observation=_independent_obs(workflow_repository="mglpsw/somewhere-else"),
        )
    assert str(raised.value) == PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2


def test_p2a_invalid_attestation_binding_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_ATTESTATION_MISMATCH_REASON_V2,
    )

    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, "9" * 40, "900", 1, check_execution_mode=INDEPENDENT
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(_optin(tmp_path), observation=observation)
    assert str(raised.value) == PRODUCER_ATTESTATION_MISMATCH_REASON_V2


def test_p2a_caller_supplied_executed_sha_still_refuses_with_opt_in(tmp_path: Path) -> None:
    from app.agent_review.authoritative_producer_evidence_v2 import (
        EXECUTED_TREE_NOT_OBSERVED_REASON_V2,
    )

    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            check_execution_mode=INDEPENDENT,
            executed_sha_derivation="caller_supplied",
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(_optin(tmp_path), observation=observation)
    assert str(raised.value) == EXECUTED_TREE_NOT_OBSERVED_REASON_V2


def test_p2a_republished_artifact_refuses_as_not_first_hand_even_when_permitted(
    tmp_path: Path,
) -> None:
    """Even if a target opts the republished mode in, it is still not
    first-hand. Authorization is a NECESSARY condition, never a sufficient one:
    it cannot buy back a property the evidence does not have."""

    loaded = _load(tmp_path, permitted=[*LEGACY_MODES, INDEPENDENT])
    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            check_execution_mode="upstream_artifact_republished",
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded, observation=observation)
    assert str(raised.value) == UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2


def test_p2a_first_hand_is_diagnosed_before_authorization(tmp_path: Path) -> None:
    """The gate ORDER the assembler docstring claims, pinned behaviourally.

    Republished evidence under a policy that does NOT permit republishing is
    the one input where two gates would both fire, so it is the only input that
    can tell their order apart. Running the first-hand gate first says "this
    evidence was forwarded"; running authorization first says "your policy
    forbids this mode". The first is the truer diagnosis -- the evidence would
    be unusable under ANY policy -- and it is the one a target can act on.

    Both lanes found the ordering claim unpinned: moving the authorization gate
    ahead of the first-hand gate passed the entire `tests/agent_review` suite
    while silently changing this reason code."""

    loaded = _load(tmp_path, permitted=[INDEPENDENT])
    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            check_execution_mode="upstream_artifact_republished",
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as raised:
        _assemble(loaded, observation=observation)
    assert str(raised.value) == UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2


def test_p2a_a_failing_independent_verdict_is_not_laundered_into_success(tmp_path: Path) -> None:
    """Opting in authorizes a JUDGE, not an outcome."""

    observation = _obs(
        conclusion="failure",
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            outcome="failure",
            check_execution_mode=INDEPENDENT,
        ),
    )
    promoted = _assemble(_optin(tmp_path), observation=observation)
    assert promoted.result.conclusion.value == "failure"
    assert promoted.result.deterministic is True


# --------------------------------------------------------------------------
# section 12 -- live configuration safety, as a standing control
# --------------------------------------------------------------------------


def test_no_policy_file_in_this_repository_opts_in() -> None:
    """`#331` SGAQ-CI1R section 12: the capability is REPRESENTABLE and
    POLICY-GATED, not deployed.

    Discovered by walking the tree rather than by a hardcoded list, so a policy
    added later is covered without anyone remembering to extend this test. That
    matters more than it looks: a one-time grep during review proves the state
    on the day it ran, and this proves it on every run.

    Every discovered policy is loaded through the REAL loader and asked for its
    EFFECTIVE authorization, so a file that opted in via some representation
    this test did not anticipate would still be caught.

    SCOPE, STATED HONESTLY. An authoritative-check policy lives in the TARGET
    repository, not here -- see `authoritative_check_policy_v2`'s module
    docstring. The only policies this walk can see are the two test fixtures.
    So this proves "nothing in this repository opts in", which is what section
    12 of the slice grant asks for, and NOT "no target has opted in", which
    cannot be answered from this repository at all. Auditing real target
    policies belongs to `#203`'s target-pack conformance path, and is the named
    successor for this control rather than something this test can bluff."""

    root = Path(__file__).resolve().parents[2]
    # Filter on the path RELATIVE to the repository root. Filtering the
    # absolute path dropped every policy whenever the checkout itself lived
    # under a component named `tmp` (`/tmp/...`, `/var/tmp/...`), which two
    # lanes hit immediately. The vacuity guard below turned that into a loud
    # failure rather than a silent pass, which is the only reason it was
    # merely annoying rather than a false green.
    policies = sorted(
        path
        for path in root.rglob("authoritative-checks*.yaml")
        if ".git" not in path.relative_to(root).parts
    )
    assert policies, "the discovery walk found nothing -- it would pass vacuously"

    opted_in = []
    for path in policies:
        loaded = load_authoritative_check_policy_v2(path.parent.parent)
        for entry in loaded.policy.authoritative_checks:
            if INDEPENDENT in entry.effective_permitted_execution_modes:
                opted_in.append(f"{path}::{entry.check_name}")
    assert opted_in == [], opted_in
