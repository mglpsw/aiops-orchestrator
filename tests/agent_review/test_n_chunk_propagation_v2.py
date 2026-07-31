"""End-to-end proof that N chunks from one #84 manifest propagate through
the #83 consumer/parser correctly: fragment_id/diff_sha256 provenance
stays cross-referenceable from every ParsedChunkResultV2 back to the
originating ManifestChunkV2/FragmentV2, with no cross-contamination
between chunks and no loss across N > 1.

This is explicitly NOT #86 (dual-target E2E): it uses only the pieces
that already exist (manifest_v2, planner_v2, payload_builder_v2,
consumer_v2, parser_v2) and does not touch synthesizer-v2, telemetry-v2,
or readiness-v2, none of which exist yet as concrete implementations
anywhere in this repository.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.agent_review.consumer_v2 import bind_chunk_response_v2
from app.agent_review.contracts_v2 import (
    ResponseBindingError,
    RunIdentityV2,
    compute_response_sha256_v2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.parser_v2 import parse_bound_chunk_response_v2
from app.agent_review.payload_builder_v2 import build_chunk_payloads_v2
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


def _hunk(path: str, *, start: int = 1, end: int = 10) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=0,
        old_start=start,
        old_end=end,
        new_start=start,
        new_end=end,
        diff_sha256=hashlib.sha256(f"{path}:{start}:{end}".encode()).hexdigest(),
        diff_chars=100,
        must_review=True,
    )


def _three_chunk_manifest() -> ManifestV2:
    """Three distinct files, each forced into its own chunk by a budget
    too small to combine any two of them."""

    hunks = [_hunk("app/a.py"), _hunk("app/b.py"), _hunk("app/c.py")]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=10, max_chunks=10
    )
    assert outcome.state == "planned"

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py", "app/b.py", "app/c.py"],
        "must_review_files": ["app/a.py", "app/b.py", "app/c.py"],
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _success_envelope_for(payload, *, finding_path: str) -> dict[str, object]:  # noqa: ANN001
    envelope: dict[str, object] = {
        "schema_id": "agent-review.chunk-response-envelope.v2",
        "schema_version": 2,
        "source": "agent-review-provider-response",
        "status": "success",
        "run_id": payload.run_id,
        "chunk_id": payload.chunk_id,
        "payload_sha256": payload.payload_sha256,
        "head_sha": payload.identity.head_sha,
        "provider": "openai",
        "model": "gpt-5.4",
        "attempt": 1,
        "request_id": f"req-{payload.chunk_id}",
        "finish_reason": "stop",
        "response_received": True,
        "response_sha256": "9" * 64,
        "result": {
            "schema_id": "agent-review.chunk-response.v2",
            "schema_version": 2,
            "summary": f"review of {payload.chunk_id}",
            "findings": [
                {
                    "finding_id": f"finding-{payload.chunk_id}",
                    "severity": "P2",
                    "title": "issue",
                    "file_path": finding_path,
                    "line_start": 1,
                    "line_end": 2,
                    "evidence": "evidence",
                    "impact": "impact",
                    "confidence": "high",
                    "contract_ids": [],
                    "disposition": "new",
                }
            ],
            "coverage": json.loads(payload.coverage.model_dump_json()),
            "limitations": [],
        },
    }
    envelope["response_sha256"] = compute_response_sha256_v2(envelope)
    return envelope


def _run_full_pipeline(manifest: ManifestV2):  # noqa: ANN201
    built = build_chunk_payloads_v2(manifest)
    parsed_results = []
    for item in built:
        finding_path = item.payload.coverage.expected_files[0]
        envelope = _success_envelope_for(item.payload, finding_path=finding_path)
        bound = bind_chunk_response_v2(envelope=envelope, payload=item.payload)
        parsed_results.append(parse_bound_chunk_response_v2(bound))
    return built, parsed_results


# -- core propagation proof -------------------------------------------------------


def test_n_chunks_all_bind_and_parse_independently() -> None:
    manifest = _three_chunk_manifest()
    assert len(manifest.chunks) == 3, "test setup requires 3 separate chunks"

    built, parsed_results = _run_full_pipeline(manifest)

    assert len(parsed_results) == 3
    assert {p.chunk_id for p in parsed_results} == {c.chunk_id for c in manifest.chunks}
    assert len({p.chunk_id for p in parsed_results}) == 3  # no duplicates, no loss


def test_parsed_chunk_ids_cross_reference_back_to_manifest_fragment_ids() -> None:
    """Provenance is preserved: from each ParsedChunkResultV2, the
    manifest can be consulted (by chunk_id) to recover exactly which
    fragment_ids -- and therefore which diff_sha256 hashes -- that chunk
    was responsible for."""

    manifest = _three_chunk_manifest()
    chunks_by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}
    fragments_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}

    _, parsed_results = _run_full_pipeline(manifest)

    for parsed in parsed_results:
        manifest_chunk = chunks_by_id[parsed.chunk_id]
        fragment_ids = manifest_chunk.fragment_ids
        assert fragment_ids, "every chunk in this fixture carries at least one fragment"
        fragment_paths = {fragments_by_id[fid].path for fid in fragment_ids}
        # Every finding this chunk actually returned must fall within the
        # file scope its own fragments cover (validate_response_binding_v2
        # already enforces this per-chunk; this asserts it holds across
        # all N chunks of the same manifest simultaneously).
        for finding in parsed.findings:
            assert finding.file_path in fragment_paths


def test_findings_across_chunks_never_duplicate_a_fragment_reference() -> None:
    """No chunk's parsed result can claim a finding for a path that
    belongs to a different chunk's fragments -- provenance stays
    partitioned exactly like the manifest's own chunk assignment."""

    manifest = _three_chunk_manifest()
    chunks_by_id = {chunk.chunk_id: chunk for chunk in manifest.chunks}
    fragments_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}

    _, parsed_results = _run_full_pipeline(manifest)

    own_paths_by_chunk = {
        parsed.chunk_id: {
            fragments_by_id[fid].path for fid in chunks_by_id[parsed.chunk_id].fragment_ids
        }
        for parsed in parsed_results
    }
    for parsed in parsed_results:
        other_chunks_paths: set[str] = set()
        for other_chunk_id, paths in own_paths_by_chunk.items():
            if other_chunk_id != parsed.chunk_id:
                other_chunks_paths.update(paths - own_paths_by_chunk[parsed.chunk_id])
        for finding in parsed.findings:
            assert finding.file_path not in other_chunks_paths


# -- cross-contamination is rejected, even within the same N-chunk manifest -----


def test_swapping_two_chunks_responses_within_the_same_manifest_is_rejected() -> None:
    manifest = _three_chunk_manifest()
    built = build_chunk_payloads_v2(manifest)
    assert len(built) >= 2

    payload_a, payload_b = built[0].payload, built[1].payload
    envelope_for_a = _success_envelope_for(payload_a, finding_path=payload_a.coverage.expected_files[0])

    with pytest.raises(ResponseBindingError) as excinfo:
        bind_chunk_response_v2(envelope=envelope_for_a, payload=payload_b)
    assert excinfo.value.reason_code == "chunk_id_mismatch"


# -- determinism across repeated runs of the same manifest -----------------------


def test_full_pipeline_is_deterministic_across_repeated_runs() -> None:
    manifest = _three_chunk_manifest()
    _, first_run = _run_full_pipeline(manifest)
    _, second_run = _run_full_pipeline(manifest)

    first_by_chunk = {p.chunk_id: tuple(f.finding_id for f in p.findings) for p in first_run}
    second_by_chunk = {p.chunk_id: tuple(f.finding_id for f in p.findings) for p in second_run}
    assert first_by_chunk == second_by_chunk
