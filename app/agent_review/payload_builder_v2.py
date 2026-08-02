"""Build ``ChunkPayloadV2`` objects from a planned ``ManifestV2`` (#84).

Turns each ``ManifestChunkV2`` in a `planned` manifest (see
``planner_v2.plan_lossless_chunks_v2``) into an actual
``contracts_v2.ChunkPayloadV2``, reusing ``compute_payload_sha256_v2``
unmodified -- the same hashing authority ``consumer_v2.py`` (#83) already
binds responses against.

## Coverage granularity gap (documented, not solved here)

``contracts_v2.ChunkCoverageV2`` (frozen by PR #81) is file-granular:
a file is ``reviewed``, ``partially_reviewed``, or ``missing`` as a whole.
``FragmentV2`` (#84) is line-range-granular: a single file's changed lines
can be split across several fragments, possibly assigned to *different*
chunks (that is #84's whole point -- lossless multi-chunk planning for
large files). These two granularities do not fully compose:

* when every fragment for a path is assigned to the same chunk, that
  chunk's payload coverage correctly reports the path as ``reviewed``;
* when a path's fragments are split across multiple chunks, each
  individual chunk's payload coverage reports that path as
  ``partially_reviewed`` (it only carries *some* of that file's
  fragments) -- accurate, but coarser than the fragment-level truth the
  manifest itself already records precisely.

Resolving this fully would mean either extending ``ChunkCoverageV2`` with
line-range awareness (touching the frozen v2 foundation, avoided
throughout #83/#85/#84) or introducing a new payload-level fragment
reference contract. Neither is attempted here; this module produces
honest, coarser-than-ideal file-level coverage that never claims more than
what ``ChunkCoverageV2``'s existing shape can represent.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ChunkPayloadV2,
    PayloadArtifactReferenceV2,
    PayloadContractReferenceV2,
    TargetProfileV2,
    compute_payload_sha256_v2,
)
from app.agent_review.manifest_v2 import ManifestChunkV2, ManifestV2
from app.agent_review.payload_references_v2 import (
    build_payload_artifact_references_v2,
    build_payload_contract_references_v2,
)


class PayloadBuilderError(ValueError):
    """Raised when a payload cannot be built for a manifest chunk. Carries
    a stable ``reason_code`` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


CHUNK_NOT_IN_MANIFEST_REASON_V2 = "chunk_not_in_manifest"


@dataclass(frozen=True)
class BuiltChunkPayloadV2:
    chunk_id: str
    payload: ChunkPayloadV2
    limitations: tuple[str, ...] = ()


def _build_chunk_coverage(manifest: ManifestV2, chunk: ManifestChunkV2) -> ChunkCoverageV2:
    fragment_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
    chunk_fragments = [fragment_by_id[fid] for fid in chunk.fragment_ids]
    chunk_paths = sorted({fragment.path for fragment in chunk_fragments})

    fragments_by_path: dict[str, list[str]] = {}
    for fragment in manifest.fragments:
        fragments_by_path.setdefault(fragment.path, []).append(fragment.fragment_id)

    reviewed: list[str] = []
    partially_reviewed: list[str] = []
    for path in chunk_paths:
        assigned_here = {fid for fid in chunk.fragment_ids} & set(fragments_by_path[path])
        if len(assigned_here) == len(fragments_by_path[path]):
            reviewed.append(path)
        else:
            partially_reviewed.append(path)

    must_review = sorted(set(manifest.must_review_files) & set(chunk_paths))
    missing_must_review = sorted(set(must_review) & set(partially_reviewed))

    status = "complete" if not partially_reviewed else "partial"

    return ChunkCoverageV2(
        status=status,
        expected_files=chunk_paths,
        reviewed_files=sorted(reviewed),
        partially_reviewed_files=sorted(partially_reviewed),
        missing_files=[],
        must_review_files=must_review,
        missing_must_review_files=missing_must_review,
        degradation_causes=[],
    )


def _resolve_manifest_chunk(manifest: ManifestV2, chunk: ManifestChunkV2) -> ManifestChunkV2:
    chunk_by_id = {c.chunk_id: c for c in manifest.chunks}
    manifest_chunk = chunk_by_id.get(chunk.chunk_id)
    if manifest_chunk is None or manifest_chunk != chunk:
        # Matching by id alone would let a caller pass a chunk that
        # shares an id with a real manifest entry but carries different
        # fragment_ids/semantic_group -- building a correctly hashed
        # payload for a forged scope that the response binder would still
        # accept under the real manifest's identity. Only the manifest's
        # own chunk record is ever used past this point.
        raise PayloadBuilderError(CHUNK_NOT_IN_MANIFEST_REASON_V2)
    return manifest_chunk


def _build_chunk_payload(
    manifest: ManifestV2,
    chunk: ManifestChunkV2,
    *,
    artifact_references: list[PayloadArtifactReferenceV2],
    contract_references: list[PayloadContractReferenceV2],
) -> ChunkPayloadV2:
    coverage = _build_chunk_coverage(manifest, chunk)
    material = {
        "schema_id": "agent-review.chunk-payload.v2",
        "schema_version": 2,
        "source": "aiops-review-build-payloads",
        "run_id": manifest.run_id,
        "identity": manifest.identity.model_dump(mode="json"),
        "chunk_id": chunk.chunk_id,
        "semantic_group": chunk.semantic_group,
        "coverage": coverage.model_dump(mode="json"),
        "artifact_references": [ref.model_dump(mode="json") for ref in artifact_references],
        "contract_references": [ref.model_dump(mode="json") for ref in contract_references],
    }
    payload_sha256 = compute_payload_sha256_v2(material)
    return ChunkPayloadV2.model_validate({**material, "payload_sha256": payload_sha256})


def build_chunk_payload_v2(manifest: ManifestV2, chunk: ManifestChunkV2) -> ChunkPayloadV2:
    """Build a single ``ChunkPayloadV2`` for one chunk of a planned
    manifest, with EMPTY ``artifact_references``/``contract_references``.

    Kept as its own zero-argument-beyond-manifest entry point, unchanged in
    signature and behavior, for every existing caller that has no
    ``TargetProfileV2``/checkout to derive real references from. Use
    ``build_chunk_payload_from_profile_v2`` (#131) when real,
    profile-derived references are available and required."""

    chunk = _resolve_manifest_chunk(manifest, chunk)
    return _build_chunk_payload(manifest, chunk, artifact_references=[], contract_references=[])


def build_chunk_payloads_v2(manifest: ManifestV2) -> tuple[BuiltChunkPayloadV2, ...]:
    """Build a ``ChunkPayloadV2`` for every chunk in a planned manifest.

    The manifest itself is not mutated: ``ManifestChunkV2.payload_sha256``
    stays ``None`` on the input manifest (it is frozen, like every other
    v2 contract model).

    **Not attempted, and not a well-defined operation with the current
    contracts:** inserting a built ``payload_sha256`` back into a chunk and
    rebuilding a new ``ManifestV2`` from it. ``ManifestChunkV2.payload_sha256``
    is itself part of ``ManifestMaterialV2``'s hash preimage
    (``compute_manifest_hash_v2_for`` only excludes ``run_id``/``identity``),
    so setting it changes ``manifest_hash``, which forces a new
    ``identity``/``run_id`` -- and ``run_id``/``identity`` are themselves
    embedded verbatim in each payload's own material
    (``compute_payload_sha256_v2``), so the just-inserted hash would no
    longer match what the payload's material actually hashes to. This is a
    genuine circular dependency, not a mutation-discipline detail; no
    non-circular preimage or lifecycle for it is defined here.
    """

    return tuple(
        BuiltChunkPayloadV2(chunk_id=chunk.chunk_id, payload=build_chunk_payload_v2(manifest, chunk))
        for chunk in manifest.chunks
    )


def build_chunk_payload_from_profile_v2(
    manifest: ManifestV2, chunk: ManifestChunkV2, *, profile: TargetProfileV2, repo_root: Path
) -> BuiltChunkPayloadV2:
    """Build a single ``ChunkPayloadV2`` with REAL, profile-derived
    ``artifact_references``/``contract_references`` (#131), read from
    ``repo_root`` per ``profile.artifacts``/``profile.contracts``.

    Every chunk of the same manifest+profile shares the identical
    reference set -- ``TargetArtifactV2``/``TargetContractV2`` are
    profile-level, not chunk-scoped, and nothing in either type associates
    a specific chunk or semantic group with a reference, so no per-chunk
    filtering is attempted here.

    ``limitations`` carries every optional artifact that was missing
    (``build_payload_artifact_references_v2``'s own return value) --
    surfaced the same way #107's ``SynthesisResultV2.limitations`` already
    documents dropped-but-not-fatal facts, never silently absorbed.
    """

    chunk = _resolve_manifest_chunk(manifest, chunk)
    artifact_references, limitations = build_payload_artifact_references_v2(profile, repo_root)
    contract_references = build_payload_contract_references_v2(profile, repo_root)
    payload = _build_chunk_payload(
        manifest, chunk, artifact_references=artifact_references, contract_references=contract_references
    )
    return BuiltChunkPayloadV2(chunk_id=chunk.chunk_id, payload=payload, limitations=limitations)


def build_chunk_payloads_from_profile_v2(
    manifest: ManifestV2, *, profile: TargetProfileV2, repo_root: Path
) -> tuple[BuiltChunkPayloadV2, ...]:
    """Build a ``ChunkPayloadV2`` with real profile-derived references for
    every chunk in a planned manifest. Reads artifact/contract content from
    ``repo_root`` exactly once, reused across every chunk -- the reference
    set does not vary per chunk (see ``build_chunk_payload_from_profile_v2``)."""

    artifact_references, limitations = build_payload_artifact_references_v2(profile, repo_root)
    contract_references = build_payload_contract_references_v2(profile, repo_root)
    return tuple(
        BuiltChunkPayloadV2(
            chunk_id=chunk.chunk_id,
            payload=_build_chunk_payload(
                manifest,
                _resolve_manifest_chunk(manifest, chunk),
                artifact_references=artifact_references,
                contract_references=contract_references,
            ),
            limitations=limitations,
        )
        for chunk in manifest.chunks
    )
