"""Typed AgentReview v2 fragment manifest.

Bound to `manifest_hash` in `RunIdentityV2` via `contracts_v2`'s existing,
unmodified `canonical_manifest_bytes_v2`/`compute_manifest_hash_v2` --
reused as-is, not reimplemented.

This is the typed contract for issue #84's manifest. It intentionally
covers the manifest's *shape and lossless-coverage invariant* only; git-diff
acquisition (rename/binary/submodule/no-newline handling) and symbol/AST-aware
grouping are separate, larger pieces of #84 not implemented in this slice --
see `docs/AGENT_REVIEW_V2_CHUNKING.md` for exactly what remains.

## Why ManifestMaterialV2 and ManifestV2 are split

`RunIdentityV2.manifest_hash` is meant to certify this manifest's content.
But `ManifestV2` also needs to carry `identity: RunIdentityV2` (so a
consumer can verify which run it belongs to) -- and `identity` itself
contains `manifest_hash`. Hashing "the full manifest, including identity"
would therefore need `identity.manifest_hash` to already be known before it
can be computed, which is circular: the hash and the field that carries it
would depend on each other.

`ChunkPayloadMaterialV2`/`ChunkPayloadV2` in `contracts_v2.py` already solve
the analogous problem for payload hashing by splitting the hashable content
(`*Material*`) from the object that carries its own hash
(`payload_sha256`). This module applies the same split, but the field
being kept out of the hash preimage lives on a *different* object
(`identity.manifest_hash`), not on `ManifestV2` itself -- so the split has
to happen one level up: `ManifestMaterialV2` carries no `run_id`/`identity`
at all; only `ManifestV2` (which extends it) does, and its own validator
proves `identity.manifest_hash` equals the hash of the material portion
alone.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.agent_review.contracts_v2 import (
    ContractV2Model,
    PositiveInt,
    RelativePath,
    RunIdentityV2,
    SafeIdentifier,
    SafeText,
    SemanticGroupValue,
    Sha256,
    compute_manifest_hash_v2,
    compute_run_id,
)

MANIFEST_SCHEMA_V2 = "agent-review.manifest.v2"

NonNegativeInt = Annotated[int, Field(ge=0)]

DegradationReasonValueV2 = Literal[
    "budget_exhausted", "artifact_missing", "transport_failure", "schema_failure", "model_uncertainty"
]


def _canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class LineRangeV2(ContractV2Model):
    start: NonNegativeInt
    end: NonNegativeInt

    @model_validator(mode="after")
    def validate_order(self) -> LineRangeV2:
        if self.end < self.start:
            raise ValueError("range end must not precede start")
        return self


def compute_fragment_id_v2(
    *, path: str, old_range: LineRangeV2, new_range: LineRangeV2, diff_sha256: str
) -> str:
    """``sha256(path|old_range|new_range|diff_sha256)`` in canonical JSON --
    the exact preimage sketched in issue #84."""

    material = {
        "path": path,
        "old_range": {"start": old_range.start, "end": old_range.end},
        "new_range": {"start": new_range.start, "end": new_range.end},
        "diff_sha256": diff_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes_v2(material)).hexdigest()


class FragmentV2(ContractV2Model):
    fragment_id: Sha256
    path: RelativePath
    old_range: LineRangeV2
    new_range: LineRangeV2
    hunk_indexes: list[NonNegativeInt]
    diff_chars: NonNegativeInt
    diff_sha256: Sha256
    coverage_required: bool

    @model_validator(mode="after")
    def validate_fragment_id(self) -> FragmentV2:
        expected = compute_fragment_id_v2(
            path=self.path,
            old_range=self.old_range,
            new_range=self.new_range,
            diff_sha256=self.diff_sha256,
        )
        if self.fragment_id != expected:
            raise ValueError("fragment_id does not match its canonical preimage")
        if not self.hunk_indexes:
            raise ValueError("a fragment must reference at least one hunk index")
        if len(self.hunk_indexes) != len(set(self.hunk_indexes)):
            raise ValueError("hunk_indexes must be unique")
        return self


class ManifestChunkV2(ContractV2Model):
    chunk_id: SafeIdentifier
    order_index: NonNegativeInt
    semantic_group: SemanticGroupValue
    fragment_ids: list[Sha256]
    payload_sha256: Sha256 | None

    @model_validator(mode="after")
    def validate_fragment_ids(self) -> ManifestChunkV2:
        if not self.fragment_ids:
            raise ValueError("a chunk must reference at least one fragment")
        if len(self.fragment_ids) != len(set(self.fragment_ids)):
            raise ValueError("fragment_ids must be unique within a chunk")
        return self


class ManifestDegradationV2(ContractV2Model):
    reason_code: DegradationReasonValueV2
    affected_fragment_ids: list[Sha256]
    detail: SafeText

    @model_validator(mode="after")
    def validate_affected_fragments(self) -> ManifestDegradationV2:
        if not self.affected_fragment_ids:
            raise ValueError("a degradation cause must identify at least one affected fragment")
        if len(self.affected_fragment_ids) != len(set(self.affected_fragment_ids)):
            raise ValueError("affected_fragment_ids must be unique")
        return self


class ManifestMaterialV2(ContractV2Model):
    """The manifest's hashable content -- deliberately without `run_id` or
    `identity`. See the module docstring for why: `RunIdentityV2` embeds
    `manifest_hash`, so a hash preimage that included `identity` would be
    circular. `compute_manifest_hash_v2_for` hashes exactly this shape."""

    schema_id: Literal["agent-review.manifest.v2"]
    schema_version: Literal[2]
    source: Literal["aiops-review-plan-chunks-v2"]
    expected_files: list[RelativePath]
    must_review_files: list[RelativePath]
    fragments: list[FragmentV2]
    chunks: list[ManifestChunkV2]
    max_chunks: PositiveInt
    degradation_causes: list[ManifestDegradationV2]

    @model_validator(mode="after")
    def validate_material(self) -> ManifestMaterialV2:
        fragment_ids = [fragment.fragment_id for fragment in self.fragments]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("fragment_id values must be unique")

        expected = set(self.expected_files)
        fragment_paths = {fragment.path for fragment in self.fragments}
        if not fragment_paths <= expected:
            raise ValueError("every fragment path must belong to expected_files")
        must_review = set(self.must_review_files)
        if not must_review <= expected:
            raise ValueError("must_review_files must be a subset of expected_files")
        # A must-review path being a subset of expected_files is not enough:
        # nothing so far requires that path to actually have any
        # coverage_required fragment at all. Without this, a producer could
        # list a path in must_review_files while emitting zero fragments for
        # it (or only coverage_required=False ones), and the manifest would
        # still validate -- the must-review guarantee would be a no-op for
        # that file. Every must-review path must be represented by at least
        # one required fragment.
        required_paths = {fragment.path for fragment in self.fragments if fragment.coverage_required}
        if not must_review <= required_paths:
            raise ValueError(
                "every must_review_files path must be represented by at least one "
                "coverage_required fragment"
            )

        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("chunk_id values must be unique")
        # Mirrors the existing v1 precedent (chunk_payload_builder.py
        # rejects duplicate order indexes): two chunks sharing the same
        # order_index leave execution order ambiguous for any consumer that
        # relies on it, and nothing else in this contract catches that.
        order_indexes = [chunk.order_index for chunk in self.chunks]
        if len(order_indexes) != len(set(order_indexes)):
            raise ValueError("chunk order_index values must be unique")
        if len(self.chunks) > self.max_chunks:
            raise ValueError("chunk count exceeds max_chunks")

        known_fragment_ids = set(fragment_ids)
        assignment_count: dict[str, int] = {}
        for chunk in self.chunks:
            for fragment_id in chunk.fragment_ids:
                if fragment_id not in known_fragment_ids:
                    raise ValueError("a chunk references an unknown fragment_id")
                assignment_count[fragment_id] = assignment_count.get(fragment_id, 0) + 1

        duplicated = {fid for fid, count in assignment_count.items() if count > 1}
        if duplicated:
            raise ValueError("a fragment must not be assigned to more than one chunk")

        # Losslessness: every fragment marked coverage_required must be
        # assigned to exactly one chunk, unless a degradation cause
        # explicitly and completely accounts for its omission. There is no
        # silent partial approval: an unexplained gap is a validation error.
        required_ids = {fragment.fragment_id for fragment in self.fragments if fragment.coverage_required}
        missing_required = required_ids - set(assignment_count)
        degraded_ids: set[str] = set()
        for cause in self.degradation_causes:
            degraded_ids.update(cause.affected_fragment_ids)
        if missing_required - degraded_ids:
            raise ValueError(
                "coverage_required fragments are omitted from chunks without a "
                "degradation cause accounting for them"
            )
        # A degradation cause must reference exactly the missing (omitted)
        # required fragments -- not merely any coverage_required fragment.
        # Without this, a fragment already assigned to a chunk could also
        # be named in a degradation cause, producing a manifest that
        # contradictorily claims both "covered" and "omitted" for the same
        # fragment -- exactly the kind of ambiguity that could later block
        # a clean readiness result even though coverage is actually
        # complete. ChunkCoverageV2's own partition rules apply the same
        # restriction (degradation causes may reference only partial/
        # missing files), so this mirrors existing contracts_v2 practice.
        if degraded_ids != missing_required:
            raise ValueError(
                "degradation causes must reference exactly the coverage_required "
                "fragments that are missing from every chunk -- not an already-"
                "covered fragment, and not a fragment that is not coverage_required"
            )

        return self


def compute_manifest_hash_v2_for(material: ManifestMaterialV2) -> str:
    """``manifest_hash`` for ``RunIdentityV2``, via ``contracts_v2``'s
    existing, unmodified ``compute_manifest_hash_v2``.

    Hashes only the material fields (``run_id``/``identity`` excluded when
    present), so this is safe to call on either a bare
    ``ManifestMaterialV2`` or a full ``ManifestV2`` -- the latter's
    ``run_id``/``identity`` fields are stripped before hashing, breaking
    the circularity described in the module docstring.
    """

    return compute_manifest_hash_v2(material.model_dump(mode="json", exclude={"run_id", "identity"}))


class ManifestV2(ManifestMaterialV2):
    run_id: Sha256
    identity: RunIdentityV2

    @model_validator(mode="after")
    def validate_identity_binding(self) -> ManifestV2:
        if self.run_id != compute_run_id(self.identity):
            raise ValueError("run_id does not match the canonical run identity")
        expected_manifest_hash = compute_manifest_hash_v2_for(self)
        if self.identity.manifest_hash != expected_manifest_hash:
            raise ValueError(
                "identity.manifest_hash does not match the canonical hash of this "
                "manifest's own content"
            )
        return self
