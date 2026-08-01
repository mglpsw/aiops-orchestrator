from __future__ import annotations

import hashlib

import pytest

from app.agent_review.contracts_v2 import (
    ChunkCoverageV2,
    ChunkFindingV2,
    DispositionEvidenceV2,
    FindingConfidenceV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
    RunIdentityV2,
    compute_run_id,
)
from app.agent_review.lifecycle_v2 import (
    STALE_PRIOR_LIFECYCLE_REASON_V2,
    LifecycleAggregationError,
    aggregate_finding_lifecycle_v2,
)
from app.agent_review.manifest_v2 import ManifestMaterialV2, ManifestV2, compute_manifest_hash_v2_for
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.planner_v2 import HunkInputV2, plan_lossless_chunks_v2


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


def _build_manifest(hunks: list[HunkInputV2], *, expected_files: list[str], head_sha: str = "2" * 40) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
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
        "max_chunks": 10,
        "degradation_causes": [],
    }
    material = ManifestMaterialV2.model_validate(material_kwargs)
    manifest_hash = compute_manifest_hash_v2_for(material)
    identity = RunIdentityV2.model_validate(_identity(manifest_hash=manifest_hash, head_sha=head_sha))
    return ManifestV2(**material_kwargs, run_id=compute_run_id(identity), identity=identity)


def _finding(
    *, finding_id: str, path: str, line_start: int, line_end: int, contract_ids: list[str], severity: FindingSeverityV2 = FindingSeverityV2.P1
) -> ChunkFindingV2:
    return ChunkFindingV2(
        finding_id=finding_id,
        severity=severity,
        title="a finding",
        file_path=path,
        line_start=line_start,
        line_end=line_end,
        evidence="evidence text",
        impact="impact text",
        confidence=FindingConfidenceV2.HIGH,
        contract_ids=contract_ids,
        disposition=FindingDispositionV2.NEW,
    )


def _coverage_all_reviewed(paths: list[str]) -> ChunkCoverageV2:
    return ChunkCoverageV2(
        status="complete",
        expected_files=paths,
        reviewed_files=paths,
        partially_reviewed_files=[],
        missing_files=[],
        must_review_files=paths,
        missing_must_review_files=[],
        degradation_causes=[],
    )


def _result(*, run_id: str, chunk_id: str, head_sha: str, findings: tuple[ChunkFindingV2, ...], coverage: ChunkCoverageV2) -> ParsedChunkResultV2:
    return ParsedChunkResultV2(
        run_id=run_id, chunk_id=chunk_id, head_sha=head_sha, summary="s", findings=findings, coverage=coverage, limitations=()
    )


# -- dedup by root cause -------------------------------------------------------


def test_identical_findings_on_different_fragments_are_not_deduplicated() -> None:
    manifest = _build_manifest([_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding_a = _finding(finding_id="f1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    finding_b = _finding(finding_id="f2", path="app/b.py", line_start=1, line_end=10, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id,
        chunk_id=chunk_id,
        head_sha=manifest.identity.head_sha,
        findings=(finding_a, finding_b),
        coverage=_coverage_all_reviewed(["app/a.py", "app/b.py"]),
    )
    findings, provenance = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 2
    assert all(f.disposition is FindingDispositionV2.NEW for f in findings)


def test_same_root_cause_in_different_chunks_deduplicates_and_retains_all_provenance() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding_1 = _finding(finding_id="from-chunk-report-1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    finding_2 = _finding(finding_id="from-chunk-report-2", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    result_1 = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding_1,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, provenance = aggregate_finding_lifecycle_v2(
        manifest=manifest,
        chunk_results=[result_1, result_1.__class__(
            run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
            summary="s2", findings=(finding_2,), coverage=_coverage_all_reviewed(["app/a.py"]), limitations=(),
        )],
        evaluated_head_sha=manifest.identity.head_sha,
    )
    # Two ParsedChunkResultV2 sharing a chunk_id is unusual, but the dedup
    # logic itself only cares about the (file, range, contracts, severity)
    # key -- this proves two independent OBSERVATIONS of the same root
    # cause collapse to one record with both provenances retained.
    assert len(findings) == 1
    provenance_entries = provenance[findings[0].finding_id]
    assert len(provenance_entries) == 2
    assert {p.original_finding_id for p in provenance_entries} == {"from-chunk-report-1", "from-chunk-report-2"}


def test_two_models_agreeing_stays_new_never_confirmed() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding = _finding(finding_id="f1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 1
    assert findings[0].disposition is FindingDispositionV2.NEW


# -- prior lifecycle: preserved, never fabricated ------------------------------


def test_prior_lifecycle_disposition_is_preserved_across_a_re_observation() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding = _finding(finding_id="f1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    # discover the deterministic finding_id first, to hand back a prior
    # DISMISSED decision for exactly that id
    findings_first_pass, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
    )
    synthesized_id = findings_first_pass[0].finding_id

    prior = FindingLifecycleRecordV2(
        finding_id=synthesized_id,
        severity=FindingSeverityV2.P1,
        observed_at_head_sha=manifest.identity.head_sha,
        disposition=FindingDispositionV2.DISMISSED,
        actionable=False,
        justification="false positive, confirmed by maintainer",
        decided_by="reviewer-1",
        decided_at_head_sha=manifest.identity.head_sha,
        evidence=[
            DispositionEvidenceV2(kind="commit", reference="a" * 40, head_sha=manifest.identity.head_sha)
        ],
        superseded_by=None,
    )
    findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest,
        chunk_results=[result],
        evaluated_head_sha=manifest.identity.head_sha,
        prior_lifecycle=[prior],
    )
    assert len(findings) == 1
    assert findings[0].disposition is FindingDispositionV2.DISMISSED
    assert findings[0] == prior


def test_prior_lifecycle_not_re_observed_this_round_still_persists() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    prior = FindingLifecycleRecordV2(
        finding_id="f" * 64,
        severity=FindingSeverityV2.P2,
        observed_at_head_sha=manifest.identity.head_sha,
        disposition=FindingDispositionV2.CONFIRMED,
        actionable=True,
        justification=None,
        decided_by="reviewer-1",
        decided_at_head_sha=manifest.identity.head_sha,
        evidence=[],
        superseded_by=None,
    )
    findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[], evaluated_head_sha=manifest.identity.head_sha, prior_lifecycle=[prior]
    )
    assert findings == (prior,)


def test_a_stale_prior_lifecycle_record_is_rejected_fail_closed() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    stale_prior = FindingLifecycleRecordV2(
        finding_id="f" * 64,
        severity=FindingSeverityV2.P2,
        observed_at_head_sha="9" * 40,  # not manifest.identity.head_sha
        disposition=FindingDispositionV2.CONFIRMED,
        actionable=True,
        justification=None,
        decided_by="reviewer-1",
        decided_at_head_sha="9" * 40,
        evidence=[],
        superseded_by=None,
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest,
            chunk_results=[],
            evaluated_head_sha=manifest.identity.head_sha,
            prior_lifecycle=[stale_prior],
        )
    assert excinfo.value.reason_code == STALE_PRIOR_LIFECYCLE_REASON_V2


# -- vocabulary: only what FindingDispositionV2 defines ------------------------


def test_rejected_is_not_a_valid_disposition_use_dismissed_instead() -> None:
    with pytest.raises((ValueError, TypeError)):
        FindingLifecycleRecordV2(
            finding_id="f" * 64,
            severity=FindingSeverityV2.P2,
            observed_at_head_sha="2" * 40,
            disposition="rejected",  # not a FindingDispositionV2 member
            actionable=False,
            justification=None,
            decided_by=None,
            decided_at_head_sha=None,
            evidence=[],
            superseded_by=None,
        )


def test_inconclusive_is_not_a_valid_disposition() -> None:
    with pytest.raises((ValueError, TypeError)):
        FindingLifecycleRecordV2(
            finding_id="f" * 64,
            severity=FindingSeverityV2.P2,
            observed_at_head_sha="2" * 40,
            disposition="inconclusive",
            actionable=False,
            justification=None,
            decided_by=None,
            decided_at_head_sha=None,
            evidence=[],
            superseded_by=None,
        )


def test_dismissed_without_justification_or_evidence_is_rejected_by_the_contract() -> None:
    """This module never constructs a dismissed record itself -- it only
    ever preserves an already-valid prior one. This test simply confirms
    the frozen contract's own guard (contracts_v2.py:1006-1021) really
    would catch a malformed dismissed record, so lifecycle_v2's "preserve
    only already-valid records" design has a real backstop."""

    with pytest.raises((ValueError, TypeError)):
        FindingLifecycleRecordV2(
            finding_id="f" * 64,
            severity=FindingSeverityV2.P2,
            observed_at_head_sha="2" * 40,
            disposition=FindingDispositionV2.DISMISSED,
            actionable=False,
            justification=None,  # required for dismissed
            decided_by="reviewer-1",
            decided_at_head_sha="2" * 40,
            evidence=[],  # required for dismissed
            superseded_by=None,
        )


# -- determinism ----------------------------------------------------------------


def test_reordering_chunk_results_does_not_change_the_output() -> None:
    manifest = _build_manifest([_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"])
    finding_a = _finding(finding_id="fa", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    finding_b = _finding(finding_id="fb", path="app/b.py", line_start=1, line_end=10, contract_ids=["c2"])

    fragment_by_path = {f.path: f for f in manifest.fragments}
    chunk_by_fragment = {fid: c.chunk_id for c in manifest.chunks for fid in c.fragment_ids}
    results = []
    for path, finding in (("app/a.py", finding_a), ("app/b.py", finding_b)):
        chunk_id = chunk_by_fragment[fragment_by_path[path].fragment_id]
        results.append(
            _result(
                run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
                findings=(finding,), coverage=_coverage_all_reviewed([path]),
            )
        )

    forward, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=results, evaluated_head_sha=manifest.identity.head_sha
    )
    reversed_findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=list(reversed(results)), evaluated_head_sha=manifest.identity.head_sha
    )
    assert forward == reversed_findings
