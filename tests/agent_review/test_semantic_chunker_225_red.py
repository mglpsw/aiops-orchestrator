"""RED/lock tests for aiops-orchestrator#225: planner/builder chunk-cost
divergence and its fix (FFD packing, fixed-point cost projection, fail-closed
oversize/hunk-unavailable handling, deterministic partitioning).

Complements test_semantic_chunker.py (existing grouping/budget tests, now
updated for real-cost semantics) and test_chunk_payload_builder.py (hard
guard, budget authority, projection-input binding). See EES #225 rev.3 for
the RED matrix these map to.
"""

from __future__ import annotations

import random

from app.agent_review.chunk_payload_builder import build_chunk_payloads
from app.agent_review.pr_brief import build_pr_brief
from app.agent_review.schemas import RedactionReport, ReviewIntake

from app.agent_review.semantic_chunker import GROUP_PRIORITY, IntakeValidationError, build_semantic_chunk_plan


def _hunk(path: str, lines: int) -> str:
    body = "\n".join(f"+    value_{i} = compute_window(index_{i}, offset_{i})" for i in range(lines))
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,{lines} @@\n{body}"


def _intake(files: list[str], hunk_lines: dict[str, int], must: list[str] | None = None) -> dict:
    """`hunk_lines` maps path -> line count; a path present in `files` but
    absent from `hunk_lines` gets no diff block at all (simulating a binary
    file or a diff producer gap), not a default-sized hunk."""
    must = must if must is not None else files
    diff = "\n".join(_hunk(p, hunk_lines[p]) for p in files if p in hunk_lines)
    return {
        "schema_id": "agent-review.intake.v1",
        "schema_version": 1,
        "source": "aiops-review-intake",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {},
        "created_at": "2026-08-13T00:00:00Z",
        "artifacts": {
            "file-diff-context.json": {
                "path": "file-diff-context.json",
                "content": {
                    "files": [{"path": p, "status": "modified", "summary": ""} for p in files],
                    "coverage_requirements": {"must_review_files": must},
                },
            },
            "full-diff.diff": {"path": "full-diff.diff", "content": diff},
        },
        "artifact_status": [],
        "redaction_summary": RedactionReport().model_dump(mode="json"),
        "limitations": [],
        "completeness": {},
        "status": "complete",
    }


def _build_real_payloads(intake_dict: dict, plan, *, optional_limitations: list[str] | None = None):
    intake = ReviewIntake.model_validate(intake_dict)
    brief = build_pr_brief(
        intake=intake,
        chunk_plan=plan,
        redaction_report=intake.redaction_summary,
        checks=None,
        validation_evidence=None,
        optional_limitations=optional_limitations,
    )
    return build_chunk_payloads(intake=intake, chunk_plan=plan, pr_brief=brief, checks=None, validation_evidence=None)


def _lost_must_review_files(plan, manifest, payloads, must: set[str]) -> set[str]:
    """A file is "lost" only if a payload was actually emitted claiming to
    cover it but its hunk didn't transport intact -- the silent-loss defect
    #225 exists to close. A file honestly reported in `files_not_covered`
    (oversize, hunk-unavailable, budget-exhausted) is a *visible* gap, not a
    silent loss, and must not be counted here.
    """
    lost: set[str] = set()
    for entry in manifest.chunks:
        if entry.payload_path is None:
            continue
        payload = payloads[entry.payload_path]
        planned = set(payload.coverage.get("files_in_chunk") or [])
        transported = {
            item["path"]
            for item in payload.chunk_context.get("chunk_hunks", [])
            if isinstance(item, dict) and isinstance(item.get("hunk"), str) and not item["hunk"].endswith("...")
        }
        lost |= (planned - transported) & must
    return lost


# ---------------------------------------------------------------------------
# RED-1 / RED-2 / RED-3: reproduce the #774 divergence shape and prove it is
# fixed -- zero must_review files ever lose hunk material, at magnitude
# equivalent to the real canary (workflow_aiops ~150k+, tests ~40k+).
# ---------------------------------------------------------------------------


def test_no_must_review_file_ever_loses_hunk_material_at_774_scale() -> None:
    workflow_files = [
        ".github/workflows/agent-review.yml",
        ".github/workflows/agent-review-publish.yml",
        ".github/workflows/aiops-collect.yml",
        "scripts/aiops/collect_metrics.py",
        "scripts/aiops/publish_review.py",
        "scripts/aiops/intake_builder.py",
    ]
    test_files = [
        "tests/aiops/test_publisher.py",
        "tests/aiops/test_intake.py",
        "tests/aiops/test_collect.py",
        "tests/aiops/test_boundary.py",
    ]
    other_files = [
        "docs/AIOPS_STATE.md",
        "docs/TRUST_BOUNDARY.md",
        "frontend/src/pages/panel.jsx",
        "backend/services/shift_service.py",
        "backend/api/schema_contract.py",
    ]
    files = workflow_files + test_files + other_files
    must = workflow_files + test_files
    hunk_lines = {**{p: 600 for p in workflow_files}, **{p: 250 for p in test_files}, **{p: 5 for p in other_files}}

    intake_dict = _intake(files, hunk_lines, must)
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    manifest, payloads = _build_real_payloads(intake_dict, plan)

    lost = _lost_must_review_files(plan, manifest, payloads, set(must))
    assert lost == set(), f"must_review files lost hunk material: {sorted(lost)}"

    # every file the plan claims is covered actually reached the reviewer
    for entry in manifest.chunks:
        if entry.payload_path is None:
            continue
        assert "chunk_hunks_reduced" not in entry.truncation.coverage_impact


# ---------------------------------------------------------------------------
# RED-4 / RED-13: permutation invariance and rerun determinism.
# ---------------------------------------------------------------------------


def test_plan_is_invariant_to_intake_file_order() -> None:
    files = [
        "backend/api/a.py",
        "tests/b_test.py",
        "tests/c_test.py",
        "docs/README.md",
        "frontend/src/x.jsx",
        ".github/workflows/ci.yml",
        "scripts/aiops/y.py",
    ]
    must = ["backend/api/a.py", "tests/b_test.py"]
    hunk_lines = {p: 40 for p in files}

    plan_a = build_semantic_chunk_plan(_intake(files, hunk_lines, must))
    plan_b = build_semantic_chunk_plan(_intake(list(reversed(files)), hunk_lines, must))
    shuffled = files[:]
    random.Random(1234).shuffle(shuffled)
    plan_c = build_semantic_chunk_plan(_intake(shuffled, hunk_lines, must))

    assert plan_a.model_dump_json() == plan_b.model_dump_json()
    assert plan_a.model_dump_json() == plan_c.model_dump_json()

    # rerun determinism: created_at no longer leaks wall-clock time
    plan_a2 = build_semantic_chunk_plan(_intake(files, hunk_lines, must))
    assert plan_a.model_dump_json() == plan_a2.model_dump_json()
    assert plan_a.created_at == "2026-08-13T00:00:00Z"


def test_plan_created_at_derives_from_intake_not_wall_clock() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    intake_dict["created_at"] = "2020-01-01T00:00:00Z"
    plan = build_semantic_chunk_plan(intake_dict)
    assert plan.created_at == "2020-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# RED-6 / RED-7: oversize fail-closed, differentiated by must_review.
# ---------------------------------------------------------------------------


def test_must_review_individual_oversize_fails_closed_not_fragmented() -> None:
    files = ["backend/services/huge_module.py"]
    plan = build_semantic_chunk_plan(_intake(files, {files[0]: 5000}, must=files), max_chars_per_block=24_000)
    assert plan.status == "degraded"
    assert plan.files_not_covered == files
    assert f"must_review_payload_oversize:{files[0]}" in plan.limitations
    # never silently fragmented into a partial chunk
    assert plan.chunks == []


def test_non_must_review_individual_oversize_is_reported_not_dropped() -> None:
    files = ["docs/huge_reference.md"]
    plan = build_semantic_chunk_plan(_intake(files, {files[0]: 5000}, must=[]), max_chars_per_block=24_000)
    assert plan.status == "degraded"
    assert plan.files_not_covered == files
    assert f"payload_oversize:{files[0]}" in plan.limitations
    assert plan.chunks == []


# ---------------------------------------------------------------------------
# RED-16: must_review with no observable hunk fails closed (binary/missing).
# ---------------------------------------------------------------------------


def test_must_review_file_with_no_hunk_fails_closed() -> None:
    files = ["backend/api/a.py", "assets/logo.png"]
    intake_dict = _intake(files, {"backend/api/a.py": 5}, must=files)
    # assets/logo.png has no diff --git block at all (binary, or the diff
    # producer never emitted one)
    plan = build_semantic_chunk_plan(intake_dict)
    assert "assets/logo.png" in plan.files_not_covered
    assert "must_review_hunk_unavailable:assets/logo.png" in plan.limitations
    assert "assets/logo.png" not in plan.files_covered


# ---------------------------------------------------------------------------
# RED-17: no empty chunk is ever emitted.
# ---------------------------------------------------------------------------


def test_no_chunk_is_ever_emitted_empty() -> None:
    files = ["backend/api/a.py", "backend/services/huge_module.py"]
    intake_dict = _intake(files, {"backend/api/a.py": 5, "backend/services/huge_module.py": 5000}, must=[])
    plan = build_semantic_chunk_plan(intake_dict, max_chars_per_block=24_000)
    for chunk in plan.chunks:
        assert chunk.files, f"{chunk.chunk_id} was emitted with an empty files list"


# ---------------------------------------------------------------------------
# RED-15: max_blocks selection prioritizes candidate chunks that themselves
# contain a must_review file, cutting across GROUP_PRIORITY.
# ---------------------------------------------------------------------------


def test_max_blocks_selection_prioritizes_must_review_bearing_chunks() -> None:
    # docs_changelog sorts after tests in GROUP_PRIORITY, but only the
    # docs file is must_review -- with max_blocks=1 the docs chunk must
    # still be the one selected, not the higher-priority-but-unrequired
    # tests chunk.
    files = ["tests/a_test.py", "docs/CHANGELOG.md"]
    intake_dict = _intake(files, {p: 5 for p in files}, must=["docs/CHANGELOG.md"])
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=1, max_chars_per_block=24_000)
    assert len(plan.chunks) == 1
    assert plan.chunks[0].files == ["docs/CHANGELOG.md"]
    assert "tests/a_test.py" in plan.files_not_covered
    assert "chunk_plan_budget_exhausted:tests" in plan.limitations


# ---------------------------------------------------------------------------
# RED-20: the plan/brief cost fixed point converges (doesn't just crash or
# loop) even when checks produce unbounded `check_scope_unclassified:*`
# limitations that feed back into brief.limitations, which feeds back into
# the projected cost of every chunk.
# ---------------------------------------------------------------------------


def test_fixed_point_converges_with_growing_checks_limitations() -> None:
    files = [f"backend/services/f{i}.py" for i in range(8)]
    intake_dict = _intake(files, {p: 20 for p in files}, must=files[:2])
    # a checks document with many unscoped, unclassified rows -- each one
    # contributes a `check_scope_unclassified:<name>` limitation that lands
    # in every chunk's projected brief.limitations
    intake_dict["artifacts"]["checks.json"] = {
        "path": "checks.json",
        "content": {
            "status": "complete",
            "mode": "scoped",
            "scope": "",
            "checks": [{"name": f"weird_check_{i}", "status": "passed"} for i in range(30)],
        },
    }
    plan = build_semantic_chunk_plan(intake_dict, max_chars_per_block=24_000)
    # must converge to a real, usable plan -- not raise
    # plan_cost_fixed_point_not_converged, and not lose any must_review file
    assert plan.status in {"complete", "degraded"}
    assert "plan_cost_fixed_point_not_converged" not in plan.limitations


# ---------------------------------------------------------------------------
# RED-22: FFD packing succeeds within max_blocks where naive canonical-order
# first-fit would have needed more chunks than available.
# ---------------------------------------------------------------------------


def test_ffd_packs_within_max_blocks_where_naive_order_would_not() -> None:
    # One large file plus several small ones, all in the same semantic
    # group: canonical-path order happens to put the large file first, which
    # would force it into its own chunk under naive first-fit and then
    # require 1 chunk per remaining small file too if packed in strict
    # insertion order without reordering by cost. FFD (cost-descending) must
    # still find a packing that fits within max_blocks.
    small_files = [f"backend/services/small_{i}.py" for i in range(5)]
    files = ["backend/services/aaa_biggest.py", *small_files]
    hunk_lines = {"backend/services/aaa_biggest.py": 200, **{p: 3 for p in small_files}}
    intake_dict = _intake(files, hunk_lines, must=[])
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=3, max_chars_per_block=24_000)
    assert plan.status == "complete"
    assert set(plan.files_covered) == set(files)
    assert len(plan.chunks) <= 3


# ---------------------------------------------------------------------------
# RED-8: v2 stays byte/semantically untouched by this fix -- the planner and
# builder for v2 never import anything from this v1-only fix surface.
# ---------------------------------------------------------------------------


def test_v2_modules_do_not_import_v1_cost_model_or_semantic_chunker() -> None:
    import ast
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    v2_files = sorted((repo_root / "app" / "agent_review").glob("*_v2.py"))
    assert v2_files, "expected at least one *_v2.py module to check"
    forbidden = {"payload_cost_model", "semantic_chunker", "chunk_payload_builder"}
    for path in v2_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module_tail = node.module.rsplit(".", 1)[-1]
                assert module_tail not in forbidden, f"{path.name} imports v1-only module {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_tail = alias.name.rsplit(".", 1)[-1]
                    assert module_tail not in forbidden, f"{path.name} imports v1-only module {alias.name}"


def test_group_priority_unchanged_shape() -> None:
    # Defensive lock: the packer's group-priority tie-break (rev.3 SS10)
    # depends on this exact, closed vocabulary staying stable.
    assert GROUP_PRIORITY == [
        "suspicious_out_of_scope",
        "api_schema_contract",
        "primary_backend_logic",
        "workflow_aiops",
        "frontend_ui",
        "tests",
        "docs_changelog",
        "unknown",
    ]


# ---------------------------------------------------------------------------
# PR #227 exact-HEAD adversarial review, P2-1/P2-2/P2-3: the projector must
# use exact facts for every payload field the shrink ladder never touches
# (chunk_context.files, target/brief metadata, brief.limitations), not an
# approximate placeholder -- proven end to end against the real builder, not
# just the isolated projection function.
# ---------------------------------------------------------------------------


def _assert_real_build_never_reduces_a_hunk(intake_dict: dict, plan, *, optional_limitations=None) -> None:
    assert plan.status == "complete", f"plan degraded unexpectedly: {plan.limitations}"
    manifest, payloads = _build_real_payloads(intake_dict, plan, optional_limitations=optional_limitations)
    assert manifest.payload_count == len(plan.chunks), (
        f"hard guard blocked a chunk the planner claimed would fit: "
        f"{[e.chunk_id for e in manifest.chunks if e.payload_path is None]}"
    )
    for entry in manifest.chunks:
        assert entry.payload_path is not None
        assert "chunk_hunks_reduced" not in entry.truncation.coverage_impact


def test_red24_adversarial_file_context_status_summary_stays_sound() -> None:
    """RED-24 (P2-1): chunk_context.files carries file-diff-context's real
    status/summary, and the shrink ladder never touches that section. A very
    long status/summary must not make `projected <= budget` true while the
    real floor is actually larger.
    """
    files = ["backend/api/a.py", "tests/b_test.py"]
    hunk_lines = {p: 30 for p in files}
    intake_dict = _intake(files, hunk_lines, must=files)
    long_status = "modified-" + ("S" * 4000)
    long_summary = "This change touches many call sites: " + ("x" * 4000)
    for item in intake_dict["artifacts"]["file-diff-context.json"]["content"]["files"]:
        item["status"] = long_status
        item["summary"] = long_summary

    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan)


def test_red25_adversarial_limitations_envelope_stays_sound() -> None:
    """RED-25 (P2-2): brief.limitations = intake.limitations +
    chunk_plan.limitations + optional_limitations +
    artifact_state_limitations(intake). None of the first three sources are
    shrinkable. A large adversarial value in any of them must not make the
    projection under-estimate the real floor.
    """
    files = ["backend/api/a.py"]
    hunk_lines = {"backend/api/a.py": 30}
    intake_dict = _intake(files, hunk_lines, must=files)
    # intake.limitations: real, pre-existing limitations from the intake
    # build step -- adversarially many.
    intake_dict["limitations"] = [f"synthetic_intake_limitation_{i}" for i in range(60)]
    # artifact_state_limitations(intake): many declared-but-missing optional
    # artifacts.
    intake_dict["target_profile"] = {
        "artifacts": [{"name": f"optional_artifact_{i}", "required": False} for i in range(30)]
    }
    intake_dict["artifact_status"] = [
        {
            "name": f"optional_artifact_{i}",
            "path": f"optional_artifact_{i}.json",
            "available": False,
            "valid": False,
            "status": "missing",
        }
        for i in range(30)
    ]
    optional_limitations = [f"optional_artifact_missing:external_doc_{i}" for i in range(30)]

    plan = build_semantic_chunk_plan(
        intake_dict,
        max_blocks=6,
        max_chars_per_block=24_000,
        optional_limitations=optional_limitations,
    )
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan, optional_limitations=optional_limitations)


# ---------------------------------------------------------------------------
# H1-B / C6 (post-merge debt, #205): pr_brief.build_pr_brief's own
# `_apply_budget` can append `brief_budget_under_minimum_required_sections`
# to `pr_brief.limitations` once every shrinker has bottomed out and the
# resolved brief budget is still exceeded. `chunk_context.brief.limitations`
# in the real builder is `list(pr_brief.limitations)` verbatim -- so the
# planner's own `brief_limitations` projection (semantic_chunker.py's
# `fixed_brief_limitations`) must assume this reason code can appear too, or
# a tight `pr_brief_max_chars` target profile makes the real emitted brief
# section larger than what was projected.
# ---------------------------------------------------------------------------


def test_c6_projection_accounts_for_brief_budget_minimum_limitation() -> None:
    files = ["backend/api/a.py"]
    hunk_lines = {"backend/api/a.py": 30}
    intake_dict = _intake(files, hunk_lines, must=files)
    # A resolved brief budget this tight cannot fit even after every
    # shrinker (validation findings, checks rows, artifact details,
    # semantic-group files, changed-files rows) has emptied its list --
    # _apply_budget's terminal branch fires and appends the limitation.
    intake_dict["target_profile"] = {"pr_brief_max_chars": 1}

    # `prompt_budget_chars` is always the caller's `max_chars_per_block`,
    # not the tight projected floor -- so a generous max_chars_per_block
    # would mask a small under-projection with slack. Learn the planner's
    # own projected floor first, then re-plan with max_chars_per_block
    # pinned to exactly that floor: the tightest possible proof that a
    # smaller, under-projected floor would have let the packer size this
    # chunk at a budget the real builder cannot honor.
    loose_plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=1_000_000)
    projected = loose_plan.chunks[0].estimated_chars
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=projected)
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan)
    # Stricter than the shared hunks-only helper: an *exact*-fit projection
    # promises nothing else needs shrinking either -- any coverage_impact
    # at all at this exact budget is itself proof the projection
    # under-counted the real, unshrinkable brief.limitations content.
    manifest, _ = _build_real_payloads(intake_dict, plan)
    assert manifest.chunks[0].truncation.coverage_impact == [], manifest.chunks[0].truncation.coverage_impact


def test_real_brief_actually_hits_the_minimum_required_sections_branch() -> None:
    """Sanity/negative-control companion for the test above: confirms the
    tiny `pr_brief_max_chars` fixture genuinely drives the real builder
    into the branch under test, rather than the soundness assertion above
    passing vacuously because the budget was never actually tight enough
    to trigger it."""
    files = ["backend/api/a.py"]
    hunk_lines = {"backend/api/a.py": 30}
    intake_dict = _intake(files, hunk_lines, must=files)
    intake_dict["target_profile"] = {"pr_brief_max_chars": 1}
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    intake = ReviewIntake.model_validate(intake_dict)
    brief = build_pr_brief(
        intake=intake,
        chunk_plan=plan,
        redaction_report=intake.redaction_summary,
        checks=None,
        validation_evidence=None,
    )
    assert "brief_budget_under_minimum_required_sections" in brief.limitations


# ---------------------------------------------------------------------------
# H1-B / C8 (post-merge debt, #205): a git binary-file diff block
# ("Binary files a/x and b/x differ") is non-empty, so
# `bool(hunks.get(path))` was True even though the block contains no `@@`
# hunk header at all -- no line-level, semantically reviewable content. A
# must_review file whose only diff block is binary-only was therefore
# never flagged `must_review_hunk_unavailable:<path>` and could count as
# covered.
# ---------------------------------------------------------------------------


def _intake_with_raw_diff(files: list[str], raw_diff: str, must: list[str] | None = None) -> dict:
    must = must if must is not None else files
    return {
        "schema_id": "agent-review.intake.v1",
        "schema_version": 1,
        "source": "aiops-review-intake",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {},
        "created_at": "2026-08-13T00:00:00Z",
        "artifacts": {
            "file-diff-context.json": {
                "path": "file-diff-context.json",
                "content": {
                    "files": [{"path": p, "status": "modified", "summary": ""} for p in files],
                    "coverage_requirements": {"must_review_files": must},
                },
            },
            "full-diff.diff": {"path": "full-diff.diff", "content": raw_diff},
        },
        "artifact_status": [],
        "redaction_summary": RedactionReport().model_dump(mode="json"),
        "limitations": [],
        "completeness": {},
        "status": "complete",
    }


def test_c8_binary_only_diff_block_is_not_an_observable_hunk() -> None:
    binary_diff = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ"
    )
    intake_dict = _intake_with_raw_diff(["assets/logo.png"], binary_diff, must=["assets/logo.png"])
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    assert "must_review_hunk_unavailable:assets/logo.png" in plan.limitations, plan.limitations
    assert "assets/logo.png" in plan.files_not_covered, plan.files_not_covered
    assert plan.status == "degraded", plan.status


def test_c8_textual_hunk_still_counts_as_covered() -> None:
    # Positive control: an ordinary textual hunk for the same file shape
    # must NOT be classified as hunk-unavailable -- the fix must not
    # over-correct into rejecting real, reviewable diffs.
    files = ["backend/api/a.py"]
    intake_dict = _intake(files, {"backend/api/a.py": 5}, must=files)
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    assert not any(item.startswith("must_review_hunk_unavailable:") for item in plan.limitations), plan.limitations
    assert "backend/api/a.py" not in plan.files_not_covered
    assert plan.status == "complete"


def test_red26_adversarial_review_metadata_stays_sound() -> None:
    """RED-26 (P2-3): target/brief.review metadata (pr_number, commit_sha,
    review_mode, contract_pack) is resolved exactly via
    payload_cost_model.resolve_review_metadata, the same authority
    pr_brief.build_pr_brief calls -- no placeholder may under-estimate what
    that resolution actually produces.
    """
    files = ["backend/api/a.py"]
    hunk_lines = {"backend/api/a.py": 30}
    intake_dict = _intake(files, hunk_lines, must=files)
    intake_dict["artifacts"]["checks.json"] = {
        "path": "checks.json",
        "content": {
            "status": "complete",
            "checks": [],
            "pr_number": 999999999,
            "commit_sha": "a" * 40,
            "review_mode": "adversarially-long-review-mode-" + ("m" * 2000),
            "contract_pack": "adversarially-long-contract-pack-" + ("p" * 2000),
        },
    }

    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan)


def test_red24_25_26_combined_adversarial_dimensions_stay_sound() -> None:
    """All three P2 dimensions compounded at once, at #774-like hunk scale:
    long file status/summary, a large limitations envelope from every
    source, and adversarial review metadata, together.
    """
    files = ["backend/api/a.py", "backend/services/b.py", "tests/c_test.py"]
    hunk_lines = {"backend/api/a.py": 200, "backend/services/b.py": 200, "tests/c_test.py": 200}
    intake_dict = _intake(files, hunk_lines, must=files)
    for item in intake_dict["artifacts"]["file-diff-context.json"]["content"]["files"]:
        item["status"] = "modified-" + ("S" * 3000)
        item["summary"] = "x" * 3000
    intake_dict["limitations"] = [f"synthetic_intake_limitation_{i}" for i in range(40)]
    intake_dict["target_profile"] = {
        "artifacts": [{"name": f"optional_artifact_{i}", "required": False} for i in range(20)]
    }
    intake_dict["artifact_status"] = [
        {
            "name": f"optional_artifact_{i}",
            "path": f"optional_artifact_{i}.json",
            "available": False,
            "valid": False,
            "status": "missing",
        }
        for i in range(20)
    ]
    intake_dict["artifacts"]["checks.json"] = {
        "path": "checks.json",
        "content": {
            "status": "complete",
            "checks": [],
            "pr_number": 999999999,
            "commit_sha": "a" * 40,
            "review_mode": "m" * 2000,
            "contract_pack": "p" * 2000,
        },
    }
    optional_limitations = [f"optional_artifact_missing:external_doc_{i}" for i in range(20)]

    # The combined adversarial envelope (3000-char status/summary per file,
    # a ~2000-char review_mode/contract_pack, dozens of limitation strings)
    # is itself far larger than the default 24,000 budget -- a budget large
    # enough to actually pack something is needed to exercise "hunks survive
    # when packed", distinct from the oversize-fails-closed property already
    # covered by test_must_review_individual_oversize_fails_closed_not_fragmented.
    plan = build_semantic_chunk_plan(
        intake_dict, max_blocks=6, max_chars_per_block=100_000, optional_limitations=optional_limitations
    )
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan, optional_limitations=optional_limitations)


# ---------------------------------------------------------------------------
# RED-27 (P2-4): `created_at` fallback is a fixed sentinel, never wall clock,
# when the intake omits the field entirely (validate_intake_contract does
# not require it).
# ---------------------------------------------------------------------------


def test_red27_created_at_falls_back_to_fixed_sentinel_not_wall_clock() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    del intake_dict["created_at"]
    plan_a = build_semantic_chunk_plan(intake_dict)
    plan_b = build_semantic_chunk_plan(intake_dict)
    assert plan_a.created_at == "1970-01-01T00:00:00Z"
    # same (missing) input, replayed twice, must be byte-identical -- a
    # wall-clock fallback (the old utc_now_iso() default) would drift
    # between the two calls above.
    assert plan_a.model_dump_json() == plan_b.model_dump_json()


# ---------------------------------------------------------------------------
# RED-28 (P2-5): must_review_files is canonicalized the same way changed
# files are, so a declaration in a different (but equivalent) path form
# still matches and keeps its must_review priority/fail-closed treatment.
# ---------------------------------------------------------------------------


def test_red28_must_review_identity_survives_non_canonical_declaration() -> None:
    files = ["backend/services/huge_module.py"]
    # declared in a non-canonical form; the changed-file side is
    # canonicalized via _canonicalize_files regardless.
    intake_dict = _intake(files, {files[0]: 5000}, must=["./backend/services/huge_module.py"])
    plan = build_semantic_chunk_plan(intake_dict, max_chars_per_block=24_000)
    # if identity matching had silently broken (string-compared instead of
    # canonicalized), this would be reported as plain payload_oversize,
    # losing must_review's fail-closed distinction.
    assert f"must_review_payload_oversize:{files[0]}" in plan.limitations
    assert f"payload_oversize:{files[0]}" not in plan.limitations


def test_red28_malformed_must_review_declaration_fails_closed_not_silently() -> None:
    files = ["backend/api/a.py"]
    intake_dict = _intake(files, {"backend/api/a.py": 5}, must=["/etc/passwd"])
    plan = build_semantic_chunk_plan(intake_dict)
    assert any(l.startswith("must_review_path_identity_absolute:") for l in plan.limitations)


# ---------------------------------------------------------------------------
# RED-31 (P2-8): brief_required_files must project the real wire bytes
# PRBrief.coverage.required_files will actually emit -- ordered-unique,
# never canonicalized -- distinct from the canonicalized identity set used
# for must_review membership/priority/oversize classification.
# ---------------------------------------------------------------------------


def test_red31_projection_sound_with_non_canonical_must_review_wire_declaration() -> None:
    files = ["backend/a.py"]
    hunk_lines = {"backend/a.py": 30}
    # duplicates and non-canonical spellings of the same file, declared as
    # must_review -- the changed-file identity still matches (RED-28), and
    # the wire bytes embedded in brief.required_files must be projected at
    # their real (non-canonicalized, ordered-unique) length.
    non_canonical_wire = ["./././backend/a.py", "backend\\a.py", "./././backend/a.py"]
    intake_dict = _intake(files, hunk_lines, must=non_canonical_wire)
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000)
    assert plan.status == "complete"
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan)


# ---------------------------------------------------------------------------
# RED-29 (P2-6): the planner cannot observe what --checks/--validation-
# evidence flags a later, separate builder invocation will be given, so it
# must unconditionally assume both are missing in its projection -- even
# when its OWN invocation happens to have optional_limitations=[] (as if it
# had received both flags itself).
# ---------------------------------------------------------------------------


def test_red29_projection_sound_despite_planner_builder_flag_asymmetry() -> None:
    files = ["backend/api/a.py"]
    intake_dict = _intake(files, {"backend/api/a.py": 30}, must=files)
    # planner invocation: as if it had received both --checks and
    # --validation-evidence (optional_limitations=[]).
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=6, max_chars_per_block=24_000, optional_limitations=[])
    assert plan.status == "complete"
    # the separate, real builder invocation: the asymmetric case -- THIS
    # invocation actually omitted both flags.
    _assert_real_build_never_reduces_a_hunk(
        intake_dict,
        plan,
        optional_limitations=["optional_artifact_missing:checks", "optional_artifact_missing:validation_evidence"],
    )


# ---------------------------------------------------------------------------
# RED-30 (P2-7): chunk_id's worst-case digit count and original_chars are
# derived from real, provable bounds (max_blocks; the actual untruncated
# payload) rather than empirically-sized placeholders (a fixed 3-digit
# assumption; 999_999_999). See test_payload_cost_model.py for the direct,
# low-level proofs of both primitives.
# ---------------------------------------------------------------------------


def test_red30_projection_sound_at_max_blocks_exceeding_old_three_digit_assumption() -> None:
    files = [f"backend/services/f{i}.py" for i in range(3)]
    hunk_lines = {p: 30 for p in files}
    intake_dict = _intake(files, hunk_lines, must=files)
    # 5000 exceeds the old fixed "up to 999 chunks" assumption -- if the
    # chunk_id bound were still hardcoded at 3 digits, this max_blocks value
    # alone would make the projection's own worst-case chunk_id shorter
    # than what the real numbering format could actually produce.
    plan = build_semantic_chunk_plan(intake_dict, max_blocks=5000, max_chars_per_block=24_000)
    assert plan.status == "complete"
    _assert_real_build_never_reduces_a_hunk(intake_dict, plan)


# ---------------------------------------------------------------------------
# RED-33 (P2-10): max_blocks/max_chars_per_block must be validated at entry.
# A non-positive max_blocks disagrees between `ordered[:max_blocks]`
# (Python negative slicing for negative values) and `worst_case_chunk_id`/
# `worst_case_order_index` (which normalize non-positive input to 1) --
# fail closed instead of letting the two silently diverge.
# ---------------------------------------------------------------------------


def test_red33_zero_max_blocks_fails_closed() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    try:
        build_semantic_chunk_plan(intake_dict, max_blocks=0)
    except IntakeValidationError as exc:
        assert str(exc) == "max_blocks_invalid"
    else:
        raise AssertionError("expected IntakeValidationError for max_blocks=0")


def test_red33_negative_max_blocks_fails_closed() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    try:
        build_semantic_chunk_plan(intake_dict, max_blocks=-1)
    except IntakeValidationError as exc:
        assert str(exc) == "max_blocks_invalid"
    else:
        raise AssertionError("expected IntakeValidationError for max_blocks=-1")


def test_red33_bool_max_blocks_fails_closed() -> None:
    # bool is a structural int subclass in Python (True == 1) -- must not
    # silently pass as a valid max_blocks value.
    intake_dict = _intake(["a.py"], {"a.py": 5})
    try:
        build_semantic_chunk_plan(intake_dict, max_blocks=True)
    except IntakeValidationError as exc:
        assert str(exc) == "max_blocks_invalid"
    else:
        raise AssertionError("expected IntakeValidationError for max_blocks=True")


def test_red33_non_positive_max_chars_per_block_fails_closed() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    for bad in (0, -1):
        try:
            build_semantic_chunk_plan(intake_dict, max_chars_per_block=bad)
        except IntakeValidationError as exc:
            assert str(exc) == "max_chars_per_block_invalid"
        else:
            raise AssertionError(f"expected IntakeValidationError for max_chars_per_block={bad}")


def test_red33_normal_max_blocks_unchanged() -> None:
    intake_dict = _intake(["a.py"], {"a.py": 5})
    for value in (1, 6):
        plan = build_semantic_chunk_plan(intake_dict, max_blocks=value)
        assert plan.status == "complete"
