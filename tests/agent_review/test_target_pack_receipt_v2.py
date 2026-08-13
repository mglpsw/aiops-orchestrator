from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_HASH_MISMATCH_REASON_V2,
    RECEIPT_SECRET_NAME_LOOKS_LIKE_VALUE_REASON_V2,
    ReceiptIdentityRefV2,
    TargetInstallReceiptV2,
    compute_target_install_receipt_hash_v2,
)


def _receipt_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "schema_id": "agent-review.target-install-receipt.v2",
        "schema_version": 2,
        "pack_version": "0.1.0",
        "toolrepo_sha": "1" * 40,
        "target_repo": "owner/repo",
        "target_profile_hash": "a" * 64,
        "target_policy_hash": "b" * 64,
        "review_pack_hashes": {},
        "generated_file_hashes": {".aiops/target-profile.v2.yaml": "c" * 64},
        "target_owned_paths": (".aiops/target-profile.v2.yaml",),
        "required_capabilities": ("router_transport",),
        "expected_runner_labels": (),
        "required_secret_names": ("AGENT_ROUTER_API_KEY",),
        "rollout_mode": "off",
        "compatibility": "compatible",
        "previous_install_identity": None,
        "generated_at": None,
    }
    fields.update(overrides)
    return fields


def _valid_receipt(**overrides: object) -> TargetInstallReceiptV2:
    fields = _receipt_fields(**overrides)
    computed = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=computed)


def test_a_correctly_hashed_receipt_constructs() -> None:
    receipt = _valid_receipt()
    assert receipt.rollout_mode == "off"


def test_a_tampered_receipt_hash_is_refused() -> None:
    fields = _receipt_fields()
    with pytest.raises(ValidationError) as exc_info:
        TargetInstallReceiptV2(**fields, receipt_hash="f" * 64)
    assert RECEIPT_HASH_MISMATCH_REASON_V2 in str(exc_info.value)


def test_receipt_hash_excludes_generated_at() -> None:
    """Two receipts differing ONLY in `generated_at` must hash identically
    -- the property `target_pack_plan_v2`'s idempotence tests depend on."""

    a = _valid_receipt(generated_at="2026-01-01T00:00:00Z")
    b_fields = _receipt_fields(generated_at="2026-06-01T00:00:00Z")
    b_hash = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**b_fields, receipt_hash="0" * 64)
    )
    assert a.receipt_hash == b_hash


def test_receipt_hash_changes_when_generated_file_hashes_change() -> None:
    a = _valid_receipt()
    b = _valid_receipt(generated_file_hashes={".aiops/target-profile.v2.yaml": "d" * 64})
    assert a.receipt_hash != b.receipt_hash


@pytest.mark.parametrize(
    "bad_name",
    [
        "ghp_abcdEFGH1234567890abcdEFGH1234567890",  # token-shaped
        "aws_secret_access_key_value_looks_like_this_1234567890",  # too long/lowercase
        "sk-proj-1234567890abcdefghijklmnopqrstuvwxyz",  # provider-key-shaped
    ],
)
def test_a_secret_shaped_value_is_refused_by_defense_in_depth(bad_name: str) -> None:
    """These are caught by `contracts_v2.py`'s own `SafeIdentifier`-level
    secret-shape heuristic (`_reject_sensitive_value`) before this module's
    own `_SECRET_NAME_RE` check ever runs -- two independent layers, either
    one sufficient. Only asserts refusal, not which layer -- see the
    dedicated test below for a name this module's OWN validator must catch
    on its own, because it is not secret-shaped, merely not uppercase."""

    fields = _receipt_fields(required_secret_names=(bad_name,))
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(
            **fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input)
        )


def test_a_merely_lowercase_name_is_refused_by_this_modules_own_validator() -> None:
    """`"lowercase_name"` is not secret-SHAPED (no `_reject_sensitive_value`
    trigger) -- it only fails this module's own `_SECRET_NAME_RE`
    uppercase-identifier convention. Proves `validate_no_secret_values`
    itself is load-bearing, not merely redundant with the field-type-level
    check."""

    bad_name = "lowercase_name"
    fields = _receipt_fields(required_secret_names=(bad_name,))
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError) as exc_info:
        TargetInstallReceiptV2(
            **fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input)
        )
    assert RECEIPT_SECRET_NAME_LOOKS_LIKE_VALUE_REASON_V2 in str(exc_info.value)


def test_an_empty_secret_name_is_refused_by_the_field_type_itself() -> None:
    fields = _receipt_fields(required_secret_names=("",))
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash="0" * 64)


def test_a_real_secret_name_is_accepted() -> None:
    receipt = _valid_receipt(required_secret_names=("AGENT_ROUTER_API_KEY", "GH_APP_PRIVATE_KEY"))
    assert receipt.required_secret_names == ("AGENT_ROUTER_API_KEY", "GH_APP_PRIVATE_KEY")


def test_previous_install_identity_round_trips() -> None:
    ref = ReceiptIdentityRefV2(receipt_hash="e" * 64, pack_version="0.1.0", toolrepo_sha="2" * 40)
    receipt = _valid_receipt(previous_install_identity=ref)
    assert receipt.previous_install_identity == ref
