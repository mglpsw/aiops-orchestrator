"""`#200-F` §4 -- public caller input is validated before the seal.

The headline case is the `#276` round-4 witness. It was not an exotic attack:
``--delivery-id 'bad id here'`` is an ordinary wrong value for a required
public flag, and it produced a raw ``pydantic.ValidationError`` traceback that
leaked virtualenv and subject temp-directory paths.

The bidirectional invariant matters as much as the headline. A boundary that
converted *everything* into a tidy refusal would hide real defects, so the
negative direction -- post-seal malformed derivation must stay a raw
programmer defect -- is asserted here with equal weight.
"""

from __future__ import annotations

import pathlib

import pydantic
import pytest

from app.agent_review.contracts_v2 import RunOriginV2
from app.agent_review.operational_ingress_v2 import (
    INGRESS_INVALID_PUBLIC_INPUT_REASON_V2,
    INGRESS_PATH_NOT_ABSOLUTE_REASON_V2,
    INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2,
    INGRESS_PATH_NOT_A_FILE_REASON_V2,
    INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2,
    PUBLIC_INPUT_FIELD_NAMES_V2,
    OperationalIngressError,
    ValidatedPublicInputsV2,
    public_input_reason_code_v2,
    validate_existing_directory_v2,
    validate_existing_file_v2,
    validate_public_inputs_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2


def _well_formed_public_inputs_v2() -> dict[str, object]:
    return {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 276,
        "base_sha": "f" * 40,
        "head_sha": "a" * 40,
        "tested_merge_sha": "b" * 40,
        "toolchain_digest": "c" * 64,
        "event_type": "pull_request",
        "event_action": "synchronize",
        "delivery_id": "delivery-0001",
    }


def test_well_formed_inputs_pass_the_gate() -> None:
    """Non-vacuity control for every rejection test in this file.

    Without it, a gate that refused *everything* would satisfy all the
    negative assertions below and look perfectly healthy.
    """
    validated = validate_public_inputs_v2(_well_formed_public_inputs_v2())

    assert isinstance(validated, ValidatedPublicInputsV2)
    assert validated.repo == "mglpsw/aiops-orchestrator"
    assert validated.pr_number == 276
    assert validated.delivery_id == "delivery-0001"
    assert isinstance(validated.as_run_origin_v2(), RunOriginV2)


def test_the_276_round_four_witness_is_a_typed_refusal_not_a_validation_error() -> None:
    """The exact predecessor escape, closed.

    Asserts three separate things, because the `#276` failure needed all three
    to go wrong: the error is a family member (so the boundary catches it
    structurally), it names the field, and it is emphatically not a
    ``ValidationError``.
    """
    inputs = _well_formed_public_inputs_v2()
    inputs["delivery_id"] = "bad id here"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert isinstance(caught.value, ExpectedOperationalRefusalV2)
    assert not isinstance(caught.value, pydantic.ValidationError)
    assert caught.value.reason_code == "operational_ingress_invalid_delivery_id"


def test_no_refusal_ever_carries_the_offending_value() -> None:
    """A wrong flag is a plausible way to leak a secret into logs.

    If an operator pastes a token into ``--delivery-id``, the refusal must not
    echo it. The whole rendered exception is checked, not just the reason
    code, because ``str(exc)`` is what ends up in an operator's terminal.
    """
    secret = "ghp-THIS-MUST-NOT-BE-ECHOED-0123456789"
    inputs = _well_formed_public_inputs_v2()
    inputs["delivery_id"] = f"{secret} with spaces"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)
    assert secret not in caught.value.reason_code


@pytest.mark.parametrize(
    "field_name, bad_value",
    [
        ("repo", "not-a-repo-slug"),
        ("pr_number", 0),
        ("base_sha", "short"),
        ("head_sha", "Z" * 40),
        ("tested_merge_sha", ""),
        ("toolchain_digest", "d" * 63),
        ("delivery_id", "has spaces"),
    ],
)
def test_every_public_field_refuses_with_its_own_reason_code(
    field_name: str, bad_value: object
) -> None:
    """Codes are field-specific, per the grant's "do not collapse" rule.

    A single generic code would be useless exactly when an operator needs to
    know which of nine flags they got wrong.
    """
    inputs = _well_formed_public_inputs_v2()
    inputs[field_name] = bad_value

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == f"operational_ingress_invalid_{field_name}"


def test_reason_codes_are_derived_from_the_model_not_a_second_list() -> None:
    """The anti-recurrence property of authority A, applied to ingress.

    `#276` failed because a second, hand-maintained list fell behind the
    first. Here the codes are a function of ``model_fields``, so a new public
    input cannot be added without its code appearing.
    """
    assert set(PUBLIC_INPUT_FIELD_NAMES_V2) == {
        "repo",
        "pr_number",
        "base_sha",
        "head_sha",
        "tested_merge_sha",
        "toolchain_digest",
        "event_type",
        "event_action",
        "delivery_id",
    }
    for field_name in PUBLIC_INPUT_FIELD_NAMES_V2:
        assert public_input_reason_code_v2(field_name) == (
            f"operational_ingress_invalid_{field_name}"
        )
    assert public_input_reason_code_v2("not-a-field") == (
        INGRESS_INVALID_PUBLIC_INPUT_REASON_V2
    )


def test_a_cross_field_rule_refuses_without_naming_a_single_field() -> None:
    """``event_type``/``event_action`` is a model-level rule, not a field one.

    pydantic reports it with an empty location, so the field-name extraction
    must fall back rather than index into an empty tuple.
    """
    inputs = _well_formed_public_inputs_v2()
    inputs["event_type"] = "manual"
    inputs["event_action"] = "synchronize"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == INGRESS_INVALID_PUBLIC_INPUT_REASON_V2


def test_an_unknown_public_input_is_refused_without_echoing_its_name() -> None:
    """An unrecognised key is caller text, so it is counted but not quoted."""
    inputs = _well_formed_public_inputs_v2()
    inputs["surprise_token"] = "sk-live-should-not-appear"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2
    assert "surprise_token" not in str(caught.value)
    assert "sk-live-should-not-appear" not in str(caught.value)


def test_paths_must_be_absolute_and_must_already_exist(tmp_path: pathlib.Path) -> None:
    """Relative paths are refused rather than resolved against the CWD.

    Resolving would make a run's meaning depend on the caller's working
    directory, which is not part of any identity the product records.
    """
    existing_file = tmp_path / "profile.yaml"
    existing_file.write_text("{}", encoding="utf-8")

    assert validate_existing_file_v2(existing_file) == existing_file
    assert validate_existing_directory_v2(tmp_path) == tmp_path

    with pytest.raises(OperationalIngressError) as relative:
        validate_existing_file_v2("profile.yaml")
    assert relative.value.reason_code == INGRESS_PATH_NOT_ABSOLUTE_REASON_V2

    with pytest.raises(OperationalIngressError) as missing:
        validate_existing_file_v2(tmp_path / "absent.yaml")
    assert missing.value.reason_code == INGRESS_PATH_NOT_A_FILE_REASON_V2

    with pytest.raises(OperationalIngressError) as not_a_dir:
        validate_existing_directory_v2(existing_file)
    assert not_a_dir.value.reason_code == INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2

    with pytest.raises(OperationalIngressError) as file_is_a_dir:
        validate_existing_file_v2(tmp_path)
    assert file_is_a_dir.value.reason_code == INGRESS_PATH_NOT_A_FILE_REASON_V2


def test_ingress_refusals_are_members_of_the_operational_family() -> None:
    """So the boundary catches them structurally, never by name."""
    assert issubclass(OperationalIngressError, ExpectedOperationalRefusalV2)
    assert issubclass(OperationalIngressError, ValueError)


def test_post_seal_malformed_derivation_is_not_converted_into_a_refusal() -> None:
    """The mandatory negative direction (grant §4).

    Ingress must translate *caller* material only. If internal post-seal
    derivation produces something a contract rejects, that is our bug, and it
    has to keep looking like a bug. This asserts the module exports no
    machinery that would launder it: constructing a contract directly still
    raises ``ValidationError``, and that error is *not* a family member, so
    the boundary will let it escape as a programmer defect.
    """
    with pytest.raises(pydantic.ValidationError) as caught:
        RunOriginV2(
            event_type="pull_request",
            event_action="opened",
            delivery_id="derived internally, malformed",
        )

    assert not isinstance(caught.value, ExpectedOperationalRefusalV2)
    assert not isinstance(caught.value, OperationalIngressError)


def test_derived_identity_components_are_not_accepted_from_callers() -> None:
    """Authority B's rule, enforced at the ingress model.

    ``profile_hash`` and friends are computed by the product from material it
    controls. Accepting them as public input would let a caller assert an
    identity rather than have one derived -- the same class of authority leak
    as the private argv flags this slice retires.
    """
    for derived_field in (
        "profile_hash",
        "policy_hash",
        "manifest_hash",
        "evidence_hash",
        "toolrepo_sha",
    ):
        assert derived_field not in PUBLIC_INPUT_FIELD_NAMES_V2

        inputs = _well_formed_public_inputs_v2()
        inputs[derived_field] = "e" * 64

        with pytest.raises(OperationalIngressError) as caught:
            validate_public_inputs_v2(inputs)
        assert caught.value.reason_code == INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2
