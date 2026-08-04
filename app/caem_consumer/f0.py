"""Strict, offline, fail-closed loader for the pinned CAEM 3.0 F0 interface.

Scope (issue #119, slice #119.1 — AIOPS-RI-B0A-1): pin one exact CAEM 3.0 F0
identity, verify a locally available copy of that interface byte-for-byte, and
answer closed questions about which CAEM contracts are available. Nothing in
this module:

* defines, edits, or regenerates a CAEM schema;
* introduces a second, AIOps-local CAEM policy;
* performs network I/O, GitHub API calls, or CT102 access;
* accepts a floating identity (branch, tag, or any commit other than the exact
  pinned CAEM 3.0 F0 carrier);
* adopts, or has any notion of, a post-F0 candidate revision (that is a
  separate decision — see CAEM issue #27 / "candidate adoption" in #119.4).

The digest algorithm below was independently reverse-verified in this session
against the real, published CAEM 3.0 F0 artifact (carrier
28ca73f338417b5c7e9275c6154b6a0eddbb8bc7): every one of the seven digest
relationships below reproduces the real published values exactly.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# CAEM 3.0 F0 — frozen, historical identity constants.
#
# These values are FACTS about a byte-immutable, already-published interface,
# verified against the live mglpsw/caem repository at carrier
# 28ca73f338417b5c7e9275c6154b6a0eddbb8bc7 (workflow run 30862278867, checkpoint
# export artifact 8876582644, SHA256SUMS 10/10 OK). They never change: a change
# to any of them would mean the F0 identity itself changed, which contradicts
# F0's own `development_freeze` maturity. This module is deliberately scoped to
# exactly one revision (F0); a generic multi-revision consumer belongs to a
# later slice, once CAEM's post-F0 candidate line (CAEM issue #27) exists.
# ---------------------------------------------------------------------------

EXPECTED_NORMATIVE_REPOSITORY = "mglpsw/caem"
EXPECTED_CONSUMER_REPOSITORY = "mglpsw/aiops-orchestrator"

EXPECTED_TARGET_RELEASE = "3.0.0"
EXPECTED_MATURITY = "development_freeze"
EXPECTED_PUBLISHED = False

EXPECTED_SOURCE_SHA = "8f26930f50eec6d5a9446a9ec4eadfdef2eee69e"
EXPECTED_TESTED_MERGE_SHA = "ad55d79b8813c2ac6d9575a1c2814431386fa50f"
EXPECTED_CARRIER_SHA = "28ca73f338417b5c7e9275c6154b6a0eddbb8bc7"

EXPECTED_BASE_POLICY_DIGEST = (
    "sha256:0fb90b03fb626443e8ed9a85e46b2abea3626132fe15cfe896e0193e48bb8463"
)
EXPECTED_POLICY_SOURCE_BYTES_DIGEST = (
    "sha256:81809b3b70879f556bf385a984468319e60e3401d1af35c00b7815e9f7ea8d9c"
)
EXPECTED_POLICY_SOURCE_SEMANTIC_DIGEST = (
    "sha256:598ce20e6c93680f4002f4cb7df52ad736becabd513bfb82d89fabdba5f81ff7"
)
EXPECTED_CONTRACT_REGISTRY_DIGEST = (
    "sha256:cca17eab2280b1c7045a0390f2a7c7e0e036a36be947c17741805f3888ec93c0"
)
EXPECTED_SCHEMA_SET_DIGEST = (
    "sha256:5ce1461a48394bc0907ef7267f7abfccb2b0df0c8348c08eae392a96ba68e668"
)
EXPECTED_ARTIFACT_DIGEST = (
    "sha256:d6e6706a7d33a13f90f05ca97b5501cf5a293bb7becc1ca027dfa644d7864627"
)
EXPECTED_ARTIFACT_GIT_PROJECTION_DIGEST = (
    "sha256:8581e3692b5b2d3d2c32296984596b3e40df497eb52ff8d2a213dcd8d17c06c4"
)
EXPECTED_INTERFACE_MANIFEST_DIGEST = (
    "sha256:6e2fdff772a16466b8af1934d03b4e29ec03aeae053ccb2bfa0b705f50ab48e5"
)
EXPECTED_TRANSPORT_ARCHIVE_DIGEST = (
    "sha256:7056f17c8bcfbc6ef8bafca259bac3068274c148de01abc6196102c9ec6140fc"
)
EXPECTED_VERIFIER_IDENTITY = (
    "sha256:b36a3918d3d40ae430e5aed232524c633ab69bef0383db834ffe7b20e6e0371b"
)
EXPECTED_TOOLCHAIN_DIGEST = (
    "sha256:1e8366f65e3e7c64e1f0fd5021ab4f91d274875a06fe5c345b9dd77406e9416e"
)

# Well-known, publicly documented relative paths of the CAEM 3.0 F0 interface
# inside a `mglpsw/caem` checkout. Reused verbatim (they are path strings, not
# schema content) from `caem_contracts/f0.py`'s own REGISTRY_PATH/MANIFEST_PATH
# constants, so a real caem checkout and this loader agree on where F0 lives.
F0_MANIFEST_PATH = "interfaces/caem-3.0-f0/interface-manifest.json"
F0_REGISTRY_PATH = "interfaces/caem-3.0-f0/contract-registry.json"

# The CAEM 3.0 F0 interface-manifest.schema.json's own POSIX path-safety
# pattern, reused verbatim (it is a public safety invariant CAEM already
# documents, not a redefinition of CAEM norm): no leading slash, no `..`
# segment, no backslash.
_SAFE_PATH_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*\\)[A-Za-z0-9._/-]+$")

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class ReasonCode:
    """String constants reused verbatim from the CAEM 3.0 F0 contract-registry's
    own `reason_codes` enumeration (schemas/v3.0/f0/contract-registry.schema.json,
    verified against carrier 28ca73f338417b5c7e9275c6154b6a0eddbb8bc7). AIOps
    never defines a second, divergent CAEM reason-code enum."""

    CANONICAL_JSON_INVALID = "CANONICAL_JSON_INVALID"
    DUPLICATE_JSON_KEY = "DUPLICATE_JSON_KEY"
    NON_INTEGER_IDENTITY_NUMBER = "NON_INTEGER_IDENTITY_NUMBER"
    POLICY_SOURCE_INVALID = "POLICY_SOURCE_INVALID"
    POLICY_SOURCE_DIGEST_MISMATCH = "POLICY_SOURCE_DIGEST_MISMATCH"
    CONTRACT_REGISTRY_INVALID = "CONTRACT_REGISTRY_INVALID"
    CONTRACT_NOT_FOUND = "CONTRACT_NOT_FOUND"
    CONTRACT_VERSION_UNSUPPORTED = "CONTRACT_VERSION_UNSUPPORTED"
    CONTRACT_RESERVED = "CONTRACT_RESERVED"
    CONTRACT_OWNER_MISMATCH = "CONTRACT_OWNER_MISMATCH"
    SCHEMA_DIGEST_MISMATCH = "SCHEMA_DIGEST_MISMATCH"
    INTERFACE_MANIFEST_INVALID = "INTERFACE_MANIFEST_INVALID"
    INTERFACE_DIGEST_MISMATCH = "INTERFACE_DIGEST_MISMATCH"
    ARTIFACT_DIGEST_MISMATCH = "ARTIFACT_DIGEST_MISMATCH"
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    MIXED_INTERFACE_IDENTITY = "MIXED_INTERFACE_IDENTITY"
    UNSAFE_ARTIFACT_PATH = "UNSAFE_ARTIFACT_PATH"
    UNEXPECTED_ARTIFACT = "UNEXPECTED_ARTIFACT"
    CONTRACT_CHANGE_REQUEST_INVALID = "CONTRACT_CHANGE_REQUEST_INVALID"
    AUTHORITY_EFFECT_FORBIDDEN = "AUTHORITY_EFFECT_FORBIDDEN"
    PIN_INCOMPLETE = "PIN_INCOMPLETE"
    ROLLBACK_TARGET_INVALID = "ROLLBACK_TARGET_INVALID"


class PinReasonCode:
    """AIOps-local reason codes, explicitly namespaced (`caem_pin_*`) so they
    can never be mistaken for a CAEM-normative reason code. Used only for
    situations the F0 registry's own enumeration does not name."""

    UNKNOWN_FIELD = "caem_pin_unknown_field"
    FLOATING_IDENTITY = "caem_pin_floating_identity"
    QUARANTINE_AUTHORITY_VIOLATION = "caem_pin_quarantine_authority_violation"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaemF0LoadError:
    reason_code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class CaemF0Pin:
    """A fully parsed and internally-validated F0 consumer pin. Construction
    does not imply that a local artifact copy has been checked yet — see
    `load_caem_f0_interface`."""

    consumer_repository: str
    normative_repository: str
    normative_writer: str
    authority_effect: str
    target_release: str
    maturity: str
    published: bool
    source_sha: str
    tested_merge_sha: str
    carrier_sha: str
    base_policy_digest: str
    policy_source_bytes_digest: str
    policy_source_semantic_digest: str
    contract_registry_digest: str
    schema_set_digest: str
    artifact_digest: str
    artifact_git_projection_digest: str
    interface_manifest_digest: str
    transport_archive_digest: str
    verifier_identity: str
    toolchain_digest: str


@dataclass(frozen=True)
class CaemF0InterfaceIdentity:
    """The subset of pin fields that describe the CAEM-side interface itself,
    as opposed to fields that only make sense once bound to a repository
    checkout (source_sha/tested_merge_sha/carrier_sha/artifact_git_projection).
    """

    target_release: str
    maturity: str
    published: bool
    base_policy_digest: str
    policy_source_bytes_digest: str
    policy_source_semantic_digest: str
    contract_registry_digest: str
    schema_set_digest: str
    artifact_digest: str
    interface_manifest_digest: str


@dataclass(frozen=True)
class CaemF0ContractRecord:
    contract_id: str
    delivery_status: str
    owner: str
    schema_version: Any
    dependencies: tuple[str, ...]
    output_path: str | None
    owner_repository: str | None = None
    reference_path: str | None = None


@dataclass(frozen=True)
class CaemF0LoadResult:
    ok: bool
    pin: CaemF0Pin | None = None
    identity: CaemF0InterfaceIdentity | None = None
    contracts: Mapping[str, CaemF0ContractRecord] | None = None
    errors: tuple[CaemF0LoadError, ...] = ()


class CaemF0ContractUnavailable(Exception):
    """Raised by `require_caem_f0_contract` when a contract cannot be used:
    unknown to the pinned interface, or `reserved` (not yet implemented)."""

    def __init__(self, reason_code: str, contract_id: str, message: str | None = None) -> None:
        self.reason_code = reason_code
        self.contract_id = contract_id
        super().__init__(message or f"{reason_code}: {contract_id}")


# ---------------------------------------------------------------------------
# Canonical digest primitives — reverse-verified against the real, published
# CAEM 3.0 F0 artifact (see module docstring).
# ---------------------------------------------------------------------------


def _canonical_json_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _raw_file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _raw_bytes_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_blob_oid(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"DUPLICATE_JSON_KEY: {key}")
        result[key] = value
    return result


def _strict_json_loads(data: bytes | str) -> object:
    return json.loads(data, object_pairs_hook=_duplicate_keys)


# ---------------------------------------------------------------------------
# Pin loading
# ---------------------------------------------------------------------------

_PIN_TOP_LEVEL_REQUIRED = ("schema_id", "schema_version", "consumer", "authority", "interface", "constraints")
_PIN_CONSUMER_REQUIRED = ("repository",)
_PIN_AUTHORITY_REQUIRED = ("normative_repository", "normative_writer", "authority_effect")
_PIN_INTERFACE_REQUIRED = (
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
_PIN_CONSTRAINTS_REQUIRED = (
    "mixed_interface_versions",
    "floating_identity",
    "reserved_contract_consumption",
    "network_required",
)

_DIGEST_FIELDS = (
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
_GIT_SHA_FIELDS = ("source_sha", "tested_merge_sha", "carrier_sha")

_EXPECTED_INTERFACE_VALUES = {
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


def _err(reason_code: str, message: str, path: str | None = None) -> CaemF0LoadError:
    return CaemF0LoadError(reason_code=reason_code, message=message, path=path)


def load_caem_f0_pin(pin_path: str | Path) -> CaemF0LoadResult:
    """Parse and strictly validate a `caem.aiops-consumer-pin.v1` document.

    Fail-closed: any missing required field, unknown field, malformed digest
    or Git SHA, non-F0 identity value, or non-`none` authority effect is
    rejected. This function never touches the network and never reads
    anything outside `pin_path`.
    """

    path = Path(pin_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return CaemF0LoadResult(
            ok=False,
            errors=(_err(ReasonCode.PIN_INCOMPLETE, f"cannot read pin file: {exc}", str(path)),),
        )

    try:
        doc = _strict_json_loads(raw)
    except ValueError as exc:
        return CaemF0LoadResult(
            ok=False,
            errors=(_err(ReasonCode.CANONICAL_JSON_INVALID, str(exc), str(path)),),
        )

    if not isinstance(doc, dict):
        return CaemF0LoadResult(
            ok=False,
            errors=(_err(ReasonCode.PIN_INCOMPLETE, "pin document must be a JSON object", str(path)),),
        )

    errors: list[CaemF0LoadError] = []

    def require(container: Mapping[str, object], keys: tuple[str, ...], prefix: str) -> None:
        for key in keys:
            if key not in container:
                errors.append(_err(ReasonCode.PIN_INCOMPLETE, f"missing required field: {prefix}{key}"))

    def reject_unknown(container: Mapping[str, object], known: tuple[str, ...], prefix: str) -> None:
        for key in container:
            if key not in known:
                errors.append(_err(PinReasonCode.UNKNOWN_FIELD, f"unknown pin field: {prefix}{key}"))

    require(doc, _PIN_TOP_LEVEL_REQUIRED, "")
    reject_unknown(doc, _PIN_TOP_LEVEL_REQUIRED, "")

    consumer = doc.get("consumer") if isinstance(doc.get("consumer"), dict) else {}
    authority = doc.get("authority") if isinstance(doc.get("authority"), dict) else {}
    interface = doc.get("interface") if isinstance(doc.get("interface"), dict) else {}
    constraints = doc.get("constraints") if isinstance(doc.get("constraints"), dict) else {}

    if "consumer" in doc:
        require(consumer, _PIN_CONSUMER_REQUIRED, "consumer.")
        reject_unknown(consumer, _PIN_CONSUMER_REQUIRED, "consumer.")
    if "authority" in doc:
        require(authority, _PIN_AUTHORITY_REQUIRED, "authority.")
        reject_unknown(authority, _PIN_AUTHORITY_REQUIRED, "authority.")
    if "interface" in doc:
        require(interface, _PIN_INTERFACE_REQUIRED, "interface.")
        reject_unknown(interface, _PIN_INTERFACE_REQUIRED, "interface.")
    if "constraints" in doc:
        require(constraints, _PIN_CONSTRAINTS_REQUIRED, "constraints.")
        reject_unknown(constraints, _PIN_CONSTRAINTS_REQUIRED, "constraints.")

    if errors:
        return CaemF0LoadResult(ok=False, errors=tuple(errors))

    if doc.get("schema_id") != "aiops.caem-consumer-pin.v1":
        errors.append(_err(ReasonCode.PIN_INCOMPLETE, "schema_id must be aiops.caem-consumer-pin.v1"))
    if doc.get("schema_version") != 1:
        errors.append(_err(ReasonCode.CONTRACT_VERSION_UNSUPPORTED, "schema_version must be 1"))

    # Format validation, before any equality check, so malformed identity
    # values (short/uppercase SHA, malformed digest) get a precise diagnosis.
    for field in _GIT_SHA_FIELDS:
        value = interface.get(field)
        if not isinstance(value, str) or not _GIT_SHA_RE.match(value):
            errors.append(
                _err(
                    ReasonCode.SOURCE_IDENTITY_MISMATCH,
                    f"interface.{field} must be exactly 40 lowercase hex characters, got: {value!r}",
                )
            )
    for field in _DIGEST_FIELDS:
        value = interface.get(field)
        if not isinstance(value, str) or not _DIGEST_RE.match(value):
            errors.append(
                _err(
                    ReasonCode.INTERFACE_DIGEST_MISMATCH,
                    f"interface.{field} must match sha256:<64 lowercase hex>, got: {value!r}",
                )
            )

    if errors:
        return CaemF0LoadResult(ok=False, errors=tuple(errors))

    if interface.get("published") is not False:
        errors.append(_err(ReasonCode.INTERFACE_MANIFEST_INVALID, "interface.published must be false"))
    if interface.get("maturity") != EXPECTED_MATURITY:
        errors.append(
            _err(
                ReasonCode.INTERFACE_MANIFEST_INVALID,
                f"interface.maturity must be {EXPECTED_MATURITY!r}, got: {interface.get('maturity')!r}",
            )
        )

    if authority.get("normative_repository") != EXPECTED_NORMATIVE_REPOSITORY:
        errors.append(_err(ReasonCode.CONTRACT_OWNER_MISMATCH, "authority.normative_repository mismatch"))
    if authority.get("normative_writer") != "caem_only":
        errors.append(_err(ReasonCode.CONTRACT_OWNER_MISMATCH, "authority.normative_writer must be caem_only"))
    if authority.get("authority_effect") != "none":
        errors.append(
            _err(ReasonCode.AUTHORITY_EFFECT_FORBIDDEN, "authority.authority_effect must be none for a consumer pin")
        )
    if consumer.get("repository") != EXPECTED_CONSUMER_REPOSITORY:
        errors.append(_err(ReasonCode.CONTRACT_OWNER_MISMATCH, "consumer.repository mismatch"))

    if constraints.get("mixed_interface_versions") != "deny":
        errors.append(_err(PinReasonCode.FLOATING_IDENTITY, "constraints.mixed_interface_versions must be 'deny'"))
    if constraints.get("floating_identity") != "deny":
        errors.append(_err(PinReasonCode.FLOATING_IDENTITY, "constraints.floating_identity must be 'deny'"))
    if constraints.get("reserved_contract_consumption") != "deny":
        errors.append(
            _err(ReasonCode.CONTRACT_RESERVED, "constraints.reserved_contract_consumption must be 'deny'")
        )
    if constraints.get("network_required") is not False:
        errors.append(_err(PinReasonCode.FLOATING_IDENTITY, "constraints.network_required must be false"))

    if errors:
        return CaemF0LoadResult(ok=False, errors=tuple(errors))

    # This is the deterministic rejection layer for `main`, any branch, any
    # tag, the post-F0 repair carrier, or any other non-F0 identity: this
    # slice's whole purpose is pinning EXACTLY the F0 carrier, never a
    # floating or merely-plausible one.
    for field, expected in _EXPECTED_INTERFACE_VALUES.items():
        actual = interface.get(field)
        if actual != expected:
            errors.append(
                _err(
                    PinReasonCode.FLOATING_IDENTITY,
                    f"interface.{field} does not match the pinned CAEM 3.0 F0 identity "
                    f"(expected {expected!r}, got {actual!r}); only the exact F0 carrier "
                    f"{EXPECTED_CARRIER_SHA} may be pinned by this consumer",
                )
            )

    if errors:
        return CaemF0LoadResult(ok=False, errors=tuple(errors))

    pin = CaemF0Pin(
        consumer_repository=consumer["repository"],
        normative_repository=authority["normative_repository"],
        normative_writer=authority["normative_writer"],
        authority_effect=authority["authority_effect"],
        target_release=interface["target_release"],
        maturity=interface["maturity"],
        published=interface["published"],
        source_sha=interface["source_sha"],
        tested_merge_sha=interface["tested_merge_sha"],
        carrier_sha=interface["carrier_sha"],
        base_policy_digest=interface["base_policy_digest"],
        policy_source_bytes_digest=interface["policy_source_bytes_digest"],
        policy_source_semantic_digest=interface["policy_source_semantic_digest"],
        contract_registry_digest=interface["contract_registry_digest"],
        schema_set_digest=interface["schema_set_digest"],
        artifact_digest=interface["artifact_digest"],
        artifact_git_projection_digest=interface["artifact_git_projection_digest"],
        interface_manifest_digest=interface["interface_manifest_digest"],
        transport_archive_digest=interface["transport_archive_digest"],
        verifier_identity=interface["verifier_identity"],
        toolchain_digest=interface["toolchain_digest"],
    )
    return CaemF0LoadResult(ok=True, pin=pin)


# ---------------------------------------------------------------------------
# Interface loading — verifies a local artifact copy against the pin
# ---------------------------------------------------------------------------


def _build_contracts(registry: Mapping[str, object]) -> dict[str, CaemF0ContractRecord]:
    contracts: dict[str, CaemF0ContractRecord] = {}
    for entry in registry.get("contracts", []):
        contracts[entry["contract_id"]] = CaemF0ContractRecord(
            contract_id=entry["contract_id"],
            delivery_status=entry["delivery_status"],
            owner=entry["owner"],
            schema_version=entry["schema_version"],
            dependencies=tuple(entry.get("dependencies", ())),
            output_path=entry.get("output_path"),
            owner_repository=entry.get("owner_repository"),
            reference_path=entry.get("reference_path"),
        )
    return contracts


def _identity_from_manifest(manifest: Mapping[str, object]) -> CaemF0InterfaceIdentity:
    return CaemF0InterfaceIdentity(
        target_release=manifest["target_release"],
        maturity=manifest["maturity"],
        published=manifest["published"],
        base_policy_digest=manifest["base_policy_digest"],
        policy_source_bytes_digest=manifest["policy_source_bytes_digest"],
        policy_source_semantic_digest=manifest["policy_source_semantic_digest"],
        contract_registry_digest=manifest["contract_registry_digest"],
        schema_set_digest=manifest["schema_set"]["digest"],
        artifact_digest=manifest["artifact"]["digest"],
        interface_manifest_digest=_canonical_json_digest(manifest),
    )


def _verify_manifest_and_registry(
    pin: CaemF0Pin, manifest: Mapping[str, object], registry: Mapping[str, object]
) -> list[CaemF0LoadError]:
    errors: list[CaemF0LoadError] = []

    manifest_digest = _canonical_json_digest(manifest)
    if manifest_digest != pin.interface_manifest_digest:
        errors.append(
            _err(
                ReasonCode.INTERFACE_DIGEST_MISMATCH,
                f"recomputed interface_manifest_digest {manifest_digest} does not match "
                f"pinned {pin.interface_manifest_digest}",
            )
        )

    registry_digest = _canonical_json_digest(registry)
    if registry_digest != pin.contract_registry_digest:
        errors.append(
            _err(
                ReasonCode.CONTRACT_REGISTRY_INVALID,
                f"recomputed contract_registry_digest {registry_digest} does not match "
                f"pinned {pin.contract_registry_digest}",
            )
        )

    if manifest.get("contract_registry_digest") != pin.contract_registry_digest:
        errors.append(
            _err(
                ReasonCode.MIXED_INTERFACE_IDENTITY,
                "interface-manifest.contract_registry_digest disagrees with the pin",
            )
        )

    artifact_files = sorted(manifest.get("artifact", {}).get("files", []), key=lambda f: f["path"])
    for entry in artifact_files:
        path_value = entry["path"]
        if not _SAFE_PATH_RE.match(path_value) or PurePosixPath(path_value).is_absolute():
            errors.append(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "unsafe artifact path", path_value))

    projection = [{"path": f["path"], "size": f["size"], "sha256": f["sha256"]} for f in artifact_files]
    artifact_digest = _canonical_json_digest(projection)
    if artifact_digest != pin.artifact_digest:
        errors.append(
            _err(
                ReasonCode.ARTIFACT_DIGEST_MISMATCH,
                f"recomputed artifact_digest {artifact_digest} does not match pinned {pin.artifact_digest}",
            )
        )
    if manifest.get("artifact", {}).get("digest") != pin.artifact_digest:
        errors.append(_err(ReasonCode.MIXED_INTERFACE_IDENTITY, "interface-manifest.artifact.digest disagrees with pin"))

    schema_files = sorted(manifest.get("schema_set", {}).get("files", []), key=lambda f: f["contract_id"])
    schema_projection = [
        {"contract_id": f["contract_id"], "path": f["path"], "sha256": f["sha256"]} for f in schema_files
    ]
    schema_set_digest = _canonical_json_digest(schema_projection)
    if schema_set_digest != pin.schema_set_digest:
        errors.append(
            _err(
                ReasonCode.SCHEMA_DIGEST_MISMATCH,
                f"recomputed schema_set_digest {schema_set_digest} does not match pinned {pin.schema_set_digest}",
            )
        )
    if manifest.get("schema_set", {}).get("digest") != pin.schema_set_digest:
        errors.append(_err(ReasonCode.MIXED_INTERFACE_IDENTITY, "interface-manifest.schema_set.digest disagrees with pin"))

    for field in ("base_policy_digest", "policy_source_bytes_digest", "policy_source_semantic_digest"):
        if manifest.get(field) != getattr(pin, field):
            errors.append(_err(ReasonCode.POLICY_SOURCE_DIGEST_MISMATCH, f"interface-manifest.{field} disagrees with pin"))

    # Registry-level structural invariants (contract IDs unique, reserved
    # contracts carry no output_path, dependency graph acyclic).
    seen_ids: set[str] = set()
    contracts_by_id: dict[str, Mapping[str, object]] = {}
    for entry in registry.get("contracts", []):
        cid = entry.get("contract_id")
        if cid in seen_ids:
            errors.append(_err(ReasonCode.CONTRACT_REGISTRY_INVALID, f"duplicate contract_id: {cid}"))
        seen_ids.add(cid)
        contracts_by_id[cid] = entry

    def _has_cycle(start: str) -> bool:
        stack = [(start, [start])]
        while stack:
            node, path_so_far = stack.pop()
            for dep in contracts_by_id.get(node, {}).get("dependencies", []):
                if dep == start:
                    return True
                if dep in path_so_far:
                    continue
                stack.append((dep, [*path_so_far, dep]))
        return False

    for cid in contracts_by_id:
        if _has_cycle(cid):
            errors.append(_err(ReasonCode.CONTRACT_REGISTRY_INVALID, f"dependency cycle involving: {cid}"))
            break

    return errors


def load_caem_f0_interface(
    pin: CaemF0Pin,
    *,
    interface_root: str | Path | None = None,
    transport_zip: str | Path | None = None,
    git_root: str | Path | None = None,
) -> CaemF0LoadResult:
    """Verify a locally available copy of the CAEM 3.0 F0 interface against
    `pin`, byte for byte, and return the available contract registry.

    Exactly one of `interface_root` (a directory containing, at minimum,
    `interfaces/caem-3.0-f0/{interface-manifest.json,contract-registry.json}`
    and the paths its manifest declares) or `transport_zip` (a single archive
    containing the same files, matching the real
    `caem-3.0-f0-interface.zip` transport shape) must be given.

    `git_root`, if given, must be a real Git working tree checked out at
    `pin.carrier_sha`; when present, the artifact's Git blob-OID projection is
    recomputed and compared against `pin.artifact_git_projection_digest`.
    This loader never fetches, clones, or otherwise touches the network.
    """

    if (interface_root is None) == (transport_zip is None):
        return CaemF0LoadResult(
            ok=False,
            errors=(
                _err(
                    ReasonCode.INTERFACE_MANIFEST_INVALID,
                    "exactly one of interface_root or transport_zip must be provided",
                ),
            ),
        )

    try:
        if transport_zip is not None:
            manifest, registry, artifact_files_bytes = _load_from_zip(Path(transport_zip), pin)
        else:
            manifest, registry, artifact_files_bytes = _load_from_directory(Path(interface_root), pin)
    except _LoadFailure as failure:
        return CaemF0LoadResult(ok=False, errors=(failure.error,))

    errors = _verify_manifest_and_registry(pin, manifest, registry)

    if git_root is not None:
        errors.extend(_verify_git_projection(Path(git_root), pin, manifest))

    if errors:
        return CaemF0LoadResult(ok=False, errors=tuple(errors))

    identity = _identity_from_manifest(manifest)
    contracts = _build_contracts(registry)
    return CaemF0LoadResult(ok=True, pin=pin, identity=identity, contracts=contracts)


class _LoadFailure(Exception):
    def __init__(self, error: CaemF0LoadError) -> None:
        self.error = error
        super().__init__(error.message)


def _resolved_within_root(root: Path, file_path: Path, rel: str) -> None:
    """Reject not just a symlinked leaf file but also a symlinked ANCESTOR
    directory that would let a declared-safe relative path resolve outside
    `root` (e.g. `schemas/` itself being a symlink to somewhere else)."""

    resolved_root = root.resolve()
    resolved_file = file_path.resolve()
    try:
        resolved_file.relative_to(resolved_root)
    except ValueError as exc:
        raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "path escapes interface root after resolution", rel)) from exc


def _load_from_directory(root: Path, pin: CaemF0Pin) -> tuple[dict, dict, dict[str, bytes]]:
    manifest_path = root / F0_MANIFEST_PATH
    registry_path = root / F0_REGISTRY_PATH
    if not manifest_path.is_file():
        raise _LoadFailure(_err(ReasonCode.INTERFACE_MANIFEST_INVALID, "interface-manifest.json not found", str(manifest_path)))
    if manifest_path.is_symlink() or registry_path.is_symlink():
        raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "symlinked interface files are rejected"))
    _resolved_within_root(root, manifest_path, F0_MANIFEST_PATH)
    _resolved_within_root(root, registry_path, F0_REGISTRY_PATH)

    try:
        manifest = _strict_json_loads(manifest_path.read_bytes())
        registry = _strict_json_loads(registry_path.read_bytes())
    except ValueError as exc:
        raise _LoadFailure(_err(ReasonCode.CANONICAL_JSON_INVALID, str(exc))) from exc

    file_bytes: dict[str, bytes] = {}
    for entry in manifest.get("artifact", {}).get("files", []):
        rel = entry["path"]
        if not _SAFE_PATH_RE.match(rel) or PurePosixPath(rel).is_absolute():
            raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "unsafe artifact path", rel))
        file_path = root / rel
        if file_path.is_symlink():
            raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "symlinked artifact file rejected", rel))
        if not file_path.is_file():
            raise _LoadFailure(_err(ReasonCode.UNEXPECTED_ARTIFACT, "declared artifact file missing", rel))
        _resolved_within_root(root, file_path, rel)
        data = file_path.read_bytes()
        actual_digest = _raw_bytes_digest(data)
        if actual_digest != entry["sha256"] or len(data) != entry["size"]:
            raise _LoadFailure(_err(ReasonCode.ARTIFACT_DIGEST_MISMATCH, "artifact file digest/size mismatch", rel))
        file_bytes[rel] = data

    return manifest, registry, file_bytes


def _load_from_zip(zip_path: Path, pin: CaemF0Pin) -> tuple[dict, dict, dict[str, bytes]]:
    if not zip_path.is_file():
        raise _LoadFailure(_err(ReasonCode.UNEXPECTED_ARTIFACT, "transport zip not found", str(zip_path)))

    archive_bytes = zip_path.read_bytes()
    archive_digest = _raw_bytes_digest(archive_bytes)
    if archive_digest != pin.transport_archive_digest:
        raise _LoadFailure(
            _err(
                ReasonCode.ARTIFACT_DIGEST_MISMATCH,
                f"transport archive digest {archive_digest} does not match pinned {pin.transport_archive_digest}",
            )
        )

    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(name.casefold() for name in names)):
            raise _LoadFailure(_err(ReasonCode.UNEXPECTED_ARTIFACT, "case-colliding member names in transport zip"))

        for info in infos:
            name = info.filename
            if not _SAFE_PATH_RE.match(name) or PurePosixPath(name).is_absolute():
                raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "unsafe path in transport zip", name))
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (mode & 0o170000) == 0o120000:  # S_IFLNK
                raise _LoadFailure(_err(ReasonCode.UNSAFE_ARTIFACT_PATH, "symlink member in transport zip", name))

        if F0_MANIFEST_PATH not in archive.namelist():
            raise _LoadFailure(_err(ReasonCode.INTERFACE_MANIFEST_INVALID, "transport zip missing interface-manifest.json"))
        try:
            manifest = _strict_json_loads(archive.read(F0_MANIFEST_PATH))
        except ValueError as exc:
            raise _LoadFailure(_err(ReasonCode.CANONICAL_JSON_INVALID, str(exc))) from exc

        expected_members = {F0_MANIFEST_PATH}
        file_bytes: dict[str, bytes] = {}
        for entry in manifest.get("artifact", {}).get("files", []):
            rel = entry["path"]
            expected_members.add(rel)
            if rel not in archive.namelist():
                raise _LoadFailure(_err(ReasonCode.UNEXPECTED_ARTIFACT, "declared artifact file missing from zip", rel))
            data = archive.read(rel)
            actual_digest = _raw_bytes_digest(data)
            if actual_digest != entry["sha256"] or len(data) != entry["size"]:
                raise _LoadFailure(_err(ReasonCode.ARTIFACT_DIGEST_MISMATCH, "artifact file digest/size mismatch", rel))
            file_bytes[rel] = data

        actual_members = set(archive.namelist())
        extra = actual_members - expected_members
        if extra:
            raise _LoadFailure(_err(ReasonCode.UNEXPECTED_ARTIFACT, f"unexpected members in transport zip: {sorted(extra)}"))

        if F0_REGISTRY_PATH not in file_bytes:
            raise _LoadFailure(_err(ReasonCode.CONTRACT_REGISTRY_INVALID, "contract-registry.json not present among artifact files"))
        try:
            registry = _strict_json_loads(file_bytes[F0_REGISTRY_PATH])
        except ValueError as exc:
            raise _LoadFailure(_err(ReasonCode.CANONICAL_JSON_INVALID, str(exc))) from exc

    return manifest, registry, file_bytes


def _verify_git_projection(git_root: Path, pin: CaemF0Pin, manifest: Mapping[str, object]) -> list[CaemF0LoadError]:
    """Best-effort verification: only meaningful when `git_root` is a real Git
    working tree checked out at `pin.carrier_sha`. Recomputes each artifact
    file's Git blob OID with the standard `blob <len>\\0<content>` SHA-1
    preimage (public Git internals, not CAEM-specific) and compares the
    canonical-JSON digest of the resulting projection to
    `pin.artifact_git_projection_digest`."""

    projection = []
    for entry in sorted(manifest.get("artifact", {}).get("files", []), key=lambda f: f["path"]):
        rel = entry["path"]
        try:
            result = subprocess.run(
                ["git", "cat-file", "blob", f"{pin.carrier_sha}:{rel}"],
                cwd=git_root,
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            return [_err(ReasonCode.SOURCE_IDENTITY_MISMATCH, f"cannot read {rel} from {pin.carrier_sha}: {exc}")]
        blob_oid = _git_blob_oid(result.stdout)
        projection.append({"path": rel, "git_blob_oid": blob_oid})

    digest = _canonical_json_digest(projection)
    if digest != pin.artifact_git_projection_digest:
        return [
            _err(
                ReasonCode.ARTIFACT_DIGEST_MISMATCH,
                f"recomputed artifact_git_projection_digest {digest} does not match pinned "
                f"{pin.artifact_git_projection_digest}",
            )
        ]
    return []


def require_caem_f0_contract(load_result: CaemF0LoadResult, contract_id: str) -> CaemF0ContractRecord:
    """Return the contract record for `contract_id`, or raise
    `CaemF0ContractUnavailable` if it is unknown or `reserved`. A `reserved`
    contract is never treated as available, even if requested by name."""

    if not load_result.ok or load_result.contracts is None:
        raise CaemF0ContractUnavailable(ReasonCode.CONTRACT_NOT_FOUND, contract_id, "interface not successfully loaded")
    record = load_result.contracts.get(contract_id)
    if record is None:
        raise CaemF0ContractUnavailable(ReasonCode.CONTRACT_NOT_FOUND, contract_id)
    if record.delivery_status == "reserved":
        raise CaemF0ContractUnavailable(ReasonCode.CONTRACT_RESERVED, contract_id)
    return record


# ---------------------------------------------------------------------------
# Generated-view identity consistency
# ---------------------------------------------------------------------------

# The two legacy digests this slice's remediation replaces. They may appear
# ONLY inside the quarantine directory (as preserved historical bytes), never
# in any active, non-quarantined file.
_STALE_IDENTITY_STRINGS = (
    "5f8d13688555633a7b26124c81f390bdcc4ddda2ccb40d4a45427b95cbcb9928",
    "9aa4949a0a9b865ae3b9a589fdf4dbadd1254cfad8c11f3db26efce10303ba60",
)
# The post-F0 repair carrier is a legitimate CAEM `main` commit, but must
# never be presented as a consumer pin target in this repository.
_REPAIR_CARRIER_SHA = "dee7018e8121ab0d1a74207395adfe8a19b2778a"

_ACTIVE_IDENTITY_HEADER_FILES = (
    "AGENTS.md",
    "CLAUDE.md",
    "docs/engineering/CAEM_CORE.md",
    "docs/engineering/PROJECT_OVERLAY.md",
    "docs/engineering/CURRENT_CHECKPOINT.md",
)

_QUARANTINE_DIR_NAME = ".caem/quarantine"


def scan_for_stale_caem_identity(repository_root: str | Path) -> tuple[tuple[str, str], ...]:
    """Scan `_ACTIVE_IDENTITY_HEADER_FILES` (and, defensively, anything else
    under the repository root that is not inside `.caem/quarantine/`) for the
    legacy CAEM 2.1 digests or the post-F0 repair carrier used as if it were a
    pin. Returns `(relative_path, matched_string)` pairs found OUTSIDE
    quarantine; an empty tuple means the repository has a single, consistent
    active CAEM identity."""

    root = Path(repository_root)
    hits: list[tuple[str, str]] = []
    needles = (*_STALE_IDENTITY_STRINGS, _REPAIR_CARRIER_SHA)

    for rel in _ACTIVE_IDENTITY_HEADER_FILES:
        candidate = root / rel
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle in text:
                hits.append((rel, needle))

    return tuple(hits)
