"""``PayloadSetV2`` -- the manifest/run/payload artifact topology (issue #105).

``ManifestChunkV2.payload_sha256`` is null-only by type
(``manifest_v2.py``): populating it would change ``manifest_hash``, forcing a
new ``run_id``, which the payload material itself embeds -- invalidating the
hash that was just inserted. There is no non-circular way to fold a built
payload's hash back into the manifest that describes it.

``PayloadSetV2`` is the deliberate resolution: a separate artifact, bound to a
run by ``run_id`` + ``manifest_hash`` (never by folding into ``RunIdentityV2``
itself), listing the ``payload_sha256`` of every chunk payload actually built
for that run. It certifies "these are the payloads F built for this run",
without ever touching ``RunIdentityV2``, ``ManifestV2``, or any golden
fixture.

Two grandezas this module keeps distinct, matching the rest of v2's
discipline of not conflating "internally coherent" with "matches this
specific external object":

    validade estrutural do payload set   = self-consistent on its own
                                             (unique chunk_id, non-empty
                                             payloads, payload_set_sha256
                                             correctness)
    validade contra um manifest          = run_id/manifest_hash match a
                                             SPECIFIC ManifestV2, and the
                                             payload set's chunk_id set is
                                             EXACTLY the manifest's chunk_id
                                             set -- neither missing nor extra

A bare ``PayloadSetV2`` cannot check the second on its own -- same reasoning
as ``RunFragmentCoverageEntryV2`` vs. ``bind_coverage_report_to_manifest_v2``
(#104/#115) and ``SemanticGroupingPolicyV2`` vs.
``bind_semantic_grouping_policy_to_target_profile_v2`` (#106) elsewhere in
this codebase: a Pydantic model has no way to reach out to another object at
construction time.

Deliberately out of scope here, per the issue: emitting a ``PayloadSetV2``
from a real run (issue #110/F2, which also owns the CLI); any change to
``RunIdentityV2``'s own fields (a future ``RunIdentityV2.1`` embedding a
``payload_set_hash`` inside identity itself is a separate, later issue,
explicitly deferred).

Limitation, stated once here and not to be silently forgotten: a
``PayloadSetV2`` is bound to a run but does NOT participate in ``run_id``.
Its uniqueness comes from the deterministic derivation of the payloads it
lists and from recomputation by a trusted producer -- not from cryptographic
identity embedded inside ``RunIdentityV2``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Literal

from pydantic import model_validator

from app.agent_review.contracts_v2 import ContractV2Model, SafeIdentifier, Sha256
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

PAYLOAD_SET_SCHEMA_V2 = "agent-review.payload-set.v2"

PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2 = "payload_set_run_id_mismatch"
PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2 = "payload_set_manifest_hash_mismatch"
PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2 = "payload_set_chunk_set_mismatch"


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, run_fragment_coverage_v2.py, and
    # semantic_grouping_policy_v2.py -- not a new rule.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class PayloadSetBindingError(ExpectedOperationalRefusalV2, ValueError):
    """Raised when a ``PayloadSetV2`` fails to bind against a specific
    ``ManifestV2``. Carries a stable ``reason_code`` only -- never manifest
    or payload content."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class PayloadSetEntryV2(ContractV2Model):
    chunk_id: SafeIdentifier
    payload_sha256: Sha256


class PayloadSetMaterialV2(ContractV2Model):
    schema_id: Literal["agent-review.payload-set.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-build-payload-set-v2"]
    run_id: Sha256
    manifest_hash: Sha256
    payloads: list[PayloadSetEntryV2]

    @model_validator(mode="after")
    def validate_material(self) -> PayloadSetMaterialV2:
        if not self.payloads:
            raise ValueError("a payload set must contain at least one payload entry")
        chunk_ids = [entry.chunk_id for entry in self.payloads]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("chunk_id must be unique across the payload set")
        return self


class PayloadSetV2(PayloadSetMaterialV2):
    payload_set_sha256: Sha256

    @model_validator(mode="after")
    def validate_payload_set_hash(self) -> PayloadSetV2:
        expected = compute_payload_set_sha256_v2(self)
        if self.payload_set_sha256 != expected:
            raise ValueError("payload_set_sha256 does not match the canonical payload set material")
        return self


def canonical_payload_set_bytes_v2(
    value: PayloadSetMaterialV2 | Mapping[str, object],
) -> bytes:
    """Hash preimage: every validated payload-set field except
    ``payload_set_sha256`` itself -- the same ``*Material*``/self-hashing
    split already established by ``ManifestMaterialV2``/``ManifestV2``,
    ``ChunkPayloadMaterialV2``/``ChunkPayloadV2``, and
    ``SemanticGroupingPolicyMaterialV2``/``SemanticGroupingPolicyV2``, reused
    here rather than reinvented.

    ``payloads`` is re-sorted by ``chunk_id`` here, independent of
    construction order -- the same canonical-ordering discipline every other
    list-of-entries hash in this v2 surface already applies
    (``canonical_semantic_grouping_policy_bytes_v2``'s ``rules``,
    ``canonical_effective_policy_bytes_v2``'s nested lists): entries are
    already validated unique by ``chunk_id`` above, so sorting cannot
    silently collapse two distinct entries, and reordering the constructor's
    input never changes these bytes.
    """

    if isinstance(value, PayloadSetMaterialV2):
        material = value.model_dump(mode="json", exclude={"payload_set_sha256"})
    else:
        if not isinstance(value, Mapping):
            raise TypeError("payload set must be a model or mapping")
        raw = dict(value)
        raw.pop("payload_set_sha256", None)
        material = PayloadSetMaterialV2.model_validate(raw).model_dump(mode="json")

    material = {
        **material,
        "payloads": sorted(material["payloads"], key=lambda entry: entry["chunk_id"]),
    }
    return _canonical_json_bytes_v2(material)


def compute_payload_set_sha256_v2(value: PayloadSetMaterialV2 | Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_payload_set_bytes_v2(value)).hexdigest()


def verify_payload_set_sha256_v2(payload_set: PayloadSetV2) -> None:
    """Revalidate ``payload_set_sha256`` on every read, not just at
    construction time -- mirrors ``contracts_v2.verify_payload_sha256_v2``:
    re-serializing to canonical JSON and re-running full model validation
    catches an object that bypassed ``validate_payload_set_hash`` (for
    example via ``model_construct``), rather than trusting that the
    in-memory object was built through the normal constructor."""

    encoded = _canonical_json_bytes_v2(payload_set.model_dump(mode="json"))
    PayloadSetV2.model_validate_json(encoded, strict=True)


def bind_payload_set_to_manifest_v2(payload_set: PayloadSetV2, manifest: ManifestV2) -> None:
    """The binding function: checks a ``PayloadSetV2`` against a SPECIFIC
    ``ManifestV2`` it was NOT constructed with. Raises
    ``PayloadSetBindingError`` fail-closed if:

    - ``run_id`` does not match the manifest's own ``run_id``
      (``payload_set_run_id_mismatch``);
    - ``manifest_hash`` does not match ``manifest.identity.manifest_hash``
      (``payload_set_manifest_hash_mismatch``);
    - the payload set's ``chunk_id`` set is not EXACTLY the manifest's
      ``chunk_id`` set -- neither missing nor extra
      (``payload_set_chunk_set_mismatch``).
    """

    if payload_set.run_id != manifest.run_id:
        raise PayloadSetBindingError(PAYLOAD_SET_RUN_ID_MISMATCH_REASON_V2)
    if payload_set.manifest_hash != manifest.identity.manifest_hash:
        raise PayloadSetBindingError(PAYLOAD_SET_MANIFEST_HASH_MISMATCH_REASON_V2)

    expected_chunk_ids = {chunk.chunk_id for chunk in manifest.chunks}
    actual_chunk_ids = {entry.chunk_id for entry in payload_set.payloads}
    if actual_chunk_ids != expected_chunk_ids:
        raise PayloadSetBindingError(PAYLOAD_SET_CHUNK_SET_MISMATCH_REASON_V2)
