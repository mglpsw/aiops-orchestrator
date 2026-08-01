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
from app.agent_review.chunk_result_scope_v2 import (
    CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2,
    CHUNK_RESULT_HEAD_MISMATCH_REASON_V2,
    CROSS_RUN_CHUNK_RESULT_REASON_V2,
    FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2,
    SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2,
    UNKNOWN_CHUNK_RESULT_REASON_V2,
)
from app.agent_review.lifecycle_v2 import (
    DUPLICATE_PRIOR_LIFECYCLE_FINDING_REASON_V2,
    PRIOR_LIFECYCLE_SEVERITY_MISMATCH_REASON_V2,
    STALE_PRIOR_LIFECYCLE_DECISION_REASON_V2,
    STALE_PRIOR_LIFECYCLE_EVIDENCE_REASON_V2,
    STALE_PRIOR_LIFECYCLE_REASON_V2,
    LifecycleAggregationError,
    aggregate_finding_lifecycle_v2,
)
from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestMaterialV2,
    ManifestV2,
    compute_fragment_id_v2,
    compute_manifest_hash_v2_for,
)
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


def _build_manifest(
    hunks: list[HunkInputV2], *, expected_files: list[str], head_sha: str = "2" * 40, max_lines_per_chunk: int = 100
) -> ManifestV2:
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=max_lines_per_chunk, max_chunks=10
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


def _build_two_chunk_split_manifest() -> ManifestV2:
    """A hand-built manifest with ONE path whose two fragments are
    genuinely assigned to TWO different, real chunk_ids."""

    def _fragment(seed: bytes, path: str, start: int) -> FragmentV2:
        diff_sha = hashlib.sha256(seed).hexdigest()
        old_range = LineRangeV2(start=start, end=start + 4)
        new_range = LineRangeV2(start=start, end=start + 4)
        fragment_id = compute_fragment_id_v2(path=path, old_range=old_range, new_range=new_range, diff_sha256=diff_sha)
        return FragmentV2(
            fragment_id=fragment_id, path=path, old_range=old_range, new_range=new_range,
            hunk_indexes=[0], diff_chars=10, diff_sha256=diff_sha, coverage_required=True,
        )

    fragment_1 = _fragment(b"lifecycle-split-1", "app/a.py", 1)
    fragment_2 = _fragment(b"lifecycle-split-2", "app/a.py", 10)
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


def _two_chunk_two_file_manifest() -> tuple[ManifestV2, dict[str, str]]:
    """Two files, each planned into its own distinct, real chunk_id."""

    manifest = _build_manifest(
        [_hunk("app/a.py"), _hunk("app/b.py")], expected_files=["app/a.py", "app/b.py"], max_lines_per_chunk=10
    )
    chunk_by_fragment = {fid: c.chunk_id for c in manifest.chunks for fid in c.fragment_ids}
    fragment_by_path = {f.path: f for f in manifest.fragments}
    chunk_id_by_path = {path: chunk_by_fragment[fragment_by_path[path].fragment_id] for path in ("app/a.py", "app/b.py")}
    return manifest, chunk_id_by_path


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


def _file_level_finding(
    *, finding_id: str, path: str, contract_ids: list[str], severity: FindingSeverityV2 = FindingSeverityV2.P1
) -> ChunkFindingV2:
    return ChunkFindingV2(
        finding_id=finding_id,
        severity=severity,
        title="a file-level finding",
        file_path=path,
        line_start=None,
        line_end=None,
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


def test_same_root_cause_reported_twice_by_the_same_chunk_deduplicates_and_retains_all_provenance() -> None:
    """A single chunk's own response can list the identical ranged root
    cause twice under two different provider-assigned finding_ids (e.g. two
    internal rules both flagging the same range) -- this collapses to one
    record with both provenances retained.

    This can only legitimately happen WITHIN one chunk for a ranged
    finding: planner_v2 guarantees two fragments never share a line range,
    so validate_chunk_results_scope_v2 correctly rejects (as
    finding_outside_chunk_scope) a SECOND, different real chunk claiming
    the identical range -- there is no way for two genuinely distinct
    chunks to legitimately report the same ranged root cause. (An earlier
    version of this test tried to model "different chunks" by attributing
    an app/a.py finding to a chunk whose real manifest scope was app/b.py
    -- exactly the cross-scope claim this module's own hardening now
    rejects; see test_rejects_a_finding_attributed_to_a_chunk_outside_its_manifest_scope.)
    """

    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding_1 = _finding(finding_id="from-provider-rule-1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    finding_2 = _finding(finding_id="from-provider-rule-2", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding_1, finding_2), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, provenance = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 1
    provenance_entries = provenance[findings[0].finding_id]
    assert len(provenance_entries) == 2
    assert {p.original_finding_id for p in provenance_entries} == {"from-provider-rule-1", "from-provider-rule-2"}
    assert {p.chunk_id for p in provenance_entries} == {chunk_id}


def test_rejects_a_finding_attributed_to_a_chunk_outside_its_manifest_scope() -> None:
    """Finding 1 from the independent adversarial review of PR #117:
    ``aggregate_finding_lifecycle_v2`` used to trust ``chunk_results``
    wholesale, with no revalidation against the manifest of its own --
    only the sibling ``synthesis_v2._validate_synthesis_inputs_v2`` did
    that, so calling this function directly (bypassing
    ``synthesize_chunk_results_v2``) silently accepted a finding on
    app/a.py attributed to a chunk whose real manifest fragments are all
    for app/b.py. This is exactly the scenario the previous version of
    ``test_same_root_cause_in_different_chunks_deduplicates_and_retains_all_provenance``
    exercised without realizing it was out of scope."""

    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    out_of_scope_finding = _finding(finding_id="f1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/b.py"], head_sha=manifest.identity.head_sha,
        findings=(out_of_scope_finding,), coverage=_coverage_all_reviewed(["app/b.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


def test_rejects_a_chunk_result_for_a_chunk_id_that_does_not_exist_in_the_manifest() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    forged_result = _result(
        run_id=manifest.run_id, chunk_id="chunk-that-does-not-exist", head_sha=manifest.identity.head_sha,
        findings=(), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[forged_result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == UNKNOWN_CHUNK_RESULT_REASON_V2


def test_rejects_a_chunk_result_from_a_different_run() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    foreign_result = _result(
        run_id="f" * 64, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[foreign_result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == CROSS_RUN_CHUNK_RESULT_REASON_V2


def test_rejects_a_chunk_result_whose_head_sha_diverges_from_the_manifest_identity() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    stale_result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha="9" * 40,
        findings=(), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[stale_result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == CHUNK_RESULT_HEAD_MISMATCH_REASON_V2


def test_rejects_aggregation_whose_evaluated_head_sha_diverges_from_the_manifest_identity() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha="9" * 40)
    assert excinfo.value.reason_code == SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2


def test_rejects_a_chunk_result_claiming_coverage_of_a_path_outside_its_own_chunk() -> None:
    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    overclaiming_result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id_by_path["app/a.py"], head_sha=manifest.identity.head_sha,
        findings=(), coverage=_coverage_all_reviewed(["app/a.py", "app/b.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[overclaiming_result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2


def test_rejects_a_finding_whose_line_range_belongs_to_a_different_chunks_fragment_on_the_same_split_path() -> None:
    """Same path (structurally split across chunk-0/chunk-1), finding
    reported by chunk-0 but whose line range only exists inside chunk-1's
    own fragment -- a file-path match alone must not be enough, even
    though a file-level (no-range) claim on the same shared path would be
    legitimate for either chunk."""

    manifest = _build_two_chunk_split_manifest()
    finding = _finding(finding_id="f1", path="app/a.py", line_start=10, line_end=14, contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
        )
    assert excinfo.value.reason_code == FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2


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


# -- P2-1: prior_lifecycle is fully revalidated, not just observed_at_head_sha -


def _decided_prior(
    *, finding_id: str, severity: FindingSeverityV2, head_sha: str, decided_at_head_sha: str, evidence_head_sha: str
) -> FindingLifecycleRecordV2:
    return FindingLifecycleRecordV2(
        finding_id=finding_id,
        severity=severity,
        observed_at_head_sha=head_sha,
        disposition=FindingDispositionV2.DISMISSED,
        actionable=False,
        justification="false positive",
        decided_by="reviewer-1",
        decided_at_head_sha=decided_at_head_sha,
        evidence=[DispositionEvidenceV2(kind="commit", reference="a" * 40, head_sha=evidence_head_sha)],
        superseded_by=None,
    )


def test_prior_with_current_observed_head_but_stale_decided_head_is_rejected() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    head = manifest.identity.head_sha
    prior = _decided_prior(
        finding_id="f" * 64, severity=FindingSeverityV2.P1, head_sha=head, decided_at_head_sha="9" * 40, evidence_head_sha=head
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=[prior]
        )
    assert excinfo.value.reason_code == STALE_PRIOR_LIFECYCLE_DECISION_REASON_V2


def test_prior_with_current_decided_head_but_stale_evidence_head_is_rejected() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    head = manifest.identity.head_sha
    prior = _decided_prior(
        finding_id="f" * 64, severity=FindingSeverityV2.P1, head_sha=head, decided_at_head_sha=head, evidence_head_sha="9" * 40
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=[prior]
        )
    assert excinfo.value.reason_code == STALE_PRIOR_LIFECYCLE_EVIDENCE_REASON_V2


def test_two_priors_sharing_the_same_finding_id_are_rejected_regardless_of_order() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    head = manifest.identity.head_sha
    prior_1 = _decided_prior(
        finding_id="f" * 64, severity=FindingSeverityV2.P1, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    prior_2 = _decided_prior(
        finding_id="f" * 64, severity=FindingSeverityV2.P2, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    for ordering in ([prior_1, prior_2], [prior_2, prior_1]):
        with pytest.raises(LifecycleAggregationError) as excinfo:
            aggregate_finding_lifecycle_v2(
                manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=ordering
            )
        assert excinfo.value.reason_code == DUPLICATE_PRIOR_LIFECYCLE_FINDING_REASON_V2


def test_prior_matching_a_reobserved_finding_with_a_different_severity_is_rejected() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    head = manifest.identity.head_sha
    finding = _finding(finding_id="f1", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"], severity=FindingSeverityV2.P1)
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=head, findings=(finding,), coverage=_coverage_all_reviewed(["app/a.py"])
    )
    findings_first_pass, _ = aggregate_finding_lifecycle_v2(manifest=manifest, chunk_results=[result], evaluated_head_sha=head)
    synthesized_id = findings_first_pass[0].finding_id

    # a prior claiming this exact finding_id, but recorded at a DIFFERENT
    # severity than what is actually observed this round -- must never
    # silently override the observed severity, nor be silently accepted.
    mismatched_prior = _decided_prior(
        finding_id=synthesized_id, severity=FindingSeverityV2.P3, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    with pytest.raises(LifecycleAggregationError) as excinfo:
        aggregate_finding_lifecycle_v2(
            manifest=manifest, chunk_results=[result], evaluated_head_sha=head, prior_lifecycle=[mismatched_prior]
        )
    assert excinfo.value.reason_code == PRIOR_LIFECYCLE_SEVERITY_MISMATCH_REASON_V2


def test_a_fully_valid_prior_bound_to_the_current_head_is_preserved() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    head = manifest.identity.head_sha
    valid_prior = _decided_prior(
        finding_id="f" * 64, severity=FindingSeverityV2.P2, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=[valid_prior]
    )
    assert findings == (valid_prior,)


def test_reordering_valid_priors_does_not_change_the_output() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    head = manifest.identity.head_sha
    prior_a = _decided_prior(
        finding_id="a" * 64, severity=FindingSeverityV2.P1, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    prior_b = _decided_prior(
        finding_id="b" * 64, severity=FindingSeverityV2.P2, head_sha=head, decided_at_head_sha=head, evidence_head_sha=head
    )
    forward, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=[prior_a, prior_b]
    )
    backward, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[], evaluated_head_sha=head, prior_lifecycle=[prior_b, prior_a]
    )
    assert forward == backward


# -- P2-2: file-level (no-range) findings dedup by chunk_id, not just path ---


def test_file_level_findings_from_two_different_chunks_on_the_same_split_path_are_not_deduplicated() -> None:
    manifest = _build_two_chunk_split_manifest()
    finding_0 = _file_level_finding(finding_id="from-chunk-0", path="app/a.py", contract_ids=["c1"])
    finding_1 = _file_level_finding(finding_id="from-chunk-1", path="app/a.py", contract_ids=["c1"])
    result_0 = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding_0,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    result_1 = _result(
        run_id=manifest.run_id, chunk_id="chunk-1", head_sha=manifest.identity.head_sha,
        findings=(finding_1,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, provenance = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result_0, result_1], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 2
    all_provenance = [p for entries in provenance.values() for p in entries]
    assert {p.chunk_id for p in all_provenance} == {"chunk-0", "chunk-1"}
    assert {p.original_finding_id for p in all_provenance} == {"from-chunk-0", "from-chunk-1"}


def test_identical_file_level_findings_within_the_same_chunk_are_deduplicated() -> None:
    manifest = _build_manifest([_hunk("app/a.py")], expected_files=["app/a.py"])
    chunk_id = manifest.chunks[0].chunk_id
    finding_a = _file_level_finding(finding_id="fa", path="app/a.py", contract_ids=["c1"])
    finding_b = _file_level_finding(finding_id="fb", path="app/a.py", contract_ids=["c1"])
    result = _result(
        run_id=manifest.run_id, chunk_id=chunk_id, head_sha=manifest.identity.head_sha,
        findings=(finding_a, finding_b), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, provenance = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 1
    assert {p.original_finding_id for p in provenance[findings[0].finding_id]} == {"fa", "fb"}


def test_findings_with_distinct_ranges_on_the_same_split_path_remain_separate() -> None:
    manifest = _build_two_chunk_split_manifest()
    finding_0 = _finding(finding_id="from-chunk-0", path="app/a.py", line_start=1, line_end=5, contract_ids=["c1"])
    finding_1 = _finding(finding_id="from-chunk-1", path="app/a.py", line_start=10, line_end=14, contract_ids=["c1"])
    result_0 = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding_0,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    result_1 = _result(
        run_id=manifest.run_id, chunk_id="chunk-1", head_sha=manifest.identity.head_sha,
        findings=(finding_1,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    findings, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result_0, result_1], evaluated_head_sha=manifest.identity.head_sha
    )
    assert len(findings) == 2


def test_reordering_file_level_split_chunk_results_does_not_change_ids_or_result() -> None:
    manifest = _build_two_chunk_split_manifest()
    finding_0 = _file_level_finding(finding_id="from-chunk-0", path="app/a.py", contract_ids=["c1"])
    finding_1 = _file_level_finding(finding_id="from-chunk-1", path="app/a.py", contract_ids=["c1"])
    result_0 = _result(
        run_id=manifest.run_id, chunk_id="chunk-0", head_sha=manifest.identity.head_sha,
        findings=(finding_0,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    result_1 = _result(
        run_id=manifest.run_id, chunk_id="chunk-1", head_sha=manifest.identity.head_sha,
        findings=(finding_1,), coverage=_coverage_all_reviewed(["app/a.py"]),
    )
    forward, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result_0, result_1], evaluated_head_sha=manifest.identity.head_sha
    )
    backward, _ = aggregate_finding_lifecycle_v2(
        manifest=manifest, chunk_results=[result_1, result_0], evaluated_head_sha=manifest.identity.head_sha
    )
    assert forward == backward


# -- determinism ----------------------------------------------------------------


def test_reordering_chunk_results_does_not_change_the_output() -> None:
    manifest, chunk_id_by_path = _two_chunk_two_file_manifest()
    finding_a = _finding(finding_id="fa", path="app/a.py", line_start=1, line_end=10, contract_ids=["c1"])
    finding_b = _finding(finding_id="fb", path="app/b.py", line_start=1, line_end=10, contract_ids=["c2"])

    results = []
    for path, finding in (("app/a.py", finding_a), ("app/b.py", finding_b)):
        results.append(
            _result(
                run_id=manifest.run_id, chunk_id=chunk_id_by_path[path], head_sha=manifest.identity.head_sha,
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
