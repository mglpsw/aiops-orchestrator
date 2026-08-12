"""Evidence that a base-owned producer ran, and which tree it executed.

`#201-C0`, C0-8. Added after a Codex review found that C0 could not promote
anything from real GitHub data.

## The five facts that were previously one string

The earlier design used a single `workflow_ref` to mean both "which producer is
this" and "was the producer base-owned". That conflation was unsatisfiable:
genuine `pull_request` runs execute under `refs/pull/<n>/merge`, while the
policy demanded `refs/heads/<default_branch>`, so no real observation could ever
match. Worse, the only runs that DID record a default-branch ref were
`pull_request_target` runs, which execute the base rather than the merge.

They are separated here, and each is sourced from something that can actually
establish it:

```text
review_origin           what the REVIEW is about        RunOriginV2 (frozen, untouched)
producer_trigger        how the PRODUCER was triggered  observed, never inferred
workflow_execution_ref  the ref the run executed under  factual observation only
producer workflow       path @ full 40-char SHA         immutable identity
executed-tree evidence  an attestation                  proof, not inference
```

`RunOriginV2` is deliberately NOT extended. It is the frozen contract for the
origin of the REVIEW, not for a producer's internal trigger; conflating the two
is what made `workflow_run` look like it needed a place in a frozen enum.

## Why a SHA-pinned reusable workflow

`workflow_execution_ref` proves nothing on its own -- a pull request controls
the ref its own runs execute under. What a pull request cannot forge is which
reusable workflow a run *referenced*: GitHub records `referenced_workflows` with
the full commit SHA of the workflow it loaded. Pinning that SHA in the
base-owned policy gives an immutable producer identity that survives the PR
being able to edit files in its own tree.

That is also why the pin must be a full 40-character SHA. An abbreviated SHA or
a branch ref is mutable, and a mutable identity is not an identity.

## Why an attestation, and what it must not do

Even a correctly-identified producer does not reveal, through any API field,
which tree it checked out. So the producer emits it. The attestation job must
run WITHOUT a checkout of the pull request and without executing any of its
code -- otherwise the subject would be attesting to its own execution, which is
the `#201-B3` boundary violated in a new place.

`attested_executed_sha == identity.tested_merge_sha` is the binding that
replaces the tautological `executed_tree_sha` removed in round 2. The
difference is that this value is produced by a base-owned workflow pinned by
SHA, not copied from the caller's own argument.
"""

from __future__ import annotations

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
)
from app.agent_review.required_check_provenance_v2 import RequiredCheckProvenanceErrorV2
from app.common.strict_json import canonical_json_digest_hex

PRODUCER_ATTESTATION_SCHEMA_V2 = "agent-review.producer-attestation.v2"

PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2 = "required_check_provenance_producer_workflow_not_pinned"
PRODUCER_ATTESTATION_MISSING_REASON_V2 = "required_check_provenance_producer_attestation_missing"
PRODUCER_ATTESTATION_MISMATCH_REASON_V2 = "required_check_provenance_producer_attestation_mismatch"

ALL_PRODUCER_EVIDENCE_REASON_CODES_V2: tuple[str, ...] = (
    PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2,
    PRODUCER_ATTESTATION_MISSING_REASON_V2,
    PRODUCER_ATTESTATION_MISMATCH_REASON_V2,
)

# The producer's OWN trigger, deliberately a separate vocabulary from
# `RunOriginV2.event_type`. `workflow_run` appears here and nowhere near the
# frozen review-origin contract; it is still never promotable without an
# attestation of the checked-out tree.
ProducerTriggerV2 = Literal[
    "pull_request",
    "pull_request_target",
    "push",
    "workflow_run",
    "merge_group",
    "workflow_dispatch",
    "schedule",
]

# Only one kind is ratified. Others must arrive with their own evidence model,
# not by widening this literal and hoping the existing checks still apply.
ProducerKindV2 = Literal["sha_pinned_reusable_workflow"]

ExecutedTreeEvidenceKindV2 = Literal["producer_attestation"]


class ProducerWorkflowIdentityV2(ContractV2Model):
    """The immutable identity a base-owned policy pins.

    `sha` is a full 40-character commit SHA by type. A short SHA or a branch
    name would be mutable, and the entire point of this contract is that the
    producer cannot be swapped after the policy was written."""

    repository: Repository
    path: RelativePath
    sha: GitSha

    def referenced_path(self) -> str:
        """How GitHub spells this workflow in `referenced_workflows`."""

        return f"{self.repository}/{self.path}"


class ProducerWorkflowReferenceV2(ContractV2Model):
    """One entry of GitHub's `referenced_workflows`, as observed.

    `ref` is recorded because GitHub reports it, and deliberately NOT compared:
    a ref is mutable and proves nothing. The SHA is what carries identity."""

    path: SafeText
    sha: GitSha
    ref: SafeText | None = None


class ProducerAttestationV2(ContractV2Model):
    """Strict, hash-bound material emitted by the producer's attestation job.

    The job that emits this must not check out the pull request or execute any
    of its code. If it did, the subject would be attesting to its own
    execution -- the `#201-B3` boundary broken in a new place."""

    schema_id: Literal["agent-review.producer-attestation.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-authoritative-check-producer"]

    repository: Repository
    pr_number: PositiveInt
    base_sha: GitSha
    head_sha: GitSha
    # The tree the producer actually checked out and ran against.
    executed_sha: GitSha
    workflow_run_id: SafeIdentifier
    run_attempt: PositiveInt
    # Only resolved verdicts, matching the closed mapping in
    # `authoritative_ci_snapshot_v2`: an attestation cannot smuggle in a
    # non-verdict that the conclusion mapping would refuse.
    test_outcome: Literal["success", "failure"]
    policy_digest: Sha256
    toolchain_digest: Sha256
    attestation_digest: Sha256

    @model_validator(mode="after")
    def validate_digest(self) -> ProducerAttestationV2:
        if self.attestation_digest != compute_producer_attestation_digest_v2(self):
            raise ValueError("attestation_digest does not match the canonical attestation material")
        return self


def compute_producer_attestation_digest_v2(value: ProducerAttestationV2) -> str:
    return canonical_json_digest_hex(value.model_dump(mode="json", exclude={"attestation_digest"}))


class AuthoritativeProducerEvidenceV2(ContractV2Model):
    """The producer half of a promotion, kept separate from the review half.

    Carrying `review_origin_event_type` alongside `producer_trigger` is the
    point: they are different facts and were previously indistinguishable."""

    producer_kind: ProducerKindV2
    producer_trigger: ProducerTriggerV2
    workflow_execution_ref: SafeText
    producer_workflow: ProducerWorkflowIdentityV2
    job_name: SafeText
    verifier_identity: SafeIdentifier
    executed_tree_evidence_kind: ExecutedTreeEvidenceKindV2
    attested_executed_sha: GitSha


def verify_producer_workflow_pinned_v2(
    *,
    pinned: ProducerWorkflowIdentityV2,
    referenced: tuple[ProducerWorkflowReferenceV2, ...],
) -> ProducerWorkflowReferenceV2:
    """Require the run to have referenced exactly the pinned workflow.

    Matched on `(path, sha)`. The observed `ref` is ignored on purpose -- it is
    mutable, so agreeing on it would add no assurance and disagreeing on it
    would reject legitimate runs."""

    expected_path = pinned.referenced_path()
    for reference in referenced:
        if reference.path == expected_path and reference.sha == pinned.sha:
            return reference
    raise RequiredCheckProvenanceErrorV2(PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2)


def verify_producer_attestation_v2(
    *,
    attestation: ProducerAttestationV2 | None,
    identity,
    workflow_run_id: str,
    run_attempt: int,
) -> ProducerAttestationV2:
    """Bind the attestation to this run, this pull request, and this tree.

    `executed_sha == identity.tested_merge_sha` is the load-bearing check. The
    rest exist so an attestation cannot be lifted from a different run, a
    different attempt, or a different pull request and replayed here."""

    if attestation is None:
        raise RequiredCheckProvenanceErrorV2(PRODUCER_ATTESTATION_MISSING_REASON_V2)

    if (
        attestation.repository != identity.repo
        or attestation.pr_number != identity.pr_number
        or attestation.base_sha != identity.base_sha
        or attestation.head_sha != identity.head_sha
        or attestation.executed_sha != identity.tested_merge_sha
        or attestation.workflow_run_id != workflow_run_id
        or attestation.run_attempt != run_attempt
    ):
        raise RequiredCheckProvenanceErrorV2(PRODUCER_ATTESTATION_MISMATCH_REASON_V2)

    return attestation
