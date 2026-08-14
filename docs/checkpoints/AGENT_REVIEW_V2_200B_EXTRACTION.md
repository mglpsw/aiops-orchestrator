# Checkpoint — AgentReview v2 semantic review content: real extraction slice (#200-B)

**Status:** `CHECKPOINT SNAPSHOT` — registro da slice no corte em que foi escrito. Não é estado atual; ver [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md).

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 200
  slice: 200-B (real extraction, redaction, declarative DLP evaluation)
  branch: feat/200-b-review-content-extraction
  base_sha: 717c51eec12b2f5cfeae13400011f8d90b8ecdcc   # origin/master after #200-A merged
  # head_sha: this slice is a single commit; the authoritative value is the
  # PR's own head SHA on GitHub, not hand-copied here -- amending this file
  # to embed its own post-amend commit SHA is circular by construction
  # (see #200-A's own checkpoint, whose hand-copied head_sha went stale for
  # the same reason). Read it from `git log` / the PR, not from this file.

state:
  contract_frozen: true                     # inherited from #200-A, unchanged
  content_extraction_implemented: true       # this slice
  redaction_dlp_engine_implemented: true     # this slice (declarative DLP; see limitations)
  router_transport_wired: false              # #200-C
  semantic_review_e2e: false                 # #200-C
  canary_review_of_a_real_repository: false  # AgentEscala#763-A, gated on #200-C
  core_synthetic_complete_for_200: false     # requires this slice + #200-C together
  existing_v2_schemas_unchanged: true        # verified -- no new/changed schemas this slice at all
  capability_state: contract_readiness_baseline   # NOT semantic_reviewer_shadow yet -- see #199

runtime:
  environment: GitHub-hosted cloud, clean venv (pydantic 2.11.3 / PyYAML 6.0.2,
    matching requirements-agent-review.lock)
  router_enabled: false            # no transport implementation exists yet in this slice
  ct102_touched: false
  target_repository_touched: false # no write to AgentEscala or InterLeitos

evidence:
  full_test_suite: "1896 passed, 4 skipped (0 failed)"   # 1882 (post-#200-A) + 14 new
  new_tests_this_slice: 14
  schema_export_check: "AgentReview v2 schemas are byte-identical. (unchanged this slice)"
  ci_validate_sh: "1872 passed, 4 skipped, 24 deselected -- OK"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (unchanged this slice)"
  git_diff_check: clean
  real_e2e_proven:
    - "real git repo -> acquire_authoritative_diff_v2 -> real ManifestV2 (run_assembly_v2)
       -> real ChunkPayloadV2 (payload_builder_v2) -> extract_review_content_v2 ->
       bind_review_content_to_manifest_v2, re-checked from the outside"
    - "windowed (over-line-budget) hunk: 8 window fragments extracted, zero line
       double-counted across windows for a realistic interleaved old/new hunk"
    - "planted secret-like token (ghp_...) redacted before reaching the sidecar"
    - "must_review fragment matching a DLP rule blocks fail-closed
       (transport_blocked_by_dlp), never silently approved"
    - "non-must_review DLP match degrades to a typed BLOCKED_BY_TARGET_DLP omission
       instead of blocking the whole run"
    - "must_review content exceeding the char budget blocks fail-closed
       (content_over_budget_requires_replan)"
    - "empty manifest (all-excluded non-must-review binary diff) refused fail-closed
       (no_reviewable_chunks), not a raw pydantic ValidationError"
  commits: 1

remaining_for_issue_200:
  - "#200-C: Router transport wiring, offline-file transport for tests, and a
     dual-target synthetic E2E producing a real ReviewReadinessV2 from real content"
  - "AgentEscala#763-A: the first real canary, gated on #200-C landing and a repin
     to the RC that contains it"
```

## What is proven here

- `review_content_extraction_v2.extract_review_content_v2` turns a REAL git
  diff into a real, bound `ReviewContentV2` -- proven end-to-end against a
  real temporary git repository (not mocked), including the real
  `ManifestV2` (`run_assembly_v2`) and real `ChunkPayloadV2`
  (`payload_builder_v2`) it binds against;
- `diff_acquisition_v2.extract_hunk_bodies_v2` reuses the SAME parser
  (`_FileBlockBuilder`) `parse_unified_diff` uses -- not a second engine --
  and every returned body is re-verified against
  `compute_hunk_diff_sha256_v2` before being trusted;
- the line-selection rule for windowed (over-budget) fragments
  (`slice_hunk_body_by_range_v2`) is lossless without duplication for a
  realistic interleaved-hunk fixture -- proven directly, not assumed, by
  `test_extract_review_content_windows_a_hunk_larger_than_the_line_budget_
  losslessly`;
- a whole-hunk fragment's extracted content independently re-derives the
  SAME `diff_sha256` the original parser computed -- the "não normalize até
  passar" stop condition from the #199 execution plan was never triggered,
  because it never needed to be: recomposition succeeded on every real
  fixture tried, including the windowed one;
- a planted secret-like token is redacted before any content reaches
  `ReviewContentV2` -- `_validate_reviewable_content_text_v2`'s own
  last-line `sanitize_artifact_value` guard (from `#200-A`) would refuse
  construction if it were not;
- every fail-closed path required by issue `#200`'s acceptance criteria is
  exercised directly: DLP block on `must_review`, budget overflow on
  `must_review`, recomposition failure, and the newly-discovered empty-
  manifest edge case -- all raise `ExtractionBlockedError` with a stable
  reason code, never a raw exception or a silent approval.

## What is NOT proven or claimed here

- no request has ever been sent to the Agent Router, or to any transport --
  that is `#200-C`;
- `ReviewReadinessV2` has not been computed from this real content anywhere
  -- `#200-C`'s deliverable;
- no canary review of a real repository (`AgentEscala#763-A`);
- automatic re-planning when extracted content exceeds budget is NOT
  implemented -- a `must_review` fragment that does not fit blocks the
  whole extraction with `content_over_budget_requires_replan` rather than
  invoking `planner_v2` again with a smaller line budget in the same call.
  Named here as an explicit limitation, not silently faked as already
  solved;
- `OMITTED_BINARY`/`OMITTED_SUBMODULE` classification is defense-in-depth,
  not reachable through today's real `run_assembly_v2` output (a binary
  file never produces a fragment at all -- see the doc's "documented
  reachability gap" section). Verified directly via a unit test against a
  hand-built fixture, not through the real pipeline;
- a `detector_name`-only (host-owned external) DLP policy contributes ZERO
  rule coverage from this extractor -- only inline `rules` are evaluated;
- `#200` does not close with this slice. It remains open pending `#200-C`.

## Rollback

One commit, additive: one new module
(`app/agent_review/review_content_extraction_v2.py`), one new test file,
and a small, additive extension of `diff_acquisition_v2.py` (a new
dataclass, two new functions, and the existing inline hash computation
replaced by a call to the new shared `compute_hunk_diff_sha256_v2` --
verified byte-identical behavior via the full existing
`test_diff_acquisition_v2.py` suite, unchanged pass count). No schema
changed. No existing public function's signature changed. Reverting this
commit requires no coordinated change elsewhere -- `#200-C` does not exist
yet to depend on it.
