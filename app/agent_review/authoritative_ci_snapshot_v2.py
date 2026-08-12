"""Offline snapshot of observed CI checks, and the rules for reading it.

`#201-C0`, C0-4.

## Acquisition and assembly are different jobs

This module is pure and offline. It parses a snapshot that a separate
host-owned acquirer already materialised, and it never opens a socket. That is
the same boundary every prior slice in this effort drew (`#103` diff
acquisition, `#129` manifest assembly, `#130` readiness emission) and the one
the quality gate itself declares: offline, no network.

One chain only: `acquire -> canonical snapshot -> verifier/assembler`. A
convenience wrapper may materialise the snapshot and then read it back, but no
in-memory API response may bypass the file. Otherwise there would be two paths
to authority and only one of them would be adversarially tested.

## A snapshot is an observation, not a credential

`AuthoritativeCheckSnapshotV2` records what was seen and who saw it. It proves
nothing on its own -- a JSON document carrying its own hash is self-consistent,
not authoritative. Authority is derived downstream, by matching the observation
against a base-owned policy and the run identity. The acquisition identity is
inside the snapshot so an observation cannot be silently re-attributed to a
different acquirer, host, or query scope.

## Selection is deterministic, never "latest green"

`select_observation_v2` filters on the FULL producer identity and then takes
the single highest run attempt. A later attempt supersedes an earlier one
unconditionally -- a stale green never outranks a current red. Two survivors at
the highest attempt is ambiguity, and ambiguity is refused rather than resolved
by preference.

## The conclusion mapping is closed and not configurable

Only `success` and `failure` are resolved verdicts. Everything else --
`cancelled`, `timed_out`, `neutral`, `skipped`, `stale`, `action_required`,
`startup_failure`, and any non-`completed` status -- is refused. This mirrors
`promote_trusted_check_to_required_v2`, which refuses environmental outcomes
rather than inventing a conclusion for them: there is no honest
`RequiredCheckConclusionV2` value meaning "the environment failed", so none is
fabricated. An environmental failure never becomes a product regression, and a
product regression never disappears as an environmental failure.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckEntryV2
from app.agent_review.authoritative_producer_evidence_v2 import (
    ProducerAttestationV2,
    ProducerTriggerV2,
    ProducerWorkflowReferenceV2,
)
from app.agent_review.contracts_v2 import (
    ContractV2Model,
    GitSha,
    PositiveInt,
    Rfc3339Timestamp,
    RelativePath,
    Repository,
    RequiredCheckConclusionV2,
    SafeIdentifier,
    SafeText,
    Sha256,
)
from app.agent_review.required_check_provenance_v2 import (
    PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2,
    PROVENANCE_INVALID_REASON_V2,
    PROVENANCE_MISSING_REASON_V2,
    PROVENANCE_OBSERVATION_STALE_REASON_V2,
    PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2,
    RequiredCheckProvenanceErrorV2,
)
from app.common.strict_json import canonical_json_digest_hex, strict_json_loads

AUTHORITATIVE_CHECK_SNAPSHOT_SCHEMA_V2 = "agent-review.authoritative-check-snapshot.v2"

# GitHub's own vocabularies, enumerated rather than accepted as free text: an
# unrecognised value is a parse failure, never something to pass through and
# hope a later stage rejects.
ObservedStatusV2 = Literal["queued", "in_progress", "completed", "waiting", "requested", "pending"]
ObservedRunEventV2 = Literal[
    "pull_request",
    "pull_request_target",
    "push",
    "merge_group",
    "workflow_run",
    "workflow_dispatch",
    "schedule",
]
ObservedConclusionV2 = Literal[
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "neutral",
    "skipped",
    "stale",
    "action_required",
    "startup_failure",
]

# The ONLY two resolved verdicts. Deliberately a module constant so the mapping
# is one fact in one place rather than a condition repeated at each call site.
_RESOLVED_CONCLUSIONS_V2: dict[str, RequiredCheckConclusionV2] = {
    "success": RequiredCheckConclusionV2.SUCCESS,
    "failure": RequiredCheckConclusionV2.FAILURE,
}


class AcquisitionIdentityV2(ContractV2Model):
    """Who observed this, from where, and scoped to what.

    Part of the snapshot's canonical material on purpose: without it, an
    observation could be re-attributed to a different acquirer or query scope
    after the fact, and the resulting sidecar would still look coherent."""

    acquired_by: SafeIdentifier
    api_host: SafeText
    repository: Repository
    head_sha: GitSha


class ObservedCheckRunV2(ContractV2Model):
    """One check run exactly as observed, with no interpretation applied.

    `check_run_name` is GitHub's own name for the run -- NOT the AgentReview
    required-check name. Keeping them separate is what makes name-matching
    structurally insufficient: the policy maps a required check to the
    producer's job name, and the two need not coincide.

    `run_base_sha`/`run_head_sha` are the base and head GitHub itself recorded
    for the run. They exist because a check run is scoped to a HEAD, while the
    thing it actually executed is a merge of that head with a base -- and the
    base can advance without the head moving. Without recording the run's own
    base, a stale run and a freshly-created local merge commit line up
    perfectly and a green from the previous base gets promoted for a merge that
    was never tested. Recorded here so the assembler can refuse that.
    """

    repository: Repository
    head_sha: GitSha
    check_run_id: SafeIdentifier
    check_run_name: SafeText
    status: ObservedStatusV2
    conclusion: ObservedConclusionV2 | None
    app_slug: SafeIdentifier
    workflow_path: RelativePath
    # Factual observation of the ref the run executed under. Deliberately NOT
    # named `workflow_ref` any more, and deliberately not used as evidence of
    # base ownership: a pull request controls the ref its own runs execute
    # under. Identity comes from `referenced_workflows`.
    workflow_execution_ref: SafeText
    # The reusable workflows this run loaded, each with the full commit SHA
    # GitHub recorded. This is what a pull request cannot forge.
    referenced_workflows: tuple[ProducerWorkflowReferenceV2, ...]
    producer_trigger: ProducerTriggerV2
    # Emitted by the producer's checkout-free attestation job. Absent for any
    # run that did not produce one, which simply cannot be promoted.
    producer_attestation: ProducerAttestationV2 | None
    workflow_run_id: SafeIdentifier
    run_attempt: PositiveInt
    # When THIS workflow run started. `run_attempt` is scoped to a single
    # workflow_run_id, so it cannot order two distinct runs against each other:
    # an old run rerun to attempt 3 would otherwise outrank a newer run at
    # attempt 1. Ordering needs a fact that spans runs.
    run_started_at: Rfc3339Timestamp
    run_event: ObservedRunEventV2
    run_base_sha: GitSha | None
    run_head_sha: GitSha | None

    @model_validator(mode="after")
    def validate_status_conclusion_pair(self) -> ObservedCheckRunV2:
        # GitHub only populates `conclusion` once a run completes. A record
        # claiming a conclusion while still running, or claiming completion
        # with no conclusion, did not come from GitHub unmodified.
        if self.status == "completed" and self.conclusion is None:
            raise ValueError("a completed check run must carry a conclusion")
        if self.status != "completed" and self.conclusion is not None:
            raise ValueError("an unfinished check run cannot carry a conclusion")
        return self

    @model_validator(mode="after")
    def validate_pull_request_identity(self) -> ObservedCheckRunV2:
        # A pull-request-family run always has both. Their absence would mean
        # the run cannot be bound to a base/head pair at all, which is exactly
        # the binding the assembler needs -- so it is refused at parse time
        # rather than becoming a silent `None` comparison downstream.
        if self.run_event in {"pull_request", "pull_request_target"}:
            if self.run_base_sha is None or self.run_head_sha is None:
                raise ValueError("a pull-request run must record its own base and head")
        return self


class AuthoritativeCheckSnapshotV2(ContractV2Model):
    """The acquirer's output.

    Exported as a published schema even though only the assembler reads it: it
    crosses a process boundary and lands on disk, which is exactly what the
    other v2 wire artifacts are given schemas for. Treating it as "internal"
    would also make it the one cross-process artifact a target pack could not
    validate independently."""

    schema_id: Literal["agent-review.authoritative-check-snapshot.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-acquire-authoritative-checks"]

    acquisition: AcquisitionIdentityV2
    observations: tuple[ObservedCheckRunV2, ...]

    # Observed locally with git, not taken from the API: the merge commit this
    # review is about, and its parents.
    #
    # There is deliberately NO `executed_tree_sha`. A Codex review found that
    # the acquirer could only ever copy the caller's own `--tested-merge-sha`
    # into it, so the assembler's "the tree that ran must be the tree the
    # identity claims" check compared the caller's input against itself -- a
    # tautology wearing the costume of a proof. GitHub exposes no execution-tree
    # SHA, so the honest binding is the run's OWN base/head recorded on each
    # observation, and origins where even that is unavailable are refused.
    tested_merge_sha: GitSha
    tested_merge_parents: Annotated[tuple[GitSha, ...], Field(min_length=1, max_length=8)]

    # Raw-bytes digest of the API payload the observations were derived from.
    observation_bytes_digest: Sha256


def parse_authoritative_ci_snapshot_v2(raw: bytes | str) -> AuthoritativeCheckSnapshotV2:
    """Parse a snapshot with duplicate-key and non-finite rejection.

    Uses the shared strict parser so this module cannot drift from the
    discipline the rest of the repository applies."""

    try:
        document = strict_json_loads(raw)
    except ValueError as exc:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2) from exc

    if not isinstance(document, dict):
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2)

    try:
        # Same JSON-text round-trip as the profile/policy loaders: strict mode
        # still applies, but a JSON array remains a valid source for a
        # tuple-typed field.
        return AuthoritativeCheckSnapshotV2.model_validate_json(
            json.dumps(document, ensure_ascii=False), strict=True
        )
    except (ValueError, TypeError) as exc:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_INVALID_REASON_V2) from exc


def compute_observation_digest_v2(observation: ObservedCheckRunV2) -> str:
    """Binds a provenance record to the exact observation it was derived from,
    not merely to the snapshot file that contained it."""

    return canonical_json_digest_hex(observation.model_dump(mode="json"))


def _matches_producer(observation: ObservedCheckRunV2, entry: AuthoritativeCheckEntryV2) -> bool:
    """Every field of the producer tuple, or no match.

    `check_run_name` is compared against the policy's `job_name` -- never
    against the required check's own name. That is the whole point: a PR job
    called `pytest` matches nothing unless the base-owned policy says the
    producer's job is called `pytest`.

    `workflow_execution_ref` is NOT compared. It is an observation a pull
    request controls, so requiring a particular value would either reject every
    real run (as an earlier revision did) or admit the wrong ones. The pinned
    reusable workflow is checked separately, by SHA."""

    return (
        observation.check_run_name == entry.job_name
        and observation.workflow_path == entry.workflow_path
        and observation.app_slug == entry.verifier_identity
    )


def select_observation_v2(
    *,
    snapshot: AuthoritativeCheckSnapshotV2,
    entry: AuthoritativeCheckEntryV2,
    repository: str,
    head_sha: str,
) -> ObservedCheckRunV2:
    """Pick the one observation entitled to speak for this required check.

    Deterministic by construction, and fail-closed at every branch:

    - producer identity must match in full;
    - a producer match at a DIFFERENT head is reported as stale rather than
      missing, because those are different problems: stale means the evidence
      exists but describes an earlier push, and silently reporting "missing"
      would hide that a green run is being aged out;
    - the latest run wins, then that run's latest attempt -- in that order, as
      two separate steps. Ordering by attempt alone was wrong (attempts count
      within a single workflow run, so an old run rerun to attempt 3 would
      outrank a newer run at attempt 1), and so is a single maximum over the
      pair, which degenerates into the same cross-run comparison whenever two
      runs tie on a second-precision timestamp;
    - ambiguity at either step -- two runs tied for latest, or two records at
      the same run and attempt -- is refused rather than resolved by preferring
      the greener one.
    """

    producer_matches = [obs for obs in snapshot.observations if _matches_producer(obs, entry)]
    if not producer_matches:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_MISSING_REASON_V2)

    scoped = [obs for obs in producer_matches if obs.repository == repository and obs.head_sha == head_sha]
    if not scoped:
        # The producer ran, just not for this head. That is staleness, and it
        # deserves its own diagnosis.
        if any(obs.repository == repository for obs in producer_matches):
            raise RequiredCheckProvenanceErrorV2(PROVENANCE_OBSERVATION_STALE_REASON_V2)
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_MISSING_REASON_V2)

    # Latest RUN first, and only THEN that run's latest attempt. These are two
    # separate steps on purpose, because `run_attempt` counts within a single
    # workflow_run_id and therefore cannot order two runs against each other.
    #
    # A single maximum over the pair `(run_started_at, run_attempt)` looks like
    # it does this, but does not: `run_started_at` has second precision, so two
    # distinct runs can tie on it, and the tuple comparison then falls through
    # to comparing attempts ACROSS runs -- the very thing that lets an old run
    # rerun to attempt 3 outrank a newer run at attempt 1, selecting a stale
    # green over a current red.
    latest_started_at = max(obs.run_started_at for obs in scoped)
    latest_run_ids = {obs.workflow_run_id for obs in scoped if obs.run_started_at == latest_started_at}
    if len(latest_run_ids) != 1:
        # Two runs started in the same second. Nothing in the data orders them,
        # and picking either would be a guess dressed as a decision.
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2)

    latest_run_id = latest_run_ids.pop()
    within_run = [obs for obs in scoped if obs.workflow_run_id == latest_run_id]
    latest_attempt = max(obs.run_attempt for obs in within_run)
    finalists = [obs for obs in within_run if obs.run_attempt == latest_attempt]
    if len(finalists) != 1:
        # Same run, same attempt, two records: a contradiction, not a choice.
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2)

    return finalists[0]


def resolve_conclusion_v2(observation: ObservedCheckRunV2) -> RequiredCheckConclusionV2:
    """Map an observed conclusion onto a required-check conclusion, or refuse.

    Refusal is not fabrication. There is no `RequiredCheckConclusionV2` value
    that honestly means "the run was cancelled" or "the job was skipped", so
    none is invented -- exactly as `promote_trusted_check_to_required_v2`
    already refuses every environmental `TrustedCheckOutcomeV2`.

    `PENDING`/`MISSING` are never synthesized here either: an unresolved check
    is an ABSENT pair for the assembler, and readiness's own fail-closed
    precedence handles absence in `#201-C`.
    """

    if observation.status != "completed" or observation.conclusion is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2)

    resolved = _RESOLVED_CONCLUSIONS_V2.get(observation.conclusion)
    if resolved is None:
        raise RequiredCheckProvenanceErrorV2(PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2)
    return resolved
