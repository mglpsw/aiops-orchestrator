# Checkpoint — adversarial audit follow-up on #200-B/#200-C (post-merge)

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 200 (already core_synthetic_complete via #200-A/#200-B/#200-C, still OPEN
    pending AgentEscala#763-A canary)
  branch: fix/200-adversarial-audit-followup
  base_sha: bc52aaddb9eedeae232e3abf07bb9efd7b3490e8   # origin/master after #201-B1 merged
  audited_commits:
    - 717c51eec12b2f5cfeae13400011f8d90b8ecdcc   # #200-A merge
    - c5c07d890800a933d5a0779408a444b366f84635   # #200-B merge
    - 71e0c74c828199fb7ea56a45430a7dff80913150   # #200-C merge

trigger: "human-authored adversarial replan gate against the live state of
  #200-B (app/agent_review/review_content_extraction_v2.py), delivered
  mid-session after #200-C had already merged. Treated as a review of
  already-merged code, not a pre-merge gate."
```

## Findings classified (all 5 CONFIRMED, zero false positives)

### 1. CI coverage gap — `requires_network` tests never actually ran

**Confirmed.** `scripts/test.sh` and `scripts/ci_validate.sh` both deselect
`requires_network`-marked tests by default (this repo's own established
convention for "spawns a real git subprocess", not literal network access
— see `test_diff_acquisition_v2.py`'s own docstring on the marker). Every
new `#200-B`/`#200-C` E2E test that spawned a real temporary git repo (11
of 14, 4 of 8 respectively) was marked `requires_network` following that
exact convention — meaning neither GitHub Actions job that reported
"green" on PRs #207/#208 ever executed them. The full local `pytest -q`
(no marker filter) that this session's own PR bodies quoted as evidence
DID run and pass them — the gap was specifically between "locally
verified" and "the CI gate GitHub actually reports."

**Fix:** `scripts/ci_validate.sh` gained a new numbered section (§8) that
runs `python3 -m pytest -q -m requires_network` explicitly, isolated from
§7's own filtered run. Proven: §7 (1917 tests) + §8 (36 tests) = 1953,
matching the full unfiltered `pytest -q` count exactly.

### 2. DLP detector-only silently treated as "clean" — real safety gap

**Confirmed.** `_apply_dlp_v2` only ever evaluated inline `rules`; a
`DlpPolicyDeclarationV2` declaring `detector_name` alone (a host-owned
detector this engine does not execute) returned `False` (not blocked) —
silently reporting "zero matches" as "verified clean" for coverage that
was never actually executed. Violates the epic's own absolute rule:
"suspeita inconclusiva não é 'limpa parcialmente e envia'."

**Fix:** `_apply_dlp_v2` now returns a typed `DlpEvaluationV2` and treats
ANY `detector_name`-declared policy as unconditionally blocked
(`CONTENT_REASON_DLP_DETECTOR_NOT_EXECUTED_V2`), regardless of whether
inline `rules` also matched — a distinct reason code from an actual rule
match (`transport_blocked_by_dlp`), so a caller can tell "proven unsafe"
apart from "never actually checked." A `must_review` fragment blocks
fail-closed; an optional one degrades to `BLOCKED_BY_TARGET_DLP`.

### 3. Budget enforced per-fragment only, never summed per chunk

**Confirmed.** Several individually-small fragments landing in the same
chunk could collectively exceed `max_chars_per_chunk` without ever being
caught — the per-fragment `len(redacted) > max_chars_per_chunk` check
never summed across a chunk. Additionally, `max_chars_per_chunk` was a
bare caller-supplied `int` with a hardcoded default (`20_000`), letting a
caller silently diverge from the target's own real profile.

**Fix:** `extract_review_content_v2` now requires `target_budgets:
TargetBudgetsV2` (a real, already-loaded profile budget object — no bare
int accepted at all, proven structurally by
`test_target_budgets_cannot_be_relaxed_by_a_bare_int_the_caller_cannot_
derive`). New `_enforce_chunk_budget_v2` sums every `INCLUDED` fragment's
`chars` per chunk after building it: a `must_review` fragment among the
overflow blocks the WHOLE extraction
(`CONTENT_REASON_CHUNK_OVER_BUDGET_REQUIRES_REPLAN_V2`); an
all-auxiliary overflow degrades the largest fragments first (mirrors
`planner_v2`'s own documented "auxiliary dropped first, must_review never
dropped" doctrine) until the chunk fits.

### 4. Windowing repeated-anchor duplication — CONFIRMED, most severe

**Confirmed by direct empirical reproduction**, not just theory. A hunk
with 1 old line replaced by 300 new lines, forced into 15 windows: before
the fix, the single real deleted line appeared in **all 15** extracted
window fragments (proven live: `occurrences == 15`). Root cause:
`planner_v2._proportional_window`'s own documented starved-side exception
collapses the old side's range to the SAME single-point anchor across
every window when one side has fewer real lines than the window count;
`slice_hunk_body_by_range_v2`'s pure range-membership rule, called
independently per fragment, had no way to know a line was already claimed
by a sibling window.

**Fix:** `_assign_hunk_line_ownership_v2` resolves, once per hunk across
the WHOLE manifest (not per chunk — a hunk's windows can land in
different chunks after packing), a deterministic line-index → owning-
fragment-id map: the fragment with the smallest `(old_range.start,
new_range.start, fragment_id)` tuple claims each real line. `slice_hunk_
body_by_owned_lines_v2` slices by this resolved ownership instead of raw
range membership for windowed (multi-fragment) hunks only — a single-
fragment hunk (the overwhelming majority) is untouched, using the
original `slice_hunk_body_by_range_v2` exactly as before. **No second
parser or planner was introduced** — the fix is a pure post-processing
resolution over `planner_v2`'s own already-emitted `FragmentV2` ranges.

Proven after the fix, on the same adversarial fixture: the deleted line
appears in exactly 1 of 15 fragments, and the full union of all 15
fragments' content covers every one of the 301 real lines (1 old + 300
new) exactly once — zero missing, zero duplicated, zero invented.

### 5. Hardening — local paths, and additional scenario coverage

**Confirmed real gap, found while fixing.** `_build_fragment_content_v2`
called `redact_text` (secret redaction) but never `_redact_local_paths` —
`review_content_v2.FragmentContentV2`'s own last-line
`sanitize_artifact_value` guard was the ONLY thing catching a leaked local
path, and it did so by raising a raw, unhandled `pydantic.ValidationError`
instead of this module's own typed, redacted, fail-closed path. This
directly violated `review_content_v2`'s own documented contract:
"#200-B's extractor is required to redact BEFORE calling this
constructor, never rely on it to redact silently."

**Fix:** content now passes through `_redact_local_paths` (the same
helper `redaction.sanitize_artifact_value` itself composes) after
`redact_text`, before ever reaching `FragmentContentV2`'s constructor.

**Additional test coverage added:** add/modify/delete/rename in a single
run (one hunk-diverse fixture); a local home-directory path in content.
**Explicitly not new gaps, already covered elsewhere, not duplicated
here:** stale HEAD/cross-run/cross-target/fragment-hash-divergence are
already proven by `#200-A`'s own `bind_review_content_to_manifest_v2`
tests, reused unchanged by this extractor (`bind_review_content_to_
manifest_v2` is called, unmodified, at the end of every `extract_review_
content_v2` call). Dual-target (AgentEscala/InterLeitos) proof is `#204`'s
own dual-target conformance slice per the epic's DAG — this extractor's
architecture is provably repo-agnostic (nothing branches on repository
name), but a per-target E2E fixture belongs to that later slice, not here.

## Verdict

All 5 findings CONFIRMED real. None demonstrated a contractual
incompatibility requiring a new phase/issue/PR — every fix landed inside
the existing `#200-B`/`#200-C` contract shape, using only primitives that
already existed (`planner_v2`'s own emitted ranges, `redaction.py`'s own
`_redact_local_paths`, `TargetProfileV2.budgets`'s own real object). Per
the instruction this audit was delivered under, **no merge was performed
without explicit confirmation** — this PR is opened, gated green, and
held for review.

## Evidence

```text
.venv/bin/python -m pytest -q                    → 1953 passed, 4 skipped
bash scripts/ci_validate.sh                       → §7: 1917 passed, 4 skipped, 36 deselected
                                                     §8: 36 passed, 1921 deselected — OK
.venv/bin/python scripts/export-agent-review-v2-schemas.py --check → byte-identical (no schema touched)
.venv/bin/python scripts/verify-caem-f0-pin.py --check → ok
.venv/bin/python scripts/generate-ri-b0a-2-reuse-view.py --check → byte-identical
git diff --check                                  → clean
ruff check (modified files)                        → All checks passed!
```
