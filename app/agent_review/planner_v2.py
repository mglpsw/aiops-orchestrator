"""Lossless line-range multi-chunk planner (issue #84, foundational slice).

Given a list of already-parsed hunks (``HunkInputV2``) and a per-chunk line
budget, produces stable ``FragmentV2``/``ManifestChunkV2`` objects such that
every ``must_review`` hunk's changed lines are covered by exactly one chunk
-- never truncated, never silently dropped -- or planning is refused
(``blocked_pipeline``) instead of silently approving partial coverage.

This implements fallback #2/#3 from issue #84's own deterministic fallback
list ("hunk completo", then "hunk maior que budget -> janelas de linha
estáveis") for content already identified as belonging to one semantic
group. It deliberately does **not** implement:

* git-diff acquisition (parsing ``git diff --no-ext-diff --binary
  BASE...HEAD``, rename/deletion/binary/submodule/no-newline handling,
  patch reconstruction from blobs) -- callers must already have parsed
  hunks;
* symbol/AST-aware grouping (fallback #1) -- grouping by semantic group is
  assumed to already be decided by the caller;
* propagation into the builder/synthesizer/telemetry/readiness gate.

See ``docs/AGENT_REVIEW_V2_CHUNKING.md`` for exactly what remains of #84.

Auxiliary (non-``must_review``) hunks are context, not required coverage:
when budget is insufficient, auxiliary fragments are dropped first and
silently (matching the issue's "podem ser reduzidos somente contextos
auxiliares declarados"); ``must_review`` fragments are never dropped --
either they all fit within ``max_chunks``, or planning is refused.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, Sequence

from app.agent_review.manifest_v2 import (
    FragmentV2,
    LineRangeV2,
    ManifestChunkV2,
    ManifestDegradationV2,
    compute_fragment_id_v2,
)


@dataclass(frozen=True)
class HunkInputV2:
    """One already-parsed diff hunk, as produced by a (separate, not yet
    implemented) diff-acquisition step."""

    path: str
    hunk_index: int
    old_start: int
    old_end: int
    new_start: int
    new_end: int
    diff_sha256: str
    diff_chars: int
    must_review: bool


@dataclass(frozen=True)
class PlanningOutcomeV2:
    state: Literal["planned", "blocked_pipeline"]
    fragments: tuple[FragmentV2, ...]
    chunks: tuple[ManifestChunkV2, ...]
    degradation_causes: tuple[ManifestDegradationV2, ...]


def _fragment_size(fragment: FragmentV2) -> int:
    # "Changed lines" is measured on the new (current/HEAD) side, since
    # that is the content that must actually be reviewed. A pure-deletion
    # hunk (empty new_range) still costs at least 1 unit so it is never
    # silently free to pack.
    return max(fragment.new_range.end - fragment.new_range.start + 1, 1)


def _split_hunk_into_fragments(
    hunk: HunkInputV2, *, max_lines_per_chunk: int
) -> list[FragmentV2]:
    new_size = max(hunk.new_end - hunk.new_start + 1, 1)
    if new_size <= max_lines_per_chunk or hunk.new_end < hunk.new_start:
        # Whole hunk as one fragment (fallback #2), including deletion-only
        # hunks where new_end < new_start is not representable by
        # LineRangeV2 (start<=end) -- collapse to a single-point anchor.
        new_start = hunk.new_start
        new_end = max(hunk.new_end, hunk.new_start)
        new_range = LineRangeV2(start=new_start, end=new_end)
        old_range = LineRangeV2(start=hunk.old_start, end=max(hunk.old_end, hunk.old_start))
        fragment_id = compute_fragment_id_v2(
            path=hunk.path, old_range=old_range, new_range=new_range, diff_sha256=hunk.diff_sha256
        )
        return [
            FragmentV2(
                fragment_id=fragment_id,
                path=hunk.path,
                old_range=old_range,
                new_range=new_range,
                hunk_indexes=[hunk.hunk_index],
                diff_chars=hunk.diff_chars,
                diff_sha256=hunk.diff_sha256,
                coverage_required=hunk.must_review,
            )
        ]

    # Hunk exceeds budget: stable, disjoint line windows (fallback #3).
    # old_range is preserved verbatim on every window for provenance --
    # precise old-side sub-ranges require the actual diff bytes, which
    # this planner does not receive; that refinement belongs to the
    # diff-acquisition piece of #84.
    fragments: list[FragmentV2] = []
    old_range = LineRangeV2(start=hunk.old_start, end=max(hunk.old_end, hunk.old_start))
    window_start = hunk.new_start
    while window_start <= hunk.new_end:
        window_end = min(window_start + max_lines_per_chunk - 1, hunk.new_end)
        window_size = window_end - window_start + 1
        new_range = LineRangeV2(start=window_start, end=window_end)
        fragment_id = compute_fragment_id_v2(
            path=hunk.path, old_range=old_range, new_range=new_range, diff_sha256=hunk.diff_sha256
        )
        fragments.append(
            FragmentV2(
                fragment_id=fragment_id,
                path=hunk.path,
                old_range=old_range,
                new_range=new_range,
                hunk_indexes=[hunk.hunk_index],
                diff_chars=max(round(hunk.diff_chars * window_size / new_size), 0),
                diff_sha256=hunk.diff_sha256,
                coverage_required=hunk.must_review,
            )
        )
        window_start = window_end + 1
    return fragments


def _pack_fragments(
    fragments: Sequence[FragmentV2], *, max_lines_per_chunk: int
) -> list[list[FragmentV2]]:
    """First-fit-decreasing bin packing by fragment size. Deterministic:
    ties in size are broken by fragment_id so packing is stable across
    equivalent runs."""

    ordered = sorted(fragments, key=lambda f: (-_fragment_size(f), f.fragment_id))
    bins: list[list[FragmentV2]] = []
    totals: list[int] = []
    for fragment in ordered:
        size = _fragment_size(fragment)
        for index, total in enumerate(totals):
            if total + size <= max_lines_per_chunk:
                bins[index].append(fragment)
                totals[index] = total + size
                break
        else:
            bins.append([fragment])
            totals.append(size)
    return bins


def plan_lossless_chunks_v2(
    hunks: Sequence[HunkInputV2],
    *,
    semantic_group: str,
    max_lines_per_chunk: int,
    max_chunks: int,
    chunk_id_prefix: str = "chunk",
) -> PlanningOutcomeV2:
    if max_lines_per_chunk < 1:
        raise ValueError("max_lines_per_chunk must be at least 1")
    if max_chunks < 1:
        raise ValueError("max_chunks must be at least 1")

    all_fragments: list[FragmentV2] = []
    for hunk in hunks:
        all_fragments.extend(_split_hunk_into_fragments(hunk, max_lines_per_chunk=max_lines_per_chunk))

    required = [fragment for fragment in all_fragments if fragment.coverage_required]
    auxiliary = [fragment for fragment in all_fragments if not fragment.coverage_required]

    required_bins = _pack_fragments(required, max_lines_per_chunk=max_lines_per_chunk)
    if len(required_bins) > max_chunks:
        cause = ManifestDegradationV2(
            reason_code="budget_exhausted",
            affected_fragment_ids=[fragment.fragment_id for fragment in required],
            detail=(
                f"{len(required_bins)} chunks are required to cover must_review "
                f"content but max_chunks={max_chunks}"
            ),
        )
        return PlanningOutcomeV2(
            state="blocked_pipeline",
            fragments=tuple(all_fragments),
            chunks=(),
            degradation_causes=(cause,),
        )

    bins = [list(group) for group in required_bins]
    totals = [sum(_fragment_size(fragment) for fragment in group) for group in bins]

    # Best-effort packing of auxiliary (non-required) context into leftover
    # budget, then into any remaining chunk slots. Auxiliary fragments that
    # still do not fit are dropped -- never flagged as a degradation cause,
    # since they were never required coverage.
    for fragment in sorted(auxiliary, key=lambda f: (-_fragment_size(f), f.fragment_id)):
        size = _fragment_size(fragment)
        placed = False
        for index, total in enumerate(totals):
            if total + size <= max_lines_per_chunk:
                bins[index].append(fragment)
                totals[index] = total + size
                placed = True
                break
        if not placed and len(bins) < max_chunks:
            bins.append([fragment])
            totals.append(size)

    # Every fragment is kept in the manifest's fragment list, including any
    # auxiliary fragment that was dropped for lack of budget: an unreferenced
    # auxiliary fragment is auditable (visible, explicitly not covered) but
    # never a validation error, since only coverage_required fragments must
    # be referenced-or-degraded.
    chunks = tuple(
        ManifestChunkV2(
            chunk_id=f"{chunk_id_prefix}-{order_index:04d}",
            order_index=order_index,
            semantic_group=semantic_group,
            fragment_ids=[fragment.fragment_id for fragment in group],
            payload_sha256=None,
        )
        for order_index, group in enumerate(bins)
    )

    return PlanningOutcomeV2(
        state="planned",
        fragments=tuple(all_fragments),
        chunks=chunks,
        degradation_causes=(),
    )
