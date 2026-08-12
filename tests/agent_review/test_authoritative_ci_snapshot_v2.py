"""Snapshot parsing, attempt selection and conclusion mapping (`#201-C0`, C0-4).

Threat coverage: C0-T2 (stale head), C0-T10/T11 (attempt ambiguity and rerun
supersession), C0-T12 (non-verdict read as success), C0-T16/T17/T18 (strict
parsing), C0-T20 (allowlisted name, different producer).
"""

from __future__ import annotations

import json

import pytest

from app.agent_review.authoritative_check_policy_v2 import AuthoritativeCheckEntryV2, OriginRulesV2
from app.agent_review.authoritative_ci_snapshot_v2 import (
    ObservedCheckRunV2,
    compute_observation_digest_v2,
    parse_authoritative_ci_snapshot_v2,
    resolve_conclusion_v2,
    select_observation_v2,
)
from app.agent_review.contracts_v2 import RequiredCheckConclusionV2
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
        "policy_digest": "5" * 64,
        "toolchain_digest": "6" * 64,
    }
    fields.update(overrides)
    digest = compute_producer_attestation_digest_v2(
        ProducerAttestationV2.model_construct(**fields, attestation_digest="0" * 64)
    )
    return ProducerAttestationV2(**fields, attestation_digest=digest).model_dump(mode="json")


from app.agent_review.required_check_provenance_v2 import (
    PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2,
    PROVENANCE_INVALID_REASON_V2,
    PROVENANCE_MISSING_REASON_V2,
    PROVENANCE_OBSERVATION_STALE_REASON_V2,
    PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2,
    RequiredCheckProvenanceErrorV2,
)

REPO = "mglpsw/AgentEscala"
HEAD = "a" * 40
OTHER_HEAD = "9" * 40
MERGE = "d" * 40
BASE = "c" * 40

ENTRY = AuthoritativeCheckEntryV2(
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
    origin_rules=OriginRulesV2(pull_request="synthetic_merge_parentage"),
)


def _obs(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "repository": REPO,
        "head_sha": HEAD,
        "check_run_id": "100",
        "check_run_name": "Validate repository",
        "status": "completed",
        "conclusion": "success",
        "app_slug": "github-actions",
        "workflow_path": ".github/workflows/ci.yml",
        "workflow_execution_ref": "refs/pull/7/merge",
        "referenced_workflows": [{"path": "mglpsw/aiops-orchestrator/.github/workflows/authoritative-checks.reusable.yml", "sha": "4f9a2c7e13b8d05e6a1c9f3427d8b0e5c2a71f96", "ref": None}],
        "producer_trigger": "pull_request",
        "producer_attestation": _attestation(REPO, 7, BASE, HEAD, MERGE, "900", 1),
        "workflow_run_id": "900",
        "run_attempt": 1,
        "run_started_at": "2026-08-11T10:00:00Z",
        "run_event": "pull_request",
        "run_base_sha": BASE,
        "run_head_sha": HEAD,
    }
    record.update(overrides)
    return record


def _snapshot_dict(*observations: dict[str, object]) -> dict[str, object]:
    return {
        "schema_id": "agent-review.authoritative-check-snapshot.v2",
        "schema_version": 2,
        "source": "aiops-acquire-authoritative-checks",
        "acquisition": {
            "acquired_by": "aiops-acquire-authoritative-checks-v2",
            "api_host": "api.github.com",
            "repository": REPO,
            "head_sha": HEAD,
        },
        "observations": list(observations) or [_obs()],
        "tested_merge_sha": MERGE,
        "tested_merge_parents": [BASE, HEAD],
        "observation_bytes_digest": "f" * 64,
    }


def _snapshot(*observations: dict[str, object]):
    return parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict(*observations)))


def _observed(record: dict) -> ObservedCheckRunV2:
    """Through JSON, like the real parser: under strict mode a Python list is
    not a tuple, but a JSON array is a valid source for a tuple field."""

    return ObservedCheckRunV2.model_validate_json(json.dumps(record), strict=True)


def _select(snapshot, head_sha: str = HEAD):
    return select_observation_v2(snapshot=snapshot, entry=ENTRY, repository=REPO, head_sha=head_sha)


# -- strict parsing -----------------------------------------------------------


def test_valid_snapshot_parses() -> None:
    snapshot = _snapshot()
    assert snapshot.acquisition.acquired_by == "aiops-acquire-authoritative-checks-v2"
    assert len(snapshot.observations) == 1


def test_duplicate_keys_are_refused() -> None:  # C0-T16
    raw = '{"schema_id": "a", "schema_id": "b"}'
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        parse_authoritative_ci_snapshot_v2(raw)
    assert exc.value.reason_code == PROVENANCE_INVALID_REASON_V2


def test_non_finite_numbers_are_refused() -> None:  # C0-T17
    raw = json.dumps(_snapshot_dict()).replace('"run_attempt": 1', '"run_attempt": NaN')
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        parse_authoritative_ci_snapshot_v2(raw)
    assert exc.value.reason_code == PROVENANCE_INVALID_REASON_V2


def test_truncated_payload_is_refused() -> None:  # C0-T18
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        parse_authoritative_ci_snapshot_v2('{"schema_id": ')
    assert exc.value.reason_code == PROVENANCE_INVALID_REASON_V2


def test_unknown_field_is_refused() -> None:
    payload = _snapshot_dict()
    payload["surprise"] = 1
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        parse_authoritative_ci_snapshot_v2(json.dumps(payload))
    assert exc.value.reason_code == PROVENANCE_INVALID_REASON_V2


def test_unknown_github_conclusion_is_refused_at_parse_time() -> None:
    """An unrecognised vocabulary value never passes through in the hope that a
    later stage rejects it."""

    with pytest.raises(RequiredCheckProvenanceErrorV2):
        parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict(_obs(conclusion="exploded"))))


def test_completed_run_without_conclusion_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        parse_authoritative_ci_snapshot_v2(json.dumps(_snapshot_dict(_obs(conclusion=None))))


def test_unfinished_run_carrying_a_conclusion_is_refused() -> None:
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        parse_authoritative_ci_snapshot_v2(
            json.dumps(_snapshot_dict(_obs(status="in_progress", conclusion="success")))
        )


# -- producer identity --------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("check_run_name", "some-other-job"),
        ("workflow_path", ".github/workflows/attacker.yml"),
        ("app_slug", "not-github-actions"),
    ],
)
def test_any_producer_field_mismatch_disqualifies(field: str, value: str) -> None:  # C0-T20
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(_snapshot(_obs(**{field: value})))
    assert exc.value.reason_code == PROVENANCE_MISSING_REASON_V2


def test_a_pr_job_named_after_the_required_check_matches_nothing() -> None:
    """The policy maps `pytest` to a job called `Validate repository`. A PR that
    adds a green job literally named `pytest` is not that producer."""

    with pytest.raises(RequiredCheckProvenanceErrorV2):
        _select(_snapshot(_obs(check_run_name="pytest", workflow_path=".github/workflows/pr.yml")))


# -- staleness vs absence -----------------------------------------------------


def test_producer_ran_at_a_different_head_is_stale_not_missing() -> None:  # C0-T2
    """Different problems deserve different diagnoses: the evidence exists, it
    just describes an earlier push."""

    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(_snapshot(_obs(head_sha=OTHER_HEAD)))
    assert exc.value.reason_code == PROVENANCE_OBSERVATION_STALE_REASON_V2


def test_no_observation_at_all_is_missing() -> None:  # C0-T25
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(_snapshot(_obs(check_run_name="unrelated")))
    assert exc.value.reason_code == PROVENANCE_MISSING_REASON_V2


def test_observation_from_another_repository_is_missing() -> None:  # C0-T5
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(_snapshot(_obs(repository="mglpsw/somewhere-else")))
    assert exc.value.reason_code == PROVENANCE_MISSING_REASON_V2


# -- attempt selection --------------------------------------------------------


def test_highest_attempt_wins() -> None:
    selected = _select(_snapshot(_obs(run_attempt=1, check_run_id="1"), _obs(run_attempt=3, check_run_id="3")))
    assert selected.run_attempt == 3


def test_a_stale_green_never_outranks_a_current_red() -> None:  # C0-T11
    snapshot = _snapshot(
        _obs(run_attempt=1, conclusion="success", check_run_id="1"),
        _obs(run_attempt=2, conclusion="failure", check_run_id="2"),
    )
    assert resolve_conclusion_v2(_select(snapshot)) is RequiredCheckConclusionV2.FAILURE


def test_a_rerun_that_fixes_a_failure_supersedes_it() -> None:
    snapshot = _snapshot(
        _obs(run_attempt=1, conclusion="failure", check_run_id="1"),
        _obs(run_attempt=2, conclusion="success", check_run_id="2"),
    )
    assert resolve_conclusion_v2(_select(snapshot)) is RequiredCheckConclusionV2.SUCCESS


def test_two_survivors_at_the_highest_attempt_are_ambiguous() -> None:  # C0-T10
    """Refused rather than resolved by recency or by preferring the greener
    one -- there is no principled tiebreak, so there is no tiebreak."""

    snapshot = _snapshot(
        _obs(run_attempt=2, conclusion="success", check_run_id="1", workflow_run_id="900"),
        _obs(run_attempt=2, conclusion="failure", check_run_id="2", workflow_run_id="901"),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(snapshot)
    assert exc.value.reason_code == PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2


def test_two_agreeing_survivors_at_the_highest_attempt_are_still_ambiguous() -> None:
    """Even agreement does not license a guess: two check runs for one producer
    identity and attempt means the identity is not as unique as the policy
    assumed, and that is worth failing on."""

    snapshot = _snapshot(
        _obs(run_attempt=2, check_run_id="1", workflow_run_id="900"),
        _obs(run_attempt=2, check_run_id="2", workflow_run_id="901"),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(snapshot)
    assert exc.value.reason_code == PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2


# -- conclusion mapping -------------------------------------------------------


@pytest.mark.parametrize(
    "conclusion,expected",
    [("success", RequiredCheckConclusionV2.SUCCESS), ("failure", RequiredCheckConclusionV2.FAILURE)],
)
def test_the_two_resolved_verdicts_map(conclusion: str, expected: RequiredCheckConclusionV2) -> None:
    assert resolve_conclusion_v2(_select(_snapshot(_obs(conclusion=conclusion)))) is expected


@pytest.mark.parametrize(
    "conclusion",
    ["cancelled", "timed_out", "neutral", "skipped", "stale", "action_required", "startup_failure"],
)
def test_no_other_conclusion_is_promotable(conclusion: str) -> None:  # C0-T12
    """`neutral` and `skipped` in particular are non-verdicts. Treating either
    as success is how a required check silently stops meaning anything."""

    observation = _observed(_obs(conclusion=conclusion))
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        resolve_conclusion_v2(observation)
    assert exc.value.reason_code == PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_an_unfinished_run_is_unresolved(status: str) -> None:
    observation = _observed(_obs(status=status, conclusion=None))
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        resolve_conclusion_v2(observation)
    assert exc.value.reason_code == PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2


def test_environmental_failure_is_never_reported_as_product_failure() -> None:
    """A cancelled run must not become `FAILURE`, any more than it may become
    `SUCCESS`. Both directions are fabrication."""

    observation = _observed(_obs(conclusion="cancelled"))
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        resolve_conclusion_v2(observation)


# -- observation digest -------------------------------------------------------


def test_observation_digest_is_deterministic_and_discriminating() -> None:
    first = _observed(_obs())
    same = _observed(_obs())
    other = _observed(_obs(conclusion="failure"))

    assert compute_observation_digest_v2(first) == compute_observation_digest_v2(same)
    assert compute_observation_digest_v2(first) != compute_observation_digest_v2(other)


# -- Codex review round 3: ordering across distinct workflow runs --------------


def test_a_newer_run_beats_an_older_rerun_with_a_higher_attempt() -> None:
    """`run_attempt` counts within ONE workflow run, so a maximum taken across
    runs identifies the highest attempt NUMBER, not the latest execution. An
    old run rerun to attempt 3 must not outrank a newer run at attempt 1 --
    that selects a stale green over a current red, the exact failure this
    selection exists to prevent."""

    snapshot = _snapshot(
        _obs(
            check_run_id="old",
            workflow_run_id="100",
            run_attempt=3,
            conclusion="success",
            run_started_at="2026-08-11T09:00:00Z",
        ),
        _obs(
            check_run_id="new",
            workflow_run_id="200",
            run_attempt=1,
            conclusion="failure",
            run_started_at="2026-08-11T12:00:00Z",
        ),
    )
    selected = _select(snapshot)
    assert selected.check_run_id == "new"
    assert resolve_conclusion_v2(selected) is RequiredCheckConclusionV2.FAILURE


def test_within_one_run_the_latest_attempt_still_wins() -> None:
    snapshot = _snapshot(
        _obs(check_run_id="a1", run_attempt=1, conclusion="failure", run_started_at="2026-08-11T09:00:00Z"),
        _obs(check_run_id="a2", run_attempt=2, conclusion="success", run_started_at="2026-08-11T09:00:00Z"),
    )
    assert _select(snapshot).check_run_id == "a2"


def test_a_tie_on_start_time_and_attempt_is_ambiguous() -> None:
    snapshot = _snapshot(
        _obs(check_run_id="x", workflow_run_id="100", run_started_at="2026-08-11T09:00:00Z"),
        _obs(check_run_id="y", workflow_run_id="200", run_started_at="2026-08-11T09:00:00Z"),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(snapshot)
    assert exc.value.reason_code == PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2


def test_runs_tied_on_start_time_never_compare_attempts() -> None:
    """`run_started_at` has second precision, so two DISTINCT workflow runs can
    tie on it. A maximum over the pair `(run_started_at, run_attempt)` then
    silently compares attempts belonging to different `workflow_run_id`s --
    which is the same category error as ordering by attempt alone, just one
    tie away.

    Concretely: an old run rerun to attempt 3 (green) and a newer run at
    attempt 1 (red) starting in the same second. Nothing in the data orders
    those two runs, so the only honest answer is to refuse -- never to hand
    back the greener one."""

    snapshot = _snapshot(
        _obs(
            check_run_id="old-rerun",
            workflow_run_id="100",
            run_attempt=3,
            conclusion="success",
            run_started_at="2026-08-11T09:00:00Z",
        ),
        _obs(
            check_run_id="new-first-try",
            workflow_run_id="200",
            run_attempt=1,
            conclusion="failure",
            run_started_at="2026-08-11T09:00:00Z",
        ),
    )
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        _select(snapshot)
    assert exc.value.reason_code == PROVENANCE_RUN_ATTEMPT_AMBIGUOUS_REASON_V2


def test_the_latest_run_is_chosen_before_any_attempt_is_compared() -> None:
    """The ordering is run-first, attempt-second -- not a single comparison over
    both. A newer run at attempt 1 wins outright over an older rerun at a
    higher attempt, and the older run's attempts are never even considered."""

    snapshot = _snapshot(
        _obs(
            check_run_id="old-a1",
            workflow_run_id="100",
            run_attempt=1,
            conclusion="failure",
            run_started_at="2026-08-11T09:00:00Z",
        ),
        _obs(
            check_run_id="old-a9",
            workflow_run_id="100",
            run_attempt=9,
            conclusion="success",
            run_started_at="2026-08-11T09:00:00Z",
        ),
        _obs(
            check_run_id="new-a1",
            workflow_run_id="200",
            run_attempt=1,
            conclusion="failure",
            run_started_at="2026-08-11T12:00:00Z",
        ),
    )
    selected = _select(snapshot)
    assert selected.check_run_id == "new-a1"
    assert resolve_conclusion_v2(selected) is RequiredCheckConclusionV2.FAILURE
