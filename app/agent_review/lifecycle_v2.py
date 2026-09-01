"""Finding lifecycle aggregation for AgentReview v2 synthesis (issue #107).

Turns the raw ``ChunkFindingV2`` objects scattered across N already-bound
``ParsedChunkResultV2`` results into a deduplicated set of
``FindingLifecycleRecordV2`` (``contracts_v2.py``, frozen, reused unmodified)
plus a provenance record of every chunk observation that fed each one.

The vocabulary is exactly, and only, what ``FindingDispositionV2`` defines:

    new -- confirmed -- fixed -- dismissed -- superseded -- stale

There is no ``rejected`` and no ``inconclusive``. A caller who means
"rejected" uses ``dismissed`` (which requires ``justification`` and typed
``evidence`` -- ``FindingLifecycleRecordV2.validate_disposition_metadata``,
``contracts_v2.py:990-1021``, already enforces that). A caller who means
"inconclusive" represents it as a run-level limitation, or as
``model_uncertainty`` in the pipeline/coverage sense -- never as a finding
disposition, because none of these words are values this enum has.

This module never creates ``confirmed``, ``fixed``, ``dismissed``,
``superseded``, or ``stale`` on its own initiative. Every finding freshly
observed in a chunk result enters as ``new`` (matching ``ChunkFindingV2``'s
own constructor-level guarantee that a provider finding is always ``new``).
Any other disposition can only be *preserved* -- carried forward from an
already-decided, already-revalidated ``FindingLifecycleRecordV2`` a caller
supplies, never synthesized from concordance between chunks or models:
two chunks (or two different models) reporting the identical finding still
collapses to one ``new`` record, not a ``confirmed`` one.

``ReadinessStateV2.STALE`` is a different concept entirely, computed later
by #108 from HEAD/identity divergence at the run level -- not from any
finding's own ``disposition``. Nothing here conflates the two.

``aggregate_finding_lifecycle_v2`` is a public entry point in its own
right, independent of ``synthesis_v2.synthesize_chunk_results_v2`` -- so it
revalidates ``chunk_results`` itself, via the same shared
``chunk_result_scope_v2.validate_chunk_results_scope_v2`` authority
``synthesize_chunk_results_v2`` uses, rather than trusting that some other
caller already did. There is no "already validated" flag to skip this: a
direct call with an out-of-scope ``chunk_id``, a stale HEAD, or a finding
claiming a path/range outside its chunk is rejected here exactly as it
would be there.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.agent_review.chunk_result_scope_v2 import ChunkResultScopeError, validate_chunk_results_scope_v2
from app.agent_review.contracts_v2 import (
    ChunkFindingV2,
    FindingDispositionV2,
    FindingLifecycleRecordV2,
    FindingSeverityV2,
)
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.parser_v2 import ParsedChunkResultV2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

_SEVERITY_RANK_V2 = {
    FindingSeverityV2.P0: 0,
    FindingSeverityV2.P1: 1,
    FindingSeverityV2.P2: 2,
    FindingSeverityV2.P3: 3,
}

STALE_PRIOR_LIFECYCLE_REASON_V2 = "stale_prior_lifecycle_record"
DUPLICATE_PRIOR_LIFECYCLE_FINDING_REASON_V2 = "duplicate_prior_lifecycle_finding"
STALE_PRIOR_LIFECYCLE_DECISION_REASON_V2 = "stale_prior_lifecycle_decision"
STALE_PRIOR_LIFECYCLE_EVIDENCE_REASON_V2 = "stale_prior_lifecycle_evidence"
PRIOR_LIFECYCLE_SEVERITY_MISMATCH_REASON_V2 = "prior_lifecycle_severity_mismatch"


class LifecycleAggregationError(ExpectedOperationalRefusalV2, ValueError):
    """Raised for a lifecycle aggregation failure. Carries a stable
    ``reason_code`` only."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class FindingProvenanceV2:
    """One chunk's observation of a finding that was merged into a
    deduplicated ``FindingLifecycleRecordV2``. Provenance is not part of
    the frozen lifecycle contract itself (``FindingLifecycleRecordV2`` has
    no field for it) -- it is orchestration-level detail this module keeps
    alongside the record, matching ``ParsedChunkResultV2``'s own status as
    "a plain data value... not a wire contract"."""

    chunk_id: str
    fragment_id: str | None
    original_finding_id: str


def _dedup_key(file_level_scope_id: tuple[str, ...], finding: ChunkFindingV2) -> tuple[object, ...]:
    """Two findings collapse into one only if they share the same root
    cause -- never merely the same title or evidence text, and NOT
    severity.

    Severity is deliberately excluded from the key (Finding from PR #117's
    Codex review): the same underlying defect can legitimately be
    re-observed at a different severity across rounds (a model or human
    reclassifying it). Including severity in the identity preimage would
    give that re-observation a DIFFERENT finding_id, so it would never
    match the prior record for the same root cause -- the prior would sit
    unobserved-but-persisted while a second, spurious ``new`` record
    appeared for what is really the same defect, and the severity-mismatch
    guard in ``_aggregate_finding_lifecycle_core_v2`` could never actually
    fire for a naturally-drifted severity, only for a hand-forged prior
    record. Severity is compared AFTER matching by this severity-free key,
    not folded into the key itself.

    For a finding with a line range, root cause is file + exact range +
    contract set: two fragments never share a line range (planner_v2's own
    disjointness guarantee), so this is already fragment-discriminating
    without needing a fragment_id field directly on ChunkFindingV2.

    A file-level finding (no range) has no such fragment-discriminating
    signal -- file + contracts alone would collapse two genuinely distinct
    file-level claims from two different chunks on the same structurally
    divided path into one record, destroying the per-chunk provenance
    separation the coverage report itself preserves (see synthesis_v2.py's
    structural_split handling). ``file_level_scope_id`` is folded into the
    key for this case instead of the chunk's own ``chunk_id``: it is the
    sorted tuple of ``fragment_id``s (content-derived hashes of
    path/range/diff, ``compute_fragment_id_v2``) that this path contributes
    to this specific chunk -- never a chunk's positional label
    (``planner_v2.plan_lossless_chunks_v2`` assigns ``chunk_id`` as
    ``f"{prefix}-{order_index:04d}"``, a bin-packing ordinal, not content).
    ``manifest_v2.py`` guarantees a fragment is never assigned to more than
    one chunk, so this set is chunk-exclusive by contract -- using it keeps
    two chunks' independent file-level claims from being conflated exactly
    as ``chunk_id`` did, but WITHOUT tying finding identity to how the
    packer happened to order or label its bins. Two identical file-level
    findings observed by the *same* chunk still dedupe, since the fragment
    set for that path is then equal; re-planning the identical diff content
    into a differently-ORDERED or differently-LABELED (but same-fragment)
    chunk assignment no longer changes the finding_id."""

    if finding.line_start is None or finding.line_end is None:
        return ("file_level", finding.file_path, file_level_scope_id, tuple(sorted(finding.contract_ids)))
    return (
        "ranged",
        finding.file_path,
        finding.line_start,
        finding.line_end,
        tuple(sorted(finding.contract_ids)),
    )


def _most_severe_v2(findings: Sequence[ChunkFindingV2]) -> FindingSeverityV2:
    """Deterministic, order-independent choice of severity for a group of
    findings that share a root-cause key but may disagree on severity
    (possible now that severity is excluded from the key -- see
    ``_dedup_key``): the most severe value wins, never an arbitrary first
    element of the (possibly caller-order-dependent) observation list."""

    return min((finding.severity for finding in findings), key=lambda severity: _SEVERITY_RANK_V2[severity])


def _synthesized_finding_id(key: tuple[object, ...]) -> str:
    canonical = json.dumps(list(key), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _fragment_id_for_finding(manifest: ManifestV2, finding: ChunkFindingV2) -> str | None:
    if finding.line_start is None or finding.line_end is None:
        return None
    for fragment in manifest.fragments:
        if fragment.path != finding.file_path:
            continue
        if fragment.new_range.start <= finding.line_start and finding.line_end <= fragment.new_range.end:
            return fragment.fragment_id
    return None


def _validate_prior_lifecycle_v2(
    prior_lifecycle: Sequence[FindingLifecycleRecordV2], evaluated_head_sha: str
) -> Mapping[str, FindingLifecycleRecordV2]:
    """Revalidate every ``prior_lifecycle`` record before it is trusted to
    be merged or preserved. A caller-supplied record is untrusted input,
    just like ``ParsedChunkResultV2`` (synthesis_v2.py) -- ``FindingLifecycleRecordV2``
    itself enforces internal consistency (e.g. dismissed requires
    justification/evidence), but nothing in the contract enforces that a
    record was genuinely revalidated for *this* ``evaluated_head_sha``, nor
    that two records don't silently claim the same ``finding_id``, nor that
    a reobserved finding's severity actually agrees with what the prior
    decision was made against."""

    prior_by_id: dict[str, FindingLifecycleRecordV2] = {}
    for record in prior_lifecycle:
        if record.finding_id in prior_by_id:
            raise LifecycleAggregationError(DUPLICATE_PRIOR_LIFECYCLE_FINDING_REASON_V2)
        prior_by_id[record.finding_id] = record

        if record.observed_at_head_sha != evaluated_head_sha:
            raise LifecycleAggregationError(STALE_PRIOR_LIFECYCLE_REASON_V2)

        if record.disposition is not FindingDispositionV2.NEW:
            if record.decided_at_head_sha != evaluated_head_sha:
                raise LifecycleAggregationError(STALE_PRIOR_LIFECYCLE_DECISION_REASON_V2)
            for evidence in record.evidence:
                if evidence.head_sha != evaluated_head_sha:
                    raise LifecycleAggregationError(STALE_PRIOR_LIFECYCLE_EVIDENCE_REASON_V2)

    return prior_by_id


def aggregate_finding_lifecycle_v2(
    *,
    manifest: ManifestV2,
    chunk_results: Sequence[ParsedChunkResultV2],
    evaluated_head_sha: str,
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> tuple[tuple[FindingLifecycleRecordV2, ...], Mapping[str, tuple[FindingProvenanceV2, ...]]]:
    """Deduplicate every finding across ``chunk_results`` by root cause
    (file + exact line range + contract set -- deliberately NOT severity,
    see ``_dedup_key``), preserving provenance for every chunk that
    observed it, and merge in any already-decided ``prior_lifecycle``
    records.

    ``chunk_results`` is revalidated against ``manifest`` by
    ``chunk_result_scope_v2.validate_chunk_results_scope_v2`` before any
    finding in it is trusted -- this function is a public entry point in
    its own right and does not assume some other caller already did this.

    ``prior_lifecycle`` entries must already be revalidated for
    ``evaluated_head_sha`` -- i.e. ``record.observed_at_head_sha ==
    evaluated_head_sha``, and for any non-``new`` disposition,
    ``decided_at_head_sha`` and every ``DispositionEvidenceV2.head_sha`` as
    well -- by whatever produced them (a human decision process, or a
    caller re-stamping a persistent decision onto a new HEAD). This
    function does not, and cannot, fabricate that revalidation itself: it
    only accepts already-valid records or rejects fail-closed. A
    finding_id is deterministic (a hash of its dedup key), so the same
    underlying defect re-observed in a later run naturally matches a prior
    decision for it, letting that decision persist instead of reverting to
    ``new`` -- but only if the prior's own recorded severity still agrees
    with what is observed now; a mismatch is rejected fail-closed, never
    silently overwritten by the freshly observed value.
    """

    try:
        results_by_chunk_id = validate_chunk_results_scope_v2(
            manifest=manifest, chunk_results=chunk_results, evaluated_head_sha=evaluated_head_sha
        )
    except ChunkResultScopeError as exc:
        raise LifecycleAggregationError(exc.reason_code) from exc

    return _aggregate_finding_lifecycle_core_v2(
        manifest=manifest,
        results_by_chunk_id=results_by_chunk_id,
        evaluated_head_sha=evaluated_head_sha,
        prior_lifecycle=prior_lifecycle,
    )


def _aggregate_finding_lifecycle_core_v2(
    *,
    manifest: ManifestV2,
    results_by_chunk_id: Mapping[str, ParsedChunkResultV2],
    evaluated_head_sha: str,
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> tuple[tuple[FindingLifecycleRecordV2, ...], Mapping[str, tuple[FindingProvenanceV2, ...]]]:
    """Internal core shared by ``aggregate_finding_lifecycle_v2`` and
    ``synthesis_v2.synthesize_chunk_results_v2``. Takes a ``chunk_id``-keyed
    mapping that MUST already have passed
    ``chunk_result_scope_v2.validate_chunk_results_scope_v2`` -- this
    function performs no scope revalidation of its own, precisely so that
    the composed ``synthesize_chunk_results_v2`` path (which validates once
    to build its coverage report, then reuses the same validated mapping
    here) does not pay for that check twice. Never call this directly with
    an unvalidated mapping; the only two call sites are the public wrapper
    above and ``synthesis_v2.py``.
    """

    prior_by_id = _validate_prior_lifecycle_v2(prior_lifecycle, evaluated_head_sha)

    # Content-derived discriminator for file-level (no-range) findings:
    # for each (chunk_id, path), the sorted tuple of fragment_ids that path
    # contributes to that chunk. See _dedup_key's docstring for why this
    # replaces the chunk's own (positional, bin-packing-ordinal) chunk_id.
    fragment_by_id = {fragment.fragment_id: fragment for fragment in manifest.fragments}
    file_level_scope_by_chunk_and_path: dict[tuple[str, str], tuple[str, ...]] = {}
    for chunk in manifest.chunks:
        fragment_ids_by_path: dict[str, list[str]] = {}
        for fragment_id in chunk.fragment_ids:
            fragment_ids_by_path.setdefault(fragment_by_id[fragment_id].path, []).append(fragment_id)
        for path, fragment_ids in fragment_ids_by_path.items():
            file_level_scope_by_chunk_and_path[(chunk.chunk_id, path)] = tuple(sorted(fragment_ids))

    observations: dict[tuple, list[tuple[str, ChunkFindingV2]]] = {}
    for result in results_by_chunk_id.values():
        for finding in result.findings:
            file_level_scope_id = file_level_scope_by_chunk_and_path.get((result.chunk_id, finding.file_path), ())
            key = _dedup_key(file_level_scope_id, finding)
            observations.setdefault(key, []).append((result.chunk_id, finding))

    findings_out: list[FindingLifecycleRecordV2] = []
    provenance_out: dict[str, tuple[FindingProvenanceV2, ...]] = {}

    for key, chunk_findings in observations.items():
        synthesized_id = _synthesized_finding_id(key)
        observed_severity = _most_severe_v2([finding for _, finding in chunk_findings])
        prior = prior_by_id.get(synthesized_id)
        if prior is not None:
            if prior.severity != observed_severity:
                raise LifecycleAggregationError(PRIOR_LIFECYCLE_SEVERITY_MISMATCH_REASON_V2)
            findings_out.append(prior)
        else:
            findings_out.append(
                FindingLifecycleRecordV2(
                    finding_id=synthesized_id,
                    severity=observed_severity,
                    observed_at_head_sha=evaluated_head_sha,
                    disposition=FindingDispositionV2.NEW,
                    actionable=True,
                    justification=None,
                    decided_by=None,
                    decided_at_head_sha=None,
                    evidence=[],
                    superseded_by=None,
                )
            )
        provenance_out[synthesized_id] = tuple(
            sorted(
                (
                    FindingProvenanceV2(
                        chunk_id=chunk_id,
                        fragment_id=_fragment_id_for_finding(manifest, finding),
                        original_finding_id=finding.finding_id,
                    )
                    for chunk_id, finding in chunk_findings
                ),
                key=lambda p: (p.chunk_id, p.original_finding_id),
            )
        )

    # A prior decision that was not re-observed in this round's chunk
    # results still persists -- a human disposition does not silently
    # expire just because this particular round did not re-detect it.
    observed_ids = {record.finding_id for record in findings_out}
    for record in prior_lifecycle:
        if record.finding_id not in observed_ids:
            findings_out.append(record)

    findings_out.sort(key=lambda record: record.finding_id)
    return tuple(findings_out), provenance_out
