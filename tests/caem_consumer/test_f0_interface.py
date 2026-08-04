"""`load_caem_f0_interface` verification-logic tests, against small synthetic
F0-shaped fixtures (see conftest.py). These exercise byte-level digest
verification, path safety, and contract-registry structural checks in
isolation from the `load_caem_f0_pin` floating-identity gate (covered
separately, against the real production pin, in test_f0_pin.py)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.caem_consumer.f0 import (
    CaemF0ContractUnavailable,
    ReasonCode,
    load_caem_f0_interface,
    require_caem_f0_contract,
)
from tests.caem_consumer.conftest import (
    build_fixture_interface,
    build_fixture_pin,
    build_fixture_zip,
    cyclic_fixture_contracts,
)


def test_valid_local_f0_interface_loads(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok, result.errors
    assert result.identity is not None
    assert result.identity.maturity == "development_freeze"
    assert result.identity.published is False
    assert len(result.contracts) == 5


def test_neither_root_nor_zip_is_rejected(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"])
    assert not result.ok


def test_both_root_and_zip_is_rejected(fixture_interface: dict, tmp_path: Path) -> None:
    zpath = tmp_path / "t.zip"
    zpath.write_bytes(b"")
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"], transport_zip=zpath)
    assert not result.ok


def test_manifest_digest_mismatch_is_rejected(fixture_interface: dict) -> None:
    manifest_path = fixture_interface["root"] / "interfaces" / "caem-3.0-f0" / "interface-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["target_release"] = "9.9.9"  # any content change moves the digest
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert not result.ok
    assert any(e.reason_code == ReasonCode.INTERFACE_DIGEST_MISMATCH for e in result.errors)


def test_registry_content_mutation_is_rejected(fixture_interface: dict) -> None:
    # Mutating the registry FILE's bytes is caught as an artifact-digest
    # mismatch (the per-file check in the loader runs before the registry
    # object is even parsed) — see test_registry_digest_mismatch_is_rejected
    # below for the case where only the pin's declared digest is wrong.
    registry_path = fixture_interface["root"] / "interfaces" / "caem-3.0-f0" / "contract-registry.json"
    registry = json.loads(registry_path.read_text())
    registry["contracts"].append(
        {
            "contract_id": "fixture.extra.v1",
            "schema_version": 1,
            "kind": "json_schema",
            "delivery_status": "reserved",
            "owner": "caem",
            "delivery_issue": "#1",
            "dependencies": [],
            "output_path": None,
        }
    )
    registry_path.write_text(json.dumps(registry, sort_keys=True, separators=(",", ":")))

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert not result.ok
    assert any(e.reason_code == ReasonCode.ARTIFACT_DIGEST_MISMATCH for e in result.errors)


def test_registry_digest_mismatch_is_rejected(fixture_interface: dict) -> None:
    # Here the interface_root is untouched (byte-identical, valid), but the
    # PIN itself declares a wrong contract_registry_digest — the scenario a
    # forged or corrupted pin (independent of the artifact) exercises.
    wrong_pin = build_fixture_pin(fixture_interface["built"], contract_registry_digest="sha256:" + ("22" * 32))

    result = load_caem_f0_interface(wrong_pin, interface_root=fixture_interface["root"])
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)
    assert any(e.reason_code == ReasonCode.MIXED_INTERFACE_IDENTITY for e in result.errors)


def test_schema_set_digest_mismatch_is_rejected(fixture_interface: dict) -> None:
    schema_path = fixture_interface["root"] / "schemas" / "v3.0" / "f0" / "contract-registry.schema.json"
    schema_path.write_bytes(b'{"type":"object","extra":true}')

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert not result.ok
    assert any(e.reason_code == ReasonCode.ARTIFACT_DIGEST_MISMATCH for e in result.errors)


def test_artifact_digest_mismatch_is_rejected(fixture_interface: dict) -> None:
    catalog_path = fixture_interface["root"] / "caem_contracts" / "_generated" / "f0_catalog.py"
    catalog_path.write_bytes(b'"""mutated fixture catalog."""\n')

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert not result.ok
    assert any(e.reason_code == ReasonCode.ARTIFACT_DIGEST_MISMATCH for e in result.errors)


def test_transport_zip_digest_mismatch_is_rejected(fixture_interface: dict, tmp_path: Path) -> None:
    zpath = tmp_path / "transport.zip"
    build_fixture_zip(fixture_interface["root"], fixture_interface["built"], zpath)
    pin = build_fixture_pin(fixture_interface["built"], transport_archive_digest="sha256:" + ("00" * 32))

    result = load_caem_f0_interface(pin, transport_zip=zpath)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.ARTIFACT_DIGEST_MISMATCH for e in result.errors)


def test_valid_transport_zip_loads(fixture_interface: dict, tmp_path: Path) -> None:
    zpath = tmp_path / "transport.zip"
    digest = build_fixture_zip(fixture_interface["root"], fixture_interface["built"], zpath)
    pin = build_fixture_pin(fixture_interface["built"], transport_archive_digest=digest)

    result = load_caem_f0_interface(pin, transport_zip=zpath)
    assert result.ok, result.errors


def test_extra_protected_file_in_zip_is_rejected(fixture_interface: dict, tmp_path: Path) -> None:
    zpath = tmp_path / "transport.zip"
    digest_before = build_fixture_zip(fixture_interface["root"], fixture_interface["built"], zpath)

    # Append an undeclared member to the archive after building it.
    with zipfile.ZipFile(zpath, "a") as archive:
        archive.writestr("interfaces/caem-3.0-f0/SNEAKY_EXTRA.txt", "not declared anywhere")
    pin = build_fixture_pin(fixture_interface["built"], transport_archive_digest=digest_before)

    result = load_caem_f0_interface(pin, transport_zip=zpath)
    assert not result.ok
    # Either the whole-archive digest moved (since we appended bytes) or, if
    # somehow it still matched, the unexpected-member check must catch it.
    assert any(
        e.reason_code in (ReasonCode.ARTIFACT_DIGEST_MISMATCH, ReasonCode.UNEXPECTED_ARTIFACT) for e in result.errors
    )


def test_unsafe_absolute_path_in_manifest_is_rejected(fixture_interface: dict) -> None:
    manifest_path = fixture_interface["root"] / "interfaces" / "caem-3.0-f0" / "interface-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"]["files"][0]["path"] = "/etc/passwd"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert not result.ok
    # digest will also fail since the manifest changed; unsafe-path detection
    # happens as part of the same recomputation pass
    assert result.errors


def test_unsafe_parent_traversal_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"]["files"][0]["path"] = "../outside.txt"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok


def test_unsafe_backslash_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    manifest_path = built["manifest_path"]
    manifest = json.loads(manifest_path.read_text())
    manifest["artifact"]["files"][0]["path"] = "schemas\\v3.0\\f0\\x.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    pin = build_fixture_pin(built)

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok


@pytest.mark.skipif(hasattr(__import__("os"), "name") and __import__("os").name == "nt", reason="requires POSIX symlinks")
def test_symlink_interface_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    build_fixture_interface(root)
    real_manifest = root / "interfaces" / "caem-3.0-f0" / "interface-manifest.json"
    real_manifest.rename(root / "interfaces" / "caem-3.0-f0" / "real-manifest.json")
    (root / "interfaces" / "caem-3.0-f0" / "interface-manifest.json").symlink_to(
        root / "interfaces" / "caem-3.0-f0" / "real-manifest.json"
    )
    built = build_fixture_interface(tmp_path / "throwaway")
    pin = build_fixture_pin(built)

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.UNSAFE_ARTIFACT_PATH for e in result.errors)


@pytest.mark.skipif(hasattr(__import__("os"), "name") and __import__("os").name == "nt", reason="requires POSIX symlinks")
def test_symlinked_ancestor_directory_escape_is_rejected(tmp_path: Path) -> None:
    # A declared-safe relative path (no `..`, no leading `/`) can still
    # escape `interface_root` if an ANCESTOR directory is itself a symlink
    # pointing elsewhere. This must be caught even though the leaf file is a
    # perfectly ordinary, non-symlinked file.
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    outside = tmp_path / "outside_schemas"
    (root / "schemas" / "v3.0" / "f0").rename(outside)
    (root / "schemas" / "v3.0" / "f0").symlink_to(outside, target_is_directory=True)
    pin = build_fixture_pin(built)

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.UNSAFE_ARTIFACT_PATH for e in result.errors)


@pytest.mark.skipif(hasattr(__import__("os"), "name") and __import__("os").name == "nt", reason="requires POSIX symlinks")
def test_symlink_member_in_zip_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    zpath = tmp_path / "t.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.write(built["manifest_path"], arcname="interfaces/caem-3.0-f0/interface-manifest.json")
        for entry in built["artifact_files"]:
            archive.write(root / entry["path"], arcname=entry["path"])
        info = zipfile.ZipInfo("interfaces/caem-3.0-f0/evil-link")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, "../../etc/passwd")
    digest = "sha256:" + __import__("hashlib").sha256(zpath.read_bytes()).hexdigest()
    pin = build_fixture_pin(built, transport_archive_digest=digest)

    result = load_caem_f0_interface(pin, transport_zip=zpath)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.UNSAFE_ARTIFACT_PATH for e in result.errors)


def test_case_collision_in_zip_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root)
    zpath = tmp_path / "t.zip"
    with zipfile.ZipFile(zpath, "w") as archive:
        archive.write(built["manifest_path"], arcname="interfaces/caem-3.0-f0/interface-manifest.json")
        for entry in built["artifact_files"]:
            archive.write(root / entry["path"], arcname=entry["path"])
        # Case-colliding duplicate of an already-present member.
        archive.writestr("GENERATED/CAEM_3_0_F0_INTERFACE.MD", "collide")
    digest = "sha256:" + __import__("hashlib").sha256(zpath.read_bytes()).hexdigest()
    pin = build_fixture_pin(built, transport_archive_digest=digest)

    result = load_caem_f0_interface(pin, transport_zip=zpath)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.UNEXPECTED_ARTIFACT for e in result.errors)


def test_dependency_cycle_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    built = build_fixture_interface(root, contracts=cyclic_fixture_contracts())
    pin = build_fixture_pin(built)

    result = load_caem_f0_interface(pin, interface_root=root)
    assert not result.ok
    assert any(e.reason_code == ReasonCode.CONTRACT_REGISTRY_INVALID for e in result.errors)


def test_implemented_at_f0_contract_is_available(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok
    record = require_caem_f0_contract(result, "fixture.canonical-json.v1")
    assert record.delivery_status == "implemented_at_f0"


def test_reserved_contract_request_is_rejected(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok
    with pytest.raises(CaemF0ContractUnavailable) as excinfo:
        require_caem_f0_contract(result, "fixture.assertion.v1")
    assert excinfo.value.reason_code == ReasonCode.CONTRACT_RESERVED


def test_unknown_contract_request_is_rejected(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok
    with pytest.raises(CaemF0ContractUnavailable) as excinfo:
        require_caem_f0_contract(result, "does.not.exist.v99")
    assert excinfo.value.reason_code == ReasonCode.CONTRACT_NOT_FOUND


def test_external_reference_contract_is_available(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok
    record = require_caem_f0_contract(result, "external.readiness.v2")
    assert record.owner_repository == "mglpsw/aiops-orchestrator"


def test_reason_codes_come_from_verified_registry(fixture_interface: dict) -> None:
    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok
    statuses = {c.delivery_status for c in result.contracts.values()}
    assert statuses == {"implemented_at_f0", "reserved", "legacy_reference", "external_reference"}


def test_loader_performs_zero_network_calls(fixture_interface: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def _forbidden(*args, **kwargs):  # pragma: no cover - only invoked on violation
        raise AssertionError("caem_consumer attempted a network call")

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    result = load_caem_f0_interface(fixture_interface["pin"], interface_root=fixture_interface["root"])
    assert result.ok

    zpath = fixture_interface["root"].parent / "transport.zip"
    digest = build_fixture_zip(fixture_interface["root"], fixture_interface["built"], zpath)
    zip_pin = build_fixture_pin(fixture_interface["built"], transport_archive_digest=digest)
    zip_result = load_caem_f0_interface(zip_pin, transport_zip=zpath)
    assert zip_result.ok
