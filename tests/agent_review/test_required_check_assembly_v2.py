"""Adversarial matrix for the host-owned assembler (`#201-C0`, C0-5).

Every threat C0-T1..C0-T25 has a test here or in the C0-3/C0-4 modules, and
every test names the threat it closes. The organising claim is narrow and
falsifiable: nothing a pull request controls can produce a promotable required
check, and nothing that merely looks well-formed is treated as entitled.
"""

from __future__ import annotations

import json

import pytest

from app.agent_review.authoritative_check_policy_v2 import load_authoritative_check_policy_v2
from app.agent_review.authoritative_ci_snapshot_v2 import parse_authoritative_ci_snapshot_v2
from app.agent_review.contracts_v2 import (
    RequiredCheckConclusionV2,
    RequiredCheckResultV2,
    RunIdentityV2,
    RunOriginV2,
    compute_run_id,
)
from app.agent_review.required_check_assembly_v2 import (
    assemble_authoritative_ci_promotion_v2,
    assemble_trusted_host_promotion_v2,
    verify_required_check_provenance_set_v2,
)
from app.agent_review.required_check_provenance_v2 import (
    PROVENANCE_CONFIG_UNVERIFIED_REASON_V2,
    PROVENANCE_HEAD_MISMATCH_REASON_V2,
    PROVENANCE_INVALID_REASON_V2,
    PROVENANCE_MISSING_REASON_V2,
    PROVENANCE_OBSERVATION_STALE_REASON_V2,
    PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2,
    PROVENANCE_PARENTAGE_MISMATCH_REASON_V2,
    PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2,
    PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2,
    PROVENANCE_REPOSITORY_MISMATCH_REASON_V2,
    PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2,
    PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2,
    PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2,
    PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2,
    PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    AuthorityEffectV2,
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
    RequiredCheckSourceKindV2,
    build_required_check_provenance_v2,
    compute_required_check_digest_v2,
)
from app.agent_review.trusted_checks_v2 import (
    TrustedCheckAuthorityV2,
    TrustedCheckOutcomeV2,
    TrustedCheckResultV2,
    compute_trusted_check_result_sha256_v2,
)

from tests.agent_review.test_authoritative_check_policy_v2 import FIXTURES

def _attestation(repo: str, pr: int, base: str, head: str, merge: str,
                 run_id: str, attempt: int, outcome: str = "success", **overrides) -> dict:
    """Attestation emitted by the producer's checkout-free job."""

    from app.agent_review.authoritative_producer_evidence_v2 import (
        ProducerAttestationV2,
        compute_producer_attestation_digest_v2,
    )

    fields: dict = {
        "schema_id": "agent-review.producer-attestation.v2",
        "schema_version": 2,
        "source": "aiops-authoritative-check-producer",
        "repository": repo,
        "pr_number": pr,
        "base_sha": base,
        "head_sha": head,
        "executed_sha": merge,
        "workflow_run_id": run_id,
        "run_attempt": attempt,
        "test_outcome": outcome,
        # The producer re-ran the check itself and read the executed tree back
        # from its own verified checkout. Anything else is a pass-through.
        "check_execution_mode": "reexecuted_in_producer_run",
        "executed_sha_derivation": "verified_checkout_rev_parse",
        "policy_digest": "5" * 64,
        "toolchain_digest": "6" * 64,
    }
    fields.update(overrides)
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**fields, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**fields, attestation_digest=digest).model_dump(mode="json")


REPO = "mglpsw/AgentEscala"
BASE = "c" * 40
HEAD = "a" * 40
MERGE = "d" * 40
TOOLCHAIN = "e" * 64
CONFIG_DIGEST = "7" * 64

POLICY = load_authoritative_check_policy_v2(FIXTURES / "agent_escala")
ORIGIN = RunOriginV2(event_type="pull_request", event_action="synchronize", delivery_id="delivery-1")


def _identity(**overrides: object) -> RunIdentityV2:
    fields: dict[str, object] = {
        "repo": REPO,
        "pr_number": 7,
        "base_sha": BASE,
        "head_sha": HEAD,
        "tested_merge_sha": MERGE,
        "toolrepo_sha": "b" * 40,
        "profile_hash": "1" * 64,
        "policy_hash": "2" * 64,
        "manifest_hash": "3" * 64,
        "evidence_hash": "4" * 64,
    }
    fields.update(overrides)
    return RunIdentityV2(**fields)


IDENTITY = _identity()


PRODUCER_WORKFLOW_PATH = ".github/workflows/authoritative-checks.yml"
PRODUCER_WORKFLOW_SHA = "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96"
PRODUCER_WORKFLOW_REF = "refs/heads/master"


def _obs(**overrides: object) -> dict[str, object]:
    """The promotable shape: a BASE-OWNED producer.

    The default is deliberately a `workflow_run` producer, not the pull
    request's own CI run. A PR-triggered run is PR-writable -- the pull request
    can add a job that uploads an attestation carrying every field the verifier
    checks -- so that topology appears below only as a negative case.

    `run_base_sha`/`run_head_sha` are absent because a `workflow_run`'s own head
    is the default branch, not this pull request. The merge binding comes from
    the attestation instead, which is emitted by a run the PR cannot write into.
    """

    record: dict[str, object] = {
        "repository": REPO,
        "head_sha": HEAD,
        "check_run_id": "100",
        "check_run_name": "authoritative-pytest",
        "status": "completed",
        "conclusion": "success",
        "app_slug": "github-actions",
        "workflow_path": PRODUCER_WORKFLOW_PATH,
        "workflow_execution_ref": PRODUCER_WORKFLOW_REF,
        "workflow_repository": REPO,
        "workflow_sha": PRODUCER_WORKFLOW_SHA,
        "referenced_workflows": [],
        "producer_trigger": "workflow_run",
        "producer_attestation": _attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1),
        "workflow_run_id": "900",
        "run_attempt": 1,
        "run_started_at": "2026-08-11T10:00:00Z",
        "run_event": "workflow_run",
        "run_base_sha": None,
        "run_head_sha": None,
    }
    record.update(overrides)
    return record


def _pr_triggered_obs(**overrides: object) -> dict[str, object]:
    """The topology round 7 proved unpromotable: the producing run is the pull
    request's own, so the pull request can author whatever it uploads."""

    record = _obs(
        workflow_execution_ref="refs/pull/7/merge",
        referenced_workflows=[
            {
                "path": f"{REPO}/{PRODUCER_WORKFLOW_PATH}",
                "sha": PRODUCER_WORKFLOW_SHA,
                "ref": None,
            }
        ],
        producer_trigger="pull_request",
        run_event="pull_request",
        run_base_sha=BASE,
        run_head_sha=HEAD,
    )
    record.update(overrides)
    return record


def _snapshot(*, observations: list[dict[str, object]] | None = None, **overrides: object):
    payload: dict[str, object] = {
        "schema_id": "agent-review.authoritative-check-snapshot.v2",
        "schema_version": 2,
        "source": "aiops-acquire-authoritative-checks",
        "acquisition": {
            "acquired_by": "aiops-acquire-authoritative-checks-v2",
            "api_host": "api.github.com",
            "repository": REPO,
            "head_sha": HEAD,
        },
        "observations": observations if observations is not None else [_obs()],
        "tested_merge_sha": MERGE,
        "tested_merge_parents": [BASE, HEAD],
        "observation_bytes_digest": "f" * 64,
    }
    payload.update(overrides)
    return parse_authoritative_ci_snapshot_v2(json.dumps(payload))


def _assemble_ci(snapshot=None, identity: RunIdentityV2 = IDENTITY, origin: RunOriginV2 = ORIGIN):
    return assemble_authoritative_ci_promotion_v2(
        check_name="pytest",
        snapshot=snapshot if snapshot is not None else _snapshot(),
        loaded_policy=POLICY,
        identity=identity,
        origin=origin,
        toolchain_digest=TOOLCHAIN,
    )


def _trusted(**overrides: object) -> TrustedCheckResultV2:
    material: dict[str, object] = {
        "schema_id": "agent-review.trusted-check-result.v2",
        "schema_version": 2,
        "run_id": compute_run_id(IDENTITY),
        "head_sha": HEAD,
        "check_name": "pytest",
        "authority": TrustedCheckAuthorityV2.TRUSTED,
        "outcome": TrustedCheckOutcomeV2.SUCCESS,
        "harness_digest": "5" * 64,
        "artifact_sha256": "6" * 64,
    }
    material.update(overrides)
    if material["outcome"] not in {TrustedCheckOutcomeV2.SUCCESS, TrustedCheckOutcomeV2.FAILURE}:
        material["artifact_sha256"] = None
    draft = TrustedCheckResultV2.model_construct(**material, result_sha256="0" * 64)
    return TrustedCheckResultV2(**material, result_sha256=compute_trusted_check_result_sha256_v2(draft))


def _assemble_host(trusted=None, toolchain_digest=TOOLCHAIN, config_digest=CONFIG_DIGEST):
    return assemble_trusted_host_promotion_v2(
        trusted_result=trusted if trusted is not None else _trusted(),
        loaded_policy=POLICY,
        identity=IDENTITY,
        origin=ORIGIN,
        toolchain_digest=toolchain_digest,
        host_owned_config_digest=config_digest,
    )


def _reason(exc_info) -> str:
    return exc_info.value.reason_code


# =============================================================================
# The happy paths, so every refusal below means something
# =============================================================================


def test_authoritative_ci_promotion_succeeds() -> None:
    promoted = _assemble_ci()
    assert promoted.result.conclusion is RequiredCheckConclusionV2.SUCCESS
    assert promoted.result.head_sha == HEAD
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI
    assert promoted.provenance.authority_effect is AuthorityEffectV2.PROMOTABLE
    assert promoted.provenance.required_check_digest == compute_required_check_digest_v2(promoted.result)


def test_a_red_ci_run_promotes_as_a_failure_not_as_absence() -> None:
    """A real red must reach readiness as a red. Dropping it would be as wrong
    as fabricating a green."""

    promoted = _assemble_ci(
        _snapshot(
            observations=[
                _obs(
                    conclusion="failure",
                    producer_attestation=_attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1, outcome="failure"),
                )
            ]
        )
    )
    assert promoted.result.conclusion is RequiredCheckConclusionV2.FAILURE


def test_trusted_host_promotion_succeeds() -> None:
    promoted = _assemble_host()
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.TRUSTED_HOST_PROMOTION
    assert promoted.provenance.ci_run_id is None


def test_head_and_tested_merge_stay_distinct_facts() -> None:
    """`RequiredCheckResultV2.head_sha` keeps its frozen meaning; the tree that
    actually ran is recorded separately."""

    promoted = _assemble_ci()
    assert promoted.result.head_sha == HEAD
    assert promoted.provenance.head_sha == HEAD
    assert promoted.provenance.tested_merge_sha == MERGE
    assert promoted.provenance.head_sha != promoted.provenance.tested_merge_sha


def test_verifier_accepts_a_correctly_assembled_pair() -> None:
    promoted = _assemble_ci()
    verify_required_check_provenance_set_v2(
        checks=[promoted.result],
        provenance=[promoted.provenance],
        identity=IDENTITY,
        loaded_policy=POLICY,
    )


# =============================================================================
# C0-T1..C0-T9 -- identity and tree binding
# =============================================================================


def test_c0_t1_check_from_another_head_is_refused() -> None:
    snapshot = _snapshot(acquisition={
        "acquired_by": "aiops-acquire-authoritative-checks-v2",
        "api_host": "api.github.com",
        "repository": REPO,
        "head_sha": "9" * 40,
    })
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_HEAD_MISMATCH_REASON_V2


def test_c0_t5_check_from_another_repository_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(identity=_identity(repo="mglpsw/interleitos"))
    assert _reason(exc) == PROVENANCE_REPOSITORY_MISMATCH_REASON_V2


def test_c0_t6_synthetic_merge_with_divergent_parents_is_refused() -> None:
    snapshot = _snapshot(tested_merge_parents=["f" * 40, HEAD])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_PARENTAGE_MISMATCH_REASON_V2


def test_c0_t6_parent_order_is_significant() -> None:
    """`[head, base]` is a different merge from `[base, head]`."""

    snapshot = _snapshot(tested_merge_parents=[HEAD, BASE])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_PARENTAGE_MISMATCH_REASON_V2


def test_c0_t6_a_third_parent_is_refused() -> None:
    snapshot = _snapshot(tested_merge_parents=[BASE, HEAD, "8" * 40])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_PARENTAGE_MISMATCH_REASON_V2


def test_c0_t7_base_moved_after_the_merge_was_generated() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(identity=_identity(base_sha="7" * 40))
    assert _reason(exc) == PROVENANCE_PARENTAGE_MISMATCH_REASON_V2


def test_c0_t8_snapshot_merge_sha_must_match_the_identity() -> None:
    """C0-T8. The snapshot must describe THIS merge.

    There is deliberately no separate `executed_tree_sha` check any more: a
    Codex review showed the acquirer could only ever copy the caller's own
    `--tested-merge-sha` into that field, so comparing it to
    `identity.tested_merge_sha` compared the caller's input against itself.
    What actually binds the run to this merge is the run's OWN base/head, which
    GitHub reports -- see `_require_run_executed_this_merge`."""

    snapshot = _snapshot(tested_merge_sha="2" * 40)
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_TESTED_MERGE_MISMATCH_REASON_V2


# =============================================================================
# C0-T24 -- per-origin rules
# =============================================================================


@pytest.mark.parametrize(
    "event_type,event_action",
    [("pull_request_target", "synchronize"), ("manual", "manual"), ("replay", "replay")],
)
def test_c0_t24_origins_without_a_declared_rule_are_unsupported(event_type: str, event_action: str) -> None:
    """`pull_request_target`, `manual` and `replay` must never inherit
    synthetic-merge semantics by default. The shipped policy declares only
    `pull_request`, so every other origin is ineligible rather than assumed."""

    origin = RunOriginV2(event_type=event_type, event_action=event_action, delivery_id="d-2")
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(origin=origin)
    assert _reason(exc) == PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2


# =============================================================================
# C0-T3, C0-T4, C0-T13, C0-T19, C0-T20 -- producer and workflow identity
# =============================================================================


def test_c0_t3_a_check_from_a_non_allowlisted_workflow_is_refused() -> None:
    snapshot = _snapshot(observations=[_obs(workflow_path=".github/workflows/attacker.yml")])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_MISSING_REASON_V2


def test_c0_t4_a_producer_workflow_at_an_unpinned_sha_is_refused() -> None:
    """C0-T4, restated again after Codex round 7.

    Round 4 moved base-ownership onto a SHA-pinned reusable workflow; round 7
    showed the pin proves only which workflow the run LOADED, never which job
    wrote the artifact. Identity now comes from the producing run BEING the
    pinned base-owned workflow, so the SHA compared is the run's own workflow
    commit."""

    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    )

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_obs(workflow_sha="e" * 40)]))
    assert _reason(exc) == PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2


def test_a_pull_ref_execution_is_never_promotable() -> None:
    """Round 4 made a pull-ref execution the ordinary promotable path; round 7
    proved that path forgeable and it is now refused outright.

    The refusal is not about the ref. It is about WHO could have written the
    evidence: a run executing under a pull ref is the pull request's own run,
    and every field the verifier checks is a value the pull request knows."""

    from app.agent_review.authoritative_producer_evidence_v2 import (
        PRODUCER_PR_WRITABLE_REASON_V2,
    )

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_pr_triggered_obs()]))
    assert _reason(exc) == PRODUCER_PR_WRITABLE_REASON_V2


def test_c0_t13_a_non_allowlisted_app_is_refused() -> None:
    snapshot = _snapshot(observations=[_obs(app_slug="attacker-app")])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_MISSING_REASON_V2


def test_c0_t20_an_allowlisted_check_name_from_a_different_producer_is_refused() -> None:
    """The required check is `pytest`; the entitled producer is a job called
    `Validate repository`. A PR job named `pytest` is not it."""

    snapshot = _snapshot(observations=[_obs(check_run_name="pytest", app_slug="attacker-app")])
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_MISSING_REASON_V2


def test_c0_t19_a_record_whose_workflow_disagrees_with_policy_is_refused_at_verify() -> None:
    promoted = _assemble_ci()
    forged = promoted.provenance.model_dump(mode="json")
    forged["workflow_path"] = ".github/workflows/attacker.yml"
    record = RequiredCheckProvenanceV2.model_validate(
        {**forged, "provenance_digest": _redigest(forged)}
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result], provenance=[record], identity=IDENTITY, loaded_policy=POLICY
        )
    assert _reason(exc) == PROVENANCE_WORKFLOW_IDENTITY_MISMATCH_REASON_V2


def test_a_record_whose_producer_disagrees_with_policy_is_refused_at_verify() -> None:
    promoted = _assemble_ci()
    forged = promoted.provenance.model_dump(mode="json")
    forged["verifier_identity"] = "attacker-app"
    record = RequiredCheckProvenanceV2.model_validate({**forged, "provenance_digest": _redigest(forged)})
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result], provenance=[record], identity=IDENTITY, loaded_policy=POLICY
        )
    assert _reason(exc) == PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2


def test_a_check_with_no_policy_entry_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        assemble_authoritative_ci_promotion_v2(
            check_name="mypy",
            snapshot=_snapshot(),
            loaded_policy=POLICY,
            identity=IDENTITY,
            origin=ORIGIN,
            toolchain_digest=TOOLCHAIN,
        )
    assert _reason(exc) == PROVENANCE_PRODUCER_NOT_ALLOWLISTED_REASON_V2


def _redigest(payload: dict) -> str:
    from app.common.strict_json import canonical_json_digest_hex

    material = {k: v for k, v in payload.items() if k != "provenance_digest"}
    return canonical_json_digest_hex(material)


# =============================================================================
# C0-T22, C0-T23 -- source separation and the deferred B3 premises
# =============================================================================


def test_c0_t22_an_advisory_executor_result_is_never_promotable() -> None:
    """The whole reason `#201-C0` exists: pytest under the isolated executor is
    `UNTRUSTED_ADVISORY` forever, no matter what CI says about a check with the
    same name."""

    advisory = _trusted(authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY)
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_host(advisory)
    assert _reason(exc) == PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2


@pytest.mark.parametrize(
    "outcome",
    [
        TrustedCheckOutcomeV2.TIMEOUT,
        TrustedCheckOutcomeV2.OOM,
        TrustedCheckOutcomeV2.CANCELLED,
        TrustedCheckOutcomeV2.INFRA_FAILURE,
    ],
)
def test_environmental_outcomes_never_promote(outcome: TrustedCheckOutcomeV2) -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_host(_trusted(outcome=outcome))
    assert _reason(exc) == PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2


def test_c0_t23_a_data_only_tool_without_a_toolchain_digest_is_refused() -> None:
    """`#201-B3` recorded that `host_owned_config: true` is necessary but not
    sufficient and deferred the material proof to C0. A missing digest is
    refused here rather than assumed checked upstream."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_host(toolchain_digest=None)
    assert _reason(exc) == PROVENANCE_TOOLCHAIN_UNVERIFIED_REASON_V2


def test_c0_t23_a_data_only_tool_without_a_config_digest_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_host(config_digest=None)
    assert _reason(exc) == PROVENANCE_CONFIG_UNVERIFIED_REASON_V2


def test_host_promotion_binds_to_the_run_identity() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        assemble_trusted_host_promotion_v2(
            trusted_result=_trusted(),
            loaded_policy=POLICY,
            identity=_identity(pr_number=99),
            origin=ORIGIN,
            toolchain_digest=TOOLCHAIN,
            host_owned_config_digest=CONFIG_DIGEST,
        )
    assert _reason(exc) == PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2


def test_the_two_paths_produce_distinguishable_provenance() -> None:
    ci = _assemble_ci().provenance
    host = _assemble_host().provenance
    assert ci.source_kind is not host.source_kind
    assert ci.ci_run_id is not None and host.ci_run_id is None


# =============================================================================
# C0-T14, C0-T15, C0-T21, C0-T25 -- the gate-facing verifier
# =============================================================================


def test_c0_t21_a_check_with_no_provenance_is_refused() -> None:
    promoted = _assemble_ci()
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result], provenance=[], identity=IDENTITY, loaded_policy=POLICY
        )
    assert _reason(exc) == PROVENANCE_INVALID_REASON_V2


def test_c0_t21_a_spare_provenance_record_is_refused() -> None:
    """Total in both directions: a spare record means the caller believes
    something about this run the check set does not reflect."""

    promoted = _assemble_ci()
    host = _assemble_host()
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result],
            provenance=[promoted.provenance, host.provenance],
            identity=IDENTITY,
            loaded_policy=POLICY,
        )
    assert _reason(exc) == PROVENANCE_INVALID_REASON_V2


def test_c0_t25_the_217_attack_a_hand_built_green_named_pytest() -> None:
    """The exact defect `#217` describes: an object called `pytest` with
    `conclusion=success`, satisfying the gate by name alone. It now has no
    provenance, so it never reaches readiness."""

    forged = RequiredCheckResultV2(
        check_name="pytest",
        required=True,
        deterministic=True,
        conclusion=RequiredCheckConclusionV2.SUCCESS,
        head_sha=HEAD,
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        verify_required_check_provenance_set_v2(
            checks=[forged], provenance=[], identity=IDENTITY, loaded_policy=POLICY
        )


def test_provenance_for_a_different_check_does_not_cover_this_one() -> None:
    """Matching by name would accept this. Matching by digest does not."""

    promoted = _assemble_ci()
    red = RequiredCheckResultV2(
        check_name="pytest",
        required=True,
        deterministic=True,
        conclusion=RequiredCheckConclusionV2.FAILURE,
        head_sha=HEAD,
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[red], provenance=[promoted.provenance], identity=IDENTITY, loaded_policy=POLICY
        )
    assert _reason(exc) == PROVENANCE_MISSING_REASON_V2


def test_c0_t15_provenance_from_another_run_is_refused() -> None:
    promoted = _assemble_ci()
    other_identity = _identity(pr_number=99)
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result],
            provenance=[promoted.provenance],
            identity=other_identity,
            loaded_policy=POLICY,
        )
    assert _reason(exc) == PROVENANCE_RUN_IDENTITY_MISMATCH_REASON_V2


def test_c0_t14_a_tampered_sidecar_cannot_even_be_constructed() -> None:
    promoted = _assemble_ci()
    tampered = promoted.provenance.model_dump(mode="json")
    tampered["observed_conclusion"] = "failure"
    with pytest.raises(Exception):
        RequiredCheckProvenanceV2.model_validate(tampered)


def test_duplicate_identical_checks_are_refused() -> None:
    """Two identical checks cannot be told apart by the join key, so a 1:1
    binding is not expressible. Refused rather than silently deduplicated."""

    promoted = _assemble_ci()
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result, promoted.result],
            provenance=[promoted.provenance],
            identity=IDENTITY,
            loaded_policy=POLICY,
        )
    assert _reason(exc) == PROVENANCE_INVALID_REASON_V2


def test_a_non_promotable_record_is_refused_at_the_gate() -> None:
    promoted = _assemble_ci()
    downgraded = build_required_check_provenance_v2(
        **{
            k: v
            for k, v in promoted.provenance.model_dump().items()
            if k != "provenance_digest"
        }
        | {"authority_effect": AuthorityEffectV2.NONE, "semantic_class": promoted.provenance.semantic_class}
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result], provenance=[downgraded], identity=IDENTITY, loaded_policy=POLICY
        )
    assert _reason(exc) == PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2


def test_a_record_assembled_under_a_different_policy_is_refused() -> None:
    """Otherwise a record built under a permissive older policy could be
    replayed against a tightened one."""

    promoted = _assemble_ci()
    other_policy = load_authoritative_check_policy_v2(FIXTURES / "interleitos")
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_required_check_provenance_set_v2(
            checks=[promoted.result],
            provenance=[promoted.provenance],
            identity=IDENTITY,
            loaded_policy=other_policy,
        )
    assert _reason(exc) == PROVENANCE_POLICY_DIGEST_MISMATCH_REASON_V2


def test_an_empty_check_set_verifies_vacuously() -> None:
    """C0 proves provenance for what IS submitted. Whether a required check may
    be absent at all is readiness's existing fail-closed precedence, in
    `#201-C` -- not something this verifier should quietly take over."""

    verify_required_check_provenance_set_v2(
        checks=[], provenance=[], identity=IDENTITY, loaded_policy=POLICY
    )


# =============================================================================
# Codex review round 1 -- regressions
# =============================================================================


def test_a_run_produced_against_a_different_base_is_refused() -> None:
    """Codex finding 2. A check run is scoped to a HEAD, but what it executed
    is a merge of that head with a base. If the base advances without the head
    moving, a green from the previous base plus a freshly created merge commit
    whose parents check out would otherwise line up perfectly."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_pr_triggered_obs(run_base_sha="7" * 40)]))
    assert _reason(exc) == PROVENANCE_OBSERVATION_STALE_REASON_V2


def test_a_run_whose_own_head_disagrees_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_pr_triggered_obs(run_head_sha="7" * 40)]))
    assert _reason(exc) == PROVENANCE_HEAD_MISMATCH_REASON_V2


def test_local_parentage_alone_no_longer_suffices() -> None:
    """The merge commit is well-formed and its parents are exactly
    [base, head] -- and it is still refused, because the observed run was
    produced against a different base. Parentage proves the shape of the merge,
    never which merge was executed."""

    snapshot = _snapshot(observations=[_pr_triggered_obs(run_base_sha="7" * 40)])
    assert tuple(snapshot.tested_merge_parents) == (BASE, HEAD)
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        _assemble_ci(snapshot)


def test_reassembly_rejects_a_fabricated_green_with_a_consistent_sidecar() -> None:
    """Codex finding 1, at the library boundary.

    The submitted pair is internally perfect: correct self-digest, correct run
    identity, correct policy digests, correct producer strings -- everything
    `verify_required_check_provenance_set_v2` inspects. It is still refused,
    because the evidence produces a failure, not a success."""

    from app.agent_review.required_check_assembly_v2 import reassemble_and_verify_required_checks_v2

    evidence = _snapshot(observations=[_obs(conclusion="failure")])
    fabricated = _assemble_ci()  # derived from a GREEN snapshot

    # The structural verifier is satisfied by the fabricated pair...
    verify_required_check_provenance_set_v2(
        checks=[fabricated.result],
        provenance=[fabricated.provenance],
        identity=IDENTITY,
        loaded_policy=POLICY,
    )

    # ...and re-derivation against the real evidence still refuses it.
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        reassemble_and_verify_required_checks_v2(
            checks=[fabricated.result],
            provenance=[fabricated.provenance],
            identity=IDENTITY,
            origin=ORIGIN,
            loaded_policy=POLICY,
            snapshot=evidence,
            toolchain_digest=TOOLCHAIN,
        )
    assert _reason(exc) == PROVENANCE_INVALID_REASON_V2


def test_reassembly_accepts_a_genuinely_derived_pair() -> None:
    from app.agent_review.required_check_assembly_v2 import reassemble_and_verify_required_checks_v2

    snapshot = _snapshot()
    promoted = _assemble_ci(snapshot)
    reassemble_and_verify_required_checks_v2(
        checks=[promoted.result],
        provenance=[promoted.provenance],
        identity=IDENTITY,
        origin=ORIGIN,
        loaded_policy=POLICY,
        snapshot=snapshot,
        toolchain_digest=TOOLCHAIN,
    )


def test_reassembly_refuses_a_trusted_host_record() -> None:
    """`#201-B3`'s executor has no operational producer yet, so there is
    nothing to re-derive a host promotion from. Accepting an un-derivable
    assertion is exactly what re-derivation exists to stop."""

    from app.agent_review.required_check_assembly_v2 import reassemble_and_verify_required_checks_v2

    host = _assemble_host()
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        reassemble_and_verify_required_checks_v2(
            checks=[host.result],
            provenance=[host.provenance],
            identity=IDENTITY,
            origin=ORIGIN,
            loaded_policy=POLICY,
            snapshot=_snapshot(),
            toolchain_digest=TOOLCHAIN,
        )
    assert _reason(exc) == PROVENANCE_SUBJECT_RESULT_NOT_PROMOTABLE_REASON_V2


# =============================================================================
# Codex review round 2 -- regressions
# =============================================================================


def test_only_synthetic_merge_parentage_can_promote() -> None:
    """Codex round 2, finding A.

    `explicit_tested_tree` was backed by nothing but the caller's own
    `--tested-merge-sha` echoed into the snapshot, so the assembler's
    "the tree that ran must be the tree the identity claims" check compared the
    caller's input against itself. The field is gone and the rule is refused.

    The policy loader now rejects `explicit_tested_tree` outright, so this is
    belt-and-braces: even if a policy object were constructed in-process with
    that rule, the assembler would still refuse to promote from it."""

    from app.agent_review.authoritative_check_policy_v2 import (
        AuthoritativeCheckEntryV2,
        ExecutedTreeRuleV2,
        OriginRulesV2,
    )

    entry = AuthoritativeCheckEntryV2.model_construct(
        check_name="pytest",
        workflow_path=".github/workflows/ci.yml",
        job_name="Validate repository",
        verifier_identity="github-actions",
        producer_kind="sha_pinned_reusable_workflow",
        producer_workflow={
            "repository": "mglpsw/aiops-orchestrator",
            "path": ".github/workflows/authoritative-checks.reusable.yml",
            "sha": "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96",
        },
        permitted_conclusions=("success", "failure"),
        origin_rules=OriginRulesV2.model_construct(
            pull_request=None,
            pull_request_target=ExecutedTreeRuleV2.EXPLICIT_TESTED_TREE,
            manual=None,
            replay=None,
        ),
    )
    policy = type(POLICY)(
        policy=POLICY.policy.model_copy(update={"authoritative_checks": (entry,)}),
        policy_source_bytes_digest=POLICY.policy_source_bytes_digest,
        policy_source_semantic_digest=POLICY.policy_source_semantic_digest,
    )
    origin = RunOriginV2(event_type="pull_request_target", event_action="synchronize", delivery_id="d-3")

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        assemble_authoritative_ci_promotion_v2(
            check_name="pytest",
            snapshot=_snapshot(observations=[_pr_triggered_obs(run_event="pull_request_target", producer_trigger="pull_request_target")]),
            loaded_policy=policy,
            identity=IDENTITY,
            origin=origin,
            toolchain_digest=TOOLCHAIN,
        )
    assert _reason(exc) == PROVENANCE_ORIGIN_UNSUPPORTED_REASON_V2


def test_the_snapshot_carries_no_self_referential_execution_field() -> None:
    """The acquirer could only ever copy `--tested-merge-sha` into it, so a
    field named for an independently observed execution tree would be a
    tautology wearing the costume of a proof."""

    assert "executed_tree_sha" not in _snapshot().model_dump()


# =============================================================================
# Codex review round 3 -- regressions
# =============================================================================


def test_an_observation_whose_event_differs_from_the_origin_is_refused() -> None:
    """Codex round 3, finding A -- and the mechanism by which the previous
    revision was actually exploitable.

    The policy demands a default-branch `workflow_ref`. Genuine `pull_request`
    runs record a PULL ref, while `pull_request_target` runs record the default
    branch -- so the only observations able to satisfy the policy today are
    exactly the base-executed ones. Declaring `pull_request` as the origin then
    granted them the synthetic-merge rule."""

    from app.agent_review.required_check_provenance_v2 import (
        PROVENANCE_ORIGIN_EVENT_MISMATCH_REASON_V2,
    )

    snapshot = _snapshot(
        observations=[_pr_triggered_obs(run_event="pull_request_target")]
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(snapshot)
    assert _reason(exc) == PROVENANCE_ORIGIN_EVENT_MISMATCH_REASON_V2


def test_a_matching_event_still_promotes() -> None:
    """So the refusal above is about the mismatch, not about the check itself."""

    assert _assemble_ci().result.conclusion is RequiredCheckConclusionV2.SUCCESS


# =============================================================================
# Codex review round 7 -- the producing run must be outside the PR's reach
# =============================================================================
#
# Round 4 established that a producer must be REPRESENTABLE. Round 7 showed the
# first representable producer was not AUTHENTICATED: a SHA-pinned reusable
# workflow proves which workflow a run loaded, never which job wrote the
# artifact that run uploaded. Inside a PR-triggered run the pull request can
# write that job itself.
#
# The fix is not a better check on the message. It is refusing to accept
# messages from inside the pull request's own run at all.


def _producer_reason(name: str) -> str:
    import app.agent_review.authoritative_producer_evidence_v2 as evidence

    return getattr(evidence, name)


def test_a_pr_job_publishing_a_perfect_attestation_is_refused() -> None:
    """Negative 1. The attestation is flawless -- correct repository, PR number,
    base, head, executed tree, run id, attempt, outcome and self-digest. Every
    one of those is a value the pull request already knows, so checking them
    proves only that the forger was careful."""

    forged = _pr_triggered_obs(
        producer_attestation=_attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1)
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[forged]))
    assert _reason(exc) == _producer_reason("PRODUCER_PR_WRITABLE_REASON_V2")


def test_a_pr_run_referencing_the_pinned_workflow_is_still_refused() -> None:
    """Negative 2. The run really did load the pinned base-owned workflow --
    `referenced_workflows` carries its full commit SHA, which the pull request
    cannot forge. It is still refused, because loading a workflow says nothing
    about which job uploaded the artifact."""

    observation = _pr_triggered_obs(
        referenced_workflows=[
            {"path": f"{REPO}/{PRODUCER_WORKFLOW_PATH}", "sha": PRODUCER_WORKFLOW_SHA, "ref": None}
        ]
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("PRODUCER_PR_WRITABLE_REASON_V2")


def test_a_pr_run_copying_every_true_field_of_the_run_is_refused() -> None:
    """Negative 3. Nothing is inconsistent anywhere: the observation's own run
    id, attempt, base and head all agree with the attestation and with the
    identity. Consistency is not provenance."""

    observation = _pr_triggered_obs(
        workflow_run_id="4242",
        run_attempt=3,
        producer_attestation=_attestation(REPO, 7, BASE, HEAD, MERGE, "4242", 3),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("PRODUCER_PR_WRITABLE_REASON_V2")


def test_an_attestation_from_the_right_run_but_a_pr_writable_trigger_is_refused() -> None:
    """Negative 4. The artifact genuinely came from the workflow run named in
    the attestation. That run was PR-writable, so "the right run" is not a
    property worth having."""

    observation = _obs(producer_trigger="pull_request_target", run_event="workflow_run")
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("PRODUCER_PR_WRITABLE_REASON_V2")


def test_a_base_owned_run_republishing_an_upstream_artifact_is_refused() -> None:
    """Negative 5. The producer is genuinely base-owned and genuinely not
    PR-writable -- and it forwarded the pull request's own artifact instead of
    re-running the check.

    Base-ownership makes a producer trustworthy about what IT did. GitHub's own
    guidance is that artifacts from a workflow which processed untrusted code
    are untrusted data; carrying one across the boundary unchanged launders it
    rather than verifying it."""

    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            check_execution_mode="upstream_artifact_republished",
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2")


def test_merge_group_is_not_assumed_base_owned() -> None:
    """Negative 6. GitHub runs each event's workflow from the ref associated
    with that event, and a merge group has its own ref and SHA. Treating it as
    base-owned because the name sounds post-merge would be exactly the
    optimistic reuse this slice keeps removing. It needs its own model."""

    observation = _obs(producer_trigger="merge_group")
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("PRODUCER_TRIGGER_UNSUPPORTED_REASON_V2")


@pytest.mark.parametrize(
    "override",
    [
        {"workflow_sha": "e" * 40},
        {"workflow_repository": "mglpsw/somewhere-else"},
        {"workflow_execution_ref": "refs/heads/attacker"},
    ],
)
def test_a_base_owned_run_with_a_divergent_producer_identity_is_refused(override: dict) -> None:
    """Negative 7. Right event, right trigger, wrong producer. Each field is
    checked independently, so satisfying the others buys nothing."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_obs(**override)]))
    assert _reason(exc) == _producer_reason("PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2")


def test_a_divergent_producer_job_name_is_refused() -> None:
    """Negative 7, job axis. A different job in the right base-owned workflow is
    a different producer, and is not selected at all."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[_obs(check_run_name="some-other-job")]))
    assert _reason(exc) == PROVENANCE_MISSING_REASON_V2


def test_a_producer_that_echoes_a_caller_supplied_executed_sha_is_refused() -> None:
    """Negative 8. The tautology removed in round 2, arriving through a new
    door: the producer did not observe the tree it ran, it was handed the value
    and repeated it. A value that travelled in a circle is not evidence, no
    matter how base-owned the courier."""

    observation = _obs(
        producer_attestation=_attestation(
            REPO, 7, BASE, HEAD, MERGE, "900", 1,
            executed_sha_derivation="caller_supplied",
        )
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _assemble_ci(_snapshot(observations=[observation]))
    assert _reason(exc) == _producer_reason("EXECUTED_TREE_NOT_OBSERVED_REASON_V2")


def test_a_base_owned_workflow_run_producer_promotes() -> None:
    """The load-bearing positive.

    Without this, "fail closed" would just mean C0 promotes nothing again --
    the round-4 state, reached by a different route. This fixture is the whole
    difference between "the model cannot express a real producer" and "targets
    have not adopted the producer the model expresses".

    Review origin stays `pull_request` (that is what the review is ABOUT) while
    the producer trigger is `workflow_run` (that is how the producer RAN). Those
    two being different facts is the point of the producer-evidence model."""

    promoted = _assemble_ci(_snapshot(observations=[_obs()]))

    assert promoted.result.conclusion is RequiredCheckConclusionV2.SUCCESS
    assert promoted.result.head_sha == HEAD
    assert promoted.provenance.authority_effect is AuthorityEffectV2.PROMOTABLE
    assert promoted.provenance.source_kind is RequiredCheckSourceKindV2.AUTHORITATIVE_CI
    # The review origin is unchanged by the producer's own trigger.
    assert promoted.provenance.event_type == "pull_request"
    assert promoted.provenance.ci_run_id == "900"


def test_the_positive_path_still_refuses_a_failing_producer_verdict() -> None:
    """The promotable path promotes a FAILURE just as readily as a success. A
    bridge that only carries greens is not a provenance bridge."""

    observation = _obs(
        conclusion="failure",
        producer_attestation=_attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1, outcome="failure"),
    )
    promoted = _assemble_ci(_snapshot(observations=[observation]))
    assert promoted.result.conclusion is RequiredCheckConclusionV2.FAILURE
