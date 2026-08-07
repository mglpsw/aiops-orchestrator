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
§7's own filtered run. Proven: §7 + §8 always sums to the same total as
the full unfiltered `pytest -q` count (re-verified on this PR's final
HEAD in the Evidence section below, not carried forward from an earlier
commit).

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

**Fix (first pass):** `extract_review_content_v2` now requires
`target_budgets: TargetBudgetsV2` (a real, already-loaded profile budget
object — no bare int accepted at all). New `_enforce_chunk_budget_v2`
sums every `INCLUDED` fragment's `chars` per chunk after building it.
**Superseded by findings 6 and 7 in the addendum below** — the
`TargetBudgetsV2` acceptance was itself replaced by a hash-checked
`TargetProfileV2`, and the block-vs-drop condition was corrected to only
block when `coverage_required` content ALONE cannot fit.

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

## Addendum — second independent pass (4 more points, same PR, no new phase)

A second, independent adversarial pass over the same fix's first commit
(`c33e490`) confirmed the 5 findings above and their fixes as real and
CI-green (including the new `requires_network` §8 run), then raised 3
additional functional points plus 1 evidence-freshness point, all
addressed in this same PR:

### 6. Budget still not bound to the profile that produced the manifest

**Confirmed.** Finding 3's fix required a real `TargetBudgetsV2`, but that
proves VALUES only, not PROVENANCE — any caller could still construct a
`TargetBudgetsV2` with a looser `max_chars_per_chunk` than the profile
that actually planned the manifest, and nothing checked it against
`RunIdentityV2.profile_hash` (which `run_assembly_v2` already computes and
stores for exactly this purpose).

**Fix:** `extract_review_content_v2` now takes the full `target_profile:
TargetProfileV2` instead of a bare `TargetBudgetsV2`, and checks
`compute_profile_hash_v2(target_profile) == manifest.identity.
profile_hash` before anything else runs — a mismatch blocks fail-closed
(`CONTENT_REASON_PROFILE_HASH_MISMATCH_V2`, new reason code). Only after
that check passes is `target_profile.budgets.max_chars_per_chunk` read.
Red-tested by `test_extract_review_content_refuses_a_target_profile_that_
did_not_plan_the_manifest`: a profile that differs ONLY in
`max_chars_per_chunk` from the one that planned the manifest is rejected;
an independently-reconstructed but value-identical profile is accepted
(proving the check is a real hash comparison, not object identity).

### 7. `_enforce_chunk_budget_v2` blocked before trying to drop auxiliaries

**Confirmed real bug**, contradicting the module's own documented
doctrine ("auxiliary dropped first; must_review never dropped"). The
original check was `if any(f.coverage_required for f in included): raise`
— triggered by the mere PRESENCE of a `coverage_required` fragment in an
over-budget chunk, before ever attempting to drop auxiliary content. A
chunk with `required=100 chars + auxiliary=450 chars`, budget `500`,
would block even though dropping the auxiliary content alone brings the
chunk to `100 <= 500` — clearly fittable.

**Fix:** the condition is now `required_chars = sum(chars for required
fragments); if required_chars > max_chars_per_chunk: raise` — only
blocking when NO amount of auxiliary-dropping could ever make the chunk
fit. Red-tested by `test_extract_review_content_drops_auxiliaries_to_
make_room_for_a_mixed_required_chunk`: a chunk with 1 required fragment
and 20 auxiliary fragments summing over budget now survives with the
required fragment `INCLUDED` and enough auxiliaries dropped to fit —
proving the doctrine is followed, not just documented.

### 8. add/modify/delete/rename test didn't prove `delete`, and accepted either rename path

**Confirmed.** The fixture created `deleted.py` and renamed `old_name.py`
→ `new_name.py`, but only asserted a fragment existed for SOME path
matching `"new_name.py" in paths_seen or "old_name.py" in paths_seen` —
never checking the deleted file's actual removed-line content, and the
`OR` accepted either the canonical or stale rename path.

**Fix:** the test now asserts `"-to be removed" in fragments_by_path[
"deleted.py"].content` (the real removed line is genuinely extracted, not
just "a fragment object with this path exists"), and asserts
`"new_name.py" in fragments_by_path` AND `"old_name.py" not in
fragments_by_path` — proving the canonical identity deterministically
(`ParsedFileDiffV2.path` is `new_path or old_path`; a rename always has a
`new_path`, so it is NEVER filed under the old name).

### One remaining checklist item verified, not a new finding

The second pass also flagged one explicit item from #200's own "testes
obrigatórios" checklist this fix had not yet directly exercised:
"múltiplos hunks, mesmo arquivo em vários chunks." Verified as a genuine
gap (no prior test covered it) and closed:
`test_extract_review_content_handles_two_hunks_of_the_same_file_landing_
in_different_chunks` proves two well-separated hunks of ONE file split
across two DIFFERENT chunks extract correctly — exercising ownership
resolution's global-across-the-manifest key (`(path, hunk_index)`, not
per-chunk) and hunk-body lookup at a chunk boundary.

The remaining items on that same checklist ("stale HEAD, cross-run,
cross-target, path/hash divergente, conteúdo fora do manifest") were
re-verified as already covered, not assumed: `tests/agent_review/
test_review_content_v2.py`'s own `test_bind_rejects_a_run_id_mismatch`,
`test_bind_rejects_a_manifest_hash_mismatch`, `test_bind_rejects_a_path_
diverging_from_the_manifest_fragment`, `test_bind_rejects_a_diff_sha256_
diverging_from_the_manifest_fragment`, `test_bind_rejects_a_content_
chunk_absent_from_the_manifest`, and `test_bind_rejects_a_fragment_not_
in_the_manifest_chunk` — all from `#200-A`, all reused UNCHANGED by
`extract_review_content_v2`'s own unconditional call to `bind_review_
content_to_manifest_v2` at the end of every invocation. A stale HEAD or
cross-target repo both surface as `run_id` divergence (HEAD and repo are
both embedded in `RunIdentityV2`, which feeds `run_id`'s own computation)
— the same test covers both without needing a separately-named fixture.

### Evidence freshness (the 4th point)

The second pass also flagged that the PR body/checkpoint/receipt still
quoted the FIRST commit's numbers (§7 1917 + §8 36 = 1953) after CI had
already re-run on a later HEAD reporting 1917+37=1954. This addendum adds
2 more `requires_network` tests (findings 6 and 7's red tests) on top of
that, so the numbers below are re-measured on THIS commit's real HEAD,
not carried forward from an earlier one.

## Verdict

All 5 first-pass findings and all 3 second-pass functional findings (8
total) CONFIRMED real, zero false positives across both independent
passes. None demonstrated a contractual incompatibility requiring a new
phase/issue/PR — every fix landed inside the existing `#200-B`/`#200-C`
contract shape, using only primitives that already existed (`planner_v2`'s
own emitted ranges, `redaction.py`'s own `_redact_local_paths`,
`RunIdentityV2.profile_hash`/`compute_profile_hash_v2` already computed by
`run_assembly_v2`). Per the instruction both audits were delivered under,
**no merge was performed without explicit confirmation** — this PR is
opened, gated green, and held for review.

## Evidence (re-measured on this commit's HEAD, not carried forward)

```text
.venv/bin/python -m pytest -q                    → 1956 passed, 4 skipped
bash scripts/ci_validate.sh                       → §7: 1917 passed, 4 skipped, 39 deselected
                                                     §8: 39 passed, 1921 deselected — OK
.venv/bin/python scripts/export-agent-review-v2-schemas.py --check → byte-identical (no schema touched)
.venv/bin/python scripts/verify-caem-f0-pin.py --check → ok
.venv/bin/python scripts/generate-ri-b0a-2-reuse-view.py --check → byte-identical
git diff --check                                  → clean
ruff check (modified files)                        → All checks passed!
```
