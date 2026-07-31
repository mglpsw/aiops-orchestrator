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
import math
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
    # The larger of the two sides, since either can dominate: a normal
    # modification's changed content is measured on the new (current/HEAD)
    # side, but a deletion-only hunk has no new content at all (new_range
    # collapses to a single-point anchor) and its real cost is the old-side
    # range being removed. Using max() of both means a large deletion is
    # never under-counted as free (or near-free) to pack -- it must be
    # split and budgeted like any other large hunk.
    old_size = fragment.old_range.end - fragment.old_range.start + 1
    new_size = fragment.new_range.end - fragment.new_range.start + 1
    return max(old_size, new_size, 1)


def _side_total(start: int, end: int) -> int:
    """0 for a collapsed/empty side (end < start, e.g. the new side of a
    pure deletion or the old side of a pure insertion), else the inclusive
    line count."""

    return max(end - start + 1, 0) if end >= start else 0


def _proportional_window(start: int, end: int, *, window_index: int, window_count: int) -> tuple[int, int]:
    """Split the inclusive range [start, end] into `window_count`
    contiguous, disjoint sub-ranges of nearly equal size (remainder lines
    distributed to the first windows) and return the sub-range for
    `window_index` (0-based). Deterministic and stable across runs.

    When `total < window_count` (this side has fewer lines than the
    number of windows the *other*, larger side requires -- e.g. replacing
    1 old line with 1,000 new lines at a 100-line budget), the naive
    proportional formula produces `window_size == 0` for most windows,
    which yields `end == start - 1`: an inverted range that ``LineRangeV2``
    rejects. Each of the first `total` windows instead gets one line of
    its own; any remaining windows anchor to the last available line
    rather than producing an empty/inverted range. This side is never the
    one enforcing the budget in that case (the larger side's own window
    count guarantees `total >= window_count` there -- see
    `_split_hunk_into_fragments`), so a repeated single-point anchor here
    is a safe, valid placeholder, not a coverage requirement.
    """

    total = end - start + 1
    if total <= window_count:
        position = min(window_index, total - 1)
        point = start + position
        return point, point
    base, remainder = divmod(total, window_count)
    window_size = base + (1 if window_index < remainder else 0)
    offset = window_index * base + min(window_index, remainder)
    window_start = start + offset
    window_end = window_start + window_size - 1
    return window_start, window_end


def _split_hunk_into_fragments(
    hunk: HunkInputV2, *, max_lines_per_chunk: int
) -> list[FragmentV2]:
    old_total = _side_total(hunk.old_start, hunk.old_end)
    new_total = _side_total(hunk.new_start, hunk.new_end)
    # The larger side determines both whether splitting is needed and how
    # many windows are required -- a deletion-only hunk (new_total == 0)
    # is windowed by its old-side size instead of being treated as free.
    effective_total = max(old_total, new_total, 1)

    if effective_total <= max_lines_per_chunk:
        # Whole hunk as one fragment (fallback #2). A collapsed side
        # (old_total or new_total == 0, i.e. pure insertion or pure
        # deletion) is represented as a single-point anchor, since
        # LineRangeV2 requires start <= end and there is no real range on
        # that side to report.
        new_range = LineRangeV2(start=hunk.new_start, end=max(hunk.new_end, hunk.new_start))
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

    # Hunk exceeds budget on its larger side: stable, disjoint windows
    # (fallback #3), proportionally on BOTH sides so neither dominates a
    # window's real size -- windowing only the new side (as a prior
    # revision did) let a large old-side deletion or a hunk kept "verbatim"
    # on the untouched side silently exceed the advertised budget.
    window_count = math.ceil(effective_total / max_lines_per_chunk)
    fragments: list[FragmentV2] = []
    for window_index in range(window_count):
        if new_total > 0:
            window_new_start, window_new_end = _proportional_window(
                hunk.new_start, hunk.new_end, window_index=window_index, window_count=window_count
            )
        else:
            window_new_start = window_new_end = hunk.new_start
        if old_total > 0:
            window_old_start, window_old_end = _proportional_window(
                hunk.old_start, hunk.old_end, window_index=window_index, window_count=window_count
            )
        else:
            window_old_start = window_old_end = hunk.old_start

        new_range = LineRangeV2(start=window_new_start, end=window_new_end)
        old_range = LineRangeV2(start=window_old_start, end=window_old_end)
        fragment_id = compute_fragment_id_v2(
            path=hunk.path, old_range=old_range, new_range=new_range, diff_sha256=hunk.diff_sha256
        )
        window_size = max(window_new_end - window_new_start + 1, window_old_end - window_old_start + 1)
        fragments.append(
            FragmentV2(
                fragment_id=fragment_id,
                path=hunk.path,
                old_range=old_range,
                new_range=new_range,
                hunk_indexes=[hunk.hunk_index],
                diff_chars=max(round(hunk.diff_chars * window_size / effective_total), 0),
                diff_sha256=hunk.diff_sha256,
                coverage_required=hunk.must_review,
            )
        )
    return fragments


#: Safety bound on the exact packer's search, in backtracking calls. Chosen
#: to keep worst-case CPU bounded for pathological inputs (e.g. many
#: same-sized fragments that are trivially, but not cheaply, infeasible)
#: without materially limiting realistic single-semantic-group fragment
#: counts. Bin packing is NP-hard: no finite bound can guarantee finding
#: every feasible packing before giving up (a numerically adversarial
#: exact-fit instance -- e.g. 25 fragments whose sizes sum to *exactly*
#: capacity*max_bins, forcing zero slack in every bin -- can still exhaust
#: this budget even with the pruning below, in which case the search
#: reports "cannot confirm" rather than hanging). This is a deliberate,
#: documented trade-off for this foundational planner slice, not a claim
#: of guaranteed optimality on arbitrary input.
_MAX_EXACT_PACKING_STATES = 200_000

#: Safety bound on the number of fragments the exact packer will even
#: attempt: recursion depth equals fragment count, and Python's default
#: recursion limit (~1000) is a hard ceiling regardless of the state
#: budget above. A single semantic group needing more than this many
#: *required* fragments is already far outside what this foundational
#: planner slice is sized for.
_MAX_EXACT_PACKING_FRAGMENTS = 500


class _ExactPackingSearchExhausted(Exception):
    """Raised internally when the state budget is exceeded before the
    search could prove either a packing or infeasibility. Caught by
    ``_pack_fragments_exact`` and reported as ``search_exhausted`` --
    deliberately distinct from ``proven_infeasible``, since a valid
    packing may still exist; the search simply did not find it in time."""


@dataclass(frozen=True)
class ExactPackingResultV2:
    """Three-way outcome, not a boolean: ``found`` (a packing exists and
    is returned), ``proven_infeasible`` (mathematically cannot fit --
    single fragment too large, or total size exceeds capacity*max_bins, or
    the search exhausted every arrangement without finding one), and
    ``search_exhausted``/``input_too_large`` (the bounded search could not
    determine an answer either way). Collapsing the latter two into
    ``proven_infeasible`` would misrepresent "we don't know" as "it
    doesn't fit"."""

    status: Literal["found", "proven_infeasible", "search_exhausted", "input_too_large"]
    bins: tuple[tuple[FragmentV2, ...], ...] | None = None


def _pack_fragments_exact(
    fragments: Sequence[FragmentV2], *, capacity: int, max_bins: int
) -> ExactPackingResultV2:
    """Exact bin-packing decision procedure: can these fragments fit into
    at most ``max_bins`` bins of the given ``capacity``?

    Used only for ``coverage_required`` fragments, where the answer gates
    ``blocked_pipeline`` -- a merely-heuristic packer (first-fit-decreasing
    is not guaranteed optimal; a small counterexample exists at sizes like
    ``[6, 5, 3, 2, 2, 2]`` with two bins of capacity 10) could wrongly
    report content as not fitting when a valid arrangement exists, which
    would block a pipeline that didn't need to be blocked. Backtracking
    with duplicate-remaining-capacity and admissible suffix-sum pruning
    keeps this tractable for most fragment counts a single semantic group
    produces; it is not intended for arbitrarily large inputs, so bounds
    on both search states and input size cap worst-case cost. Bin packing
    is NP-hard, so those bounds cannot guarantee an answer on every
    input -- see ``ExactPackingResultV2`` for how that is reported
    honestly rather than folded into "infeasible".
    """

    ordered = sorted(fragments, key=lambda f: (-_fragment_size(f), f.fragment_id))
    sizes = [_fragment_size(fragment) for fragment in ordered]

    if any(size > capacity for size in sizes):
        return ExactPackingResultV2(status="proven_infeasible")  # one fragment alone can never fit
    if sum(sizes) > capacity * max_bins:
        return ExactPackingResultV2(status="proven_infeasible")  # infeasible by total size -- no search needed
    if len(ordered) > _MAX_EXACT_PACKING_FRAGMENTS:
        return ExactPackingResultV2(status="input_too_large")
    if not ordered:
        return ExactPackingResultV2(status="found", bins=())

    # Suffix sums let each node cheaply check "can what's left even fit in
    # the room left?" -- an admissible prune (never eliminates a feasible
    # branch) that materially reduces the search for many instances,
    # though it cannot make an NP-hard exact-fit instance tractable in
    # general (see _MAX_EXACT_PACKING_STATES).
    suffix_sums = [0] * (len(sizes) + 1)
    for i in range(len(sizes) - 1, -1, -1):
        suffix_sums[i] = suffix_sums[i + 1] + sizes[i]

    bins: list[list[FragmentV2]] = []
    remaining: list[int] = []
    states_explored = 0

    def backtrack(index: int) -> bool:
        nonlocal states_explored
        states_explored += 1
        if states_explored > _MAX_EXACT_PACKING_STATES:
            raise _ExactPackingSearchExhausted()
        if index == len(ordered):
            return True

        remaining_room = sum(remaining) + (max_bins - len(bins)) * capacity
        if suffix_sums[index] > remaining_room:
            return False

        fragment = ordered[index]
        size = sizes[index]

        tried_remaining: set[int] = set()
        for bin_index in range(len(bins)):
            room = remaining[bin_index]
            if room in tried_remaining:
                continue  # equivalent bin state already explored at this depth
            tried_remaining.add(room)
            if room >= size:
                bins[bin_index].append(fragment)
                remaining[bin_index] = room - size
                if backtrack(index + 1):
                    return True
                remaining[bin_index] = room
                bins[bin_index].pop()

        if len(bins) < max_bins and size <= capacity:
            bins.append([fragment])
            remaining.append(capacity - size)
            if backtrack(index + 1):
                return True
            bins.pop()
            remaining.pop()

        return False

    try:
        found = backtrack(0)
    except _ExactPackingSearchExhausted:
        return ExactPackingResultV2(status="search_exhausted")
    if not found:
        return ExactPackingResultV2(status="proven_infeasible")
    return ExactPackingResultV2(status="found", bins=tuple(tuple(group) for group in bins))


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

    packing_result = _pack_fragments_exact(
        required, capacity=max_lines_per_chunk, max_bins=max_chunks
    )
    if packing_result.status != "found":
        reason_code, detail = {
            "proven_infeasible": (
                "budget_exhausted",
                f"must_review content cannot be packed into max_chunks={max_chunks} "
                f"chunks of max_lines_per_chunk={max_lines_per_chunk} -- proven infeasible",
            ),
            "search_exhausted": (
                "packing_search_exhausted",
                f"the exact packing search could not confirm within its bounded search "
                f"budget whether must_review content fits into max_chunks={max_chunks} "
                f"chunks of max_lines_per_chunk={max_lines_per_chunk} -- a valid packing "
                f"may exist but was not found within the search limit",
            ),
            "input_too_large": (
                "planner_limit_exceeded",
                f"{len(required)} required fragments exceed this planner's safe exact-search "
                f"input limit before any packing search was attempted",
            ),
        }[packing_result.status]
        cause = ManifestDegradationV2(
            reason_code=reason_code,
            affected_fragment_ids=[fragment.fragment_id for fragment in required],
            detail=detail,
        )
        return PlanningOutcomeV2(
            state="blocked_pipeline",
            fragments=tuple(all_fragments),
            chunks=(),
            degradation_causes=(cause,),
        )

    assert packing_result.bins is not None
    bins = [list(group) for group in packing_result.bins]
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
