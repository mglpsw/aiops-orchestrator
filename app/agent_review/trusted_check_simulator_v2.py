"""Offline simulator for AgentReview v2 trusted checks (#201-B1, second
slice of #201, child of the distribution epic #199).

Produces real, fully-validated, plan-bound ``TrustedCheckResultV2``
instances WITHOUT spawning a single process, reading a repository
checkout, or touching a filesystem -- there is no isolation to prove here
because nothing runs. This exists to let every downstream consumer
(`#201-C`'s readiness wiring, and this repository's own test suite) be
built and tested against a real, contract-shaped result before the real
isolated executor (`#201-B2`) exists, mirroring `review_transport_v2.
offline_file_transport_v2`'s own role for the Router transport: the
DEFAULT, deterministic stand-in every test exercises, with the real thing
arriving in the next slice.

## This is not a shortcut around isolation -- it is honest about not being one

`simulate_trusted_check_plan_v2` takes ``authority`` as a REQUIRED keyword
argument with no default. There is deliberately no way to call this
function and get a `TrustedCheckAuthorityV2.TRUSTED` result "by accident"
-- a caller stamping simulated output as trusted is making that choice
explicitly, every time, and is asserting something about ITS OWN test
context (e.g. "this test is verifying `#201-C`'s wiring, not `#201-B2`'s
isolation"), not something this module asserts on its behalf. Nothing
about calling this function makes a result MORE trustworthy than an
`UNTRUSTED_ADVISORY` one from `promote_trusted_check_to_required_v2`'s own
point of view -- authority is a value the caller sets, not a property this
simulator earns.
"""

from __future__ import annotations

import hashlib
import json
from typing import Mapping

from app.agent_review.contracts_v2 import ContractV2Model, SafeIdentifier
from app.agent_review.trusted_checks_v2 import (
    TrustedCheckAuthorityV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckResultMaterialV2,
    TrustedCheckResultV2,
    _RESOLVED_OUTCOMES_V2,
    compute_trusted_check_result_sha256_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

SIMULATION_MISSING_FIXTURE_REASON_V2 = "trusted_check_simulation_missing_fixture"
SIMULATION_UNKNOWN_CHECK_REASON_V2 = "trusted_check_simulation_unknown_check"


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class SimulatedCheckOutcomeV2(ContractV2Model):
    """One fixture: what a specific `check_name` should simulate to. Not a
    wire contract of its own (no `schema_id`/`schema_version`) -- purely a
    test/simulation input value, never persisted or transported."""

    check_name: SafeIdentifier
    outcome: TrustedCheckOutcomeV2


class TrustedCheckSimulationError(ExpectedOperationalRefusalV2, ValueError):
    """Raised by `simulate_trusted_check_plan_v2`. Carries a stable
    `reason_code` only."""

    def __init__(self, reason_code: str, *, check_name: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.check_name = check_name


def _compute_simulated_artifact_sha256_v2(
    *, run_id: str, head_sha: str, check_name: str, outcome: TrustedCheckOutcomeV2
) -> str:
    """Deterministic, offline artifact hash for a simulated resolved
    outcome. Derived ONLY from identity already present in the plan/fixture
    -- no clock, no randomness, no filesystem read -- so the same
    ``(plan, fixtures)`` pair always simulates to byte-identical results,
    proven directly by this module's own test suite, not assumed."""

    material = {"run_id": run_id, "head_sha": head_sha, "check_name": check_name, "outcome": outcome.value}
    return hashlib.sha256(_canonical_json_bytes_v2(material)).hexdigest()


def simulate_trusted_check_plan_v2(
    plan: TrustedCheckPlanV2,
    *,
    fixtures: Mapping[str, SimulatedCheckOutcomeV2],
    authority: TrustedCheckAuthorityV2,
) -> tuple[TrustedCheckResultV2, ...]:
    """Simulate every check `plan` authorizes, in `plan.checks` order.
    Every `check_name` in `plan.checks` MUST have a matching entry in
    `fixtures` -- a missing one is a caller configuration error
    (`SIMULATION_MISSING_FIXTURE_REASON_V2`), not silently skipped or
    degraded to an environmental outcome (a caller wanting to simulate an
    environmental failure supplies that outcome explicitly in the
    fixture). A `fixtures` entry naming a check the plan does NOT
    authorize is equally rejected (`SIMULATION_UNKNOWN_CHECK_REASON_V2`)
    -- the simulator's own inputs are held to the same closed-set
    discipline the real executor will eventually need.

    Every returned `TrustedCheckResultV2` is independently constructed
    (self-hash validated) and bindable against `plan` via `trusted_checks_
    v2.bind_trusted_check_result_to_plan_v2` -- proven directly by this
    module's own test suite for every returned result, not merely assumed
    from shape.
    """

    plan_check_names = {check.check_name for check in plan.checks}
    fixture_names = set(fixtures)
    unknown = fixture_names - plan_check_names
    if unknown:
        raise TrustedCheckSimulationError(
            SIMULATION_UNKNOWN_CHECK_REASON_V2, check_name=sorted(unknown)[0]
        )
    missing = plan_check_names - fixture_names
    if missing:
        raise TrustedCheckSimulationError(
            SIMULATION_MISSING_FIXTURE_REASON_V2, check_name=sorted(missing)[0]
        )

    results: list[TrustedCheckResultV2] = []
    for check in plan.checks:
        fixture = fixtures[check.check_name]
        is_resolved = fixture.outcome in _RESOLVED_OUTCOMES_V2
        artifact_sha256 = (
            _compute_simulated_artifact_sha256_v2(
                run_id=plan.run_id, head_sha=plan.head_sha,
                check_name=check.check_name, outcome=fixture.outcome,
            )
            if is_resolved
            else None
        )
        material = TrustedCheckResultMaterialV2(
            schema_id="agent-review.trusted-check-result.v2", schema_version=2,
            run_id=plan.run_id, head_sha=plan.head_sha, check_name=check.check_name,
            authority=authority, outcome=fixture.outcome, harness_digest=plan.harness_digest,
            artifact_sha256=artifact_sha256,
        )
        result_sha256 = compute_trusted_check_result_sha256_v2(material)
        results.append(
            TrustedCheckResultV2(
                schema_id="agent-review.trusted-check-result.v2", schema_version=2,
                run_id=plan.run_id, head_sha=plan.head_sha, check_name=check.check_name,
                authority=authority, outcome=fixture.outcome, harness_digest=plan.harness_digest,
                artifact_sha256=artifact_sha256, result_sha256=result_sha256,
            )
        )
    return tuple(results)
