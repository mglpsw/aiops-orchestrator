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

## Why the producing RUN, not just the producing workflow

The first model here pinned a reusable workflow by full commit SHA and treated
that as producer identity. A Codex review then found the gap: the pin proves
which workflow a run LOADED, and nothing more. Inside a `pull_request` run the
pull request can call the pinned workflow AND add a job of its own that uploads
the attestation artifact -- filling in repository, PR number, base, head,
executed tree, run id and attempt, every one of which it already knows.

Checking those fields therefore proves only that the forger was careful. The
correct boundary is not a better check on the message; it is refusing messages
sent from inside a run the pull request can write into.

So identity comes from the producing run BEING the base-owned workflow:
triggered by `workflow_run`, whose definition GitHub loads from the default
branch, executing under a ref the pull request does not control, matched on
repository, path, commit SHA and job name.

`merge_group` is deliberately NOT treated as base-owned. GitHub runs each
event's workflow from the ref associated with that event, and a merge group has
its own ref and SHA; assuming base-ownership from the event name would be the
same optimistic reuse this module keeps removing.

`sha_pinned_reusable_workflow` stays declarable so a policy naming it earns a
precise refusal instead of a vague one. It becomes promotable only when the
attestation's ISSUER is cryptographically authenticated -- a separate, additive
mode, not something to improvise here.

## Why an attestation, and what it must not do

Even a correctly-identified producer does not reveal, through any API field,
which tree it checked out. So the producer emits it. The attestation job must
run WITHOUT a checkout of the pull request and without executing any of its
code -- otherwise the subject would be attesting to its own execution, which is
the `#201-B3` boundary violated in a new place.

`attested_executed_sha == identity.tested_merge_sha` is the binding that
replaces the tautological `executed_tree_sha` removed in round 2. The
difference is that this value is produced by a run the pull request cannot
write into, not copied from the caller's own argument.

Two further things the producer must declare, because being base-owned makes it
trustworthy about what IT did and not about what the pull request's run did:

- it re-executed the check itself, rather than republishing an artifact built
  by the pull request's own run. GitHub's own guidance is that artifacts from a
  workflow which processed untrusted code are untrusted data; forwarding one
  unchanged launders it rather than verifying it;
- it derived the executed tree from its own verified checkout, rather than
  repeating a value handed to it through workflow inputs. A value that
  travelled in a circle is not evidence, however trustworthy the courier.

## Round-7 architectural correction -- base-ownership is not the same axis as
## `#201-B3`'s theorem, and satisfying one does not satisfy the other

An independent audit of the model above, once implemented, found the gap this
paragraph exists to close. `check_execution_mode == "reexecuted_in_producer_
run"` means exactly what it says: the producer re-ran the PULL REQUEST'S OWN
test suite and reported ITS exit code. Everything above this paragraph proves
the workflow DEFINITION is base-owned -- which job ran, from which commit,
under which ref. None of it changes who authored the value being measured: the
subject's own test code still determines whether `pytest` exits 0 or 1.

`#201-B3` ratified a boundary, not a detector, and it is stated as a theorem,
not as a property of *where* code runs:

```text
controls(subject, success_signal)  =>  not authoritative(success_signal)
```

Re-running the subject's own tests inside a base-owned `workflow_run`
relocates that execution from the isolated executor to a differently-owned
runner. It does not cross the boundary the theorem describes: the subject
still controls the success_signal, because the subject's own code is what
produces it. A base-owned CALLER (the workflow steps: checkout, invoke,
capture, attest) does not launder a subject-controlled CALLEE (the test suite
itself) into something independent of the subject.

That was round 7's conclusion, and it stands. It was also a REVOCATION of the
round-7 acceptance condition ("C0 exits with a working base-owned positive
pytest path"), not a patch to the model that condition produced: new evidence
showed the condition itself forced an incoherent architecture, so it was
withdrawn rather than worked around.

WHAT `#331` SGAQ-CI1R CHANGED, AND WHAT IT DID NOT

Round 7 concluded that no `check_execution_mode` defined AT THAT TIME supplied
a judge independent of the subject, and that `AuthoritativeCIPromotion` was
therefore refused categorically. That is no longer the current state, and this
paragraph exists because leaving the old one standing would describe a trust
boundary this module no longer has.

`independent_data_only_host_tool` is a third execution mode whose verdict is
NOT authored by the subject: a host-owned tool decides it with the target
consumed strictly as DATA, so `controls(subject, success_signal)` does not
hold and the theorem above does not refuse it. The refusal is consequently
CONDITIONAL, not categorical.

The condition has two halves, and both are required:

- the producer must DECLARE the independent mode, which
  `verify_independent_semantic_judge_v2` checks; and
- the trusted base-owned policy entry must AUTHORIZE that mode in
  `permitted_execution_modes`, which
  `verify_execution_mode_is_policy_authorized_v2` checks.

A declaration alone buys nothing. A policy omitting the field authorizes
exactly the two pre-CI1R modes, so no target acquired a promotion path by the
engine learning this vocabulary, and no shipped policy authorizes the new mode
today. `AuthoritativeCIPromotion` is therefore unreachable for every current
target -- by AUTHORIZATION, not by absence of vocabulary. That distinction is
the whole content of SGAQ-CI1R and the reason this module can no longer say
"categorically".

This did NOT unwind the rest of this module. Producer identity, base-ownership
and tree binding remain real, tested infrastructure -- they answer "is this
evidence from where it claims to be", a necessary condition for authority and
a defect C0 still closes (`#217`'s check_name-only bypass). What they still do
not answer is "was the verdict decided by someone other than the subject";
that is the execution-mode axis, and it is answered by the two gates named
above rather than by anything on the identity axis.
"""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

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
# A refusal must say WHICH impossibility it hit. "attestation missing" and "the
# only attestation obtainable here is one the pull request could have written"
# are different diagnoses and lead to different fixes.
PRODUCER_PR_WRITABLE_REASON_V2 = "required_check_provenance_pr_writable_producer"
UNSIGNED_PR_PRODUCER_REASON_V2 = "required_check_provenance_unsigned_pr_producer"
BASE_OWNED_PRODUCER_REQUIRED_REASON_V2 = "required_check_provenance_base_owned_producer_required"
UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2 = "required_check_provenance_upstream_artifact_untrusted"
PRODUCER_TRIGGER_UNSUPPORTED_REASON_V2 = "required_check_provenance_producer_trigger_unsupported"
PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2 = (
    "required_check_provenance_producer_workflow_identity_mismatch"
)
EXECUTED_TREE_NOT_OBSERVED_REASON_V2 = "required_check_provenance_executed_tree_not_observed"
# Round-7 architectural correction: base-ownership of the WORKFLOW DEFINITION
# is not the same axis as `#201-B3`'s theorem about who controls the
# success_signal. The two PRE-`#331` execution modes each re-run or forward the
# SUBJECT's own test outcome, so neither supplies a judge independent of the
# subject, and this reason code is what they receive.
#
# `#331` SGAQ-CI1R added a third mode that does supply one. This code therefore
# means "the declared execution mode is not the independent one", NOT "no such
# mode exists" -- see the module docstring. It is distinct from
# EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2 below, which means the mode IS
# independent but this target never authorized it.
INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2 = (
    "required_check_provenance_independent_semantic_judge_required"
)
# `#331` SGAQ-CI1R. Distinct from the judge reason above, and the distinction is
# the whole point: "no mode supplies an independent judge" and "this target
# never authorized this mode" are different diagnoses with different fixes. The
# first is answered by building a judge; the second by a base-owned policy edit.
EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2 = (
    "required_check_provenance_execution_mode_not_policy_authorized"
)

ALL_PRODUCER_EVIDENCE_REASON_CODES_V2: tuple[str, ...] = (
    PRODUCER_WORKFLOW_NOT_PINNED_REASON_V2,
    PRODUCER_ATTESTATION_MISSING_REASON_V2,
    PRODUCER_ATTESTATION_MISMATCH_REASON_V2,
    PRODUCER_PR_WRITABLE_REASON_V2,
    UNSIGNED_PR_PRODUCER_REASON_V2,
    BASE_OWNED_PRODUCER_REQUIRED_REASON_V2,
    UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2,
    PRODUCER_TRIGGER_UNSUPPORTED_REASON_V2,
    PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2,
    EXECUTED_TREE_NOT_OBSERVED_REASON_V2,
    INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2,
    EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2,
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

# Two kinds are declarable; exactly ONE is promotable today.
#
# `sha_pinned_reusable_workflow` was the first model, and a Codex review then
# showed why it cannot stand unsigned: the pinned SHA proves the run LOADED the
# base-owned reusable workflow, but nothing binds the uploaded artifact to that
# workflow's job. Inside a PR-triggered run the pull request can call the pinned
# workflow AND, from a job of its own, upload an attestation carrying every
# field the verifier checks -- repository, pr_number, base/head/executed SHA,
# run id, attempt -- because it already knows all of them. It stays declarable
# so a policy naming it earns a precise refusal instead of a vague one, and it
# becomes promotable only once the attestation's ISSUER is authenticated.
#
# `base_owned_workflow_run` is the promotable kind. It moves the producer out
# of the pull request's reach entirely rather than trying to authenticate a
# message sent from inside it.
ProducerKindV2 = Literal["sha_pinned_reusable_workflow", "base_owned_workflow_run"]

# The only producer trigger whose workflow definition GitHub loads from the
# default branch AND whose run the pull request cannot write jobs into.
BASE_OWNED_PRODUCER_TRIGGER_V2 = "workflow_run"

# Triggers where the pull request can contribute jobs to the producing run, and
# therefore can author anything that run uploads.
PR_WRITABLE_PRODUCER_TRIGGERS_V2: frozenset[str] = frozenset({"pull_request", "pull_request_target"})

ExecutedTreeEvidenceKindV2 = Literal["producer_attestation"]

# How the producer obtained the verdict it is attesting to. Republishing an
# artifact built by the pull request's own run is NOT re-execution: GitHub's own
# guidance is that artifacts from a workflow which processed untrusted code are
# untrusted data. A base-owned run that merely forwards one has laundered the
# pull request's output, not verified it.
CheckExecutionModeV2 = Literal[
    "reexecuted_in_producer_run",
    "upstream_artifact_republished",
    # `#331` SGAQ-CI1R. A host-owned tool that decided the verdict itself, with
    # the target consumed strictly as DATA -- no target code, plugin, conftest,
    # hook or verdict-affecting config executed. The first mode whose
    # success_signal is not authored by the subject.
    #
    # It is a DECLARATION and grants nothing on its own. Reaching a promotion
    # requires, additionally, that the trusted base-owned policy entry lists
    # this mode in `permitted_execution_modes` -- see
    # `verify_execution_mode_is_policy_authorized_v2`. A producer saying "I am
    # independent" is evidence; a base-owned policy saying "I accept
    # independent" is authorization; promotion needs both.
    "independent_data_only_host_tool",
]

#: Modes where the producer obtained the verdict ITSELF rather than forwarding
#: someone else's. Re-executing the subject's suite and deciding over data are
#: both first-hand; they differ on WHO AUTHORED the value, which is the separate
#: axis `verify_independent_semantic_judge_v2` owns.
FIRST_HAND_EXECUTION_MODES_V2: frozenset[str] = frozenset(
    {"reexecuted_in_producer_run", "independent_data_only_host_tool"}
)

#: The only mode whose verdict does not derive from executing or trusting the
#: subject's own code.
INDEPENDENT_JUDGE_EXECUTION_MODE_V2 = "independent_data_only_host_tool"

#: The execution-mode universe as it existed BEFORE this slice, and therefore
#: the effective authorization of every policy written before it. A policy that
#: omits `permitted_execution_modes` authorizes exactly this set -- so learning
#: the new vocabulary hands no existing target a promotion path it did not
#: already have. Owned here, next to the Literal it partitions, so the two
#: cannot drift apart.
LEGACY_PERMITTED_EXECUTION_MODES_V2: frozenset[str] = frozenset(
    {"reexecuted_in_producer_run", "upstream_artifact_republished"}
)

# How the producer learned which tree it ran. `caller_supplied` means the value
# was handed in via workflow inputs or client_payload and merely echoed back --
# the tautology removed in round 2, arriving through a new door.
ExecutedShaDerivationV2 = Literal["verified_checkout_rev_parse", "caller_supplied"]


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
    # Declared by the producer so the assembler can refuse the two ways a
    # base-owned run can still be a pass-through rather than a witness. Both are
    # self-reported, and that is sound ONLY because the producer is base-owned:
    # nothing the pull request can write reaches these fields. For a
    # PR-writable producer the whole attestation is refused before these are
    # ever read.
    check_execution_mode: CheckExecutionModeV2
    executed_sha_derivation: ExecutedShaDerivationV2
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


def verify_producer_is_base_owned_v2(
    *,
    producer_kind: str,
    producer_trigger: str,
) -> None:
    """Refuse every producer whose run the pull request can write into.

    Ordered so the reason code names the actual obstacle:

    - a `pull_request`/`pull_request_target` producer is PR-writable, so
      anything it uploads may have been authored by the pull request;
    - `sha_pinned_reusable_workflow` is refused wherever it appears, because
      its evidence is an unauthenticated artifact by convention -- the pin
      proves which workflow was LOADED, never which job wrote the file;
    - `merge_group` is NOT assumed base-owned. GitHub runs each event's
      workflow from the ref associated with that event, and a merge group has
      its own ref and SHA; treating it as base-owned by name alone would be the
      same optimistic reuse this slice keeps removing. It needs its own model;
    - everything else is unsupported rather than tolerated.
    """

    if producer_trigger in PR_WRITABLE_PRODUCER_TRIGGERS_V2:
        raise RequiredCheckProvenanceErrorV2(PRODUCER_PR_WRITABLE_REASON_V2)

    if producer_kind == "sha_pinned_reusable_workflow":
        raise RequiredCheckProvenanceErrorV2(UNSIGNED_PR_PRODUCER_REASON_V2)

    if producer_kind != "base_owned_workflow_run":
        raise RequiredCheckProvenanceErrorV2(BASE_OWNED_PRODUCER_REQUIRED_REASON_V2)

    if producer_trigger != BASE_OWNED_PRODUCER_TRIGGER_V2:
        raise RequiredCheckProvenanceErrorV2(PRODUCER_TRIGGER_UNSUPPORTED_REASON_V2)


def verify_base_owned_producer_workflow_v2(
    *,
    pinned: ProducerWorkflowIdentityV2,
    pinned_ref: str,
    observed_repository: str,
    observed_path: str,
    observed_sha: str,
    observed_ref: str,
) -> None:
    """Require the producing run to BE the pinned base-owned workflow.

    For `base_owned_workflow_run` the producer is not referenced by the run --
    it *is* the run, so identity is checked against the run's own workflow
    repository, path and commit SHA rather than against `referenced_workflows`.

    Unlike the `pull_request` case, the ref is compared here and is meaningful:
    a `workflow_run` event loads its definition from the default branch, so a
    run executing under anything else is not the base-owned producer.
    """

    if (
        observed_repository != pinned.repository
        or observed_path != pinned.path
        or observed_sha != pinned.sha
        or observed_ref != pinned_ref
    ):
        raise RequiredCheckProvenanceErrorV2(PRODUCER_WORKFLOW_IDENTITY_MISMATCH_REASON_V2)


def verify_producer_execution_is_first_hand_v2(*, attestation: ProducerAttestationV2) -> None:
    """Refuse a base-owned run that forwarded someone else's work.

    Being base-owned makes a producer trustworthy about what IT did. It does not
    make it authoritative about what the pull request's own run did. A producer
    that republishes an upstream artifact, or that repeats an executed SHA it
    was handed rather than one it observed after checkout, has moved untrusted
    data across the trust boundary without checking it.
    """

    if attestation.check_execution_mode not in FIRST_HAND_EXECUTION_MODES_V2:
        raise RequiredCheckProvenanceErrorV2(UPSTREAM_ARTIFACT_UNTRUSTED_REASON_V2)

    if attestation.executed_sha_derivation != "verified_checkout_rev_parse":
        raise RequiredCheckProvenanceErrorV2(EXECUTED_TREE_NOT_OBSERVED_REASON_V2)


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


def verify_execution_mode_is_policy_authorized_v2(
    *,
    attestation: ProducerAttestationV2,
    permitted_execution_modes: frozenset[str],
) -> None:
    """Require the TRUSTED BASE POLICY to have authorized this way of judging.

    `#331` SGAQ-CI1R, and the reason this slice exists rather than shipping the
    vocabulary alone. `AGENTS.md` requires authorization to be fail-closed. A
    producer declaring `independent_data_only_host_tool` is making a claim about
    itself; without this gate that claim would be the ONLY thing standing
    between categorical refusal and a promotable verdict, and no target could
    say "I did not ask for an independent-judge producer".

    So the promotion needs two independent facts from two different owners:

        the producer says     HOW it judged        (attestation, evidence)
        the base policy says  WHICH ways it accepts (policy, authorization)

    `permitted_execution_modes` is resolved by the caller from the policy
    entry, never from the attestation -- passing the resolved SET rather than
    the entry keeps this module free of any policy import and makes it
    structurally impossible for producer-supplied data to select its own
    authorization.

    A policy written before this slice omits the field, so its effective set is
    `LEGACY_PERMITTED_EXECUTION_MODES_V2` and the new mode is refused here. No
    target gains authority by an engine upgrade.

    This is a NECESSARY condition, never a sufficient one. It cannot buy back a
    property the evidence lacks: authorizing `upstream_artifact_republished`
    does not make a republished artifact first-hand, because
    `verify_producer_execution_is_first_hand_v2` has already refused it."""

    # The empty check is not redundant with the loader's refusal of an empty
    # `permitted_execution_modes`. This function is public and takes a raw
    # frozenset, so its fail-closed behaviour must not depend on a validator in
    # a module it does not import. An adversarial lane showed both
    # `if permitted and mode not in permitted` and a loader-side default change
    # surviving the whole suite, safe only by that indirect coupling.
    if not permitted_execution_modes:
        raise RequiredCheckProvenanceErrorV2(EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2)

    if attestation.check_execution_mode not in permitted_execution_modes:
        raise RequiredCheckProvenanceErrorV2(EXECUTION_MODE_NOT_POLICY_AUTHORIZED_REASON_V2)


def verify_independent_semantic_judge_v2(*, attestation: ProducerAttestationV2) -> None:
    """Refuse promotion when the verdict is authored by the subject.

    Deliberately the LAST check in the promotion path -- every
    producer-identity, base-ownership, tree-binding and policy-authorization
    check above it still runs and still refuses on its own specific reason code
    first when it applies. Reaching this function means all of that
    infrastructure already succeeded.

    Two of the three `check_execution_mode` values do not supply a judge
    independent of the subject's own code, and are refused here:

    - `reexecuted_in_producer_run` re-ran the pull request's own test suite
      and reported its exit code. The workflow DEFINITION is base-owned; the
      value being measured is still authored by the subject. This is round 7's
      correction: a base-owned CALLER does not launder a subject-controlled
      CALLEE.
    - `upstream_artifact_republished` is refused earlier, by
      `verify_producer_execution_is_first_hand_v2`, for the same underlying
      reason stated more bluntly: it did not even re-run anything, it merely
      forwarded the subject's own claim.

    `#331` SGAQ-CI1R adds the third value, and it is the first whose verdict is
    not authored by the subject:

    - `independent_data_only_host_tool` is a host-owned tool that decided the
      verdict itself with the target consumed strictly as DATA. No target code,
      plugin, conftest or hook runs, so `controls(subject, success_signal)`
      does not hold and `#201-B3`'s theorem does not refuse it.

    PASSING THIS FUNCTION IS ONE PREDICATE, NOT AUTHORITY. It answers only
    "was this way of judging independent of the subject". It does not ask who
    the producer was, which tree ran, or whether the target authorized this
    kind of judge -- those are the gates above, and in the real assembler ALL
    of them run first. Calling this function on a detached attestation proves
    the predicate and nothing else.

    The discriminant is the EXECUTION MODE and never the producer identity.
    This function cannot confuse the two even by accident, because
    `producer_kind` is not a field of `ProducerAttestationV2` and is therefore
    not reachable from this signature."""

    if attestation.check_execution_mode != INDEPENDENT_JUDGE_EXECUTION_MODE_V2:
        raise RequiredCheckProvenanceErrorV2(INDEPENDENT_SEMANTIC_JUDGE_REQUIRED_REASON_V2)
