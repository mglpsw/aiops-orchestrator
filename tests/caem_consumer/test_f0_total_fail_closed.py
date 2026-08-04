"""Totality / fail-closed regression tests.

Independent adversarial review of this slice found that several classes of
malformed input could previously escape as unstructured Python exceptions
(`FileNotFoundError`, `AttributeError`, `zipfile.BadZipFile`) instead of a
`CaemF0LoadResult(ok=False, ...)`, and that `schema_version: true` was
silently accepted because `True == 1` in Python. Each was independently
confirmed as a real failure against the prior commit's code (git rev
e86f2b7) before being fixed here:

    registry absent            -> uncaught FileNotFoundError
    manifest root == []         -> uncaught AttributeError ('list' has no 'get')
    transport zip not a zip     -> uncaught zipfile.BadZipFile
    pin schema_version = True   -> silently ACCEPTED (ok=True) — a real bug,
                                    not just a missing check

These tests prove all four are now green, plus the other structural-type and
read-failure cases the same review asked to be covered.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from app.caem_consumer.f0 import (
    CaemF0Pin,
    ReasonCode,
    PinReasonCode,
    _verify_artifact_against_pin,
    load_caem_f0_pin,
)
from tests.caem_consumer.conftest import (
    build_fixture_interface,
    build_fixture_pin,
    default_fixture_contracts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIN_PATH = REPO_ROOT / "config" / "caem" / "caem-3.0-f0.pin.json"


def _real_pin_doc() -> dict:
    return json.loads(REAL_PIN_PATH.read_text(encoding="utf-8"))


# ── the four scenarios independently confirmed red against the prior commit ──


def test_missing_registry_file_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    (root / "interfaces" / "caem-3.0-f0" / "contract-registry.json").unlink()
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)


def test_manifest_root_as_list_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    (root / "interfaces" / "caem-3.0-f0" / "interface-manifest.json").write_bytes(b"[]")
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_MANIFEST_INVALID for e in result.errors)


def test_registry_root_as_list_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    (root / "interfaces" / "caem-3.0-f0" / "contract-registry.json").write_bytes(b"[]")
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)


def test_invalid_transport_zip_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    zpath = tmp_path / "bad.zip"
    zpath.write_bytes(b"this is not a zip file at all")
    digest = "sha256:" + __import__("hashlib").sha256(zpath.read_bytes()).hexdigest()
    pin = build_fixture_pin(built, transport_archive_digest=digest)

    result = _verify_artifact_against_pin(pin, transport_zip=zpath)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.UNEXPECTED_ARTIFACT for e in result.errors)


def test_pin_schema_version_true_is_rejected_not_silently_accepted(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["schema_version"] = True
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = load_caem_f0_pin(path)
    assert not result.ok, "schema_version=True must never be accepted (True == 1 in Python is not a valid guard)"
    assert any(e.reason_code == ReasonCode.CONTRACT_VERSION_UNSUPPORTED for e in result.errors)


# ── additional structural-type and read-failure coverage requested by review ──


def test_manifest_artifact_field_wrong_type_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"] = "not-an-object"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_MANIFEST_INVALID for e in result.errors)


def test_manifest_schema_set_field_wrong_type_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_set"] = ["not", "an", "object"]
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_MANIFEST_INVALID for e in result.errors)


def test_registry_contracts_field_wrong_type_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    registry_path = built["registry_path"]
    registry = json.loads(registry_path.read_text())
    registry["contracts"] = {"not": "a list"}
    registry_path.write_text(json.dumps(registry, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)


def test_artifact_file_entry_missing_required_keys_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"]["files"][0] = {"path": "x"}  # missing sha256/size
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_MANIFEST_INVALID for e in result.errors)


def test_registry_contract_entry_not_an_object_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    registry_path = built["registry_path"]
    registry = json.loads(registry_path.read_text())
    registry["contracts"].append("not-a-dict")
    registry_path.write_text(json.dumps(registry, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)


def test_missing_manifest_file_is_total_and_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    built["manifest_path"].unlink()
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_MANIFEST_INVALID for e in result.errors)


def test_unreadable_manifest_directory_is_total_and_fail_closed(tmp_path: Path) -> None:
    # Replace the manifest FILE with a directory of the same name: read_bytes()
    # raises IsADirectoryError (an OSError subclass) rather than succeeding.
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest_path.unlink()
    manifest_path.mkdir()
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok


def test_pin_root_not_object_is_total_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "pin.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    result = load_caem_f0_pin(path)  # must not raise
    assert not result.ok
    assert any(e.reason_code == PinReasonCode.MALFORMED_DOCUMENT for e in result.errors)


def test_pin_nested_section_not_object_is_total_and_fail_closed(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    doc["interface"] = "not-an-object"
    path = tmp_path / "pin.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    result = load_caem_f0_pin(path)  # must not raise
    assert not result.ok


def test_pin_non_finite_number_is_rejected(tmp_path: Path) -> None:
    doc = _real_pin_doc()
    raw = json.dumps(doc)
    raw = raw.replace('"schema_version": 1', '"schema_version": NaN')
    path = tmp_path / "pin.json"
    path.write_text(raw, encoding="utf-8")

    result = load_caem_f0_pin(path)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CANONICAL_JSON_INVALID for e in result.errors)


def test_manifest_non_finite_number_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    raw = manifest_path.read_text()
    raw = raw.replace('"target_release":"3.0.0"', '"target_release":"3.0.0","injected":Infinity')
    manifest_path.write_text(raw)
    pin = build_fixture_pin(built)

    result = _verify_artifact_against_pin(pin, interface_root=root)  # must not raise
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CANONICAL_JSON_INVALID for e in result.errors)
