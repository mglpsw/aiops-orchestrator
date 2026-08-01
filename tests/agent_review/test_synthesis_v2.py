from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ChunkFindingV2,
    FindingConfidenceV2,
    FindingDispositionV2,
    FindingSeverityV2,
    RunIdentityV2,
    compute_run_id,
)
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestDegradationV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2
from app.agent_review.run_fragment_coverage_v2 import (
    FragmentCoverageReasonV2,
    FragmentCoverageStatusV2,
    bind_coverage_report_to_manifest_v2,
)
from app.agent_review.synthesis_v2 import (
    CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2,
    CHUNK_RESULT_HEAD_MISMATCH_REASON_V2,
    CROSS_RUN_CHUNK_RESULT_REASON_V2,
    DUPLICATE_CHUNK_RESULT_REASON_V2,
    FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2,
    INVALID_CHUNK_RESULT_TYPE_REASON_V2,
    SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2,
    UNKNOWN_CHUNK_RESULT_REASON_V2,
    SynthesisErrorV2,
    synthesize_chunk_results_v2,
)


# -- fixture helpers ----------------------------------------------------------


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 107,
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


def _hunk(path: str, *, index: int = 0, start: int = 1, end: int = 10) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=index,
        old_start=start,
        old_end=end,
        new_start=start,
        new_end=end,
        diff_sha256=hashlib.sha256(f"{path}:{index}:{start}:{end}".encode()).hexdigest(),
        diff_chars=100,
        must_review=True,
    )


def _build_manifest(
    hunks: list[HunkInputV2], *, expected_files: list[str], max_lines_per_chunk: int = 100, max_chunks: int = 10
) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=max_lines_per_chunk, max_chunks=max_chunks
    )
    assert outcome.state == "planned"
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": expected_files,
        "must_review_files": expected_files,
        "fragments": list(outcome.fragments),
        "chunks": list(outcome.chunks),
        "max_chunks": max_chunks,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _build_two_chunk_split_manifest() -> ManifestV2:
    """A hand-built manifest with ONE path whose two fragments are
    genuinely assigned to TWO different chunks."""

    def _fragment(seed: bytes, path: str, start: int) -> FragmentV2:
        diff_sha = hashlib.sha256(seed).hexdigest()
        old_range = LineRangeV2(start=start, end=start + 4)
        new_range = LineRangeV2(start=start, end=start + 4)
        fragment_id = compute_fragment_id_v2(path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha)
        return FragmentV2(
            fragment_id=fragment_id, path=path, old_range=old_range, new_range=new_range,
            hunk_indexes=[0], diff_chars=10, diff_sha256=diff_sha, coverage_required=True,
        )

    fragment_1 = _fragment(b"split-1", "app/a.py", 1)
    fragment_2 = _fragment(b"split-2", "app/a.py", 10)
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py"],
        "must_review_files": ["app/a.py"],
        "fragments": [fragment_1, fragment_2],
        "chunks": [
            ManifestChunkV2(chunk_id="chunk-0", order_index=0, semantic_group="primary_backend_logic",
                             fragment_ids=[fragment_1.fragment_id], payload_sha256=None),
            ManifestChunkV2(chunk_id="chunk-1", order_index=1, semantic_group="primary_backend_logic",
                             fragment_ids=[fragment_2.fragment_id], payload_sha256=None),
        ],
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _build_manifest_with_degradation() -> ManifestV2:
    def _fragment(seed: bytes, path: str) -> FragmentV2:
        diff_sha = hashlib.sha256(seed).hexdigest()
        old_range = LineRangeV2(start=1, end=5)
        new_range = LineRangeV2(start=1, end=5)
        fragment_id = compute_fragment_id_v2(path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha)
        return FragmentV2(
            fragment_id=fragment_id, path=path, old_range=old_range, new_range=new_range,
            hunk_indexes=[0], diff_chars=10, diff_sha256=diff_sha, coverage_required=True,
        )

    ok_fragment = _fragment(b"ok", "app/a.py")
    degraded_fragment = _fragment(b"degraded", "app/a.py")
    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py"],
        "must_review_files": ["app/a.py"],
        "fragments": [ok_fragment, degraded_fragment],
        "chunks": [
            ManifestChunkV2(chunk_id="chunk-0", order_index=0, semantic_group="primary_backend_logic",
                             fragment_ids=[ok_fragment.fragment_id], payload_sha256=None)
        ],
        "max_chunks": 10,
        "degradation_causes": [
            ManifestDegradationV2(reason_code="budget_exhausted", affected_fragment_ids=[degraded_fragment.fragment_id],
                                   detail="deliberately degraded for a synthesis regression")
        ],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity), ok_fragment.fragment_id, degraded_fragment.fragment_id


def _coverage(paths: list[str], *, reviewed: list[str] = (), partial: list[str] = (), missing: list[str] = ()) -> ChunkCoverageV2:
    reviewed, partial, missing = list(reviewed), list(partial), list(missing)
    covered = set(reviewed) | set(partial) | set(missing)
    if covered != set(paths):
        missing = missing + [p for p in paths if p not in covered]
    status = "complete" if not partial and not missing else "partial"
    return ChunkCoverageV2(
        status=status, expected_files=paths, reviewed_files=reviewed, partially_reviewed_files=partial,
        missing_files=missing, must_review_files=paths, missing_must_review_files=[p for p in paths if p in (partial + missing)],
        degradation_causes=[],
    )


def _result(
    *, run_id: str, chunk_id: str, head_sha: str, findings: tuple = (), coverage: ChunkCoverageV2, limitations: tuple[str, ...] = ()
) -> ParsedChunkResultV2:
    return ParsedChunkResultV2(
        run_id=run_id, chunk_id=chunk_id, head_sha=head_sha, summary="s", findings=findings, coverage=coverage,
        limitations=limitations,
    )


def _finding(
    *, finding_id: str, path: str, line_start: int = 1, line_end: int = 5, contract_ids: list[str] = ("c1",),
    severity: FindingSeverityV2 = FindingSeverityV2.P1,
) -> ChunkFindingV2:
    return ChunkFindingV2(
        finding_id=finding_id, severity=severity, title="finding", file_path=path,
        line_start=line_start, line_end=line_end, evidence="ev", impact="impact",
        confidence=FindingConfidenceV2.HIGH, contract_ids=list(contract_ids), disposition=FindingDispositionV2.NEW,
    )


# -- non-divided path: reviewed reaches run-level reviewed ---------------------


def test_a_non_divided_fully_reviewed_path_reaches_run_level_reviewed() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    entry = synthesis.coverage_report.paths[0]
    assert entry.status is FragmentCoverageStatusV2.REVIEWED
    assert entry.reason_codes == []


# -- structural split: never reviewed, even if every chunk says reviewed ------


def test_a_structurally_divided_path_never_reaches_reviewed_even_if_both_chunks_say_reviewed() -> None:
    manifest = _build_two_chunk_split_manifest()
    result_0 = _result(run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    result_1 = _result(run_id=manifest.run_id, chunk_id="chunk-1", head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=[result_0, result_1], evaluated_head_sha=manifest.identity.head_sha
    )
    entry = synthesis.coverage_report.paths[0]
    assert entry.status is FragmentCoverageStatusV2.PARTIAL
    assert FragmentCoverageReasonV2.STRUCTURAL_SPLIT in entry.reason_codes
    assert entry.reviewed_fragment_ids == []
    assert set(entry.partially_reviewed_fragment_ids) == set(entry.expected_fragment_ids)
    # the produced report must itself bind cleanly against the manifest it describes
    bind_coverage_report_to_manifest_v2(synthesis.coverage_report, manifest)


def test_a_structurally_divided_path_missing_must_review_blocks_ready_by_construction() -> None:
    """A divided must_review path can never be fully reviewed at the run
    level -- this is what makes ready structurally impossible for it later
    at the readiness layer (#108); this test only proves the coverage
    report entry itself is never 'reviewed' for a divided must_review path."""

    manifest = _build_two_chunk_split_manifest()
    assert "app/a.py" in manifest.must_review_files
    result_0 = _result(run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    result_1 = _result(run_id=manifest.run_id, chunk_id="chunk-1", head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], missing=["app/a.py"]))
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=[result_0, result_1], evaluated_head_sha=manifest.identity.head_sha
    )
    entry = synthesis.coverage_report.paths[0]
    assert entry.status is not FragmentCoverageStatusV2.REVIEWED


# -- fragment degradation propagation ------------------------------------------


def test_a_degraded_fragment_never_reaches_reviewed_and_propagates_its_cause() -> None:
    manifest, ok_fragment_id, degraded_fragment_id = _build_manifest_with_degradation()
    result = _result(run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
                      coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    entry = synthesis.coverage_report.paths[0]
    assert entry.status is not FragmentCoverageStatusV2.REVIEWED
    assert degraded_fragment_id in entry.missing_fragment_ids
    assert FragmentCoverageReasonV2.FRAGMENT_DEGRADED in entry.reason_codes
    bind_coverage_report_to_manifest_v2(synthesis.coverage_report, manifest)


# -- a chunk that never produced a bound result (error envelope, upstream) ----


def test_an_unavailable_chunk_caps_its_paths_at_missing_never_reviewed() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    # no chunk_results at all -- the one real chunk never bound successfully
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[], evaluated_head_sha=manifest.identity.head_sha)
    entry = synthesis.coverage_report.paths[0]
    assert entry.status is FragmentCoverageStatusV2.MISSING
    assert FragmentCoverageReasonV2.CHUNK_UNAVAILABLE in entry.reason_codes


# -- input validation: fail-closed on anything but genuine v2 bound results ---


def test_rejects_a_chunk_result_from_a_different_run() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    foreign_result = _result(run_id="f" * 64, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
                              coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[foreign_result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == CROSS_RUN_CHUNK_RESULT_REASON_V2


def test_rejects_a_raw_dict_instead_of_a_parsed_chunk_result() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[{"run_id": manifest.run_id}], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == INVALID_CHUNK_RESULT_TYPE_REASON_V2


def test_rejects_a_v1_style_object_instead_of_a_parsed_chunk_result() -> None:
    class FakeV1Result:
        run_id = "x"

    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[FakeV1Result()], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == INVALID_CHUNK_RESULT_TYPE_REASON_V2


def test_rejects_duplicate_chunk_results_for_the_same_chunk_id() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    result_1 = _result(run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    result_2 = _result(run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
                        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]))
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result_1, result_2], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == DUPLICATE_CHUNK_RESULT_REASON_V2


# -- determinism: reordering chunk_results changes neither the coverage -------
# -- report's hash nor the semantic findings result. --------------------------


def test_reordering_chunk_results_does_not_change_the_coverage_report_hash() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"],
        max_lines_per_chunk=10,
    )
    chunk_by_fragment = {fid: c.chunk_id for c in manifest.chunks for fid in c.fragment_ids}
    fragment_by_path = {f.path: f for f in manifest.fragments}
    results = [
        _result(
            run_id=manifest.run_id, chunk_id=chunk_by_fragment[fragment_by_path[path].fragment_id],
            head_sha=manifest.identity.head_sha, coverage=_coverage([path], reviewed=[path]),
        )
        for path in ("app/a.py", "app/b.py")
    ]
    forward = synthesize_chunk_results_v2(manifest=manifest, chunk_results=results, evaluated_head_sha=manifest.identity.head_sha)
    backward = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=list(reversed(results)), evaluated_head_sha=manifest.identity.head_sha
    )
    assert forward.coverage_report.coverage_report_sha256 == backward.coverage_report.coverage_report_sha256
    assert forward.findings == backward.findings


# -- coverage report self-consistency: always binds to its own manifest -------


def test_synthesized_coverage_report_always_binds_to_its_manifest() -> None:
    manifest = _build_manifest([_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"])
    result = _result(
        run_id=manifest.run_id, chunk_id=manifest.chunks[0].chunk_id, head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py", "app/b.py"], reviewed=["app/a.py", "app/b.py"]),
    )
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    bind_coverage_report_to_manifest_v2(synthesis.coverage_report, manifest)  # must not raise


# -- lifecycle passthrough is exercised through the full pipeline -------------


def test_findings_flow_through_to_the_synthesis_result() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding = _finding(finding_id="f1", path="app/a.py")
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    assert len(synthesis.findings) == 1
    assert synthesis.findings[0].disposition is FindingDispositionV2.NEW
    assert synthesis.findings[0].finding_id in synthesis.provenance


# -- boundary hardening: ParsedChunkResultV2 is a freely constructible value --
# -- and carries no seal proving it came from parse_bound_chunk_response_v2. --
# -- Every claim it makes is revalidated against the manifest's own scope. ---


def _two_chunk_two_file_manifest() -> tuple[ManifestV2, dict[str, str]]:
    """Two files, each planned into its own distinct chunk."""

    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"], max_lines_per_chunk=10
    )
    chunk_by_fragment = {fid: c.chunk_id for c in manifest.chunks for fid in c.fragment_ids}
    fragment_by_path = {f.path: f for f in manifest.fragments}
    chunk_id_by_path = {path: chunk_by_fragment[fragment_by_path[path].fragment_id] for path in ("app/a.py", "app/b.py")}
    return manifest, chunk_id_by_path


def test_rejects_a_chunk_result_for_a_chunk_id_that_does_not_exist_in_the_manifest() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    forged_result = _result(
        run_id=manifest.run_id, chunk_id="chunk-that-does-not-exist", head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[forged_result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == UNKNOWN_CHUNK_RESULT_REASON_V2


def test_rejects_a_chunk_result_whose_head_sha_diverges_from_the_manifest_identity() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    stale_result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha="9" * 40,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[stale_result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == CHUNK_RESULT_HEAD_MISMATCH_REASON_V2


def test_rejects_synthesis_whose_evaluated_head_sha_diverges_from_the_manifest_identity() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha="9" * 40)
    assert excinfo.value.reason_code == SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2


def test_rejects_a_chunk_result_claiming_coverage_of_a_path_outside_its_own_chunk() -> None:
    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    overclaiming_result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/a.py"], head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py", "app/b.py"], reviewed=["app/a.py", "app/b.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(
            manifest=manifest, chunk_results=[overclaiming_result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2


def test_rejects_a_chunk_result_claiming_must_review_for_a_path_the_manifest_never_marked_must_review() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    # override must_review_files to [] at the manifest-material level would break
    # identity binding, so instead assert the coverage claim disagrees with the
    # manifest's own must_review_files directly via a hand-built coverage object.
    chunk_id = manifest.chunks[0].chunk_id
    assert manifest.must_review_files == ["app/a.py"]
    coverage = ChunkCoverageV2(
        status="complete", expected_files=["app/a.py"], reviewed_files=["app/a.py"], partially_reviewed_files=[],
        missing_files=[], must_review_files=[], missing_must_review_files=[], degradation_causes=[],
    )
    result = _result(run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha, coverage=coverage)
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2


def test_rejects_a_finding_whose_file_path_belongs_to_a_different_chunk() -> None:
    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    finding = _finding(finding_id="f1", path="app/b.py")
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/a.py"], head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


def test_rejects_a_finding_whose_line_range_belongs_to_a_different_chunks_fragment_on_the_same_path() -> None:
    """Same path (structurally split across chunk-0/chunk-1), finding reported
    by chunk-0 but whose line range only exists inside chunk-1's own
    fragment -- a file-path match alone must not be enough."""

    manifest = _build_two_chunk_split_manifest()
    finding = _finding(finding_id="f1", path="app/a.py", line_start=10, line_end=14)
    result = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    with pytest.raises(SynthesisErrorV2) as excinfo:
        synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    assert excinfo.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


def test_a_genuine_multi_chunk_multi_finding_result_still_synthesizes_cleanly() -> None:
    """The hardening must not reject a well-formed multi-chunk run: each
    chunk's coverage and findings genuinely stay within its own scope."""

    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    finding_a = _finding(finding_id="fa", path="app/a.py", line_start=1, line_end=5)
    finding_b = _finding(finding_id="fb", path="app/b.py", line_start=1, line_end=5, contract_ids=["c2"])
    result_a = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/a.py"], head_sha=manifest.identity.head_sha,
        findings=(finding_a,), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    result_b = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/b.py"], head_sha=manifest.identity.head_sha,
        findings=(finding_b,), coverage=_coverage(["app/b.py"], reviewed=["app/b.py"]),
    )
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=[result_a, result_b], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(synthesis.findings) == 2


# -- Codex review of PR #117: limitations and severity-in-identity fixes -----


def test_a_chunks_limitations_are_preserved_and_deduplicated_in_the_synthesis_result() -> None:
    """Codex finding on PR #117: a valid, in-scope chunk carrying a nonempty
    ``limitations`` tuple (e.g. ``model_uncertainty``) used to vanish --
    ``SynthesisResultV2`` had no field for it and nothing aggregated it, so
    #108's readiness computation had nothing to turn into pipeline
    degradation for an otherwise-unsupported run."""

    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    result_a = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/a.py"], head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]), limitations=("model_uncertainty",),
    )
    result_b = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/b.py"], head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/b.py"], reviewed=["app/b.py"]), limitations=("model_uncertainty", "transport_degraded"),
    )
    synthesis = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=[result_a, result_b], evaluated_head_sha=manifest.identity.head_sha
    )
    assert synthesis.limitations == ("model_uncertainty", "transport_degraded")


def test_no_limitations_produces_an_empty_tuple_not_a_missing_field() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    synthesis = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    assert synthesis.limitations == ()


def test_the_same_root_cause_at_different_severities_across_two_chunks_picks_the_most_severe_deterministically() -> None:
    """Codex finding on PR #117: severity used to be part of the dedup key,
    so removing it (to let genuine severity drift be caught by the
    prior-severity-mismatch guard instead of silently producing a
    different finding_id) means TWO observations of the same root cause at
    DIFFERENT severities within one round can now collapse into one key.
    The chosen severity must be deterministic (most severe wins), not an
    artifact of chunk_results ordering."""

    manifest = _build_two_chunk_split_manifest()
    # Same file-level (no-range) root cause claimed identically by BOTH
    # chunks that share this structurally split path -- but this test
    # cares about a single grouped finding for one chunk that reports it
    # twice at different severities (route-independent of chunk_id, since
    # the file-level key already folds in chunk_id -- so two DIFFERENT
    # severities for the SAME range within the SAME chunk is the concrete
    # scenario to exercise here).
    finding_p2 = _finding(finding_id="f-p2", path="app/a.py", line_start=1, line_end=5, severity=FindingSeverityV2.P2)
    finding_p0 = _finding(finding_id="f-p0", path="app/a.py", line_start=1, line_end=5, severity=FindingSeverityV2.P0)
    result = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding_p2, finding_p0), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    forward = synthesize_chunk_results_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha)
    result_reordered = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding_p0, finding_p2), coverage=_coverage(["app/a.py"], reviewed=["app/a.py"]),
    )
    backward = synthesize_chunk_results_v2(
        manifest=manifest, chunk_results=[result_reordered], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(forward.findings) == 1
    assert forward.findings[0].severity is FindingSeverityV2.P0
    assert forward.findings == backward.findings
