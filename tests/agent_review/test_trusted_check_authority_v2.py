"""Authority-boundary unit tests for #201-B3 (rev. 4, C1).

Pure classification/eligibility logic -- no subprocess, so these run under
the default marker set (unlike `test_isolated_executor_v2.py`).

The rule these tests exist to enforce, stated once:

    controls(subject, success_signal) => not authoritative(success_signal)

Isolating PR-controlled code does not make its output authoritative. The
defence is the CLASS boundary, refused BEFORE spawn -- never a post-hoc
detector.
"""

from __future__ import annotations

import pytest

from app.agent_review.trusted_check_authority_v2 import (
    ELIGIBILITY_ELIGIBLE_V2,
    ELIGIBILITY_INELIGIBLE_V2,
    EXECUTOR_REASON_EXECUTABLE_NOT_ABSOLUTE_V2,
    EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2,
    EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2,
    EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2,
    STATE_COMPLETE_V2,
    STATE_UNAVAILABLE_V2,
    STATE_UNKNOWN_V2,
    STATE_VERIFIED_V2,
    WORKLOAD_HOST_OWNED_V2,
    WORKLOAD_SUBJECT_OWNED_V2,
    ExecutionAssessmentV2,
    ExecutionClassV2,
    classify_command_spec_v2,
    compute_promotion_eligibility_v2,
    coverage_authority_for_class_v2,
    trusted_refusal_reason_v2,
    workload_authority_for_class_v2,
)
from app.agent_review.trusted_checks_v2 import TrustedCheckAuthorityV2


def _classify(**overrides):
    raw = dict(
        execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL,
        host_owned_config=True,
        loads_checkout_plugins=False,
        executable_is_absolute=True,
    )
    raw.update(overrides)
    return classify_command_spec_v2(**raw)


# -- classification -----------------------------------------------------------


def test_subject_code_is_never_trusted_eligible():
    classification = _classify(execution_class=ExecutionClassV2.SUBJECT_CODE)
    assert classification.effective_class is ExecutionClassV2.SUBJECT_CODE
    assert classification.trusted_refusal_reason == EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2


def test_unknown_class_is_refused_rather_than_inferred():
    classification = _classify(execution_class=ExecutionClassV2.UNKNOWN)
    assert classification.effective_class is ExecutionClassV2.UNKNOWN
    assert classification.trusted_refusal_reason == EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2


def test_data_only_tool_without_host_owned_config_is_downgraded():
    classification = _classify(host_owned_config=False)
    assert classification.declared_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL
    assert classification.effective_class is ExecutionClassV2.SUBJECT_CODE
    assert classification.trusted_refusal_reason == EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2


def test_declaration_cannot_override_plugin_loading():
    classification = _classify(loads_checkout_plugins=True)
    assert classification.declared_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL
    assert classification.effective_class is ExecutionClassV2.SUBJECT_CODE
    assert classification.trusted_refusal_reason == EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2


def test_host_owned_data_only_tool_is_the_only_trusted_eligible_class():
    classification = _classify()
    assert classification.effective_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL
    assert classification.trusted_refusal_reason is None


def test_a_bare_executable_name_downgrades_a_data_only_tool():
    """A review caught that a bare argv[0] (e.g. `"ruff"`, not
    `"/usr/bin/ruff"`) is resolved by `execvp` searching $PATH from the
    CHECKOUT's own cwd -- a runner with `.` (or another PR-writable
    directory) on PATH would let the PR supply the "host-owned" binary
    itself and choose its own exit status, reaching the exact exit-code
    forgery this class boundary exists to close through argv[0] resolution
    instead of config content."""

    classification = _classify(executable_is_absolute=False)
    assert classification.declared_class is ExecutionClassV2.DATA_ONLY_HOST_TOOL
    assert classification.effective_class is ExecutionClassV2.SUBJECT_CODE
    assert classification.trusted_refusal_reason == EXECUTOR_REASON_EXECUTABLE_NOT_ABSOLUTE_V2


@pytest.mark.parametrize(
    "execution_class",
    [ExecutionClassV2.DATA_ONLY_HOST_TOOL, ExecutionClassV2.SUBJECT_CODE, ExecutionClassV2.UNKNOWN],
)
def test_untrusted_advisory_is_permitted_for_every_class(execution_class):
    classification = _classify(execution_class=execution_class, host_owned_config=True)
    assert (
        trusted_refusal_reason_v2(
            classification=classification, authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY
        )
        is None
    )


def test_trusted_refusal_reason_is_reported_only_for_trusted_authority():
    classification = _classify(execution_class=ExecutionClassV2.SUBJECT_CODE)
    assert (
        trusted_refusal_reason_v2(
            classification=classification, authority=TrustedCheckAuthorityV2.TRUSTED
        )
        == EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2
    )


# -- coverage / workload authority --------------------------------------------


def test_subject_code_never_establishes_coverage_authority():
    assert coverage_authority_for_class_v2(ExecutionClassV2.SUBJECT_CODE) == STATE_UNAVAILABLE_V2
    assert coverage_authority_for_class_v2(ExecutionClassV2.UNKNOWN) == STATE_UNAVAILABLE_V2
    assert (
        coverage_authority_for_class_v2(ExecutionClassV2.DATA_ONLY_HOST_TOOL) == STATE_COMPLETE_V2
    )


def test_workload_authority_follows_the_effective_class():
    assert (
        workload_authority_for_class_v2(ExecutionClassV2.DATA_ONLY_HOST_TOOL)
        == WORKLOAD_HOST_OWNED_V2
    )
    assert workload_authority_for_class_v2(ExecutionClassV2.SUBJECT_CODE) == WORKLOAD_SUBJECT_OWNED_V2
    assert workload_authority_for_class_v2(ExecutionClassV2.UNKNOWN) == STATE_UNKNOWN_V2


# -- promotion eligibility ----------------------------------------------------


def _eligible_assessment(**overrides) -> ExecutionAssessmentV2:
    raw = dict(
        subject_identity=STATE_VERIFIED_V2,
        inventory_binding=STATE_VERIFIED_V2,
        execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL.value,
        workload_authority=WORKLOAD_HOST_OWNED_V2,
        harness_integrity=STATE_VERIFIED_V2,
        protocol_integrity=STATE_VERIFIED_V2,
        containment=STATE_VERIFIED_V2,
        resource_boundary=STATE_VERIFIED_V2,
        coverage_authority=STATE_COMPLETE_V2,
        product_outcome="success",
    )
    raw.update(overrides)
    return ExecutionAssessmentV2(**raw)


def test_every_premise_verified_and_data_only_is_eligible():
    assert compute_promotion_eligibility_v2(_eligible_assessment()) == ELIGIBILITY_ELIGIBLE_V2


@pytest.mark.parametrize(
    "field",
    [
        "subject_identity",
        "inventory_binding",
        "harness_integrity",
        "protocol_integrity",
        "containment",
        "resource_boundary",
    ],
)
def test_any_unknown_premise_is_ineligible_never_optimistic(field):
    assessment = _eligible_assessment(**{field: STATE_UNKNOWN_V2})
    assert compute_promotion_eligibility_v2(assessment) == ELIGIBILITY_INELIGIBLE_V2


def test_subject_code_is_ineligible_even_with_every_other_premise_verified():
    assessment = _eligible_assessment(
        execution_class=ExecutionClassV2.SUBJECT_CODE.value,
        workload_authority=WORKLOAD_SUBJECT_OWNED_V2,
        coverage_authority=STATE_UNAVAILABLE_V2,
    )
    assert compute_promotion_eligibility_v2(assessment) == ELIGIBILITY_INELIGIBLE_V2


def test_failed_containment_is_ineligible_without_erasing_the_product_outcome():
    assessment = _eligible_assessment(product_outcome="failure", containment="failed")
    assert assessment.product_outcome == "failure"
    assert assessment.containment == "failed"
    assert compute_promotion_eligibility_v2(assessment) == ELIGIBILITY_INELIGIBLE_V2


def test_non_success_product_outcome_is_ineligible():
    for outcome in ("failure", "timeout", "cancelled", "setup_failure", "unknown"):
        assessment = _eligible_assessment(product_outcome=outcome)
        assert compute_promotion_eligibility_v2(assessment) == ELIGIBILITY_INELIGIBLE_V2
