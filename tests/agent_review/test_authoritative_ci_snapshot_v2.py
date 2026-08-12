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
    workflow_ref="refs/heads/master",
    job_name="Validate repository",
    verifier_identity="github-actions",
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
        "workflow_ref": "refs/heads/master",
        "workflow_run_id": "900",
        "run_attempt": 1,
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
        ("workflow_ref", "refs/heads/attacker-branch"),
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

    observation = ObservedCheckRunV2.model_validate(_obs(conclusion=conclusion))
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        resolve_conclusion_v2(observation)
    assert exc.value.reason_code == PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_an_unfinished_run_is_unresolved(status: str) -> None:
    observation = ObservedCheckRunV2.model_validate(_obs(status=status, conclusion=None))
    with pytest.raises(RequiredCheckProvenanceErrorV2) as exc:
        resolve_conclusion_v2(observation)
    assert exc.value.reason_code == PROVENANCE_CONCLUSION_UNRESOLVED_REASON_V2


def test_environmental_failure_is_never_reported_as_product_failure() -> None:
    """A cancelled run must not become `FAILURE`, any more than it may become
    `SUCCESS`. Both directions are fabrication."""

    observation = ObservedCheckRunV2.model_validate(_obs(conclusion="cancelled"))
    with pytest.raises(RequiredCheckProvenanceErrorV2):
        resolve_conclusion_v2(observation)


# -- observation digest -------------------------------------------------------


def test_observation_digest_is_deterministic_and_discriminating() -> None:
    first = ObservedCheckRunV2.model_validate(_obs())
    same = ObservedCheckRunV2.model_validate(_obs())
    other = ObservedCheckRunV2.model_validate(_obs(conclusion="failure"))

    assert compute_observation_digest_v2(first) == compute_observation_digest_v2(same)
    assert compute_observation_digest_v2(first) != compute_observation_digest_v2(other)
