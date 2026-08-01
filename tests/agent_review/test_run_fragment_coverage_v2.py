from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_review.contracts_v2 import RunIdentityV2, compute_run_id
from app.agent_review.manifest_v2 import (
    ManifestChunkV2,
    ManifestDegradationV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_manifest_hash_v2_for,
)
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2
from app.agent_review.run_fragment_coverage_v2 import (
    AFFECTED_CHUNK_SET_MISMATCH_REASON_V2,
    DEGRADED_FRAGMENT_CANNOT_BE_REVIEWED_REASON_V2,
    DEGRADED_FRAGMENT_NOT_ACCOUNTED_REASON_V2,
    FRAGMENT_SET_MISMATCH_REASON_V2,
    MANIFEST_HASH_MISMATCH_REASON_V2,
    PATH_SET_MISMATCH_REASON_V2,
    RUN_ID_MISMATCH_REASON_V2,
    UNKNOWN_CHUNK_REFERENCE_REASON_V2,
    FragmentCoverageBindingError,
    FragmentCoverageReasonV2,
    FragmentCoverageStatusV2,
    RunFragmentCoverageEntryV2,
    RunFragmentCoverageReportMaterialV2,
    RunFragmentCoverageReportV2,
    bind_coverage_report_to_manifest_v2,
    compute_coverage_report_sha256_v2,
)


# -- fixture helpers, matching the pattern already used by test_payload_builder_v2.py --


def _identity(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 104,
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


def _report_material(*, run_id: str, manifest_hash: str, paths: list[RunFragmentCoverageEntryV2]) -> dict[str, object]:
    return {
        "schema_id": "agent-review.run-fragment-coverage.v2",
        "schema_version": 2,
        "source": "aiops-review-fragment-coverage",
        "run_id": run_id,
        "manifest_hash": manifest_hash,
        "paths": paths,
    }


def _reviewed_entry(path: str, fragment_id: str, chunk_id: str) -> RunFragmentCoverageEntryV2:
    return RunFragmentCoverageEntryV2(
        path=path,
        expected_fragment_ids=[fragment_id],
        assigned_fragment_ids=[fragment_id],
        reviewed_fragment_ids=[fragment_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=[chunk_id],
        status=FragmentCoverageStatusV2.REVIEWED,
        reason_codes=[],
    )


# -- RunFragmentCoverageEntryV2: the fail-closed policy itself --------------


def test_entry_accepts_a_non_divided_fully_reviewed_path() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    assert entry.status is FragmentCoverageStatusV2.REVIEWED
    assert entry.reason_codes == []


def test_entry_rejects_reviewed_status_for_a_structurally_divided_path() -> None:
    """The core fail-closed policy: even a caller that claims every fragment
    was reviewed cannot mark a path 'reviewed' once it is split across more
    than one chunk -- there is no fragment-level proof channel to trust
    that claim against."""

    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64, "f" * 64],
            assigned_fragment_ids=["e" * 64, "f" * 64],
            reviewed_fragment_ids=["e" * 64, "f" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0", "chunk-1"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[],
        )


def test_entry_accepts_a_divided_path_marked_partial_with_structural_split() -> None:
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=["e" * 64, "f" * 64],
        assigned_fragment_ids=["e" * 64, "f" * 64],
        reviewed_fragment_ids=["e" * 64, "f" * 64],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0", "chunk-1"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT],
    )
    assert entry.status is FragmentCoverageStatusV2.PARTIAL


def test_entry_rejects_structural_split_reason_for_a_non_divided_path() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=[],
            partially_reviewed_fragment_ids=["e" * 64],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.PARTIAL,
            reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT],
        )


def test_entry_requires_structural_split_reason_for_a_divided_path() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64, "f" * 64],
            assigned_fragment_ids=["e" * 64, "f" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=["f" * 64],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0", "chunk-1"],
            status=FragmentCoverageStatusV2.PARTIAL,
            reason_codes=[FragmentCoverageReasonV2.CHUNK_UNAVAILABLE],
        )


def test_entry_accepts_a_fully_missing_path() -> None:
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=["e" * 64],
        assigned_fragment_ids=[],
        reviewed_fragment_ids=[],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=["e" * 64],
        affected_chunk_ids=[],
        status=FragmentCoverageStatusV2.MISSING,
        reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
    )
    assert entry.status is FragmentCoverageStatusV2.MISSING


def test_entry_rejects_a_partition_that_omits_expected_fragments() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64, "f" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],  # "f" * 64 is unaccounted for
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[],
        )


def test_entry_rejects_overlap_between_reviewed_and_partially_reviewed() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=["e" * 64],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[],
        )


def test_entry_rejects_assigned_fragment_not_in_expected() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=["e" * 64, "f" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[],
        )


def test_entry_rejects_an_unassigned_fragment_not_reported_missing() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64, "f" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],  # "f"*64 was never assigned -- must be missing
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.PARTIAL,
            reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
        )


def test_entry_rejects_duplicate_fragment_ids_within_a_field() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64, "e" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[],
        )


def test_entry_rejects_a_status_that_does_not_match_its_partition() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=[],
            partially_reviewed_fragment_ids=["e" * 64],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.MISSING,  # actually partial
            reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
        )


def test_entry_rejects_reason_codes_on_a_reviewed_path() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=["e" * 64],
            reviewed_fragment_ids=["e" * 64],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],
            affected_chunk_ids=["chunk-0"],
            status=FragmentCoverageStatusV2.REVIEWED,
            reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
        )


def test_entry_requires_a_reason_code_on_a_non_reviewed_path() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=["e" * 64],
            assigned_fragment_ids=[],
            reviewed_fragment_ids=[],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=["e" * 64],
            affected_chunk_ids=[],
            status=FragmentCoverageStatusV2.MISSING,
            reason_codes=[],
        )


def test_entry_rejects_at_least_one_expected_fragment_being_required() -> None:
    with pytest.raises(ValidationError):
        RunFragmentCoverageEntryV2(
            path="app/a.py",
            expected_fragment_ids=[],
            assigned_fragment_ids=[],
            reviewed_fragment_ids=[],
            partially_reviewed_fragment_ids=[],
            missing_fragment_ids=[],
            affected_chunk_ids=[],
            status=FragmentCoverageStatusV2.MISSING,
            reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
        )


# -- RunFragmentCoverageReportMaterialV2 / RunFragmentCoverageReportV2 ------


def test_report_rejects_duplicate_path_entries() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    with pytest.raises(ValidationError):
        RunFragmentCoverageReportMaterialV2.model_validate(
            _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry, entry])
        )


def test_report_hash_covers_every_field_except_itself() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    material = _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    assert report.coverage_report_sha256 == sha


def test_report_rejects_a_tampered_hash() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    material = _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry])
    with pytest.raises(ValidationError):
        RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": "0" * 64})


def test_report_hash_is_invariant_to_path_entry_order() -> None:
    entry_a = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    entry_b = _reviewed_entry("app/b.py", "f" * 64, "chunk-1")
    material_forward = RunFragmentCoverageReportMaterialV2.model_validate(
        _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry_a, entry_b])
    )
    material_reversed = RunFragmentCoverageReportMaterialV2.model_validate(
        _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry_b, entry_a])
    )
    assert compute_coverage_report_sha256_v2(material_forward) == compute_coverage_report_sha256_v2(
        material_reversed
    )


def test_report_hash_changes_when_material_content_changes() -> None:
    entry_a = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    entry_a2 = _reviewed_entry("app/a.py", "e" * 64, "chunk-1")  # different chunk
    material_a = RunFragmentCoverageReportMaterialV2.model_validate(
        _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry_a])
    )
    material_a2 = RunFragmentCoverageReportMaterialV2.model_validate(
        _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry_a2])
    )
    assert compute_coverage_report_sha256_v2(material_a) != compute_coverage_report_sha256_v2(material_a2)


def test_report_rejects_an_unknown_schema_id() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    material = _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry])
    material["schema_id"] = "agent-review.something-else.v2"
    with pytest.raises(ValidationError):
        RunFragmentCoverageReportMaterialV2.model_validate(material)


def test_report_rejects_an_unknown_field() -> None:
    entry = _reviewed_entry("app/a.py", "e" * 64, "chunk-0")
    material = _report_material(run_id="1" * 64, manifest_hash="2" * 64, paths=[entry])
    material["unexpected_field"] = "value"
    with pytest.raises(ValidationError):
        RunFragmentCoverageReportMaterialV2.model_validate(material)


# -- bind_coverage_report_to_manifest_v2: real manifest cross-check ---------


def _report_for_manifest(manifest: ManifestV2, *, status_by_path: dict[str, FragmentCoverageStatusV2]) -> RunFragmentCoverageReportV2:
    fragments_by_path: dict[str, list[str]] = {}
    for fragment in manifest.fragments:
        fragments_by_path.setdefault(fragment.path, []).append(fragment.fragment_id)
    chunk_by_fragment: dict[str, str] = {}
    for chunk in manifest.chunks:
        for fid in chunk.fragment_ids:
            chunk_by_fragment[fid] = chunk.chunk_id

    entries: list[RunFragmentCoverageEntryV2] = []
    for path, fragment_ids in fragments_by_path.items():
        status = status_by_path[path]
        chunks_for_path = sorted({chunk_by_fragment[fid] for fid in fragment_ids if fid in chunk_by_fragment})
        if status is FragmentCoverageStatusV2.REVIEWED:
            entries.append(
                RunFragmentCoverageEntryV2(
                    path=path,
                    expected_fragment_ids=fragment_ids,
                    assigned_fragment_ids=fragment_ids,
                    reviewed_fragment_ids=fragment_ids,
                    partially_reviewed_fragment_ids=[],
                    missing_fragment_ids=[],
                    affected_chunk_ids=chunks_for_path,
                    status=FragmentCoverageStatusV2.REVIEWED,
                    reason_codes=[],
                )
            )
        else:
            entries.append(
                RunFragmentCoverageEntryV2(
                    path=path,
                    expected_fragment_ids=fragment_ids,
                    assigned_fragment_ids=[],
                    reviewed_fragment_ids=[],
                    partially_reviewed_fragment_ids=[],
                    missing_fragment_ids=fragment_ids,
                    affected_chunk_ids=[],
                    status=FragmentCoverageStatusV2.MISSING,
                    reason_codes=[FragmentCoverageReasonV2.NOT_YET_PROCESSED],
                )
            )

    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=entries)
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    return RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})


def test_bind_accepts_a_report_matching_its_manifest() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    report = _report_for_manifest(manifest, status_by_path={"app/a.py": FragmentCoverageStatusV2.REVIEWED})
    bind_coverage_report_to_manifest_v2(report, manifest)  # must not raise


def test_bind_rejects_a_run_id_mismatch() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    report = _report_for_manifest(manifest, status_by_path={"app/a.py": FragmentCoverageStatusV2.REVIEWED})
    tampered = report.model_copy(update={"run_id": "9" * 64})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(tampered, manifest)
    assert excinfo.value.reason_code == RUN_ID_MISMATCH_REASON_V2


def test_bind_rejects_a_manifest_hash_mismatch() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    report = _report_for_manifest(manifest, status_by_path={"app/a.py": FragmentCoverageStatusV2.REVIEWED})
    tampered = report.model_copy(update={"manifest_hash": "9" * 64})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(tampered, manifest)
    assert excinfo.value.reason_code == MANIFEST_HASH_MISMATCH_REASON_V2


def test_bind_rejects_a_replayed_report_against_a_different_manifest() -> None:
    """Cross-manifest replay: a report proven internally consistent for one
    manifest must not validate against an unrelated one, even if by
    coincidence their run identities were both freshly computed."""

    manifest_1 = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    manifest_2 = _build_manifest(
        [_hunk("app/b.py")], expected_files=["app/b.py"], must_review_files=["app/b.py"]
    )
    report = _report_for_manifest(manifest_1, status_by_path={"app/a.py": FragmentCoverageStatusV2.REVIEWED})
    with pytest.raises(FragmentCoverageBindingError):
        bind_coverage_report_to_manifest_v2(report, manifest_2)


def test_bind_rejects_a_path_set_mismatch() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")],
        expected_files=["app/a.py", "app/b.py"],
        must_review_files=[],
    )
    entry = _reviewed_entry("app/a.py", next(f.fragment_id for f in manifest.fragments if f.path == "app/a.py"), "irrelevant")
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == PATH_SET_MISMATCH_REASON_V2


def test_bind_rejects_a_fragment_set_mismatch() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    entry = _reviewed_entry("app/a.py", "f" * 64, "chunk-0")  # not a real fragment of app/a.py
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == FRAGMENT_SET_MISMATCH_REASON_V2


def test_bind_rejects_an_unknown_chunk_reference() -> None:
    manifest = _build_manifest(
        [_hunk("app/a.py")], expected_files=["app/a.py"], must_review_files=["app/a.py"]
    )
    fragment_id = manifest.fragments[0].fragment_id
    entry = _reviewed_entry("app/a.py", fragment_id, "chunk-does-not-exist")
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == UNKNOWN_CHUNK_REFERENCE_REASON_V2


def _build_manifest_with_degradation() -> tuple[ManifestV2, str]:
    """A hand-built manifest (bypassing plan_lossless_chunks_v2, which never
    produces a 'planned' outcome carrying a degradation cause) with one
    coverage_required fragment explicitly degraded -- omitted from every
    chunk, accounted for by a ManifestDegradationV2 cause, exactly the shape
    manifest_v2's own validator requires."""

    fragment_ok_id = hashlib.sha256(b"ok-fragment").hexdigest()
    fragment_degraded_id = hashlib.sha256(b"degraded-fragment").hexdigest()

    from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, compute_fragment_id_v2

    def _fragment(seed: bytes, path: str) -> FragmentV2:
        diff_sha = hashlib.sha256(seed).hexdigest()
        old_range = LineRangeV2(start=1, end=5)
        new_range = LineRangeV2(start=1, end=5)
        fragment_id = compute_fragment_id_v2(
            path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha
        )
        return FragmentV2(
            fragment_id=fragment_id,
            path=path,
            old_range=old_range,
            new_range=new_range,
            hunk_indexes=[0],
            diff_chars=10,
            diff_sha256=diff_sha,
            coverage_required=True,
        )

    ok_fragment = _fragment(b"ok-fragment", "app/a.py")
    degraded_fragment = _fragment(b"degraded-fragment", "app/a.py")

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py"],
        "must_review_files": ["app/a.py"],
        "fragments": [ok_fragment, degraded_fragment],
        "chunks": [
            ManifestChunkV2(
                chunk_id="chunk-0",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[ok_fragment.fragment_id],
                payload_sha256=None,
            )
        ],
        "max_chunks": 10,
        "degradation_causes": [
            ManifestDegradationV2(
                reason_code="budget_exhausted",
                affected_fragment_ids=[degraded_fragment.fragment_id],
                detail="deliberately degraded for a coverage-binding regression",
            )
        ],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)
    return manifest, degraded_fragment.fragment_id


def test_bind_rejects_a_degraded_fragment_marked_reviewed() -> None:
    manifest, degraded_fragment_id = _build_manifest_with_degradation()
    ok_fragment_id = next(f.fragment_id for f in manifest.fragments if f.fragment_id != degraded_fragment_id)
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        assigned_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        reviewed_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0"],
        status=FragmentCoverageStatusV2.REVIEWED,
        reason_codes=[],
    )
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == DEGRADED_FRAGMENT_CANNOT_BE_REVIEWED_REASON_V2


def test_bind_rejects_a_degraded_fragment_not_accounted_as_missing() -> None:
    manifest, degraded_fragment_id = _build_manifest_with_degradation()
    ok_fragment_id = next(f.fragment_id for f in manifest.fragments if f.fragment_id != degraded_fragment_id)
    # degraded fragment reported as "partially_reviewed" instead of "missing" -- must be rejected
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        assigned_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        reviewed_fragment_ids=[ok_fragment_id],
        partially_reviewed_fragment_ids=[degraded_fragment_id],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.FRAGMENT_DEGRADED],
    )
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == DEGRADED_FRAGMENT_NOT_ACCOUNTED_REASON_V2


def test_bind_accepts_a_degraded_fragment_correctly_reported_missing() -> None:
    manifest, degraded_fragment_id = _build_manifest_with_degradation()
    ok_fragment_id = next(f.fragment_id for f in manifest.fragments if f.fragment_id != degraded_fragment_id)
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[ok_fragment_id, degraded_fragment_id],
        assigned_fragment_ids=[ok_fragment_id],
        reviewed_fragment_ids=[ok_fragment_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[degraded_fragment_id],
        affected_chunk_ids=["chunk-0"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.FRAGMENT_DEGRADED],
    )
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    bind_coverage_report_to_manifest_v2(report, manifest)  # must not raise


# -- affected_chunk_ids must be the REAL per-path chunk set, not merely a --
# -- subset of chunks that exist somewhere in the manifest. Found by       --
# -- independent review: RunFragmentCoverageEntryV2 alone has no manifest --
# -- to check "is this path divided?" against, so an entry that           --
# -- under-reports affected_chunk_ids could otherwise make a genuinely    --
# -- multi-chunk-split path look undivided -- and therefore eligible for  --
# -- status=reviewed -- purely at the entry level. --------------------------


def _build_manifest_with_a_two_chunk_split_path() -> tuple[ManifestV2, str, str]:
    """A hand-built manifest with ONE path whose two fragments are
    genuinely assigned to TWO different chunks -- a real structural split,
    unlike the plan_lossless_chunks_v2-built manifests elsewhere in this
    file, which don't deterministically guarantee splitting a single
    path's fragments across chunks."""

    from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, compute_fragment_id_v2

    def _fragment(seed: bytes, path: str, start: int) -> FragmentV2:
        diff_sha = hashlib.sha256(seed).hexdigest()
        old_range = LineRangeV2(start=start, end=start + 4)
        new_range = LineRangeV2(start=start, end=start + 4)
        fragment_id = compute_fragment_id_v2(
            path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha
        )
        return FragmentV2(
            fragment_id=fragment_id,
            path=path,
            old_range=old_range,
            new_range=new_range,
            hunk_indexes=[0],
            diff_chars=10,
            diff_sha256=diff_sha,
            coverage_required=True,
        )

    fragment_1 = _fragment(b"split-fragment-1", "app/a.py", 1)
    fragment_2 = _fragment(b"split-fragment-2", "app/a.py", 10)

    material_kwargs = {
        "schema_id": "agent-review.manifest.v2",
        "schema_version": 2,
        "source": "aiops-review-plan-chunks-v2",
        "expected_files": ["app/a.py"],
        "must_review_files": ["app/a.py"],
        "fragments": [fragment_1, fragment_2],
        "chunks": [
            ManifestChunkV2(
                chunk_id="chunk-0",
                order_index=0,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_1.fragment_id],
                payload_sha256=None,
            ),
            ManifestChunkV2(
                chunk_id="chunk-1",
                order_index=1,
                semantic_group="primary_backend_logic",
                fragment_ids=[fragment_2.fragment_id],
                payload_sha256=None,
            ),
        ],
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash))
    manifest = ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)
    return manifest, fragment_1.fragment_id, fragment_2.fragment_id


def test_bind_rejects_an_entry_that_underreports_affected_chunk_ids_to_hide_a_real_split() -> None:
    """The exact regression the independent review found: an entry that
    claims only ONE of a path's two real chunks -- making it look
    undivided to RunFragmentCoverageEntryV2's own validator, which has no
    manifest to check against -- while claiming full review, must be
    rejected by the manifest-aware binder even though the entry alone
    validates successfully in isolation."""

    manifest, fragment_1_id, fragment_2_id = _build_manifest_with_a_two_chunk_split_path()
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[fragment_1_id, fragment_2_id],
        assigned_fragment_ids=[fragment_1_id, fragment_2_id],
        reviewed_fragment_ids=[fragment_1_id, fragment_2_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0"],  # real chunks are chunk-0 AND chunk-1 -- under-reported
        status=FragmentCoverageStatusV2.REVIEWED,
        reason_codes=[],
    )
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == AFFECTED_CHUNK_SET_MISMATCH_REASON_V2


def test_bind_accepts_a_correctly_reported_two_chunk_split_path_as_partial() -> None:
    """The honest counterpart: the same manifest, but the report correctly
    names BOTH real chunks and caps status at partial -- accepted."""

    manifest, fragment_1_id, fragment_2_id = _build_manifest_with_a_two_chunk_split_path()
    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[fragment_1_id, fragment_2_id],
        assigned_fragment_ids=[fragment_1_id, fragment_2_id],
        reviewed_fragment_ids=[fragment_1_id, fragment_2_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=["chunk-0", "chunk-1"],
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT],
    )
    material = _report_material(run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry])
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    bind_coverage_report_to_manifest_v2(report, manifest)  # must not raise


def test_bind_rejects_an_entry_that_overreports_affected_chunk_ids() -> None:
    """The opposite direction: claiming a chunk that genuinely exists in
    the manifest, but does not actually carry this path's fragments."""

    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")],
        expected_files=["app/a.py", "app/b.py"],
        must_review_files=[],
        max_lines_per_chunk=10,
        max_chunks=10,
    )
    chunk_by_fragment = {
        fid: chunk.chunk_id for chunk in manifest.chunks for fid in chunk.fragment_ids
    }
    fragment_a_id = next(f.fragment_id for f in manifest.fragments if f.path == "app/a.py")
    real_chunk_for_a = chunk_by_fragment[fragment_a_id]
    other_chunk_ids = {chunk.chunk_id for chunk in manifest.chunks} - {real_chunk_for_a}
    assert other_chunk_ids, "test setup requires app/a.py and app/b.py in different chunks"
    unrelated_chunk = sorted(other_chunk_ids)[0]

    entry = RunFragmentCoverageEntryV2(
        path="app/a.py",
        expected_fragment_ids=[fragment_a_id],
        assigned_fragment_ids=[fragment_a_id],
        reviewed_fragment_ids=[fragment_a_id],
        partially_reviewed_fragment_ids=[],
        missing_fragment_ids=[],
        affected_chunk_ids=sorted([real_chunk_for_a, unrelated_chunk]),
        status=FragmentCoverageStatusV2.PARTIAL,
        reason_codes=[FragmentCoverageReasonV2.STRUCTURAL_SPLIT],
    )
    other_fragment_id = next(f.fragment_id for f in manifest.fragments if f.path == "app/b.py")
    other_entry = _reviewed_entry("app/b.py", other_fragment_id, chunk_by_fragment[other_fragment_id])
    material = _report_material(
        run_id=manifest.run_id, manifest_hash=manifest.identity.manifest_hash, paths=[entry, other_entry]
    )
    sha = compute_coverage_report_sha256_v2(RunFragmentCoverageReportMaterialV2.model_validate(material))
    report = RunFragmentCoverageReportV2.model_validate({**material, "coverage_report_sha256": sha})
    with pytest.raises(FragmentCoverageBindingError) as excinfo:
        bind_coverage_report_to_manifest_v2(report, manifest)
    assert excinfo.value.reason_code == AFFECTED_CHUNK_SET_MISMATCH_REASON_V2
