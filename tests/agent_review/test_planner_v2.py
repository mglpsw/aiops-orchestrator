from __future__ import annotations

import hashlib

import pytest

from app.agent_review.planner_v2 import (
    HunkInputV2,
    plan_lossless_chunks_v2,
)


def _hunk(
    path: str = "app/service.py",
    *,
    index: int = 0,
    start: int = 1,
    end: int = 10,
    old_start: int | None = None,
    old_end: int | None = None,
    must_review: bool = True,
    diff_chars: int = 100,
    diff_sha256: str | None = None,
) -> HunkInputV2:
    return HunkInputV2(
        path=path,
        hunk_index=index,
        old_start=old_start if old_start is not None else start,
        old_end=old_end if old_end is not None else end,
        new_start=start,
        new_end=end,
        diff_sha256=diff_sha256 or hashlib.sha256(f"{path}:{index}:{start}:{end}".encode()).hexdigest(),
        diff_chars=diff_chars,
        must_review=must_review,
    )


def _covered_ranges(fragments) -> list[tuple[int, int]]:  # noqa: ANN001
    return sorted((fragment.new_range.start, fragment.new_range.end) for fragment in fragments)


# -- 1. a single file with many hunks, diff larger than several payloads --


def test_plan_splits_a_large_single_hunk_into_lossless_windows() -> None:
    hunks = [_hunk(start=1, end=500)]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    assert _covered_ranges(outcome.fragments) == [(1, 100), (101, 200), (201, 300), (301, 400), (401, 500)]
    # union of covered ranges == union of input ranges: no gap, no overlap.
    covered_lines: set[int] = set()
    for fragment in outcome.fragments:
        rng = range(fragment.new_range.start, fragment.new_range.end + 1)
        assert not (covered_lines & set(rng)), "overlapping fragments"
        covered_lines.update(rng)
    assert covered_lines == set(range(1, 501))


# -- 2. a single hunk larger than budget -----------------------------------


def test_plan_never_shrinks_a_must_review_hunk_larger_than_budget() -> None:
    hunks = [_hunk(start=1, end=250)]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="tests", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    total_lines = sum(f.new_range.end - f.new_range.start + 1 for f in outcome.fragments)
    assert total_lines == 250


# -- 3. a few large files in the same group --------------------------------


def test_plan_covers_multiple_large_files_in_the_same_group() -> None:
    hunks = [
        _hunk("app/a.py", index=0, start=1, end=150),
        _hunk("app/b.py", index=0, start=1, end=150),
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    by_path: dict[str, int] = {}
    for fragment in outcome.fragments:
        by_path[fragment.path] = by_path.get(fragment.path, 0) + (
            fragment.new_range.end - fragment.new_range.start + 1
        )
    assert by_path == {"app/a.py": 150, "app/b.py": 150}


# -- 4. API/service/tests relationship preserved (same semantic_group) ----


def test_plan_keeps_all_fragments_of_a_group_addressable_by_the_same_chunks_or_more() -> None:
    hunks = [
        _hunk("app/api.py", index=0, start=1, end=40),
        _hunk("app/service.py", index=0, start=1, end=40),
        _hunk("tests/test_api.py", index=0, start=1, end=40),
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="api_schema_contract", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    assert {fragment.path for fragment in outcome.fragments} == {
        "app/api.py",
        "app/service.py",
        "tests/test_api.py",
    }
    assert all(chunk.semantic_group == "api_schema_contract" for chunk in outcome.chunks)


# -- 5. insufficient total cap -> blocked_pipeline --------------------------


def test_plan_blocks_the_pipeline_when_max_chunks_is_insufficient() -> None:
    hunks = [_hunk(start=1, end=500)]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=2
    )
    assert outcome.state == "blocked_pipeline"
    assert outcome.chunks == ()
    assert len(outcome.degradation_causes) == 1
    cause = outcome.degradation_causes[0]
    assert cause.reason_code == "budget_exhausted"
    assert set(cause.affected_fragment_ids) == {fragment.fragment_id for fragment in outcome.fragments}


def test_plan_never_returns_a_planned_state_with_partial_required_coverage() -> None:
    """A budget too small for required content must never come back as
    'planned' with only some of the required lines assigned."""

    hunks = [_hunk(start=1, end=500)]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=2
    )
    if outcome.state == "planned":
        covered = {
            line
            for chunk in outcome.chunks
            for fragment in outcome.fragments
            if fragment.fragment_id in chunk.fragment_ids
            for line in range(fragment.new_range.start, fragment.new_range.end + 1)
        }
        assert covered == set(range(1, 501))
    else:
        assert outcome.state == "blocked_pipeline"


# -- must_review completeness never silently dropped ------------------------


def test_plan_never_drops_a_must_review_fragment_in_the_planned_state() -> None:
    hunks = [
        _hunk("app/a.py", index=0, start=1, end=80, must_review=True),
        _hunk("app/b.py", index=0, start=1, end=80, must_review=True),
        _hunk("app/c.py", index=0, start=1, end=80, must_review=True),
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    required_ids = {f.fragment_id for f in outcome.fragments if f.coverage_required}
    referenced_ids = {fid for chunk in outcome.chunks for fid in chunk.fragment_ids}
    assert required_ids <= referenced_ids


def test_plan_drops_auxiliary_context_before_blocking_the_pipeline() -> None:
    """Auxiliary (non-must_review) content can be silently trimmed to make
    room; must_review content is what actually gates blocked_pipeline."""

    hunks = [
        _hunk("app/required.py", index=0, start=1, end=100, must_review=True),
        _hunk("app/context.py", index=0, start=1, end=100, must_review=False),
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=1
    )
    assert outcome.state == "planned"
    referenced_ids = {fid for chunk in outcome.chunks for fid in chunk.fragment_ids}
    required_fragment = next(f for f in outcome.fragments if f.path == "app/required.py")
    auxiliary_fragment = next(f for f in outcome.fragments if f.path == "app/context.py")
    assert required_fragment.fragment_id in referenced_ids
    assert auxiliary_fragment.fragment_id not in referenced_ids


# -- overlap of context without double counting ------------------------------


def test_plan_reports_disjoint_ranges_even_when_two_hunks_touch_the_same_file() -> None:
    hunks = [
        _hunk("app/a.py", index=0, start=1, end=50),
        _hunk("app/a.py", index=1, start=51, end=100),
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    ranges = [(f.new_range.start, f.new_range.end) for f in outcome.fragments]
    covered: set[int] = set()
    for start, end in ranges:
        rng = set(range(start, end + 1))
        assert not (covered & rng)
        covered.update(rng)
    assert covered == set(range(1, 101))


# -- retries / determinism ---------------------------------------------------


def test_plan_is_stateless_and_deterministic_across_repeated_calls() -> None:
    hunks = [
        _hunk("app/a.py", index=0, start=1, end=150),
        _hunk("app/b.py", index=0, start=1, end=90),
    ]
    outcome_1 = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    outcome_2 = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert [f.fragment_id for f in outcome_1.fragments] == [f.fragment_id for f in outcome_2.fragments]
    assert [tuple(c.fragment_ids) for c in outcome_1.chunks] == [
        tuple(c.fragment_ids) for c in outcome_2.chunks
    ]


def test_plan_does_not_duplicate_coverage_across_out_of_order_calls() -> None:
    """Calling the planner twice (e.g. simulating a retry) never causes a
    fragment to be double-counted -- each call is an independent, pure
    computation over its own input."""

    hunks = [_hunk(start=1, end=120)]
    first = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    second = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert len(first.fragments) == len(second.fragments)
    for fragment in first.fragments:
        assert sum(1 for chunk in first.chunks if fragment.fragment_id in chunk.fragment_ids) <= 1


# -- validation guards --------------------------------------------------------


@pytest.mark.parametrize("max_lines_per_chunk,max_chunks", [(0, 10), (10, 0), (-1, 10)])
def test_plan_rejects_a_non_positive_budget_or_cap(max_lines_per_chunk: int, max_chunks: int) -> None:
    with pytest.raises(ValueError):
        plan_lossless_chunks_v2(
            [_hunk()],
            semantic_group="primary_backend_logic",
            max_lines_per_chunk=max_lines_per_chunk,
            max_chunks=max_chunks,
        )


def test_plan_rejects_a_hunk_with_both_sides_empty() -> None:
    """plan_lossless_chunks_v2 is a public boundary: HunkInputV2 can be
    constructed by any caller, not just diff_acquisition_v2 (whose own
    parser already rejects a zero-changed-line hunk at the text level --
    that rejection does not protect this path). A hunk with both sides
    empty must not fall into the whole-hunk-as-one-fragment branch and
    materialize a fragment (and, if must_review, a required-coverage
    claim) for content that does not exist."""

    empty_hunk = HunkInputV2(
        path="app/a.py",
        hunk_index=0,
        old_start=0,
        old_end=-1,
        new_start=0,
        new_end=-1,
        diff_sha256="a" * 64,
        diff_chars=0,
        must_review=True,
    )
    with pytest.raises(ValueError):
        plan_lossless_chunks_v2(
            [empty_hunk],
            semantic_group="primary_backend_logic",
            max_lines_per_chunk=100,
            max_chunks=10,
        )


def test_plan_rejects_a_duplicated_hunk() -> None:
    """fragment_id is a deterministic hash of path/old_range/new_range/
    diff_sha256 -- it does not include hunk_index. A duplicated
    HunkInputV2 (e.g. a replayed API page from a caller other than
    diff_acquisition_v2) produces two fragments with the same
    fragment_id, which would otherwise reach ManifestChunkV2/
    ManifestMaterialV2 as an unusable "planned" outcome depending on how
    packing happened to group the duplicates. Must be rejected here,
    deterministically."""

    hunk = _hunk("app/a.py")
    duplicated_hunk = _hunk("app/a.py")  # same path/range/diff_sha256 defaults
    with pytest.raises(ValueError):
        plan_lossless_chunks_v2(
            [hunk, duplicated_hunk],
            semantic_group="primary_backend_logic",
            max_lines_per_chunk=100,
            max_chunks=10,
        )


def test_plan_handles_a_deletion_only_hunk_without_a_negative_range() -> None:
    hunk = _hunk(start=10, end=9, old_start=10, old_end=20, must_review=True)  # new_end < new_start
    outcome = plan_lossless_chunks_v2(
        [hunk], semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=10
    )
    assert outcome.state == "planned"
    assert len(outcome.fragments) == 1
    assert outcome.fragments[0].new_range.start == outcome.fragments[0].new_range.end


# -- post-merge finding (P2, Codex review of PR #99) -------------------------


def test_plan_finds_a_valid_packing_that_a_greedy_heuristic_would_miss() -> None:
    """The exact counterexample from the Codex finding: sizes [6,5,3,2,2,2]
    with max_lines_per_chunk=10 and max_chunks=2. First-fit-decreasing
    produces 3 bins (6+3, 5+2+2, 2) and would wrongly report
    blocked_pipeline, even though a valid 2-bin packing exists
    (6+2+2=10, 5+3+2=10). The planner must find it."""

    sizes = [6, 5, 3, 2, 2, 2]
    hunks = [
        _hunk(f"app/file-{i}.py", index=0, start=1, end=size, must_review=True)
        for i, size in enumerate(sizes)
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=10, max_chunks=2
    )
    assert outcome.state == "planned"
    assert len(outcome.chunks) <= 2

    fragments_by_id = {fragment.fragment_id: fragment for fragment in outcome.fragments}
    for chunk in outcome.chunks:
        total = sum(
            fragments_by_id[fid].new_range.end - fragments_by_id[fid].new_range.start + 1
            for fid in chunk.fragment_ids
        )
        assert total <= 10

    referenced = {fid for chunk in outcome.chunks for fid in chunk.fragment_ids}
    required_ids = {f.fragment_id for f in outcome.fragments if f.coverage_required}
    assert required_ids <= referenced


def test_plan_blocks_quickly_on_a_trivially_infeasible_large_input() -> None:
    """Post-merge finding (P2, second Codex review of PR #99): thirty
    size-5 fragments with capacity 10 and max_bins=14 are infeasible by
    total size alone (30*5=150 > 10*14=140) and must be rejected via the
    cheap sum check, not by exhaustively searching ~13.4M states."""

    import time

    hunks = [
        _hunk(f"app/file-{i}.py", index=0, start=1, end=5, must_review=True) for i in range(30)
    ]
    started = time.monotonic()
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=10, max_chunks=14
    )
    elapsed = time.monotonic() - started

    assert outcome.state == "blocked_pipeline"
    assert elapsed < 5.0, f"trivial infeasibility took {elapsed:.2f}s -- expected a cheap short-circuit"


def test_plan_blocks_rather_than_hangs_on_a_large_ambiguous_input() -> None:
    """A large-but-not-trivially-infeasible required-fragment set must
    still terminate quickly: the search-state cap bounds worst-case cost,
    and the fragment-count cap bounds recursion depth."""

    import time

    hunks = [
        _hunk(f"app/file-{i}.py", index=0, start=1, end=5, must_review=True) for i in range(60)
    ]
    started = time.monotonic()
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=10, max_chunks=30
    )
    elapsed = time.monotonic() - started

    assert outcome.state in ("planned", "blocked_pipeline")
    assert elapsed < 10.0, f"bounded search took {elapsed:.2f}s -- expected the state cap to bound cost"


# -- post-merge finding (P2, third Codex review of PR #99) -------------------


def test_plan_splits_a_large_deletion_only_hunk_against_its_old_side_budget() -> None:
    """The exact scenario from the Codex finding: deleting 10,000 old
    lines with max_lines_per_chunk=100 must not be returned as a single
    fragment costing "1 line" -- it must be split (or correctly sized)
    against the old-side range that is actually being removed."""

    hunk = _hunk(
        "app/big-deletion.py",
        index=0,
        start=1,
        end=0,  # new_end < new_start: pure deletion, no new content
        old_start=1,
        old_end=10_000,
        must_review=True,
    )
    outcome = plan_lossless_chunks_v2(
        hunks=[hunk],
        semantic_group="primary_backend_logic",
        max_lines_per_chunk=100,
        max_chunks=200,
    )
    assert outcome.state == "planned"
    assert len(outcome.fragments) > 1, "a 10,000-line deletion must be split, not kept as one fragment"

    total_old_lines = sum(
        fragment.old_range.end - fragment.old_range.start + 1 for fragment in outcome.fragments
    )
    assert total_old_lines == 10_000

    for fragment in outcome.fragments:
        old_size = fragment.old_range.end - fragment.old_range.start + 1
        assert old_size <= 100, f"fragment old-side size {old_size} exceeds max_lines_per_chunk"

    # Each chunk's total assigned size must also respect the budget --
    # proves the packer is using the corrected (old-side-aware) fragment
    # size, not the collapsed new_range.
    fragments_by_id = {fragment.fragment_id: fragment for fragment in outcome.fragments}
    for chunk in outcome.chunks:
        chunk_total = sum(
            max(
                fragments_by_id[fid].old_range.end - fragments_by_id[fid].old_range.start + 1,
                fragments_by_id[fid].new_range.end - fragments_by_id[fid].new_range.start + 1,
            )
            for fid in chunk.fragment_ids
        )
        assert chunk_total <= 100


def test_plan_blocks_a_large_deletion_that_cannot_fit_in_max_chunks() -> None:
    """A 10,000-line deletion with a budget that genuinely cannot fit it
    within max_chunks must block the pipeline, not silently under-count
    the deletion as free and report success."""

    hunk = _hunk(
        "app/big-deletion.py",
        index=0,
        start=1,
        end=0,
        old_start=1,
        old_end=10_000,
        must_review=True,
    )
    outcome = plan_lossless_chunks_v2(
        hunks=[hunk],
        semantic_group="primary_backend_logic",
        max_lines_per_chunk=100,
        max_chunks=2,  # 10,000 / 100 = 100 chunks needed, far more than 2
    )
    assert outcome.state == "blocked_pipeline"


# -- post-merge finding (P2, fourth Codex review of PR #99) -----------------


def test_plan_handles_a_hunk_replacing_one_old_line_with_many_new_lines() -> None:
    """The exact scenario from the Codex finding: 1 old line replaced by
    1,000 new lines at a 100-line budget. The old side (1 line) has far
    fewer lines than the 10 windows the new side requires; a naive
    proportional split produces an inverted range on the old side and
    LineRangeV2 raises instead of the planner returning a result. Must not
    raise -- must return planned or blocked_pipeline."""

    hunk = _hunk(
        "app/imbalanced.py",
        index=0,
        old_start=1,
        old_end=1,
        start=1,
        end=1000,
        must_review=True,
    )
    outcome = plan_lossless_chunks_v2(
        hunks=[hunk],
        semantic_group="primary_backend_logic",
        max_lines_per_chunk=100,
        max_chunks=20,
    )
    assert outcome.state == "planned"
    for fragment in outcome.fragments:
        assert fragment.old_range.start <= fragment.old_range.end
        assert fragment.new_range.start <= fragment.new_range.end

    covered_new_lines: set[int] = set()
    for fragment in outcome.fragments:
        rng = range(fragment.new_range.start, fragment.new_range.end + 1)
        assert not (covered_new_lines & set(rng)), "new-side windows must stay disjoint"
        covered_new_lines.update(rng)
    assert covered_new_lines == set(range(1, 1001))

    # The starved old side (1 real line) cannot be split into 10 genuinely
    # distinct single-line ranges -- disjointness is only guaranteed on
    # the side that drives window_count (the new side, checked above).
    # Every fragment's old_range must still be a valid, in-bounds
    # single-line anchor on the one real old line, and reusing that
    # anchor across fragments must not inflate the represented old-side
    # content: the union of old-side points is still exactly {1}, never
    # more than the hunk's real old-side line count.
    old_range_points: set[int] = set()
    for fragment in outcome.fragments:
        assert fragment.old_range.start == fragment.old_range.end == 1
        old_range_points.add(fragment.old_range.start)
    assert old_range_points == {1}

    # Distinct new-side windows still yield distinct fragment_ids even
    # though every fragment shares the same (anchor) old_range.
    assert len({fragment.fragment_id for fragment in outcome.fragments}) == len(outcome.fragments)


# -- post-merge finding (P2, sixth Codex review of PR #99) -------------------


def test_plan_fails_fast_and_safe_on_a_numerically_adversarial_exact_fit_case() -> None:
    """Known, documented limitation: bin packing is NP-hard, so no finite
    search bound can guarantee finding every feasible packing. This
    specific 25-fragment instance sums to *exactly* capacity*max_bins
    (700 == 100*7), forcing zero slack in every bin if a solution exists
    -- an adversarial exact-fit case that a valid packing does exist for,
    but which exhausts the state budget before finding it. This is not
    asserted as a bug fix: it documents the accepted trade-off (fail fast
    and safe -- never hang, never crash, never falsely report success --
    rather than search indefinitely for a guaranteed answer)."""

    import time

    sizes = [67, 66, 57, 52, 51, 43, 35, 34, 29, 28, 26, 26, 24, 20, 18, 17, 16, 15, 12, 12, 11, 11, 10, 10, 10]
    hunks = [
        _hunk(f"app/file-{i}.py", index=0, start=1, end=size, must_review=True)
        for i, size in enumerate(sizes)
    ]
    started = time.monotonic()
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=100, max_chunks=7
    )
    elapsed = time.monotonic() - started

    # The only hard guarantees: it terminates quickly and never crashes.
    assert outcome.state in ("planned", "blocked_pipeline")
    assert elapsed < 5.0, f"exact-fit adversarial case took {elapsed:.2f}s -- expected a bounded, fast failure"
    if outcome.state == "blocked_pipeline":
        # A valid 7-bin packing is independently confirmed to exist (see
        # the commit message), so this must NEVER be reported as
        # "budget_exhausted" (mathematically proven infeasible) -- that
        # would misrepresent "the search gave up" as "it does not fit".
        assert outcome.degradation_causes[0].reason_code == "packing_search_exhausted"
        assert outcome.degradation_causes[0].reason_code != "budget_exhausted"


# -- reason-code precedence: proven_infeasible vs search_exhausted vs input_too_large --


def test_pack_fragments_exact_distinguishes_proven_infeasible_from_search_exhausted() -> None:
    from app.agent_review.planner_v2 import _pack_fragments_exact
    from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, compute_fragment_id_v2
    import hashlib

    def frag(path: str, size: int) -> FragmentV2:
        rng = LineRangeV2(start=1, end=size)
        diff_sha = hashlib.sha256(path.encode()).hexdigest()
        fid = compute_fragment_id_v2(path=path, old_range=rng, new_range=rng, diff_sha256=diff_sha)
        return FragmentV2(
            fragment_id=fid, path=path, old_range=rng, new_range=rng, hunk_indexes=[0],
            diff_chars=10, diff_sha256=diff_sha, coverage_required=True,
        )

    # Proven infeasible: total (30*5=150) exceeds capacity*max_bins (10*14=140).
    trivial = [frag(f"app/t{i}.py", 5) for i in range(30)]
    result = _pack_fragments_exact(trivial, capacity=10, max_bins=14)
    assert result.status == "proven_infeasible"

    # Search exhausted: the documented adversarial 25-fragment exact-fit case.
    sizes = [67, 66, 57, 52, 51, 43, 35, 34, 29, 28, 26, 26, 24, 20, 18, 17, 16, 15, 12, 12, 11, 11, 10, 10, 10]
    adversarial = [frag(f"app/a{i}.py", s) for i, s in enumerate(sizes)]
    result = _pack_fragments_exact(adversarial, capacity=100, max_bins=7)
    assert result.status == "search_exhausted"

    # Found: a simple case that fits trivially.
    easy = [frag("app/e.py", 5)]
    result = _pack_fragments_exact(easy, capacity=10, max_bins=1)
    assert result.status == "found"
    assert result.bins is not None
    assert len(result.bins) == 1


def test_pack_fragments_exact_reports_input_too_large_distinctly() -> None:
    from app.agent_review.planner_v2 import _pack_fragments_exact, _MAX_EXACT_PACKING_FRAGMENTS
    from app.agent_review.manifest_v2 import FragmentV2, LineRangeV2, compute_fragment_id_v2
    import hashlib

    def frag(path: str) -> FragmentV2:
        rng = LineRangeV2(start=1, end=1)
        diff_sha = hashlib.sha256(path.encode()).hexdigest()
        fid = compute_fragment_id_v2(path=path, old_range=rng, new_range=rng, diff_sha256=diff_sha)
        return FragmentV2(
            fragment_id=fid, path=path, old_range=rng, new_range=rng, hunk_indexes=[0],
            diff_chars=1, diff_sha256=diff_sha, coverage_required=True,
        )

    too_many = [frag(f"app/x{i}.py") for i in range(_MAX_EXACT_PACKING_FRAGMENTS + 1)]
    result = _pack_fragments_exact(too_many, capacity=10, max_bins=1000)
    assert result.status == "input_too_large"


def test_plan_reports_planner_limit_exceeded_distinctly_from_budget_exhausted() -> None:
    from app.agent_review.planner_v2 import _MAX_EXACT_PACKING_FRAGMENTS

    hunks = [
        _hunk(f"app/many-{i}.py", index=0, start=1, end=1, must_review=True)
        for i in range(_MAX_EXACT_PACKING_FRAGMENTS + 1)
    ]
    outcome = plan_lossless_chunks_v2(
        hunks, semantic_group="primary_backend_logic", max_lines_per_chunk=10, max_chunks=1000
    )
    assert outcome.state == "blocked_pipeline"
    assert outcome.degradation_causes[0].reason_code == "planner_limit_exceeded"
