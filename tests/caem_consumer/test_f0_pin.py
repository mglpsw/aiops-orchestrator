"""Pin-level validation tests.

These tests are run against the REAL production pin
(`config/caem/caem-3.0-f0.pin.json`) wherever they exercise the "is this
really the pinned CAEM 3.0 F0 identity?" floating-identity gate, per the
red-first requirement to prove real failures rather than a fabricated
red state. Structural/format-only tests (missing field, unknown field,
malformed digest shape) use a minimal in-memory pin document instead, since
those checks are independent of which identity is pinned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.caem_consumer.f0 import (
    EXPECTED_ARTIFACT_DIGEST,
    EXPECTED_ARTIFACT_GIT_PROJECTION_DIGEST,
    EXPECTED_BASE_POLICY_DIGEST,
    EXPECTED_CARRIER_SHA,
    EXPECTED_CONTRACT_REGISTRY_DIGEST,
    EXPECTED_INTERFACE_MANIFEST_DIGEST,
    EXPECTED_MATURITY,
    EXPECTED_POLICY_SOURCE_BYTES_DIGEST,
    EXPECTED_POLICY_SOURCE_SEMANTIC_DIGEST,
    EXPECTED_PUBLISHED,
    EXPECTED_SCHEMA_SET_DIGEST,
    EXPECTED_SOURCE_SHA,
    EXPECTED_TARGET_RELEASE,
    EXPECTED_TESTED_MERGE_SHA,
    EXPECTED_TOOLCHAIN_DIGEST,
    EXPECTED_TRANSPORT_ARCHIVE_DIGEST,
    EXPECTED_VERIFIER_IDENTITY,
    CaemF0Pin,
    ReasonCode,
    PinReasonCode,
    load_caem_f0_interface,
    load_caem_f0_pin,
)
from tests.caem_consumer.conftest import build_fixture_interface, build_fixture_pin

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIN_PATH = REPO_ROOT / "config" / "caem" / "caem-3.0-f0.pin.json"


def _real_pin_doc() -> dict:
    return json.loads(REAL_PIN_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_valid_exact_f0_pin_loads() -> None:
    result = load_caem_f0_pin(REAL_PIN_PATH)
    assert result.ok, result.errors
    assert result.pin.carrier_sha == EXPECTED_CARRIER_SHA
    assert result.pin.published is False
    assert result.pin.maturity == "development_freeze"


@pytest.mark.parametrize(
    "path_to_clear",
    [
        ("consumer",),
        ("authority",),
        ("interface",),
        ("constraints",),
        ("interface", "carrier_sha"),
        ("interface", "interface_manifest_digest"),
        ("authority", "authority_effect"),
    ],
)
def test_pin_missing_each_required_field_is_rejected(tmp_path: Path, path_to_clear: tuple[str, ...]) -> None:
    doc = _real_pin_doc()
    node = doc
    for key in path_to_clear[:-1]:
        node = node[key]
    del node[path_to_clear[-1]]
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == ReasonCode.PIN_INCOMPLETE for e in result.errors)


def test_unknown_pin_field_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["unexpected_top_level_field"] = "x"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.UNKNOWN_FIELD for e in result.errors)


def test_unknown_nested_pin_field_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["unexpected"] = "x"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.UNKNOWN_FIELD for e in result.errors)


def test_short_git_sha_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["carrier_sha"] = doc["interface"]["carrier_sha"][:10]
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == ReasonCode.SOURCE_IDENTITY_MISMATCH for e in result.errors)


def test_uppercase_git_sha_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["carrier_sha"] = doc["interface"]["carrier_sha"].upper()
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == ReasonCode.SOURCE_IDENTITY_MISMATCH for e in result.errors)


def test_malformed_digest_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["interface_manifest_digest"] = "not-a-digest"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_DIGEST_MISMATCH for e in result.errors)


def test_main_or_branch_identity_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    # A 40-hex value that is well-formed but is not the pinned F0 carrier
    # (as if someone had pinned an arbitrary moving branch's current tip).
    doc["interface"]["carrier_sha"] = "f483c555f11c0ddaa084fad699b6556004df45f4"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)


def test_repair_carrier_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["carrier_sha"] = "dee7018e8121ab0d1a74207395adfe8a19b2778a"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)


def test_mixed_interface_manifest_digests_are_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["interface_manifest_digest"] = "sha256:" + ("11" * 32)
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)


def test_published_true_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"]["published"] = True
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok


def test_wrong_authority_effect_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["authority"]["authority_effect"] = "publication"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok
    assert any(e.reason_code == ReasonCode.AUTHORITY_EFFECT_FORBIDDEN for e in result.errors)


def test_reserved_contract_consumption_constraint_must_deny(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["constraints"]["reserved_contract_consumption"] = "allow"
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok


def test_network_required_constraint_must_be_false(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["constraints"]["network_required"] = True
    result = load_caem_f0_pin(_write(tmp_path, doc))
    assert not result.ok


def test_expected_constants_match_the_real_pin_exactly() -> None:
    # Model: the pin is the single DECLARATIVE source; EXPECTED_* is an
    # INDEPENDENT verifier binding (defense in depth), never a second source
    # to keep in sync by hand. This test is the proof obligation that keeps
    # that claim true: every EXPECTED_* constant must equal the value the
    # shipped pin itself declares, field for field.
    doc = _real_pin_doc()
    interface = doc["interface"]
    expected = {
        "target_release": EXPECTED_TARGET_RELEASE,
        "maturity": EXPECTED_MATURITY,
        "published": EXPECTED_PUBLISHED,
        "source_sha": EXPECTED_SOURCE_SHA,
        "tested_merge_sha": EXPECTED_TESTED_MERGE_SHA,
        "carrier_sha": EXPECTED_CARRIER_SHA,
        "base_policy_digest": EXPECTED_BASE_POLICY_DIGEST,
        "policy_source_bytes_digest": EXPECTED_POLICY_SOURCE_BYTES_DIGEST,
        "policy_source_semantic_digest": EXPECTED_POLICY_SOURCE_SEMANTIC_DIGEST,
        "contract_registry_digest": EXPECTED_CONTRACT_REGISTRY_DIGEST,
        "schema_set_digest": EXPECTED_SCHEMA_SET_DIGEST,
        "artifact_digest": EXPECTED_ARTIFACT_DIGEST,
        "artifact_git_projection_digest": EXPECTED_ARTIFACT_GIT_PROJECTION_DIGEST,
        "interface_manifest_digest": EXPECTED_INTERFACE_MANIFEST_DIGEST,
        "transport_archive_digest": EXPECTED_TRANSPORT_ARCHIVE_DIGEST,
        "verifier_identity": EXPECTED_VERIFIER_IDENTITY,
        "toolchain_digest": EXPECTED_TOOLCHAIN_DIGEST,
    }
    for field_name, constant_value in expected.items():
        assert interface[field_name] == constant_value, (
            f"EXPECTED_{field_name.upper()} diverges from the shipped pin's "
            f"interface.{field_name} — the verifier binding has drifted from "
            f"its declarative source"
        )
    # And nothing in the pin's interface block is left unchecked by this test.
    assert set(expected.keys()) == set(interface.keys())


def test_reason_code_mirror_matches_verified_registry() -> None:
    """`ReasonCode` is documented as a verified mirror of the real CAEM 3.0 F0
    registry's own `reason_codes` set, not an independently-defined AIOps
    enum. No CAEM registry file is bundled in this repository (that would be
    copying CAEM content, which this module explicitly avoids) — so this
    test compares the mirror against a verbatim transcription of the real
    registry's `reason_codes` array as pinned by
    `config/caem/caem-3.0-f0.pin.json` (`contract_registry_digest`
    sha256:cca17eab...), independently confirmed live in this session against
    a real `mglpsw/caem` checkout at the pinned carrier
    (`ReasonCode.all() == result.reason_codes` after a real
    `load_caem_f0_interface(..., interface_root=<real caem checkout>)`,
    22 == 22). The live wiring itself — that `result.reason_codes` comes from
    whatever registry is actually loaded, not from this mirror — is what
    `test_load_result_reason_codes_come_from_the_verified_registry_not_the_mirror`
    proves, against a synthetic fixture whose `reason_codes` deliberately
    differ from this mirror."""
    real_registry_reason_codes = frozenset(
        {
            "CANONICAL_JSON_INVALID",
            "DUPLICATE_JSON_KEY",
            "NON_INTEGER_IDENTITY_NUMBER",
            "POLICY_SOURCE_INVALID",
            "POLICY_SOURCE_DIGEST_MISMATCH",
            "CONTRACT_REGISTRY_INVALID",
            "CONTRACT_NOT_FOUND",
            "CONTRACT_VERSION_UNSUPPORTED",
            "CONTRACT_RESERVED",
            "CONTRACT_OWNER_MISMATCH",
            "SCHEMA_DIGEST_MISMATCH",
            "INTERFACE_MANIFEST_INVALID",
            "INTERFACE_DIGEST_MISMATCH",
            "ARTIFACT_DIGEST_MISMATCH",
            "SOURCE_IDENTITY_MISMATCH",
            "MIXED_INTERFACE_IDENTITY",
            "UNSAFE_ARTIFACT_PATH",
            "UNEXPECTED_ARTIFACT",
            "CONTRACT_CHANGE_REQUEST_INVALID",
            "AUTHORITY_EFFECT_FORBIDDEN",
            "PIN_INCOMPLETE",
            "ROLLBACK_TARGET_INVALID",
        }
    )
    assert ReasonCode.all() == real_registry_reason_codes
    assert len(ReasonCode.all()) == 22


def test_manually_constructed_non_f0_pin_is_rejected_by_load_caem_f0_interface(tmp_path: Path) -> None:
    """Independent Codex review of this PR found: `CaemF0Pin` is a public,
    plain dataclass, so a caller can construct one directly — bypassing
    `load_caem_f0_pin`'s JSON-level identity check entirely — and pass it
    straight to `load_caem_f0_interface`. Before the fix, a self-consistent
    but non-F0 pin plus a matching local artifact verified successfully
    (`ok=True`), silently accepting an identity that was never the pinned
    F0 carrier. `load_caem_f0_interface` must re-check identity itself,
    independent of how the pin object was obtained."""

    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    # A manually constructed CaemF0Pin, self-consistent with the fixture
    # artifact, but NOT the pinned CAEM 3.0 F0 identity (placeholder SHAs
    # and digests, never touching load_caem_f0_pin at all).
    non_f0_pin = build_fixture_pin(built)

    result = load_caem_f0_interface(non_f0_pin, interface_root=root)

    assert not result.ok, "a manually constructed non-F0 pin must never verify successfully"
    assert any(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)
    # And this must be the identity gate firing BEFORE artifact verification
    # even runs — not an artifact-level digest mismatch.
    assert not any(e.reason_code == ReasonCode.ARTIFACT_DIGEST_MISMATCH for e in result.errors)


def test_pin_identity_errors_cannot_raise() -> None:
    # _pin_identity_errors is documented as pure attribute comparison that
    # cannot raise, even given a pin with pathological field values.
    from app.caem_consumer.f0 import _pin_identity_errors

    pathological = CaemF0Pin(
        consumer_repository="",
        normative_repository="",
        normative_writer="",
        authority_effect="",
        target_release="",
        maturity="",
        published=True,
        source_sha="",
        tested_merge_sha="",
        carrier_sha="",
        base_policy_digest="",
        policy_source_bytes_digest="",
        policy_source_semantic_digest="",
        contract_registry_digest="",
        schema_set_digest="",
        artifact_digest="",
        artifact_git_projection_digest="",
        interface_manifest_digest="",
        transport_archive_digest="",
        verifier_identity="",
        toolchain_digest="",
    )
    errors, snapshot = _pin_identity_errors(pathological)
    assert len(errors) == 21  # every one of the 21 verified fields mismatches
    assert all(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in errors)
    assert snapshot == {}  # nothing verified, so nothing captured


def test_pin_with_always_equal_fields_cannot_bypass_identity_gate(tmp_path: Path) -> None:
    """Round-2 Codex review found: a directly constructed `CaemF0Pin` could
    supply objects whose `__eq__`/`__ne__` always claim equality with the
    expected value, silently defeating `_pin_identity_errors`'s `!=`
    comparison and letting a self-consistent non-F0 artifact verify
    successfully. Confirmed red against commit 18dafdc (ok=True with this
    exact pin). Fixed by checking the exact primitive type (`str`/`bool`)
    before ever comparing values, so a pathological object's `__eq__` is
    never invoked."""

    class AlwaysEqual:
        def __eq__(self, other: object) -> bool:
            return True

        def __ne__(self, other: object) -> bool:
            return False

    root = tmp_path / "root"
    root.mkdir()
    build_fixture_interface(root)

    always_equal_fields = {
        field_name: AlwaysEqual()
        for field_name in (
            "target_release",
            "maturity",
            "published",
            "source_sha",
            "tested_merge_sha",
            "carrier_sha",
            "base_policy_digest",
            "policy_source_bytes_digest",
            "policy_source_semantic_digest",
            "contract_registry_digest",
            "schema_set_digest",
            "artifact_digest",
            "artifact_git_projection_digest",
            "interface_manifest_digest",
            "transport_archive_digest",
            "verifier_identity",
            "toolchain_digest",
        )
    }
    pin = CaemF0Pin(
        consumer_repository="mglpsw/aiops-orchestrator",
        normative_repository="mglpsw/caem",
        normative_writer="caem_only",
        authority_effect="none",
        **always_equal_fields,
    )

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok, "an all-AlwaysEqual pin must never verify successfully"
    assert len(result.errors) == 17
    assert all(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)


def test_none_pin_is_rejected_without_raising() -> None:
    """Round-2 Codex review found: `load_caem_f0_interface(None, ...)` raised
    an uncaught `AttributeError` instead of returning
    `CaemF0LoadResult(ok=False, ...)`, contradicting the function's own
    documented total/fail-closed guarantee. Confirmed red against commit
    18dafdc. Fixed via `getattr(pin, field, _MISSING)` plus an outer
    try/except in `load_caem_f0_interface`."""

    result = load_caem_f0_interface(None, interface_root="/nonexistent")
    assert not result.ok
    assert len(result.errors) > 0


def test_pin_missing_identity_attribute_is_rejected_without_raising() -> None:
    class Empty:
        pass

    result = load_caem_f0_interface(Empty(), interface_root="/nonexistent")
    assert not result.ok
    assert all(e.reason_code == PinReasonCode.FLOATING_IDENTITY for e in result.errors)
