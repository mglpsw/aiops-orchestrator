"""Aggregate bound v2 chunk results into synthesis and lifecycle (issue #107).

Entry point: ``synthesize_chunk_results_v2``. Turns N already-bound
``ParsedChunkResultV2`` objects (parser_v2, issue #83) for one run, plus the
``ManifestV2`` (#84) that planned them, into a ``SynthesisResultV2`` carrying:

    - a ``RunFragmentCoverageReportV2`` (#104) proving, per path, exactly
      which fragments were structurally assigned versus semantically
      reviewed -- with the fail-closed policy #104 already froze applied
      here, not re-decided: a path whose fragments span more than one
      chunk can never reach ``status="reviewed"``;
    - a deduplicated set of ``FindingLifecycleRecordV2`` (lifecycle_v2.py),
      with every fresh finding entering as ``new`` and every other
      disposition only ever *preserved* from an already-decided,
      already-revalidated prior record -- never synthesized here from
      concordance between chunks or models;
    - the deduplicated union of every bound chunk's own
      ``limitations`` (e.g. ``model_uncertainty``), so a real per-chunk
      limitation is never silently dropped between here and #108's
      readiness computation.

Accepts nothing but genuine ``ParsedChunkResultV2`` instances for the exact
run this manifest describes. A raw envelope, a v1 result, a hand-built dict,
or a result from a different run is rejected before any aggregation happens
-- fail-closed, never a silently incomplete synthesis.

``ParsedChunkResultV2`` is, by its own module's docstring, "a plain data
value, freely constructible" -- unlike ``BoundChunkResponseV2``, it carries
no seal proving it actually came from ``parse_bound_chunk_response_v2``.
``isinstance`` and a matching ``run_id``/``chunk_id`` therefore prove
nothing about whether the result's HEAD, coverage, or findings are
consistent with what this manifest's own chunk actually describes. This
module treats every ``ParsedChunkResultV2`` as untrusted downstream input
and revalidates it deterministically against ``manifest`` before any
aggregation, via ``chunk_result_scope_v2.validate_chunk_results_scope_v2`` --
the single shared authority also used independently by
``lifecycle_v2.aggregate_finding_lifecycle_v2``: HEAD identity, chunk
membership, coverage scope, and per-finding scope must all match the
manifest's own structure, or synthesis fails closed.

Deliberately out of scope, per the issue: readiness, quality gate,
publication, Router/provider, target workflow, Codex, release, any CLI, and
any change to the v1 modules (``final_synthesizer.py``,
``finding_normalizer.py``, ``chunk_result_parser.py``). This module imports
none of them, and reuses none of their heuristics -- v1 deduplicates by
normalized finding text and applies a heuristic severity downgrade, both
incompatible with the fragment-aware, root-cause dedup this module performs
instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.agent_review.chunk_result_scope_v2 import (
    CHUNK_RESULT_COVERAGE_SCOPE_MISMATCH_REASON_V2,
    CHUNK_RESULT_HEAD_MISMATCH_REASON_V2,
    CROSS_RUN_CHUNK_RESULT_REASON_V2,
    DUPLICATE_CHUNK_RESULT_REASON_V2,
    FINDING_OUTSIDE_CHUNK_SCOPE_REASON_V2,
    INVALID_CHUNK_RESULT_TYPE_REASON_V2,
    SYNTHESIS_EVALUATED_HEAD_MISMATCH_REASON_V2,
    UNKNOWN_CHUNK_RESULT_REASON_V2,
    ChunkResultScopeError,
    validate_chunk_results_scope_v2,
)
from app.agent_review.contracts_v2 import FindingLifecycleRecordV2
from app.agent_review.lifecycle_v2 import FindingProvenanceV2, _aggregate_finding_lifecycle_core_v2
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.run_fragment_coverage_v2 import (
    RunFragmentCoverageEntryV2,
    RunFragmentCoverageReportMaterialV2,
    RunFragmentCoverageReportV2,
    FragmentCoverageReasonV2,
    FragmentCoverageStatusV2,
    compute_coverage_report_sha256_v2,
    compute_fragment_coverage_status_v2,
)
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

FRAGMENTLESS_EXPECTED_FILE_REASON_V2 = "fragmentless_expected_file"

_COVERAGE_REPORT_SCHEMA_ID_V2 = "agent-review.run-fragment-coverage.v2"
_COVERAGE_REPORT_SOURCE_V2 = "aiops-review-fragment-coverage"


class SynthesisErrorV2(ExpectedOperationalRefusalV2, ValueError):
    """Raised for a synthesis failure. Carries a stable ``reason_code``
    only -- never chunk content, findings, or manifest data."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SynthesisResultV2:
    """Plain, freely constructible data value -- like ``ParsedChunkResultV2``,
    not a wire contract with its own schema/hash. Meant for consumption by
    #108's readiness computation.

    ``limitations`` is the deduplicated union of every successfully-bound
    chunk's own ``ParsedChunkResultV2.limitations`` (e.g. ``model_uncertainty``).
    Without it, a chunk that reported a real limitation but was otherwise
    valid and in-scope would have that limitation silently vanish here,
    leaving #108's readiness computation nothing to turn into pipeline
    degradation -- it could evaluate an unsupported run as healthy."""

    run_id: str
    evaluated_head_sha: str
    coverage_report: RunFragmentCoverageReportV2
    findings: tuple[FindingLifecycleRecordV2, ...]
    provenance: Mapping[str, tuple[FindingProvenanceV2, ...]]
    limitations: tuple[str, ...]


def _file_status_in_chunk(coverage, path: str) -> str:
    if path in coverage.reviewed_files:
        return "reviewed"
    if path in coverage.partially_reviewed_files:
        return "partial"
    # A path this chunk's bound response coverage does not mention at all
    # is treated the same as an explicit "missing" -- never silently
    # skipped -- since validate_response_binding_v2 already requires a
    # bound response's coverage.expected_files to equal the payload's, so
    # any path genuinely belonging to this chunk is always accounted for
    # in exactly one of reviewed/partially_reviewed/missing.
    return "missing"


def _build_coverage_report(
    *,
    manifest: ManifestV2,
    results_by_chunk_id: Mapping[str, ParsedChunkResultV2],
) -> RunFragmentCoverageReportV2:
    fragments_by_path: dict[str, list] = {}
    for fragment in manifest.fragments:
        fragments_by_path.setdefault(fragment.path, []).append(fragment)

    chunk_by_fragment: dict[str, str] = {}
    for chunk in manifest.chunks:
        for fragment_id in chunk.fragment_ids:
            chunk_by_fragment[fragment_id] = chunk.chunk_id

    degraded_fragment_ids: set[str] = set()
    for cause in manifest.degradation_causes:
        degraded_fragment_ids.update(cause.affected_fragment_ids)

    entries: list[RunFragmentCoverageEntryV2] = []
    for path in manifest.expected_files:
        fragments = fragments_by_path.get(path, [])
        expected_ids = [fragment.fragment_id for fragment in fragments]
        if not expected_ids:
            # manifest_v2's own validator only requires fragment paths to
            # be a SUBSET of expected_files, never that every expected
            # file has at least one fragment -- so a fragmentless expected
            # file is a real, if currently unreached, manifest shape. No
            # code path in this repository constructs one today (every
            # existing manifest-building helper always supplies matching
            # hunks for every expected file), and RunFragmentCoverageEntryV2
            # requires at least one expected fragment, so there is no
            # non-circular way to represent it as an entry yet. Failing
            # closed with a dedicated reason code, rather than guessing an
            # empty-but-valid entry or crashing on an opaque
            # pydantic.ValidationError, until a real caller needs this
            # resolved (likely #109, which decides what such files even
            # mean at the manifest level).
            raise SynthesisErrorV2(FRAGMENTLESS_EXPECTED_FILE_REASON_V2)

        real_chunks = sorted({chunk_by_fragment[fid] for fid in expected_ids if fid in chunk_by_fragment})
        divided = len(real_chunks) > 1

        reviewed_ids: list[str] = []
        partial_ids: list[str] = []
        missing_ids: list[str] = []
        reason_codes: set[FragmentCoverageReasonV2] = set()
        if divided:
            reason_codes.add(FragmentCoverageReasonV2.STRUCTURAL_SPLIT)

        for fragment in fragments:
            fragment_id = fragment.fragment_id
            if fragment_id in degraded_fragment_ids:
                missing_ids.append(fragment_id)
                reason_codes.add(FragmentCoverageReasonV2.FRAGMENT_DEGRADED)
                continue
            chunk_id = chunk_by_fragment.get(fragment_id)
            if chunk_id is None:
                missing_ids.append(fragment_id)
                reason_codes.add(FragmentCoverageReasonV2.NOT_YET_PROCESSED)
                continue
            result = results_by_chunk_id.get(chunk_id)
            if result is None:
                missing_ids.append(fragment_id)
                reason_codes.add(FragmentCoverageReasonV2.CHUNK_UNAVAILABLE)
                continue

            file_status = _file_status_in_chunk(result.coverage, path)
            if file_status == "reviewed" and not divided:
                reviewed_ids.append(fragment_id)
            elif file_status in ("reviewed", "partial"):
                # "reviewed" folds into "partial" here whenever divided:
                # H-9's whole point -- a per-chunk file-level "reviewed"
                # claim is not a fragment-level proof once more than one
                # chunk shares the path, so it is never trusted into
                # reviewed_fragment_ids for a divided path.
                partial_ids.append(fragment_id)
                if not divided:
                    reason_codes.add(FragmentCoverageReasonV2.NOT_YET_PROCESSED)
            else:
                missing_ids.append(fragment_id)
                reason_codes.add(FragmentCoverageReasonV2.NOT_YET_PROCESSED)

        status = compute_fragment_coverage_status_v2(
            expected=set(expected_ids),
            reviewed=set(reviewed_ids),
            partially_reviewed=set(partial_ids),
            missing=set(missing_ids),
            divided=divided,
        )
        if status is FragmentCoverageStatusV2.REVIEWED:
            reason_codes = set()

        assigned_ids = [fid for fid in expected_ids if fid in chunk_by_fragment]
        entries.append(
            RunFragmentCoverageEntryV2(
                path=path,
                expected_fragment_ids=expected_ids,
                assigned_fragment_ids=assigned_ids,
                reviewed_fragment_ids=reviewed_ids,
                partially_reviewed_fragment_ids=partial_ids,
                missing_fragment_ids=missing_ids,
                affected_chunk_ids=real_chunks,
                status=status,
                reason_codes=sorted(reason_codes, key=lambda code: code.value),
            )
        )

    material = RunFragmentCoverageReportMaterialV2.model_validate(
        {
            "schema_id": _COVERAGE_REPORT_SCHEMA_ID_V2,
            "schema_version": 2,
            "source": _COVERAGE_REPORT_SOURCE_V2,
            "run_id": manifest.run_id,
            "manifest_hash": manifest.identity.manifest_hash,
            "paths": entries,
        }
    )
    coverage_report_sha256 = compute_coverage_report_sha256_v2(material)
    return RunFragmentCoverageReportV2.model_validate(
        {**material.model_dump(mode="json"), "coverage_report_sha256": coverage_report_sha256}
    )


def synthesize_chunk_results_v2(
    *,
    manifest: ManifestV2,
    chunk_results: Sequence[ParsedChunkResultV2],
    evaluated_head_sha: str,
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> SynthesisResultV2:
    """Aggregate N bound chunk results for ``manifest``'s run into one
    ``SynthesisResultV2``. Fail-closed on cross-run input, wrong-typed
    input, duplicate chunk results, unknown chunk, HEAD mismatch, or a
    coverage/finding claim outside the chunk's own manifest scope; never on
    a merely incomplete or degraded run -- an incomplete run still produces
    a valid result whose coverage report honestly reports what is missing.
    """

    try:
        results_by_chunk_id = validate_chunk_results_scope_v2(
            manifest=manifest, chunk_results=chunk_results, evaluated_head_sha=evaluated_head_sha
        )
    except ChunkResultScopeError as exc:
        raise SynthesisErrorV2(exc.reason_code) from exc

    coverage_report = _build_coverage_report(manifest=manifest, results_by_chunk_id=results_by_chunk_id)
    # Calls lifecycle_v2's private core directly, on the SAME
    # already-validated mapping this function just produced, rather than
    # the public aggregate_finding_lifecycle_v2 -- which independently
    # calls validate_chunk_results_scope_v2 itself for any OTHER caller.
    # Re-running that same check a second time here would be redundant,
    # not additional safety: this composed path is safe because it is the
    # SAME single authority, called once, not because either side trusts
    # the other's say-so.
    findings, provenance = _aggregate_finding_lifecycle_core_v2(
        manifest=manifest,
        results_by_chunk_id=results_by_chunk_id,
        evaluated_head_sha=evaluated_head_sha,
        prior_lifecycle=prior_lifecycle,
    )
    limitations = tuple(
        sorted({limitation for result in results_by_chunk_id.values() for limitation in result.limitations})
    )

    return SynthesisResultV2(
        run_id=manifest.run_id,
        evaluated_head_sha=evaluated_head_sha,
        coverage_report=coverage_report,
        findings=findings,
        provenance=provenance,
        limitations=limitations,
    )
