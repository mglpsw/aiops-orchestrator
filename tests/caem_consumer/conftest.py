"""Fixture builders for the CAEM 3.0 F0 consumer loader test suite.

These builders construct small, self-consistent, F0-shaped interface trees
(same relative-path layout and same digest algebra as the real CAEM 3.0 F0
interface, independently reverse-verified against the real artifact in this
session's evidence) with their OWN correctly-computed digests. They are not
copies of the real CAEM production content, and they exercise
`load_caem_f0_interface`'s verification logic in isolation from the
`load_caem_f0_pin`-level "is this really the pinned F0?" identity check
(exercised separately, against the real production pin, in
`test_f0_pin.py`).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.caem_consumer.f0 import CaemF0Pin

PLACEHOLDER_DIGEST = "sha256:" + ("ab" * 32)
PLACEHOLDER_SHA_A = "a" * 40
PLACEHOLDER_SHA_B = "b" * 40
PLACEHOLDER_SHA_C = "c" * 40

_SCHEMA_FILES = (
    ("caem.contract-registry.v1", "schemas/v3.0/f0/contract-registry.schema.json"),
    ("caem.interface-manifest.v1", "schemas/v3.0/f0/interface-manifest.schema.json"),
    ("caem.contract-change-request.v1", "schemas/v3.0/f0/contract-change-request.schema.json"),
)
_NON_SCHEMA_ARTIFACT_PATHS = (
    "caem_contracts/_generated/f0_catalog.py",
    "generated/CAEM_3_0_F0_INTERFACE.md",
    "interfaces/caem-3.0-f0/contract-registry.json",
)


def _canon_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _raw_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def default_fixture_contracts() -> list[dict]:
    return [
        {
            "contract_id": "fixture.canonical-json.v1",
            "schema_version": 1,
            "kind": "canonicalization_profile",
            "delivery_status": "implemented_at_f0",
            "owner": "caem",
            "delivery_issue": "#10",
            "dependencies": [],
            "output_path": "generated/CAEM_3_0_F0_INTERFACE.md",
        },
        {
            "contract_id": "fixture.contract-registry.v1",
            "schema_version": 1,
            "kind": "json_schema",
            "delivery_status": "implemented_at_f0",
            "owner": "caem",
            "delivery_issue": "#10",
            "dependencies": ["fixture.canonical-json.v1"],
            "output_path": "schemas/v3.0/f0/contract-registry.schema.json",
        },
        {
            "contract_id": "fixture.assertion.v1",
            "schema_version": 1,
            "kind": "json_schema",
            "delivery_status": "reserved",
            "owner": "caem",
            "delivery_issue": "#12",
            "dependencies": ["fixture.canonical-json.v1"],
            "output_path": None,
        },
        {
            "contract_id": "fixture.legacy.v1",
            "schema_version": "2.2",
            "kind": "legacy_schema_reference",
            "delivery_status": "legacy_reference",
            "owner": "caem",
            "delivery_issue": "historical",
            "dependencies": [],
            "output_path": None,
            "reference_path": "schemas/legacy-fixture.schema.json",
        },
        {
            "contract_id": "external.readiness.v2",
            "schema_version": 2,
            "kind": "external_schema_reference",
            "delivery_status": "external_reference",
            "owner": "agentreview",
            "owner_repository": "mglpsw/aiops-orchestrator",
            "delivery_issue": "AIOps #119",
            "dependencies": [],
            "output_path": None,
        },
    ]


def cyclic_fixture_contracts() -> list[dict]:
    return [
        {
            "contract_id": "fixture.cycle-a.v1",
            "schema_version": 1,
            "kind": "json_schema",
            "delivery_status": "reserved",
            "owner": "caem",
            "delivery_issue": "#99",
            "dependencies": ["fixture.cycle-b.v1"],
            "output_path": None,
        },
        {
            "contract_id": "fixture.cycle-b.v1",
            "schema_version": 1,
            "kind": "json_schema",
            "delivery_status": "reserved",
            "owner": "caem",
            "delivery_issue": "#99",
            "dependencies": ["fixture.cycle-a.v1"],
            "output_path": None,
        },
    ]


def build_fixture_interface(root: Path, *, contracts: list[dict] | None = None) -> dict[str, Any]:
    """Write a small, self-consistent F0-shaped interface tree under `root`
    and return the computed digests needed to build a matching `CaemF0Pin`.
    """

    contracts = contracts if contracts is not None else default_fixture_contracts()

    (root / "schemas" / "v3.0" / "f0").mkdir(parents=True, exist_ok=True)
    (root / "caem_contracts" / "_generated").mkdir(parents=True, exist_ok=True)
    (root / "generated").mkdir(parents=True, exist_ok=True)
    (root / "interfaces" / "caem-3.0-f0").mkdir(parents=True, exist_ok=True)

    for _, rel in _SCHEMA_FILES:
        (root / rel).write_bytes(b'{"type":"object"}')
    (root / "caem_contracts" / "_generated" / "f0_catalog.py").write_bytes(b'"""fixture catalog."""\n')
    (root / "generated" / "CAEM_3_0_F0_INTERFACE.md").write_bytes(b"# fixture\n")

    registry = {
        "contract_id": "fixture.contract-registry.v1",
        "schema_version": 1,
        "target_release": "3.0.0",
        "maturity": "development_freeze",
        "published": False,
        "contracts": contracts,
    }
    registry_path = root / "interfaces" / "caem-3.0-f0" / "contract-registry.json"
    registry_path.write_bytes(json.dumps(registry, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    artifact_paths = sorted((*_NON_SCHEMA_ARTIFACT_PATHS, *(rel for _, rel in _SCHEMA_FILES)))
    artifact_files = []
    for rel in artifact_paths:
        data = (root / rel).read_bytes()
        artifact_files.append({"path": rel, "size": len(data), "sha256": _raw_digest(data)})
    artifact_projection = [{"path": f["path"], "size": f["size"], "sha256": f["sha256"]} for f in artifact_files]
    artifact_digest = _canon_digest(artifact_projection)

    schema_set_files = [
        {"contract_id": cid, "path": rel, "sha256": _raw_digest((root / rel).read_bytes())}
        for cid, rel in _SCHEMA_FILES
    ]
    schema_set_files_sorted = sorted(schema_set_files, key=lambda f: f["contract_id"])
    schema_projection = [
        {"contract_id": f["contract_id"], "path": f["path"], "sha256": f["sha256"]} for f in schema_set_files_sorted
    ]
    schema_set_digest = _canon_digest(schema_projection)

    contract_registry_digest = _canon_digest(registry)

    manifest = {
        "target_release": "3.0.0",
        "maturity": "development_freeze",
        "published": False,
        "base_policy_digest": PLACEHOLDER_DIGEST,
        "policy_source_bytes_digest": PLACEHOLDER_DIGEST,
        "policy_source_semantic_digest": PLACEHOLDER_DIGEST,
        "contract_registry_digest": contract_registry_digest,
        "schema_set": {"digest": schema_set_digest, "files": schema_set_files_sorted},
        "artifact": {"digest": artifact_digest, "files": artifact_files},
    }
    interface_manifest_digest = _canon_digest(manifest)
    manifest_path = root / "interfaces" / "caem-3.0-f0" / "interface-manifest.json"
    manifest_path.write_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "registry": registry,
        "registry_path": registry_path,
        "artifact_files": artifact_files,
        "contract_registry_digest": contract_registry_digest,
        "schema_set_digest": schema_set_digest,
        "artifact_digest": artifact_digest,
        "interface_manifest_digest": interface_manifest_digest,
    }


def build_fixture_zip(root: Path, built: dict[str, Any], zip_path: Path) -> str:
    """Package `root`'s manifest + declared artifact files into a transport
    zip matching the real `caem-3.0-f0-interface.zip` shape (manifest +
    artifact files, nothing else). Returns the raw-bytes digest of the zip."""

    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(built["manifest_path"], arcname="interfaces/caem-3.0-f0/interface-manifest.json")
        for entry in built["artifact_files"]:
            archive.write(root / entry["path"], arcname=entry["path"])
    return _raw_digest(zip_path.read_bytes())


def build_fixture_pin(built: dict[str, Any], **overrides: Any) -> CaemF0Pin:
    values = {
        "consumer_repository": "mglpsw/aiops-orchestrator",
        "normative_repository": "mglpsw/caem",
        "normative_writer": "caem_only",
        "authority_effect": "none",
        "target_release": "3.0.0",
        "maturity": "development_freeze",
        "published": False,
        "source_sha": PLACEHOLDER_SHA_A,
        "tested_merge_sha": PLACEHOLDER_SHA_B,
        "carrier_sha": PLACEHOLDER_SHA_C,
        "base_policy_digest": PLACEHOLDER_DIGEST,
        "policy_source_bytes_digest": PLACEHOLDER_DIGEST,
        "policy_source_semantic_digest": PLACEHOLDER_DIGEST,
        "contract_registry_digest": built["contract_registry_digest"],
        "schema_set_digest": built["schema_set_digest"],
        "artifact_digest": built["artifact_digest"],
        "artifact_git_projection_digest": PLACEHOLDER_DIGEST,
        "interface_manifest_digest": built["interface_manifest_digest"],
        "transport_archive_digest": PLACEHOLDER_DIGEST,
        "verifier_identity": PLACEHOLDER_DIGEST,
        "toolchain_digest": PLACEHOLDER_DIGEST,
    }
    values.update(overrides)
    return CaemF0Pin(**values)


@pytest.fixture()
def fixture_interface(tmp_path: Path) -> dict[str, Any]:
    root = tmp_path / "interface_root"
    root.mkdir()
    built = build_fixture_interface(root)
    pin = build_fixture_pin(built)
    return {"root": root, "built": built, "pin": pin}
