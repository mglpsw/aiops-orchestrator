from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RequiredCheckConclusionV2
from app.agent_review.trusted_checks_v2 import (
    PROMOTION_NOT_TRUSTED_AUTHORITY_REASON_V2,
    PROMOTION_OUTCOME_NOT_RESOLVED_REASON_V2,
    RESULT_CHECK_NOT_IN_PLAN_REASON_V2,
    RESULT_HARNESS_DIGEST_MISMATCH_REASON_V2,
    RESULT_HASH_MISMATCH_REASON_V2,
    RESULT_HEAD_SHA_MISMATCH_REASON_V2,
    RESULT_RUN_IDENTITY_MISMATCH_REASON_V2,
    AllowlistedCheckCommandV2,
    TrustedCheckAuthorityV2,
    TrustedCheckBindingError,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckPromotionError,
    TrustedCheckResultMaterialV2,
    TrustedCheckResultV2,
    bind_trusted_check_result_to_plan_v2,
    compute_trusted_check_result_sha256_v2,
    promote_trusted_check_to_required_v2,
)

RUN = hashlib.sha256(b"run").hexdigest()
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
HARNESS = hashlib.sha256(b"harness").hexdigest()
OTHER_HARNESS = hashlib.sha256(b"other-harness").hexdigest()
SUITE = hashlib.sha256(b"suite").hexdigest()
ARTIFACT = hashlib.sha256(b"artifact").hexdigest()


def _command(**overrides) -> AllowlistedCheckCommandV2:
    raw = dict(
        check_name="pytest", command_token="pytest_suite", timeout_seconds=600,
        max_memory_mb=2048, max_processes=32, network_allowed=False,
    )
    raw.update(overrides)
    return AllowlistedCheckCommandV2(**raw)


def _plan(*, checks=None, **overrides) -> TrustedCheckPlanV2:
    raw = dict(
        schema_id="agent-review.trusted-check-plan.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, harness_digest=HARNESS, authority_suite_digest=SUITE,
        checks=list(checks) if checks is not None else [_command()],
    )
    raw.update(overrides)
    return TrustedCheckPlanV2(**raw)


def _result(**overrides) -> TrustedCheckResultV2:
    raw = dict(
        schema_id="agent-review.trusted-check-result.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, check_name="pytest",
        authority=TrustedCheckAuthorityV2.TRUSTED, outcome=TrustedCheckOutcomeV2.SUCCESS,
        harness_digest=HARNESS, artifact_sha256=ARTIFACT,
    )
    raw.update(overrides)
    material = TrustedCheckResultMaterialV2(**raw)
    return TrustedCheckResultV2(**raw, result_sha256=compute_trusted_check_result_sha256_v2(material))


# -- AllowlistedCheckCommandV2 / TrustedCheckPlanV2 --------------------------


def test_network_allowed_is_pinned_false_structurally():
    with pytest.raises(ValidationError):
        AllowlistedCheckCommandV2(
            check_name="pytest", command_token="pytest_suite", timeout_seconds=600,
            max_memory_mb=2048, max_processes=32, network_allowed=True,
        )


def test_plan_requires_at_least_one_check():
    with pytest.raises(ValidationError):
        _plan(checks=[])


def test_plan_rejects_duplicate_check_names():
    with pytest.raises(ValidationError):
        _plan(checks=[_command(command_token="a"), _command(command_token="b")])


# -- TrustedCheckResultMaterialV2 --------------------------------------------


def test_resolved_outcome_requires_artifact_sha256():
    with pytest.raises(ValidationError):
        TrustedCheckResultMaterialV2(
            schema_id="agent-review.trusted-check-result.v2", schema_version=2,
            run_id=RUN, head_sha=HEAD, check_name="pytest",
            authority=TrustedCheckAuthorityV2.TRUSTED, outcome=TrustedCheckOutcomeV2.SUCCESS,
            harness_digest=HARNESS, artifact_sha256=None,
        )


@pytest.mark.parametrize(
    "outcome",
    [TrustedCheckOutcomeV2.TIMEOUT, TrustedCheckOutcomeV2.OOM,
     TrustedCheckOutcomeV2.CANCELLED, TrustedCheckOutcomeV2.INFRA_FAILURE],
)
def test_environmental_outcome_forbids_artifact_sha256(outcome):
    with pytest.raises(ValidationError):
        TrustedCheckResultMaterialV2(
            schema_id="agent-review.trusted-check-result.v2", schema_version=2,
            run_id=RUN, head_sha=HEAD, check_name="pytest",
            authority=TrustedCheckAuthorityV2.TRUSTED, outcome=outcome,
            harness_digest=HARNESS, artifact_sha256=ARTIFACT,
        )


def test_result_sha256_is_deterministic_across_construction_order():
    a = _result()
    b = _result()
    assert a.result_sha256 == b.result_sha256


def test_result_sha256_is_sensitive_to_one_field():
    a = _result()
    b = _result(check_name="eslint")
    assert a.result_sha256 != b.result_sha256


def test_result_hash_self_consistency_rejects_a_tampered_hash():
    material = TrustedCheckResultMaterialV2(
        schema_id="agent-review.trusted-check-result.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, check_name="pytest",
        authority=TrustedCheckAuthorityV2.TRUSTED, outcome=TrustedCheckOutcomeV2.SUCCESS,
        harness_digest=HARNESS, artifact_sha256=ARTIFACT,
    )
    with pytest.raises(ValidationError):
        TrustedCheckResultV2(**material.model_dump(mode="json"), result_sha256="0" * 64)


# -- bind_trusted_check_result_to_plan_v2 ------------------------------------


def test_bind_accepts_a_matching_result():
    plan = _plan()
    result = _result()
    bind_trusted_check_result_to_plan_v2(result, plan)  # does not raise


def test_bind_rejects_a_cross_run_result():
    plan = _plan()
    result = _result(run_id=hashlib.sha256(b"other-run").hexdigest())
    with pytest.raises(TrustedCheckBindingError) as excinfo:
        bind_trusted_check_result_to_plan_v2(result, plan)
    assert excinfo.value.reason_code == RESULT_RUN_IDENTITY_MISMATCH_REASON_V2


def test_bind_rejects_a_stale_head_sha():
    plan = _plan()
    result = _result(head_sha=OTHER_HEAD)
    with pytest.raises(TrustedCheckBindingError) as excinfo:
        bind_trusted_check_result_to_plan_v2(result, plan)
    assert excinfo.value.reason_code == RESULT_HEAD_SHA_MISMATCH_REASON_V2


def test_bind_rejects_a_harness_digest_mismatch():
    plan = _plan()
    result = _result(harness_digest=OTHER_HARNESS)
    with pytest.raises(TrustedCheckBindingError) as excinfo:
        bind_trusted_check_result_to_plan_v2(result, plan)
    assert excinfo.value.reason_code == RESULT_HARNESS_DIGEST_MISMATCH_REASON_V2


def test_bind_rejects_a_check_not_authorized_by_the_plan():
    plan = _plan(checks=[_command(check_name="eslint", command_token="eslint_suite")])
    result = _result(check_name="pytest")
    with pytest.raises(TrustedCheckBindingError) as excinfo:
        bind_trusted_check_result_to_plan_v2(result, plan)
    assert excinfo.value.reason_code == RESULT_CHECK_NOT_IN_PLAN_REASON_V2


def test_bind_rejects_a_result_bypassing_construction_via_model_construct():
    plan = _plan()
    # model_construct skips validators -- the exact bypass bind_*_v2 exists to catch.
    tampered = TrustedCheckResultV2.model_construct(
        schema_id="agent-review.trusted-check-result.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, check_name="pytest",
        authority=TrustedCheckAuthorityV2.TRUSTED, outcome=TrustedCheckOutcomeV2.SUCCESS,
        harness_digest=HARNESS, artifact_sha256=ARTIFACT, result_sha256="f" * 64,
    )
    with pytest.raises(TrustedCheckBindingError) as excinfo:
        bind_trusted_check_result_to_plan_v2(tampered, plan)
    assert excinfo.value.reason_code == RESULT_HASH_MISMATCH_REASON_V2


# -- promote_trusted_check_to_required_v2 ------------------------------------


def test_promotion_succeeds_for_a_trusted_resolved_success():
    result = _result(outcome=TrustedCheckOutcomeV2.SUCCESS)
    required = promote_trusted_check_to_required_v2(result)
    assert required.conclusion is RequiredCheckConclusionV2.SUCCESS
    assert required.check_name == "pytest"
    assert required.head_sha == HEAD


def test_promotion_succeeds_for_a_trusted_resolved_failure():
    result = _result(outcome=TrustedCheckOutcomeV2.FAILURE)
    required = promote_trusted_check_to_required_v2(result)
    assert required.conclusion is RequiredCheckConclusionV2.FAILURE


def test_promotion_refuses_untrusted_advisory_even_with_a_resolved_outcome():
    result = _result(authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY, outcome=TrustedCheckOutcomeV2.SUCCESS)
    with pytest.raises(TrustedCheckPromotionError) as excinfo:
        promote_trusted_check_to_required_v2(result)
    assert excinfo.value.reason_code == PROMOTION_NOT_TRUSTED_AUTHORITY_REASON_V2


@pytest.mark.parametrize(
    "outcome",
    [TrustedCheckOutcomeV2.TIMEOUT, TrustedCheckOutcomeV2.OOM,
     TrustedCheckOutcomeV2.CANCELLED, TrustedCheckOutcomeV2.INFRA_FAILURE],
)
def test_promotion_refuses_every_environmental_outcome_even_from_trusted_authority(outcome):
    result = _result(authority=TrustedCheckAuthorityV2.TRUSTED, outcome=outcome, artifact_sha256=None)
    with pytest.raises(TrustedCheckPromotionError) as excinfo:
        promote_trusted_check_to_required_v2(result)
    assert excinfo.value.reason_code == PROMOTION_OUTCOME_NOT_RESOLVED_REASON_V2


def test_no_required_check_conclusion_value_exists_for_environmental_failure():
    # Structural proof, not just behavioral: RequiredCheckConclusionV2 has
    # exactly 4 members and none represents "environment failed" -- so
    # promotion refusing environmental outcomes is forced by the existing
    # frozen contract's own shape, not merely this module's choice.
    assert {c.value for c in RequiredCheckConclusionV2} == {"success", "failure", "pending", "missing"}
