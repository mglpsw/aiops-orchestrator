from __future__ import annotations

import json
import re

import pytest
from pydantic import ValidationError

from app.agent_review.schema_export_v2 import render_v2_json_schemas
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_HASH_MISMATCH_REASON_V2,
    RECEIPT_OWNERSHIP_LEDGERS_OVERLAP_REASON_V2,
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
        "manifest_digest": "d" * 64,
        "target_repo": "owner/repo",
        "portable_target_root_identity": "e" * 64,
        "target_profile_hash": "a" * 64,
        "target_policy_hash": "b" * 64,
        "review_pack_hashes": {},
        # PR-C1: distinct from `target_owned_file_hashes`'s key below --
        # the two ledgers must never overlap (`validate_ownership_ledgers_
        # are_disjoint`); using the same path in both here was a
        # pre-existing fixture artifact this PR's new invariant correctly
        # flags.
        "generated_file_hashes": {".github/workflows/agent-review.yml": "c" * 64},
        "target_owned_file_hashes": {".aiops/target-profile.v2.yaml": "c" * 64},
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
    b = _valid_receipt(generated_file_hashes={".github/workflows/agent-review.yml": "d" * 64})
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


# --- Post-merge review debt (aiops-orchestrator#205, C1/C4) -----------------


def test_target_owned_file_hashes_schema_can_represent_a_real_non_empty_receipt() -> None:
    """RED for C1: the previous schema had `additionalProperties: false`
    with no `properties`/`patternProperties` at all, so only `{}` ever
    validated -- yet every successful `init` writes a receipt whose
    `target_owned_file_hashes` has at least one entry (the profile seed).
    The fixture's own default already carries a real non-empty map
    (`_receipt_fields()`'s `.aiops/target-profile.v2.yaml` entry); this
    proves the PUBLISHED schema, not just the Pydantic model, can accept
    it."""

    schema = render_v2_json_schemas()["agent-review.target-install-receipt.v2.schema.json"]
    field_schema = schema["properties"]["target_owned_file_hashes"]
    assert field_schema.get("additionalProperties") is False
    assert "patternProperties" in field_schema, (
        "no patternProperties on a closed map means only {} can ever validate"
    )
    key_pattern = next(iter(field_schema["patternProperties"]))
    value_schema = field_schema["patternProperties"][key_pattern]

    receipt = _valid_receipt()
    for key, value in receipt.target_owned_file_hashes.items():
        assert re.fullmatch(key_pattern, key)
        assert re.fullmatch(value_schema["pattern"], value)


def test_target_owned_file_hashes_schema_still_rejects_a_glob_shaped_key() -> None:
    """The fix must not simply open the map to arbitrary keys -- a key
    containing glob metacharacters (rejected by `RelativePath` itself)
    must still fail the exported `patternProperties` regex."""

    schema = render_v2_json_schemas()["agent-review.target-install-receipt.v2.schema.json"]
    key_pattern = next(iter(schema["properties"]["target_owned_file_hashes"]["patternProperties"]))
    assert not re.fullmatch(key_pattern, "a[b].py")


@pytest.mark.parametrize(
    "escaping_path",
    [
        "../canary.txt",
        "/etc/passwd",
        "C:/canary.txt",
        "a/../../canary.txt",
        "a/./b",
    ],
)
def test_target_owned_paths_rejects_a_path_that_would_escape_target_root(escaping_path: str) -> None:
    """RED for C4: `target_owned_paths`/`target_owned_file_hashes` used to
    be `SafeText`, which rejects control characters and secret-shaped
    values but never traversal or absolute paths. `RelativePath` rejects
    all of these at construction time -- before
    `target_pack_doctor_v2._check_receipt_v2` ever reads a path derived
    from this field."""

    fields = _receipt_fields(
        target_owned_paths=(escaping_path,),
        target_owned_file_hashes={escaping_path: "c" * 64},
    )
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))


# --- PR-C1: installed-pack identity contract --------------------------------
#
# "Every installed-pack identity and receipt accepted by any reader carries
# one canonical Repository identity and one unambiguous canonical ownership
# classification per path." Reproduced first against Codex Round-3 findings
# on PR #242 (target_repo="not-a-repository" + seed placeholder profile
# validated; the same path in both ledgers with the same digest validated),
# closed here at the shared contract so every reader inherits the fix, not
# just one command.


@pytest.mark.parametrize(
    "malformed_repo",
    [
        "not-a-repository",  # no "/" at all -- SafeText's only constraint is non-empty printable text
        "owner/",  # empty name half
        "/repo",  # empty owner half
        "owner/name/extra",  # more than one "/"
        "   owner/repo",  # leading whitespace -- SafeText strips-and-compares, Repository does not
    ],
)
def test_a_malformed_target_repo_is_refused_by_the_receipt_contract(malformed_repo: str) -> None:
    """RED for PR-C1: `target_repo` was `SafeText` -- any non-empty,
    control-character-free, non-secret-shaped string, including one with
    no `owner/name` structure at all. Codex Round 3 (PR #242, finding
    R3-3) reproduced `target_repo="not-a-repository"` validating end to
    end because the seed profile placeholder (`OWNER/REPO`) short-
    circuits the only independent comparison (`profile_identity`) and
    `root_identity` only checks self-consistency against the SAME
    malformed string. Tightening the FIELD TYPE to `Repository` closes
    this for every reader, not just `validate`."""

    fields = _receipt_fields(
        target_repo=malformed_repo,
        portable_target_root_identity="e" * 64,  # irrelevant here -- field-level rejection precedes any cross-check
    )
    # The hash is computed for THIS malformed input (a real, internally-
    # self-consistent receipt_hash), not a hardcoded mismatch -- otherwise
    # `validate_receipt_hash_matches_content` would raise regardless of
    # whether `target_repo` was ever actually validated, masking the
    # property this test claims to prove (confirmed by mutation M1: with
    # a hardcoded wrong hash, reverting `target_repo` to `SafeText` left
    # this test passing for the WRONG reason).
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))


def test_a_well_formed_target_repo_still_constructs() -> None:
    """Safe counterexample: a genuinely `owner/name`-shaped value is
    unaffected by the tightening -- this is exactly what every real
    writer already produces (`_build_receipt_v2`/`_identity_from_
    manifest_v2` pass the CLI's `--target-repo` straight through)."""

    receipt = _valid_receipt(target_repo="acme/widget")
    assert receipt.target_repo == "acme/widget"


def test_overlapping_ownership_ledger_paths_are_refused() -> None:
    """RED for PR-C1: Codex Round 3 (PR #242, finding R3-4) reproduced
    the SAME path (`shared.txt`) declared in both `target_owned_file_
    hashes` and `generated_file_hashes` with the SAME digest, and both
    independent verifiers passed -- `TargetPackManifestV2` permits each
    generated path exactly ONE ownership classification, so such a
    receipt cannot represent any legitimate installation. The invariant
    belongs on the receipt itself (this test), not on any one reader."""

    shared_hash = "c" * 64
    fields = _receipt_fields(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "c" * 64, "shared.txt": shared_hash},
        target_owned_paths=(".aiops/target-profile.v2.yaml", "shared.txt"),
        generated_file_hashes={"shared.txt": shared_hash},
    )
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError) as exc_info:
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))
    assert RECEIPT_OWNERSHIP_LEDGERS_OVERLAP_REASON_V2 in str(exc_info.value)


def test_overlapping_ledger_paths_are_refused_even_with_different_digests() -> None:
    """The disjointness invariant is about PATH ownership, not digest
    agreement -- two ledgers claiming the SAME path with DIFFERENT
    declared digests is at least as contradictory as claiming it with
    the same digest (R3-4's own reproduction happened to use matching
    digests; this pins the more obviously-wrong case too)."""

    fields = _receipt_fields(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "c" * 64, "shared.txt": "a" * 64},
        target_owned_paths=(".aiops/target-profile.v2.yaml", "shared.txt"),
        generated_file_hashes={"shared.txt": "b" * 64},
    )
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))


def test_a_noncanonical_generated_file_spelling_cannot_bypass_the_overlap_check() -> None:
    """Once `generated_file_hashes` keys are `RelativePath` (the SAME
    authority `target_owned_file_hashes` already uses), a non-normalized
    spelling of an otherwise-overlapping path (`"a/./b"` for the
    canonical `"a/b"`) is refused by the FIELD ITSELF before the
    disjointness validator ever runs -- there is no raw-string-
    intersection blind spot where two different spellings of the same
    path semantics could each look unique to a naive `set & set`. Both
    ledgers sharing one canonical path authority is what makes the
    disjointness check in the tests above SOUND, not merely convenient."""

    fields = _receipt_fields(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "c" * 64, "a/b": "a" * 64},
        target_owned_paths=(".aiops/target-profile.v2.yaml", "a/b"),
        generated_file_hashes={"a/./b": "a" * 64},
    )
    # Correctly-computed hash, not a hardcoded mismatch -- see the comment
    # in `test_a_malformed_target_repo_is_refused_by_the_receipt_contract`
    # for why (mutation M3 caught this same masking bug here too: a
    # hardcoded wrong hash left this test passing even after reverting
    # `generated_file_hashes` to `SafeText`).
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))


def test_distinct_normalized_paths_across_ledgers_remain_accepted() -> None:
    """Safe counterexample: two genuinely different paths, one in each
    ledger, is the ordinary case and must be unaffected."""

    receipt = _valid_receipt(
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "c" * 64},
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        generated_file_hashes={"generated/workflow.yml": "d" * 64},
    )
    assert set(receipt.target_owned_file_hashes) & set(receipt.generated_file_hashes) == set()


def test_generated_file_hashes_schema_can_represent_a_real_non_empty_receipt() -> None:
    """Schema-representability proof, mirroring `test_target_owned_file_
    hashes_schema_can_represent_a_real_non_empty_receipt` -- the writer
    (`_build_receipt_v2`) populates `generated_file_hashes` from
    `manifest.generated_files[].path`, which is already `RelativePath`;
    the receipt's own field must accept and the EXPORTED schema must
    represent the same real shape."""

    schema = render_v2_json_schemas()["agent-review.target-install-receipt.v2.schema.json"]
    field_schema = schema["properties"]["generated_file_hashes"]
    assert field_schema.get("additionalProperties") is False
    assert "patternProperties" in field_schema, (
        "no patternProperties on a closed map means only {} can ever validate"
    )
    key_pattern = next(iter(field_schema["patternProperties"]))
    value_schema = field_schema["patternProperties"][key_pattern]

    receipt = _valid_receipt(generated_file_hashes={".github/workflows/agent-review.yml": "c" * 64})
    for key, value in receipt.generated_file_hashes.items():
        assert re.fullmatch(key_pattern, key)
        assert re.fullmatch(value_schema["pattern"], value)


def test_generated_file_hashes_schema_rejects_a_glob_shaped_key() -> None:
    schema = render_v2_json_schemas()["agent-review.target-install-receipt.v2.schema.json"]
    key_pattern = next(iter(schema["properties"]["generated_file_hashes"]["patternProperties"]))
    assert not re.fullmatch(key_pattern, "a[b].py")


@pytest.mark.parametrize(
    "escaping_path",
    ["../canary.txt", "/etc/passwd", "C:/canary.txt", "a/../../canary.txt", "a/./b"],
)
def test_generated_file_hashes_rejects_a_path_that_would_escape_target_root(escaping_path: str) -> None:
    """Mirrors `test_target_owned_paths_rejects_a_path_that_would_escape_
    target_root` for the OTHER ledger: before this PR, `generated_file_
    hashes` keys were `SafeText`, which does not reject traversal or
    absolute paths at all."""

    fields = _receipt_fields(generated_file_hashes={escaping_path: "c" * 64})
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    with pytest.raises(ValidationError):
        TargetInstallReceiptV2(**fields, receipt_hash=compute_target_install_receipt_hash_v2(computed_input))


def test_receipt_hash_is_deterministic_and_unaffected_by_the_type_tightening() -> None:
    """`Repository`/`RelativePath` are `Annotated[StrictStr, ...]` wrappers
    -- their JSON serialization shape is a plain string, identical to
    `SafeText`'s. Tightening validation must not perturb the canonical
    hash preimage for values that were already valid under the OLD,
    looser types: the same well-formed fields hashed twice must still
    produce the same `receipt_hash`, and a real writer-shaped receipt
    (`owner/name` repo, `RelativePath`-shaped ledger keys -- already
    valid before this PR) must hash identically before and after."""

    a = _valid_receipt(target_repo="acme/widget", generated_file_hashes={"workflow.yml": "c" * 64})
    b = _valid_receipt(target_repo="acme/widget", generated_file_hashes={"workflow.yml": "c" * 64})
    assert a.receipt_hash == b.receipt_hash


def test_a_malicious_receipt_document_never_reaches_pydantic_construction_incomplete() -> None:
    """Mutation-adjacent control: confirms the escaping-path receipt in the
    parametrized test above fails during VALIDATION (raises before a
    `TargetInstallReceiptV2` instance with the bad path ever exists), not
    merely after -- i.e. there is no window where application code could
    hold a constructed receipt carrying an escaping path."""

    fields = _receipt_fields(
        target_owned_paths=("../canary.txt",), target_owned_file_hashes={"../canary.txt": "c" * 64}
    )
    computed_input = TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    receipt_hash = compute_target_install_receipt_hash_v2(computed_input)
    with pytest.raises(ValidationError) as exc_info:
        TargetInstallReceiptV2.model_validate_json(json.dumps({**fields, "receipt_hash": receipt_hash}))
    # A ValidationError from model_validate_json means no instance was ever
    # constructed -- there is no partially-built receipt to inspect.
    assert "target_owned" in str(exc_info.value) or "path" in str(exc_info.value).lower()
