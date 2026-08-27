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
from app.agent_review.redaction import sanitize_artifact_value

PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2 = "payload_required_artifact_missing"
PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2 = "payload_artifact_exceeds_max_bytes"
PAYLOAD_ARTIFACT_UNREADABLE_REASON_V2 = "payload_artifact_unreadable"
PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2 = "payload_required_contract_missing"
PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2 = "payload_contract_sha256_mismatch"
PAYLOAD_CONTRACT_UNREADABLE_REASON_V2 = "payload_contract_unreadable"

OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2 = "optional_artifact_missing"

# Every profile artifact is referenced the same uniform way: TargetArtifactV2
# carries no field distinguishing "primary" from "supporting"/"validation"/
# "coverage" content, so inventing a per-kind or per-artifact role
# assignment would be a guess this module has no real signal to base on.
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
    """Build one ``PayloadArtifactReferenceV2`` per readable, in-budget
    artifact declared in ``profile.artifacts``. Returns
    ``(references, limitations)`` -- ``limitations`` names every OPTIONAL
    artifact that was missing (``optional_artifact_missing:<artifact_id>``),
    mirroring how a dropped auxiliary fragment is documented rather than
    silently vanishing. A REQUIRED artifact that is missing, unreadable, or
    exceeds ``max_bytes`` fails the whole build closed instead."""

    references: list[PayloadArtifactReferenceV2] = []
    limitations: list[str] = []
    for artifact in profile.artifacts:
        path = Path(repo_root) / artifact.path
        if not path.is_file():
            if artifact.required:
                raise PayloadReferenceError(PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2)
            limitations.append(f"{OPTIONAL_ARTIFACT_MISSING_LIMITATION_PREFIX_V2}:{artifact.artifact_id}")
            continue

        size = path.stat().st_size
        if size > artifact.max_bytes:
            raise PayloadReferenceError(PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2)

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
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
    """Build one ``PayloadContractReferenceV2`` per readable contract
    declared in ``profile.contracts``, verifying the file on disk still
    hashes to the profile's own pre-declared ``TargetContractV2.sha256`` --
    fail closed on divergence, never silently trust the profile's stale
    claim. A REQUIRED contract that is missing fails the whole build
    closed; a non-required missing contract is simply omitted (contracts
    have no analogous "optional" concept to surface as a limitation the
    way artifacts do -- ``TargetContractV2.required`` already exists purely
    to gate this).

    ``paths`` is always empty: nothing in ``TargetContractV2`` associates a
    contract with a specific subset of the diff's changed files, so no
    per-file linkage is attempted here -- a repository-scoped contract
    reference applies without needing one.
    """

    references: list[PayloadContractReferenceV2] = []
    for contract in profile.contracts:
        path = Path(repo_root) / contract.path
        if not path.is_file():
            if contract.required:
                raise PayloadReferenceError(PAYLOAD_REQUIRED_CONTRACT_MISSING_REASON_V2)
            continue

        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            # The artifact branch above has always guarded its read; this one
            # did not, so an unreadable-but-present contract escaped as a raw
            # `PermissionError` carrying the checkout path. Same owner, same
            # discipline, and a reason as precise as the artifact one.
            raise PayloadReferenceError(
                PAYLOAD_CONTRACT_UNREADABLE_REASON_V2
            ) from exc
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
