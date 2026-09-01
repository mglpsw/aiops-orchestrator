"""`#200-G4` -- public caller input is validated before the seal.

Ported with revalidation from `#277`'s ``test_operational_ingress_v2.py``
(the nine-scalar and single-document tests below are unchanged in intent) and
extended with the coverage `#277` round 2 proved was still missing: the
``--responses`` directory and its individual entries, the inner-control-fd
environment variable, and the argparse usage-error path.

The bidirectional invariant matters as much as any single headline witness. A
boundary that converted *everything* into a tidy refusal would hide real
defects, so the negative direction -- post-seal malformed derivation, and a
genuine internal programmer defect, must both stay raw -- is asserted with
equal weight throughout this file, not just once at the end.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pydantic
import pytest

from app.agent_review.contracts_v2 import ContractV2Model, RunOriginV2
from app.agent_review.operational_ingress_v2 import (
    INGRESS_CONTROL_FD_INVALID_REASON_V2,
    INGRESS_DOCUMENT_INVALID_REASON_V2,
    INGRESS_DOCUMENT_UNREADABLE_REASON_V2,
    INGRESS_INVALID_PUBLIC_INPUT_REASON_V2,
    INGRESS_PATH_NOT_ABSOLUTE_REASON_V2,
    INGRESS_PATH_NOT_A_DIRECTORY_REASON_V2,
    INGRESS_PATH_NOT_A_FILE_REASON_V2,
    INGRESS_RESPONSE_ESCAPES_DIRECTORY_REASON_V2,
    INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2,
    INGRESS_USAGE_ERROR_REASON_V2,
    PUBLIC_INPUT_FIELD_NAMES_V2,
    NoEchoArgumentParserV2,
    OperationalIngressError,
    ValidatedPublicInputsV2,
    public_input_reason_code_v2,
    read_offline_response_document_v2,
    resolve_inner_control_fd_v2,
    validate_caller_document_v2,
    validate_existing_directory_v2,
    validate_existing_file_v2,
    validate_public_inputs_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2


def _well_formed_public_inputs_v2() -> dict[str, object]:
    return {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 282,
        "base_sha": "f" * 40,
        "head_sha": "a" * 40,
        "tested_merge_sha": "b" * 40,
        "toolchain_digest": "c" * 64,
        "event_type": "pull_request",
        "event_action": "synchronize",
        "delivery_id": "delivery-0001",
    }


# ---------------------------------------------------------------------------
# Ported: nine scalar public inputs.
# ---------------------------------------------------------------------------


def test_well_formed_inputs_pass_the_gate() -> None:
    """Non-vacuity control for every rejection test in this file."""
    validated = validate_public_inputs_v2(_well_formed_public_inputs_v2())

    assert isinstance(validated, ValidatedPublicInputsV2)
    assert validated.repo == "mglpsw/aiops-orchestrator"
    assert validated.pr_number == 282
    assert validated.delivery_id == "delivery-0001"
    assert isinstance(validated.as_run_origin_v2(), RunOriginV2)


def test_the_276_round_four_witness_is_a_typed_refusal_not_a_validation_error() -> None:
    """The exact predecessor escape, closed."""
    inputs = _well_formed_public_inputs_v2()
    inputs["delivery_id"] = "bad id here"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert isinstance(caught.value, ExpectedOperationalRefusalV2)
    assert not isinstance(caught.value, pydantic.ValidationError)
    assert caught.value.reason_code == "operational_ingress_invalid_delivery_id"


def test_no_refusal_ever_carries_the_offending_value() -> None:
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
    inputs = _well_formed_public_inputs_v2()
    inputs[field_name] = bad_value

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == f"operational_ingress_invalid_{field_name}"


def test_reason_codes_are_derived_from_the_model_not_a_second_list() -> None:
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
    inputs = _well_formed_public_inputs_v2()
    inputs["event_type"] = "manual"
    inputs["event_action"] = "synchronize"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == INGRESS_INVALID_PUBLIC_INPUT_REASON_V2


def test_an_unknown_public_input_is_refused_without_echoing_its_name() -> None:
    inputs = _well_formed_public_inputs_v2()
    inputs["surprise_token"] = "sk-live-should-not-appear"

    with pytest.raises(OperationalIngressError) as caught:
        validate_public_inputs_v2(inputs)

    assert caught.value.reason_code == INGRESS_UNKNOWN_PUBLIC_INPUT_REASON_V2
    assert "surprise_token" not in str(caught.value)
    assert "sk-live-should-not-appear" not in str(caught.value)


def test_paths_must_be_absolute_and_must_already_exist(tmp_path: pathlib.Path) -> None:
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
    assert issubclass(OperationalIngressError, ExpectedOperationalRefusalV2)
    assert issubclass(OperationalIngressError, ValueError)


def test_post_seal_malformed_derivation_is_not_converted_into_a_refusal() -> None:
    """The mandatory negative direction, for the ported scalar-input gate."""
    with pytest.raises(pydantic.ValidationError) as caught:
        RunOriginV2(
            event_type="pull_request",
            event_action="opened",
            delivery_id="derived internally, malformed",
        )

    assert not isinstance(caught.value, ExpectedOperationalRefusalV2)
    assert not isinstance(caught.value, OperationalIngressError)


def test_derived_identity_components_are_not_accepted_from_callers() -> None:
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


# ---------------------------------------------------------------------------
# Ported: single-file document reading (`--profile`/`--grouping-policy`
# pattern), retested generically here.
# ---------------------------------------------------------------------------


class _TrivialDocumentModelV2(ContractV2Model):
    label: str


def test_a_well_formed_document_round_trips(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"label": "ok"}), encoding="utf-8")

    result = validate_caller_document_v2(
        path, model=_TrivialDocumentModelV2, field_name="thing"
    )
    assert result.label == "ok"


def test_malformed_document_json_is_a_typed_refusal_never_a_validation_error(
    tmp_path: pathlib.Path,
) -> None:
    secret = "sk-live-DOCUMENT-SECRET-0123456789"
    path = tmp_path / "doc.json"
    path.write_text(f"{{not valid json, but contains {secret}", encoding="utf-8")

    with pytest.raises(OperationalIngressError) as caught:
        validate_caller_document_v2(path, model=_TrivialDocumentModelV2, field_name="thing")

    assert caught.value.reason_code == f"{INGRESS_DOCUMENT_INVALID_REASON_V2}_thing"
    assert not isinstance(caught.value, pydantic.ValidationError)
    assert secret not in str(caught.value)


def test_non_utf8_document_bytes_are_a_typed_refusal_never_a_unicode_error(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "doc.json"
    path.write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(OperationalIngressError) as caught:
        validate_caller_document_v2(path, model=_TrivialDocumentModelV2, field_name="thing")

    assert caught.value.reason_code == f"{INGRESS_DOCUMENT_UNREADABLE_REASON_V2}_thing"
    assert not isinstance(caught.value, UnicodeDecodeError)


# ---------------------------------------------------------------------------
# New: `--responses` directory entries. The mandatory RED witness.
# ---------------------------------------------------------------------------


class _TrivialResponseModelV2(ContractV2Model):
    chunk_label: str


def test_unanswered_chunk_is_none_not_a_refusal(tmp_path: pathlib.Path) -> None:
    """Absence is an ordinary state, distinct from a malformed-but-present
    response -- callers must be able to tell the two apart."""
    result = read_offline_response_document_v2(
        tmp_path, "chunk-0000", model=_TrivialResponseModelV2
    )
    assert result is None


def test_answered_chunk_round_trips(tmp_path: pathlib.Path) -> None:
    (tmp_path / "chunk-0000.json").write_text(
        json.dumps({"chunk_label": "ok"}), encoding="utf-8"
    )
    result = read_offline_response_document_v2(
        tmp_path, "chunk-0000", model=_TrivialResponseModelV2
    )
    assert result is not None
    assert result.chunk_label == "ok"


def test_the_277_round_two_witness_malformed_response_json_is_a_typed_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """RED WITNESS (mandatory): `#277` round 2 found this exact shape --
    malformed JSON in a ``--responses`` entry -- still reaching a raw
    ``pydantic.ValidationError``/``model_validate_json`` traceback. Proven
    raw below in ``test_the_raw_predecessor_shape_really_did_leak``; this is
    the fixed shape."""
    (tmp_path / "chunk-0000.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(OperationalIngressError) as caught:
        read_offline_response_document_v2(
            tmp_path, "chunk-0000", model=_TrivialResponseModelV2
        )

    assert not isinstance(caught.value, pydantic.ValidationError)
    assert caught.value.reason_code == f"{INGRESS_DOCUMENT_INVALID_REASON_V2}_responses"


def test_the_277_round_two_witness_non_utf8_response_is_a_typed_refusal(
    tmp_path: pathlib.Path,
) -> None:
    """RED WITNESS (mandatory), the ``UnicodeDecodeError`` half."""
    (tmp_path / "chunk-0000.json").write_bytes(b"\xff\xfe\x00bad")

    with pytest.raises(OperationalIngressError) as caught:
        read_offline_response_document_v2(
            tmp_path, "chunk-0000", model=_TrivialResponseModelV2
        )

    assert not isinstance(caught.value, UnicodeDecodeError)
    assert caught.value.reason_code == f"{INGRESS_DOCUMENT_UNREADABLE_REASON_V2}_responses"


def test_the_raw_predecessor_shape_really_did_leak(tmp_path: pathlib.Path) -> None:
    """Proves the RED witness is real, independent of this module's fix:
    reading the same file the way the predecessor's un-fixed
    ``_offline_transport_v2`` + downstream ``model_validate_json`` call did
    -- ``Path.read_text`` then ``model_validate_json`` directly, no ingress
    translation -- still raises a raw, un-family exception whose text
    embeds the offending bytes via pydantic's own ``input_value=`` echo."""
    malformed = tmp_path / "chunk-0000.json"
    malformed.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(pydantic.ValidationError) as caught:
        _TrivialResponseModelV2.model_validate_json(malformed.read_text(encoding="utf-8"))

    assert not isinstance(caught.value, ExpectedOperationalRefusalV2)
    assert "not valid json" in str(caught.value)  # the leak, unfixed


def test_reading_a_response_never_echoes_the_chunk_id(tmp_path: pathlib.Path) -> None:
    """A chunk id can be derived from caller-supplied diff content, so it
    must never reach a reason code, exactly like a document's bytes."""
    secret_looking_chunk_id = "chunk-ghp-SHOULD-NOT-BE-ECHOED-0123456789"
    (tmp_path / f"{secret_looking_chunk_id}.json").write_text(
        "{not valid json", encoding="utf-8"
    )

    with pytest.raises(OperationalIngressError) as caught:
        read_offline_response_document_v2(
            tmp_path, secret_looking_chunk_id, model=_TrivialResponseModelV2
        )

    assert secret_looking_chunk_id not in str(caught.value)
    assert secret_looking_chunk_id not in caught.value.reason_code


def test_a_chunk_id_cannot_escape_the_responses_directory(tmp_path: pathlib.Path) -> None:
    """Defence in depth: a chunk id built from caller-influenced text must
    not be usable to read a file outside ``--responses``."""
    outside = tmp_path.parent / "outside-secret.json"
    outside.write_text(json.dumps({"chunk_label": "should not be reachable"}), encoding="utf-8")
    responses_root = tmp_path / "responses"
    responses_root.mkdir()

    with pytest.raises(OperationalIngressError) as caught:
        read_offline_response_document_v2(
            responses_root,
            f"../{outside.stem}",
            model=_TrivialResponseModelV2,
        )

    assert caught.value.reason_code == INGRESS_RESPONSE_ESCAPES_DIRECTORY_REASON_V2


# ---------------------------------------------------------------------------
# New: the inner-control-fd environment variable.
# ---------------------------------------------------------------------------


def test_absent_or_empty_control_fd_resolves_to_no_channel() -> None:
    assert resolve_inner_control_fd_v2(None) is None
    assert resolve_inner_control_fd_v2("") is None


def test_a_well_formed_control_fd_resolves() -> None:
    assert resolve_inner_control_fd_v2("5") == 5
    assert resolve_inner_control_fd_v2("3") == 3


def test_the_277_witness_fd_zero_is_refused_not_used_to_read_stdin() -> None:
    """RED WITNESS (mandatory): `#277` left ``AGENT_REVIEW_INNER_CONTROL_
    FD_V2=0`` accepted as-is, and using it to read the control document
    then blocked forever on the process's own stdin. This function must
    never return ``0``: refusing it here, before any code ever attempts a
    read, is what prevents the hang -- there is no call in this codebase
    that can block on a value this function refused to hand back."""
    with pytest.raises(OperationalIngressError) as caught:
        resolve_inner_control_fd_v2("0")
    assert caught.value.reason_code == INGRESS_CONTROL_FD_INVALID_REASON_V2

    with pytest.raises(OperationalIngressError):
        resolve_inner_control_fd_v2("1")
    with pytest.raises(OperationalIngressError):
        resolve_inner_control_fd_v2("2")


def test_the_277_witness_out_of_range_fd_is_refused_not_an_overflow_error() -> None:
    """RED WITNESS (mandatory): a value outside the platform's representable
    fd range raised a raw ``OverflowError`` at `#277`'s STOP, the first time
    anything used it as a real fd. Reproduced raw below in
    ``test_the_raw_overflow_shape_really_did_happen``; this asserts the fix."""
    with pytest.raises(OperationalIngressError) as caught:
        resolve_inner_control_fd_v2("9" * 40)
    assert caught.value.reason_code == INGRESS_CONTROL_FD_INVALID_REASON_V2
    assert not isinstance(caught.value, OverflowError)

    with pytest.raises(OperationalIngressError):
        resolve_inner_control_fd_v2("-1")


def test_the_raw_overflow_shape_really_did_happen() -> None:
    """Proves the RED witness is real: passing an out-of-C-int-range value
    to a real fd-consuming syscall really does raise a raw ``OverflowError``,
    independent of this module's fix."""
    import os

    huge_fd = 10**30
    with pytest.raises(OverflowError):
        os.fstat(huge_fd)


def test_a_malformed_non_numeric_control_fd_is_refused() -> None:
    with pytest.raises(OperationalIngressError) as caught:
        resolve_inner_control_fd_v2("not-a-number")
    assert caught.value.reason_code == INGRESS_CONTROL_FD_INVALID_REASON_V2


# ---------------------------------------------------------------------------
# New: the argparse usage-error path.
# ---------------------------------------------------------------------------


class _ProbeParserV2(NoEchoArgumentParserV2, argparse.ArgumentParser):
    pass


def test_the_277_witness_usage_error_does_not_echo_argv_text() -> None:
    """RED WITNESS (mandatory): argparse's own ``error()`` writes ``message``
    -- which, for "unrecognized arguments", embeds the caller's own argv
    tokens -- to stderr directly, contradicting this module's own no-echo
    rule. Reproduced raw below in
    ``test_the_raw_argparse_echo_shape_really_did_leak``; this asserts the
    fix: the override never constructs a message containing argv text, and
    reports a stable, content-free reason code as a family member instead."""
    parser = _ProbeParserV2(prog="probe", allow_abbrev=False)
    parser.add_argument("--known", required=True)

    secret = "sk-live-ARGV-SECRET-0123456789"
    with pytest.raises(OperationalIngressError) as caught:
        parser.parse_args(["--known", "x", "--unknown-flag", secret])

    assert caught.value.reason_code == INGRESS_USAGE_ERROR_REASON_V2
    assert secret not in str(caught.value)
    assert secret not in repr(caught.value)


def test_the_raw_argparse_echo_shape_really_did_leak() -> None:
    """Proves the RED witness is real: stock ``argparse.ArgumentParser``
    really does embed argv text in the message passed to ``error()``."""
    parser = argparse.ArgumentParser(prog="probe", allow_abbrev=False, exit_on_error=False)
    parser.add_argument("--known", required=True)

    secret = "sk-live-ARGV-SECRET-0123456789"
    captured: list[str] = []

    class _CapturingParser(argparse.ArgumentParser):
        def error(self, message: str) -> None:  # type: ignore[override]
            captured.append(message)
            raise SystemExit(2)

    capturing = _CapturingParser(prog="probe", allow_abbrev=False)
    capturing.add_argument("--known", required=True)
    with pytest.raises(SystemExit):
        capturing.parse_args(["--known", "x", "--unknown-flag", secret])

    assert any(secret in message for message in captured)  # the leak, unfixed


def test_usage_error_is_a_member_of_the_operational_family() -> None:
    assert issubclass(OperationalIngressError, ExpectedOperationalRefusalV2)


def test_a_well_formed_parse_does_not_raise() -> None:
    """Non-vacuity control for the usage-error tests."""
    parser = _ProbeParserV2(prog="probe", allow_abbrev=False)
    parser.add_argument("--known", required=True)
    namespace = parser.parse_args(["--known", "value"])
    assert namespace.known == "value"


# ---------------------------------------------------------------------------
# Mandatory bidirectional invariant: a genuine post-seal / internal
# programmer defect must NOT be laundered by any G4 mechanism.
# ---------------------------------------------------------------------------


def test_a_genuine_internal_assertion_error_is_not_swallowed_by_response_reading(
    tmp_path: pathlib.Path,
) -> None:
    """If the *model* passed to ``read_offline_response_document_v2`` -- not
    the caller's file content -- is itself broken (a real programmer
    defect: raises on construction regardless of input), that must surface
    raw. This is the exact shape of the mandatory bidirectional invariant:
    over-catching in the conversion machinery would make a bug in *our own*
    code look like an ordinary caller mistake."""
    (tmp_path / "chunk-0000.json").write_text(json.dumps({"anything": 1}), encoding="utf-8")

    class _BrokenModelV2:
        @classmethod
        def model_validate_json(cls, raw_text: str) -> "None":
            raise AssertionError("programmer defect: this model is broken by construction")

    with pytest.raises(AssertionError):
        read_offline_response_document_v2(tmp_path, "chunk-0000", model=_BrokenModelV2)


def test_a_genuine_internal_assertion_error_is_not_swallowed_by_document_validation(
    tmp_path: pathlib.Path,
) -> None:
    path = tmp_path / "doc.json"
    path.write_text(json.dumps({"anything": 1}), encoding="utf-8")

    class _BrokenModelV2:
        @classmethod
        def model_validate_json(cls, raw_text: str) -> "None":
            raise AssertionError("programmer defect")

    with pytest.raises(AssertionError):
        validate_caller_document_v2(path, model=_BrokenModelV2, field_name="thing")


def test_a_genuine_internal_defect_in_argparse_action_callback_is_not_swallowed() -> None:
    """``NoEchoArgumentParserV2.error()`` only intercepts argparse's own
    usage-error path. A real defect inside a caller-supplied ``type=``
    callback -- this codebase's own code, not caller argv -- must still
    surface as whatever it naturally raises, not be reinterpreted as a
    content-free usage refusal."""

    def _broken_type_callback(_value: str) -> int:
        raise AssertionError("programmer defect in a type= callback")

    parser = _ProbeParserV2(prog="probe", allow_abbrev=False)
    parser.add_argument("--number", type=_broken_type_callback, required=True)

    with pytest.raises(AssertionError):
        parser.parse_args(["--number", "5"])


def test_resolve_inner_control_fd_does_not_swallow_a_non_str_programmer_defect() -> None:
    """A genuine type confusion at a call site -- passing something that is
    not ``str | None`` -- is this codebase's own bug, not caller material,
    and must not be relabelled as ``INGRESS_CONTROL_FD_INVALID_REASON_V2``."""

    class _NotAString:
        def __int__(self) -> int:
            raise AssertionError("programmer defect: not a real fd value")

    with pytest.raises(AssertionError):
        resolve_inner_control_fd_v2(_NotAString())  # type: ignore[arg-type]
