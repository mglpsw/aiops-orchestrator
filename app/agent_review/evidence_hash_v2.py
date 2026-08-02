"""E1 -- the canonical evidence hash (issue #128, child of tracker #109).

Defines and computes ``RunIdentityV2.evidence_hash`` (``contracts_v2.py:413``),
which today exists only as a declared field with no production producer.
``evidence_hash`` is inside ``_RUN_IDENTITY_FIELDS`` (``contracts_v2.py:47-58``),
therefore inside the preimage of ``run_id`` itself -- defining it incorrectly
is irreversible without re-hashing everything downstream (manifest, payloads,
run identity). This is contract definition, not wiring, and is scoped
accordingly: no manifest assembly, no diff adapter, no run-assembly wiring --
that is E2/#129's job, the next child of tracker #109.

## What an "evidence bundle" is

A sanitized, minimal list of references to the target-declared inputs a run
actually drew on -- artifacts (``TargetArtifactV2``) and contracts
(``TargetContractV2``) -- each identified by id and a content ``sha256``,
never raw content. This mirrors the existing v2 discipline of referencing
sanitized content by hash rather than embedding it
(``PayloadArtifactReferenceV2``/``PayloadContractReferenceV2``,
``contracts_v2.py:555-568``) and the v1 precedent of a sanitized evidence
index (``evidence_index.py``'s ``build_evidence_index``, which lists artifact
presence/validity references rather than content).

Computing the actual ``sha256`` of a real artifact's sanitized content (via
``redaction.sanitize_artifact_value``) requires reading that artifact from a
real target checkout -- assembly, deliberately out of scope here. This module
owns only the CONTRACT for a bundle of already-computed references, and the
canonical hash over that contract; E2 populates real ``EvidenceReferenceV2``
entries from a real run.

## Why no ``*Material*``/self-hashing split

Every other v2 artifact that splits into ``*Material*``/self-hashing
(``ManifestMaterialV2``/``ManifestV2``, ``ChunkPayloadMaterialV2``/
``ChunkPayloadV2``, ``SemanticGroupingPolicyMaterialV2``/
``SemanticGroupingPolicyV2``, ``PayloadSetMaterialV2``/``PayloadSetV2``) does
so because the object carries ITS OWN hash as one of its own fields, which
would otherwise make the hash preimage circular. ``EvidenceBundleV2`` does
not carry its own hash -- ``evidence_hash`` lives on ``RunIdentityV2``, a
DIFFERENT object, exactly the shape ``manifest_v2.py``'s own module
docstring already describes for ``manifest_hash`` ("the field being kept out
of the hash preimage lives on a different object"). No split is needed: the
bundle is hashed in full, unconditionally.

## Determinism and byte-reproducibility

``canonical_evidence_bundle_bytes_v2`` sorts ``references`` by
``(kind, reference_id)`` before hashing -- the same canonical-ordering
discipline every other list-of-entries hash in this v2 surface already
applies. References are already validated unique by ``(kind, reference_id)``
in ``EvidenceBundleV2``'s own validator, so sorting cannot silently collapse
two distinct entries, and reordering the constructor's input never changes
these bytes.

Deliberately out of scope, per the issue: manifest assembly; the diff
adapter; any run-assembly wiring (E2/#129); reading real artifacts from a
target checkout or calling ``redaction.sanitize_artifact_value`` (E2's job).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import model_validator

from app.agent_review.contracts_v2 import ContractV2Model, SafeIdentifier, SafeText, Sha256

EVIDENCE_BUNDLE_SCHEMA_V2 = "agent-review.evidence-bundle.v2"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, run_fragment_coverage_v2.py, semantic_grouping_policy_v2.py,
    # and payload_set_v2.py -- not a new rule.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class EvidenceReferenceV2(ContractV2Model):
    """One sanitized reference: an id, its kind, and the content ``sha256``
    of its already-sanitized form. Never raw content -- mirrors
    ``PayloadContractReferenceV2``'s own reference-by-hash discipline."""

    reference_id: SafeIdentifier
    kind: Literal["artifact", "contract"]
    sha256: Sha256
    detail: SafeText


class EvidenceBundleV2(ContractV2Model):
    schema_id: Literal["agent-review.evidence-bundle.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-evidence-bundle-v2"]
    references: list[EvidenceReferenceV2]

    @model_validator(mode="after")
    def validate_bundle(self) -> EvidenceBundleV2:
        keys = [(reference.kind, reference.reference_id) for reference in self.references]
        if len(keys) != len(set(keys)):
            raise ValueError("references must be unique by (kind, reference_id)")
        return self


def canonical_evidence_bundle_bytes_v2(bundle: EvidenceBundleV2 | Mapping[str, object]) -> bytes:
    """Hash preimage: every validated field of the bundle, unconditionally
    (no field is excluded -- see the module docstring for why no
    ``*Material*`` split is needed here).

    ``references`` is re-sorted by ``(kind, reference_id)`` here,
    independent of construction order: entries are already validated unique
    by that same key, so sorting cannot silently collapse two distinct
    entries, and reordering the constructor's input never changes these
    bytes.
    """

    if isinstance(bundle, EvidenceBundleV2):
        dumped = bundle.model_dump(mode="json")
    else:
        if not isinstance(bundle, Mapping):
            raise TypeError("evidence bundle must be a model or mapping")
        dumped = EvidenceBundleV2.model_validate(dict(bundle)).model_dump(mode="json")

    dumped = {
        **dumped,
        "references": sorted(dumped["references"], key=lambda ref: (ref["kind"], ref["reference_id"])),
    }
    return _canonical_json_bytes_v2(dumped)


def compute_evidence_hash_v2(bundle: EvidenceBundleV2 | Mapping[str, object]) -> str:
    """The value meant for ``RunIdentityV2.evidence_hash`` once E2/#129
    assembles a real run."""

    return hashlib.sha256(canonical_evidence_bundle_bytes_v2(bundle)).hexdigest()
