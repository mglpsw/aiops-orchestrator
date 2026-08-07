from __future__ import annotations

import hashlib

import pytest

from app.agent_review.trusted_check_simulator_v2 import (
    SIMULATION_MISSING_FIXTURE_REASON_V2,
    SIMULATION_UNKNOWN_CHECK_REASON_V2,
    SimulatedCheckOutcomeV2,
    TrustedCheckSimulationError,
    simulate_trusted_check_plan_v2,
)
from app.agent_review.trusted_checks_v2 import (
    AllowlistedCheckCommandV2,
    TrustedCheckAuthorityV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckPromotionError,
    bind_trusted_check_result_to_plan_v2,
    promote_trusted_check_to_required_v2,
)

RUN = hashlib.sha256(b"run").hexdigest()
HEAD = "a" * 40
HARNESS = hashlib.sha256(b"harness").hexdigest()
SUITE = hashlib.sha256(b"suite").hexdigest()


def _command(**overrides) -> AllowlistedCheckCommandV2:
    raw = dict(
        check_name="pytest", command_token="pytest_suite", timeout_seconds=600,
        max_memory_mb=2048, max_processes=32, network_allowed=False,
    )
    raw.update(overrides)
    return AllowlistedCheckCommandV2(**raw)


def _plan(*, checks=None) -> TrustedCheckPlanV2:
    return TrustedCheckPlanV2(
        schema_id="agent-review.trusted-check-plan.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, harness_digest=HARNESS, authority_suite_digest=SUITE,
        checks=list(checks) if checks is not None else [_command()],
    )


def test_simulate_produces_a_result_bindable_to_the_plan():
    plan = _plan()
    fixtures = {"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)}
    results = simulate_trusted_check_plan_v2(plan, fixtures=fixtures, authority=TrustedCheckAuthorityV2.TRUSTED)
    assert len(results) == 1
    bind_trusted_check_result_to_plan_v2(results[0], plan)  # does not raise


def test_simulate_result_is_deterministic_across_calls():
    plan = _plan()
    fixtures = {"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)}
    a = simulate_trusted_check_plan_v2(plan, fixtures=fixtures, authority=TrustedCheckAuthorityV2.TRUSTED)
    b = simulate_trusted_check_plan_v2(plan, fixtures=fixtures, authority=TrustedCheckAuthorityV2.TRUSTED)
    assert a[0].result_sha256 == b[0].result_sha256
    assert a[0].artifact_sha256 == b[0].artifact_sha256


def test_simulate_artifact_hash_changes_with_outcome():
    plan = _plan()
    success = simulate_trusted_check_plan_v2(
        plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)},
        authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    failure = simulate_trusted_check_plan_v2(
        plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.FAILURE)},
        authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert success[0].artifact_sha256 != failure[0].artifact_sha256


@pytest.mark.parametrize(
    "outcome",
    [TrustedCheckOutcomeV2.TIMEOUT, TrustedCheckOutcomeV2.OOM,
     TrustedCheckOutcomeV2.CANCELLED, TrustedCheckOutcomeV2.INFRA_FAILURE],
)
def test_simulate_environmental_outcome_has_no_artifact_hash(outcome):
    plan = _plan()
    results = simulate_trusted_check_plan_v2(
        plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=outcome)},
        authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert results[0].artifact_sha256 is None
    assert results[0].outcome is outcome


def test_simulate_environmental_outcome_never_promotes_even_with_trusted_authority():
    plan = _plan()
    results = simulate_trusted_check_plan_v2(
        plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.TIMEOUT)},
        authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    with pytest.raises(TrustedCheckPromotionError):
        promote_trusted_check_to_required_v2(results[0])


def test_simulate_untrusted_authority_never_promotes():
    plan = _plan()
    results = simulate_trusted_check_plan_v2(
        plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)},
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    with pytest.raises(TrustedCheckPromotionError):
        promote_trusted_check_to_required_v2(results[0])


def test_simulate_multiple_checks_in_plan_order():
    plan = _plan(checks=[_command(check_name="pytest", command_token="pytest_suite"), _command(check_name="eslint", command_token="eslint_suite")])
    fixtures = {
        "pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS),
        "eslint": SimulatedCheckOutcomeV2(check_name="eslint", outcome=TrustedCheckOutcomeV2.FAILURE),
    }
    results = simulate_trusted_check_plan_v2(plan, fixtures=fixtures, authority=TrustedCheckAuthorityV2.TRUSTED)
    assert [r.check_name for r in results] == ["pytest", "eslint"]
    assert results[0].outcome is TrustedCheckOutcomeV2.SUCCESS
    assert results[1].outcome is TrustedCheckOutcomeV2.FAILURE
    for result in results:
        bind_trusted_check_result_to_plan_v2(result, plan)


def test_simulate_raises_on_a_check_missing_a_fixture():
    plan = _plan(checks=[_command(check_name="pytest"), _command(check_name="eslint", command_token="eslint_suite")])
    with pytest.raises(TrustedCheckSimulationError) as excinfo:
        simulate_trusted_check_plan_v2(
            plan, fixtures={"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)},
            authority=TrustedCheckAuthorityV2.TRUSTED,
        )
    assert excinfo.value.reason_code == SIMULATION_MISSING_FIXTURE_REASON_V2
    assert excinfo.value.check_name == "eslint"


def test_simulate_raises_on_a_fixture_naming_a_check_not_in_the_plan():
    plan = _plan()
    with pytest.raises(TrustedCheckSimulationError) as excinfo:
        simulate_trusted_check_plan_v2(
            plan,
            fixtures={
                "pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS),
                "eslint": SimulatedCheckOutcomeV2(check_name="eslint", outcome=TrustedCheckOutcomeV2.SUCCESS),
            },
            authority=TrustedCheckAuthorityV2.TRUSTED,
        )
    assert excinfo.value.reason_code == SIMULATION_UNKNOWN_CHECK_REASON_V2
    assert excinfo.value.check_name == "eslint"


def test_simulate_never_touches_the_filesystem_or_spawns_a_process(monkeypatch):
    # Adversarial: patch subprocess.Popen/run to explode if ever called --
    # the simulator must never reach either.
    import subprocess

    def _explode(*args, **kwargs):
        raise AssertionError("simulator must never spawn a process")

    monkeypatch.setattr(subprocess, "Popen", _explode)
    monkeypatch.setattr(subprocess, "run", _explode)

    plan = _plan()
    fixtures = {"pytest": SimulatedCheckOutcomeV2(check_name="pytest", outcome=TrustedCheckOutcomeV2.SUCCESS)}
    simulate_trusted_check_plan_v2(plan, fixtures=fixtures, authority=TrustedCheckAuthorityV2.TRUSTED)
