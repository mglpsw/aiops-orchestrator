"""SHA-pinned reusable-workflow producer evidence (`#201-C0`, C0-8).

Codex round 4 found that C0 could not promote anything from real GitHub data:
the policy demanded `workflow_ref == refs/heads/<default>` as proof of base
ownership, while genuine `pull_request` runs record `refs/pull/<n>/merge`. No
target configuration could satisfy it, because `RunOriginV2` is frozen and does
not admit a base-owned producer trigger.

The ratified resolution separates five facts that were previously collapsed
into one string:

```text
review_origin          -- what the REVIEW is about        (RunOriginV2, frozen)
producer_trigger       -- how the PRODUCER was triggered  (not RunOriginV2)
workflow_execution_ref -- factual observation, proves nothing on its own
producer workflow      -- immutable identity: path @ full 40-char SHA
executed-tree evidence -- an attestation, not an inference
```

Authority comes from the SHA-pinned reusable workflow plus its attestation, NOT
from the ref a run happened to execute under.
"""

from __future__ import annotations

import pytest

from app.agent_review.authoritative_producer_evidence_v2 import (
    PRODUCER_ATTESTATION_MISSING_REASON_V2,
    PRODUCER_ATTESTATION_MISMATCH_REASON_V2,
    PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2,
    ProducerAttestationV2,
    ProducerWorkflowIdentityV2,
    ProducerWorkflowReferenceV2,
    compute_producer_attestation_digest_v2,
    verify_producer_workflow_pinned_v2,
    verify_producer_attestation_v2,
)
from app.agent_review.contracts_v2 import RunIdentityV2
from app.agent_review.required_check_provenance_v2 import RequiredCheckProvenanceErrorV2

REPO = "mglpsw/AgentEscala"
BASE = "c" * 40
HEAD = "a" * 40
MERGE = "d" * 40
WORKFLOW_SHA = "9" * 40

IDENTITY = RunIdentityV2(
    repo=REPO,
    pr_number=7,
    base_sha=BASE,
    head_sha=HEAD,
    tested_merge_sha=MERGE,
    toolrepo_sha="b" * 40,
    profile_hash="1" * 64,
    policy_hash="2" * 64,
    manifest_hash="3" * 64,
    evidence_hash="4" * 64,
)

PINNED = ProducerWorkflowIdentityV2(
    repository="mglpsw/aiops-orchestrator",
    path=".github/workflows/authoritative-checks.reusable.yml",
    sha=WORKFLOW_SHA,
)


def _reference(**overrides: object) -> ProducerWorkflowReferenceV2:
    fields: dict[str, object] = {
        "path": "mglpsw/aiops-orchestrator/.github/workflows/authoritative-checks.reusable.yml",
        "sha": WORKFLOW_SHA,
    }
    fields.update(overrides)
    return ProducerWorkflowReferenceV2(**fields)


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


# -- the workflow must be pinned by full SHA ----------------------------------


def test_a_pinned_reusable_workflow_is_accepted() -> None:
    verify_producer_workflow_pinned_v2(pinned=PINNED, referenced=(_reference(),))


def test_a_workflow_referenced_at_a_different_sha_is_refused() -> None:
    """The pin is the whole point: the same path at a different SHA is a
    different workflow, and a mutable ref proves nothing."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_workflow_pinned_v2(pinned=PINNED, referenced=(_reference(sha="e" * 40),))
    assert exc.value.reason_code == PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2


def test_a_workflow_at_a_different_path_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_workflow_pinned_v2(
            pinned=PINNED,
            referenced=(_reference(path="mglpsw/aiops-orchestrator/.github/workflows/other.yml"),),
        )
    assert exc.value.reason_code == PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2


def test_a_run_referencing_no_reusable_workflow_is_refused() -> None:
    """A run that inlined its own job never referenced the pinned producer."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_workflow_pinned_v2(pinned=PINNED, referenced=())
    assert exc.value.reason_code == PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2


def test_the_pinned_sha_must_be_a_full_forty_character_sha() -> None:
    """An abbreviated SHA is not an immutable identity."""

    with pytest.raises(Exception):
        ProducerWorkflowIdentityV2(
            repository="mglpsw/aiops-orchestrator",
            path=".github/workflows/authoritative-checks.reusable.yml",
            sha="9" * 7,
        )


# -- the attestation binds the executed tree ----------------------------------


def test_a_matching_attestation_is_accepted() -> None:
    verify_producer_attestation_v2(
        attestation=_attestation(), identity=IDENTITY, workflow_run_id="900", run_attempt=1
    )


def test_a_missing_attestation_is_refused() -> None:
    """Without it there is no evidence of which tree ran -- which is the whole
    defect the previous revision carried."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_attestation_v2(
            attestation=None, identity=IDENTITY, workflow_run_id="900", run_attempt=1
        )
    assert exc.value.reason_code == PRODUCER_ATTESTATION_MISSING_REASON_V2


def test_an_attestation_for_a_different_executed_tree_is_refused() -> None:
    """`attested_executed_sha == identity.tested_merge_sha` is the binding that
    replaces the tautology removed in round 2."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_attestation_v2(
            attestation=_attestation(executed_sha="f" * 40),
            identity=IDENTITY,
            workflow_run_id="900",
            run_attempt=1,
        )
    assert exc.value.reason_code == PRODUCER_ATTESTATION_MISMATCH_REASON_V2


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "mglpsw/somewhere-else"),
        ("pr_number", 99),
        ("base_sha", "7" * 40),
        ("head_sha", "8" * 40),
    ],
)
def test_an_attestation_describing_another_run_is_refused(field: str, value: object) -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_attestation_v2(
            attestation=_attestation(**{field: value}),
            identity=IDENTITY,
            workflow_run_id="900",
            run_attempt=1,
        )
    assert exc.value.reason_code == PRODUCER_ATTESTATION_MISMATCH_REASON_V2


def test_an_attestation_from_another_workflow_run_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_attestation_v2(
            attestation=_attestation(), identity=IDENTITY, workflow_run_id="999", run_attempt=1
        )
    assert exc.value.reason_code == PRODUCER_ATTESTATION_MISMATCH_REASON_V2


def test_an_attestation_from_another_attempt_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        verify_producer_attestation_v2(
            attestation=_attestation(), identity=IDENTITY, workflow_run_id="900", run_attempt=2
        )
    assert exc.value.reason_code == PRODUCER_ATTESTATION_MISMATCH_REASON_V2


def test_a_tampered_attestation_cannot_be_constructed() -> None:
    tampered = _attestation().model_dump(mode="json")
    tampered["test_outcome"] = "failure"
    with pytest.raises(Exception):
        ProducerAttestationV2.model_validate(tampered)


def test_attestation_digest_is_deterministic_and_discriminating() -> None:
    assert _attestation().attestation_digest == _attestation().attestation_digest
    assert _attestation().attestation_digest != _attestation(executed_sha="f" * 40).attestation_digest
