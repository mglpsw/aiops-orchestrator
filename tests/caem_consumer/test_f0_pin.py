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
    EXPECTED_CARRIER_SHA,
    ReasonCode,
    PinReasonCode,
    load_caem_f0_pin,
)

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
