from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import (
    ManifestChunkV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.payload_builder_v2 import (
    CHUNK_NOT_IN_MANIFEST_REASON_V2,
    PayloadBuilderError,
    build_chunk_payload_v2,
    build_chunk_payloads_v2,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 84,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _hunk(path: str, *, index: int = 0, start: int = 1, end: int = 10, must_review: bool = True) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=index,
        old_start=start,
        old_end=end,
        new_start=start,
        new_end=end,
        diff_sha256=hashlib.sha256(f"{path}:{index}:{start}:{end}".encode()).hexdigest(),
        diff_chars=100,
        must_review=must_review,
    )


def _build_manifest(
    hunks: list[HunkInputV2],
    *,
    expected_files: list[str],
    must_review_files: list[str],
    max_lines_per_chunk: int = 100,
    max_chunks: int = 10,
    semantic_group: str = "primary_backend_logic",
) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        hunks,
        semantic_group=semantic_group,
        max_lines_per_chunk=max_lines_per_chunk,
        max_chunks=max_chunks,
    )
    assert outcome.state == "planned", "test setup requires a planned outcome"

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files,
        "must_review_files": must_review_files,
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": max_chunks,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


# -- basic building -----------------------------------------------------------


def test_build_chunk_payloads_produces_one_payload_per_chunk() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")],
        expected_files=["app/a.py", "app/b.py"],
        must_review_files=["app/a.py", "app/b.py"],
    )
    built = build_chunk_payloads_v2(manifest)
    assert len(built) == len(manifest.chunks)
    assert {item.chunk_id for item in built} == {chunk.chunk_id for chunk in manifest.chunks}


def test_built_payload_is_a_valid_chunk_payload_v2() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    built = build_chunk_payloads_v2(manifest)
    payload = built[0].payload
    assert payload.run_id == manifest.run_id
    assert payload.identity == manifest.identity
    assert payload.chunk_id == manifest.chunks[0].chunk_id


def test_built_payload_hash_is_reproducible() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    payload_1 = build_chunk_payload_v2(manifest, manifest.chunks[0])
    payload_2 = build_chunk_payload_v2(manifest, manifest.chunks[0])
    assert payload_1.payload_sha256 == payload_2.payload_sha256
    # And it must independently re-validate through the frozen v2 contract
    # (payload_sha256 must match compute_payload_sha256_v2(payload_1)).
    from app.agent_review.contracts_v2 import verify_payload_sha256_v2

    verify_payload_sha256_v2(payload_1)  # raises if the hash is wrong


# -- coverage: whole-file chunk (not split) -------------------------------------


def test_a_chunk_with_every_fragment_of_a_file_reports_it_as_reviewed() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py", start=1, end=50)],
        expected_files=["app/a.py"],
        must_review_files=["app/a.py"],
        max_lines_per_chunk=100,
    )
    assert len(manifest.chunks) == 1
    payload = build_chunk_payload_v2(manifest, manifest.chunks[0])
    assert payload.coverage.status.value == "complete"
    assert payload.coverage.reviewed_files == ["app/a.py"]
    assert payload.coverage.partially_reviewed_files == []
    assert payload.coverage.missing_must_review_files == []


# -- coverage: a file split across multiple chunks (the core #84 case) ---------


def test_a_chunk_with_only_some_of_a_files_fragments_reports_it_as_partial() -> None:
    """The documented coverage-granularity gap: ChunkCoverageV2 is
    file-granular, FragmentV2 is line-range-granular. When one file's
    fragments are split across chunks, each chunk must honestly report
    that file as partially_reviewed, never reviewed."""

    manifest = _build_manifest(
        [_hunk("app/big.py", start=1, end=200)],
        expected_files=["app/big.py"],
        must_review_files=["app/big.py"],
        max_lines_per_chunk=100,
        max_chunks=2,
    )
    assert len(manifest.chunks) == 2  # confirms the file really was split
    for chunk in manifest.chunks:
        payload = build_chunk_payload_v2(manifest, chunk)
        assert payload.coverage.status.value == "partial"
        assert payload.coverage.partially_reviewed_files == ["app/big.py"]
        assert payload.coverage.reviewed_files == []
        assert payload.coverage.missing_must_review_files == ["app/big.py"]


def test_a_chunk_never_reports_reviewed_for_a_file_it_does_not_fully_own() -> None:
    """Even when a chunk carries the majority of a split file's content,
    if it does not carry ALL of that file's fragments, it must not claim
    'reviewed' -- only 'partially_reviewed'."""

    manifest = _build_manifest(
        [_hunk("app/big.py", start=1, end=250)],
        expected_files=["app/big.py"],
        must_review_files=["app/big.py"],
        max_lines_per_chunk=100,
        max_chunks=3,
    )
    assert len(manifest.chunks) == 3
    for chunk in manifest.chunks:
        payload = build_chunk_payload_v2(manifest, chunk)
        assert "app/big.py" not in payload.coverage.reviewed_files


# -- must_review propagation ------------------------------------------------------


def test_payload_coverage_only_flags_must_review_files_actually_in_this_chunk() -> None:
    manifest = _build_manifest(
        [_hunk("app/required.py"), _hunk("app/other.py", must_review=False)],
        expected_files=["app/required.py", "app/other.py"],
        must_review_files=["app/required.py"],
    )
    for chunk in manifest.chunks:
        payload = build_chunk_payload_v2(manifest, chunk)
        assert set(payload.coverage.must_review_files) <= {"app/required.py"}


# -- error handling -----------------------------------------------------------


def test_build_chunk_payload_rejects_a_chunk_not_from_this_manifest() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    foreign_chunk = ManifestChunkV2(
        chunk_id="chunk-foreign",
        order_index=99,
        semantic_group="primary_backend_logic",
        fragment_ids=[manifest.fragments[0].fragment_id],
        payload_sha256=None,
    )
    with pytest.raises(PayloadBuilderError) as excinfo:
        build_chunk_payload_v2(manifest, foreign_chunk)
    assert excinfo.value.reason_code == CHUNK_NOT_IN_MANIFEST_REASON_V2


# -- determinism / multi-chunk propagation ----------------------------------------


def test_build_chunk_payloads_preserves_fragment_and_chunk_identity_across_n_chunks() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py"), _hunk("app/c.py")],
        expected_files=["app/a.py", "app/b.py", "app/c.py"],
        must_review_files=["app/a.py", "app/b.py", "app/c.py"],
        max_lines_per_chunk=5,
        max_chunks=10,
    )
    assert len(manifest.chunks) >= 2  # forced into multiple chunks by a tiny budget
    built = build_chunk_payloads_v2(manifest)
    seen_paths: set[str] = set()
    for item in built:
        seen_paths.update(item.payload.coverage.expected_files)
    assert seen_paths == {"app/a.py", "app/b.py", "app/c.py"}
    # Every payload_sha256 must be unique per chunk (different coverage/
    # chunk_id material).
    hashes = [item.payload.payload_sha256 for item in built]
    assert len(hashes) == len(set(hashes))
