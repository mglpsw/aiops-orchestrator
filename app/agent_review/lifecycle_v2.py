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
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.agent_review.contracts_v2 import ChunkFindingV2, FindingDispositionV2, FindingLifecycleRecordV2
from app.agent_review.manifest_v2 import ManifestV2
from app.agent_review.parser_v2 import ParsedChunkResultV2

STALE_PRIOR_LIFECYCLE_REASON_V2 = "stale_prior_lifecycle_record"


class LifecycleAggregationError(ValueError):
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


def _dedup_key(finding: ChunkFindingV2) -> tuple[str, int | None, int | None, tuple[str, ...], str]:
    """Two findings collapse into one only if they name the same file, the
    same exact line range, the same set of violated contracts, and the
    same severity -- never merely the same title or evidence text. Two
    fragments never share a line range (planner_v2's own disjointness
    guarantee), so this key is already fragment-discriminating without
    needing a fragment_id field directly on ChunkFindingV2."""

    return (
        finding.file_path,
        finding.line_start,
        finding.line_end,
        tuple(sorted(finding.contract_ids)),
        finding.severity.value,
    )


def _synthesized_finding_id(key: tuple[str, int | None, int | None, tuple[str, ...], str]) -> str:
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


def aggregate_finding_lifecycle_v2(
    *,
    manifest: ManifestV2,
    chunk_results: Sequence[ParsedChunkResultV2],
    evaluated_head_sha: str,
    prior_lifecycle: Sequence[FindingLifecycleRecordV2] = (),
) -> tuple[tuple[FindingLifecycleRecordV2, ...], Mapping[str, tuple[FindingProvenanceV2, ...]]]:
    """Deduplicate every finding across ``chunk_results`` by root cause
    (file + exact line range + contract set + severity), preserving
    provenance for every chunk that observed it, and merge in any
    already-decided ``prior_lifecycle`` records.

    ``prior_lifecycle`` entries must already be revalidated for
    ``evaluated_head_sha`` -- i.e. ``record.observed_at_head_sha ==
    evaluated_head_sha`` -- by whatever produced them (a human decision
    process, or a caller re-stamping a persistent decision onto a new
    HEAD). This function does not, and cannot, fabricate that revalidation
    itself: it only accepts already-valid records or rejects fail-closed.
    A finding_id is deterministic (a hash of its dedup key), so the same
    underlying defect re-observed in a later run naturally matches a prior
    decision for it, letting that decision persist instead of reverting to
    ``new``.
    """

    for record in prior_lifecycle:
        if record.observed_at_head_sha != evaluated_head_sha:
            raise LifecycleAggregationError(STALE_PRIOR_LIFECYCLE_REASON_V2)

    observations: dict[tuple, list[tuple[str, ChunkFindingV2]]] = {}
    for result in chunk_results:
        for finding in result.findings:
            key = _dedup_key(finding)
            observations.setdefault(key, []).append((result.chunk_id, finding))

    prior_by_id = {record.finding_id: record for record in prior_lifecycle}

    findings_out: list[FindingLifecycleRecordV2] = []
    provenance_out: dict[str, tuple[FindingProvenanceV2, ...]] = {}

    for key, chunk_findings in observations.items():
        synthesized_id = _synthesized_finding_id(key)
        prior = prior_by_id.get(synthesized_id)
        if prior is not None:
            findings_out.append(prior)
        else:
            _, first_finding = chunk_findings[0]
            findings_out.append(
                FindingLifecycleRecordV2(
                    finding_id=synthesized_id,
                    severity=first_finding.severity,
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
