"""F1 -- profile-derived payload references (issue #131, child of tracker
#110).

Populates real ``artifact_references``/``contract_references`` for
``ChunkPayloadV2``, hardcoded ``[]`` until now
(``payload_builder_v2.py:128-129``). Reads real artifact/contract content
from a target checkout (``profile.artifacts``/``profile.contracts``, loaded
by #85's ``profile_loader_v2.load_target_profile_v2``), respecting
``max_bytes``/``required``, sanitizing artifact content before hashing.

Deliberately out of scope, per the issue: ``PayloadSetV2`` emission (F2/#132,
which only NEEDS a real ``ChunkPayloadV2`` to attest against -- doesn't need
this module changed); any CLI.

## Two different verifications, because artifacts and contracts mean
different things

**Artifacts** (``TargetArtifactV2``) have no pre-declared hash in the
profile -- there is nothing to "diverge" from. What can fail is presence
(``required=True`` but missing -> fail closed) and size
(``max_bytes`` exceeded -> refuse, never silently truncate). Artifact
content is sanitized (``redaction.sanitize_artifact_value``) BEFORE
hashing: an artifact's raw text could plausibly reach an LLM prompt later
(that is what an "artifact reference" is *for*), so it must never be
referenced by a hash computed over unsanitized content.

**Contracts** (``TargetContractV2``) DO carry a pre-declared ``sha256`` in
the profile -- the whole point of referencing one is proving the real file
on disk still matches what the profile claims. Comparing a REDACTED hash
against that pre-declared value would only ever match if the profile's own
`sha256` had been computed post-redaction too, which cannot be assumed
--and nothing about a contract's own raw bytes is embedded in the resulting
reference (only the hash is), so there is no exposure risk in hashing the
raw content. Contract content is therefore never redacted before hashing;
a mismatch fails closed (``PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2``).

Both use plain UTF-8 text hashing/redaction uniformly across every declared
``kind`` (``json``/``yaml``/``text``/``markdown``/``diff``) rather than
kind-aware structured parsing: ``redaction.sanitize_artifact_value``
already redacts a plain string via the same regex-based scanning
(``redact_text``) any other string field in this codebase goes through: no
kind-specific parser is needed to apply that same discipline, and inventing
one here would be unrequested complexity this issue's own acceptance
criteria never asks for.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.agent_review.contracts_v2 import (
    PayloadArtifactReferenceV2,
    PayloadContractReferenceV2,
    TargetProfileV2,
)
from app.agent_review.external_path_ingress_v2 import (
    EXTERNAL_PATH_MISSING_REASON_V2,
    EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
    ExternalPathIngressError,
    validate_external_input_file_v2,
)
from app.agent_review.redaction import sanitize_artifact_value

PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2 = "payload_required_artifact_missing"
PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2 = "payload_artifact_exceeds_max_bytes"
PAYLOAD_ARTIFACT_UNREADABLE_REASON_V2 = "payload_artifact_unreadable"
PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2 = "payload_required_contract_missing"
PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2 = "payload_contract_sha256_mismatch"
PAYLOAD_CONTRACT_UNREADABLE_REASON_V2 = "payload_contract_unreadable"

OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2 = "optional_artifact_missing"

_UNIFORM_ARTIFACT_ROLE_V2 = "primary"


class PayloadReferenceError(ValueError):
    """Raised when an artifact/contract reference cannot be built. Carries
    a stable ``reason_code`` only -- never file content or a local path."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def build_payload_artifact_references_v2(
    profile: TargetProfileV2, repo_root: Path
) -> tuple[list[PayloadArtifactReferenceV2], tuple[str, ...]]:
    """Build references from artifact bytes read through G4B path capabilities."""

    references: list[PayloadArtifactReferenceV2] = []
    limitations: list[str] = []
    for artifact in profile.artifacts:
        path = Path(repo_root) / artifact.path
        try:
            capability = validate_external_input_file_v2(path, root=repo_root)
        except ExternalPathIngressError as exc:
            if exc.reason_code in {
                EXTERNAL_PATH_MISSING_REASON_V2,
                EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
            }:
                if artifact.required:
                    raise PayloadReferenceError(PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2) from exc
                limitations.append(
                    f"{OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2}:{artifact.artifact_id}"
                )
                continue
            raise PayloadReferenceError(PAYLOAD_ARTIFACT_UNREADABLE_REASON_V2) from exc

        # Bounded read: never materialize more than `max_bytes + 1` bytes,
        # so an oversized external artifact cannot be fully read into memory
        # before this size check runs (see `read_bytes_bounded`'s own
        # docstring for why a pre-read `stat()` isn't the fix either).
        try:
            raw_bytes = capability.read_bytes_bounded(artifact.max_bytes)
        except ExternalPathIngressError as exc:
            raise PayloadReferenceError(PAYLOAD_ARTIFACT_UNREADABLE_REASON_V2) from exc
        if len(raw_bytes) > artifact.max_bytes:
            raise PayloadReferenceError(PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2)
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PayloadReferenceError(PAYLOAD_ARTIFACT_UNREADABLE_REASON_V2) from exc

        sanitized = sanitize_artifact_value(text)
        sha256 = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        references.append(
            PayloadArtifactReferenceV2(
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                sha256=sha256,
                role=_UNIFORM_ARTIFACT_ROLE_V2,
            )
        )
    return references, tuple(limitations)


def build_payload_contract_references_v2(
    profile: TargetProfileV2, repo_root: Path
) -> list[PayloadContractReferenceV2]:
    """Build and verify contract references through G4B path capabilities."""

    references: list[PayloadContractReferenceV2] = []
    for contract in profile.contracts:
        path = Path(repo_root) / contract.path
        try:
            capability = validate_external_input_file_v2(path, root=repo_root)
        except ExternalPathIngressError as exc:
            if exc.reason_code in {
                EXTERNAL_PATH_MISSING_REASON_V2,
                EXTERNAL_PATH_WRONG_TYPE_REASON_V2,
            }:
                if contract.required:
                    raise PayloadReferenceError(PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2) from exc
                continue
            raise PayloadReferenceError(PAYLOAD_CONTRACT_UNREADABLE_REASON_V2) from exc

        try:
            raw_bytes = capability.read_bytes()
        except ExternalPathIngressError as exc:
            raise PayloadReferenceError(PAYLOAD_CONTRACT_UNREADABLE_REASON_V2) from exc
        sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if sha256 != contract.sha256:
            raise PayloadReferenceError(PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2)

        references.append(
            PayloadContractReferenceV2(
                contract_id=contract.contract_id,
                contract_version=contract.contract_version,
                sha256=sha256,
                scope=contract.scope,
                paths=[],
            )
        )
    return references
