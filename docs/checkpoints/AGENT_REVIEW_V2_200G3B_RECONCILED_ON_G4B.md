# AgentReview v2 -- #200-G3B Checkpoint: Reconciled on #200-G4B

**Scope:** issue [#288](https://github.com/mglpsw/aiops-orchestrator/issues/288)
(`#200-G3B`) / [#289](https://github.com/mglpsw/aiops-orchestrator/issues/289)
(`#200-G4B`), child of `#200`.

**Why this branch exists.** PR [#295](https://github.com/mglpsw/aiops-orchestrator/pull/295)
(`feat/200-g3b-authoritative-diff-identity`, base `9dace90c`) reached a
correction-round-complete state, but its own final checkpoint
(`docs/checkpoints/AGENT_REVIEW_V2_200G3B_AUTHORITATIVE_DIFF_IDENTITY.md`,
§9) explicitly declares **no terminal verdict** -- round-2 review was never
confirmed through a genuine channel, and that document records an active
attempt, during that round, to inject a false completion claim through
unverified, format-mismatched messages. Separately, `#200-G4B` (issue
#289) landed on master as `a81303d5` and modified
`app/agent_review/diff_acquisition_v2.py`'s `_run_git_v2` -- the same
acquisition layer G3B's own diff-identity primitive sits on top of. A
qualification obtained against one acquisition boundary does not remain
qualified once that boundary changes underneath it; this branch is a
genuine reconciliation and full requalification from zero, not a
trust-transfer from PR #295.

**Branch:** `feat/200-g3b-reconciled-on-g4b`.
**This document's head:** `accb473540e4c294e7dab11c1c3dd94428562d80`.

## 1. Reconciliation

Starting point verified live (not assumed): `git fetch origin && git rev-parse
origin/master` = `a81303d564610d9cfd036df37b9396efb4b73f01` at the start of
this work, matching the task's stated live master.

Created `feat/200-g3b-reconciled-on-g4b` from that commit and cherry-picked
PR #295's full commit range (`9dace90c..3f24b39a`, 12 commits, source branch
`feat/200-g3b-authoritative-diff-identity` fetched read-only, never pushed
to or reused as a working branch). **All 12 commits applied via git's 3-way
merge with zero manual conflict resolution** -- inspection beforehand showed
G3B's only change to `diff_acquisition_v2.py` (a 30-line docstring/delegation
edit to `acquire_authoritative_diff_v2`, around line 1257) and G4B's only
change to the same file (the `_run_git_v2` ingress-validation call, around
line 815) sit in textually disjoint regions with no overlapping context.

**Composition verified structurally, not just non-conflicting.** G3B's new
module (`authoritative_diff_identity_v2.py`) never calls `_run_git_v2`
directly -- it calls `diff_acquisition_v2.acquire_diff_v2`/
`acquire_raw_diff_v2`, the existing higher-level wrappers, which internally
call `_run_git_v2`. G3B's diff acquisition therefore routes through G4B's
ingress authority by composition, with no manual wiring required. Confirmed
by grep after reconciliation: `_run_git_v2` still calls
`validate_external_input_directory_v2` (G4B), and
`acquire_authoritative_diff_v2` still delegates to
`acquire_authoritative_diff_with_identity_v2` (G3B).

## 2. Master moved again mid-task: `#200-G4B` post-merge corrective PR #296

While this branch was paused awaiting review-lane results, an external
Codex review landed on the already-merged G4B commit (PR #294) and found
two real issues, later closed by PR #296
(`2242169cb6410baf9ddc9ce96b8c6f9f70f51875`), independently verified via
`gh api repos/mglpsw/aiops-orchestrator/pulls/294/comments` (not accepted
from any relayed description):

- **P1** (`app/agent_review/payload_references_v2.py:114`): the bounded-read
  primitive materialized a whole external artifact into memory before
  comparing against `max_bytes`, defeating the size-limit protection for an
  oversized untrusted artifact.
- **P2** (`scripts/aiops-review-build-payload-set-v2.py:98`): payload
  enumeration filtered on `entry.resolved_path.name` (symlink-resolved)
  instead of the entry's own name, changing dedup/matching semantics
  relative to the pre-G4B `glob("*.json")` behaviour.

PR #296 also folded in a third fix matching a gap this session found and
independently verified by direct code inspection before any external claim
about it: `_run_git_v2` called `validate_external_input_directory_v2(repo_root)`
and discarded the returned capability, then passed the raw, unresolved
`repo_root` to `subprocess.run(cwd=...)` -- a TOCTOU/discipline break, the
only call site among every `validate_external_input_directory_v2`/
`validate_external_input_file_v2` caller in the codebase that did not use
`.resolved_path` (confirmed by grepping every call site). Assessed (and
independently agreed with) as a real defense-in-depth gap, not a
demonstrated bypass of the diff-binding/digest check itself -- git's diff
acquisition is bound to commit objects by content-addressing (SHA-validated
refs), not directory identity, so a TOCTOU race on `cwd` cannot forge a
diff for SHAs it does not actually contain.

All three fixes independently verified present in `2242169`'s diff before
rebasing onto it (`git diff a81303d5 2242169 -- <files>`).

Rebased `feat/200-g3b-reconciled-on-g4b` onto `2242169` (`git rebase
origin/master`) -- clean, zero conflicts, 12 commits replayed. Post-rebase
grep confirms the TOCTOU fix is present in the reconciled tree
(`validated_repo_root = validate_external_input_directory_v2(repo_root).resolved_path`,
used for `subprocess.run(cwd=validated_repo_root)`).

## 3. Requalification evidence (post-reconciliation, pre-fix)

- `python scripts/export-agent-review-v2-schemas.py --check`: passes,
  byte-identical.
- `python scripts/run-agent-review-v2-evals.py --check`: passes, byte-
  identical (durations excluded).
- `pytest tests/agent_review/ tests/ri_b0a/`: 2747 passed, 12 skipped, 2
  failed -- both the pre-existing, environment-class sudo-denial failures
  (`SUDO_PATH_V2 is None` / sudo blocked in this sandbox), the same
  signature documented throughout this whole lineage's checkpoints. No new
  failures relative to that baseline.
- Both P1-class fixes from PR #295's original correction round reconfirmed
  intact by direct code read: (a) `_extract_review_content_v2` derives
  `file_diffs`/`hunk_bodies` from a single
  `acquire_authoritative_diff_with_identity_v2` call, not two; (b) the
  windowed-fragment range-containment check
  (`CONTENT_REASON_FRAGMENT_RANGE_OUTSIDE_HUNK_BOUNDS_V2`) runs
  unconditionally before either slicing path.
- End-to-end binding property re-verified against the reconciled acquisition
  path: `test_truncated_diff_with_same_apparent_path_is_rejected_before_scope`
  (real git repo, real `_run_git_v2` subprocess acquisition, therefore
  genuinely exercising G4B's ingress authority) -- manifest built from the
  full, real diff; verifier fed a truncated diff with identical apparent
  paths; digest mismatch; `ManifestDiffBindingError`
  (`diff_binding_diff_identity_mismatch`); scope classification tracked and
  confirmed never reached. Passing.
- Mutation test on the digest check itself (`authoritative_diff_identity_v2.
  verify_manifest_diff_binding_v2`): commit clean at `c2584e2`; changed
  `if observed != binding.authoritative_diff_sha256:` to `if False and (...)`
  ; both `test_truncated_diff_with_same_apparent_path_is_rejected_before_
  scope` and `test_extract_review_content_refuses_a_tampered_re_acquisition_
  before_any_classification` went RED (the production-entrypoint witness
  failed differently and informatively: execution proceeded past the
  disabled check into downstream hunk-recomposition failure, not into a
  clean pass); restored via `git checkout --`; both GREEN; `git status`
  clean.

## 4. New finding this session, independently reproduced: fragment-coverage
completeness gap

**Not accepted from any relayed description.** Reproduced from scratch
against real code before any fix was written (script preserved in this
session's own record, logic folded into the permanent regression test
below): built a real git repo, a real 6-line single-hunk change to a
must-review file (line 4 carrying a deliberate marker), and a real manifest
via the actual `assemble_manifest_from_diff_v2` pipeline. Hand-built a
replacement `ManifestV2` (freely constructible -- not exclusively produced
by the trusted pipeline) whose sole fragment for that path declared a
narrower `old_range`/`new_range` (3 of 6 real lines) using the SAME
hunk-level `diff_sha256` the real fragment used (confirmed
`FragmentV2.diff_sha256` is the hunk's digest, not the fragment's own
sliced-content digest -- a legitimate construction, not malformed). This
adversarial manifest passed `ManifestV2`'s own self-validators,
`bind_manifest_to_diff_identity_v2`, and `verify_manifest_diff_binding_v2`
against the real, untampered diff bytes (byte identity was never violated --
only the manifest's claimed fragment coverage was narrowed). `extract_review_
content_v2` (the real production entrypoint) then succeeded with no error,
`policy=INCLUDED`, returning only lines 1-3 -- the marker line never
appeared anywhere in the output, silently, with no reason code.

**Root cause:** `verify_manifest_diff_binding_v2` proves diff-BYTE identity;
nothing proved that `manifest.fragments`' declared ranges actually cover
every real line of a hunk a `coverage_required` fragment claims to
represent. This is the same defect class that produced `STOP_G3_SCOPE_
CONTRACT_NOT_CONVERGING` on the #285 predecessor (self-consistency proven,
authenticity/completeness against the real source not proven), recurring
one layer deeper -- fragment-range coverage, not path-set agreement -- and
is exactly the class of defect this whole lineage's operating discipline
says not to defer once found real.

**Current production reachability confirmed zero**, independently, via
`grep -rn "bind_manifest_to_diff_identity_v2" app/ scripts/`: no caller
exists outside `authoritative_diff_identity_v2.py`'s own definition and a
docstring reference. The gap is real in the primitive, not (yet) reachable
through any live entrypoint, since `run_assembly_v2`/the real pipeline does
not call `bind_manifest_to_diff_identity_v2` today.

### Fix (commit `accb473`)

Added `_verify_hunk_fragment_coverage_completeness_v2` in
`review_content_extraction_v2.py`: a global, pre-classification gate. For
every hunk with at least one `coverage_required` fragment, the union of
every fragment referencing that hunk (required or not -- coverage can
legitimately split across a required and an auxiliary fragment) must equal
the hunk's own real, re-acquired full range on BOTH old and new sides.
Coverage is checked via a proper interval-union walk
(`_ranges_fully_cover_v2`), not a `min(starts)/max(ends)` comparison, which
would miss a gap in the MIDDLE of the union. New reason code:
`fragment_coverage_incomplete`. Wired immediately after `fragments_by_hunk`
is built and before any chunk's content is constructed -- deliberately
separate from, and not a replacement for, the existing `fragment_range_
outside_hunk_bounds` guard (that check rejects a fragment claiming MORE
than its hunk; this one rejects a hunk's fragment set claiming LESS than
its hunk).

New permanent regression test:
`test_extract_review_content_rejects_a_manifest_whose_fragments_do_not_
cover_the_real_hunk` (`tests/agent_review/test_review_content_extraction_v2.py`),
the pytest-ified form of the from-scratch reproduction above, going through
the real `extract_review_content_v2` entrypoint end to end.

**Verified no false positive against real, planner-produced multi-fragment
coverage**: re-ran `test_extract_review_content_windows_a_hunk_larger_than_
the_line_budget_losslessly` and `test_extract_review_content_never_
duplicates_a_repeated_anchor_line_across_windows` (both exercise the real
planner forcing genuine windowing via a small `max_lines_per_chunk`) --
both pass; the real planner's fragments do tile each hunk exactly, so the
new completeness check does not reject legitimate windowed coverage.

**Mutation test:** commit clean at `accb473`; changed the completeness
check's condition to `if False and (...)`; new test went RED (`DID NOT
RAISE ExtractionBlockedError`); restored via `git checkout --`; GREEN;
`git status` clean.

**On Lane B's related "Finding 2" concern** (windowed multi-fragment content
never independently verified against real content): the actual CONTENT
returned for a windowed fragment was already, and remains, always sliced
directly from the real, re-acquired `hunk_body.body_text` via
`slice_hunk_body_by_range_v2`/`slice_hunk_body_by_owned_lines_v2` -- a
fragment's own `diff_sha256`/`diff_chars` fields (attacker-controlled in the
adversarial-construction scenario) are never used to determine returned
content, only to compute `fragment_id`. The gap was specifically in RANGE
completeness, not content authenticity, and is closed by this fix combined
with the pre-existing range-containment guard and the whole-hunk exact-
recomposition digest check.

## 5. Historical-falsifier reconciliation matrix

Per this lineage's own operating discipline ("a property qualified on one
subject does not remain qualified once the subject changes"), checked
whether findings from #277, #285, and PR #295's original correction round
reproduce against this branch's final head -- not asserted as "already
fixed," each independently re-derived against the real, current tree.

| Source | Finding | Classification | Evidence |
|---|---|---|---|
| #277, Authority B | `declared_toolrepo_sha` forgeable by fabrication (tamper a module, recompute digest honestly, declare the real HEAD sha) | `NOT_APPLICABLE_WITH_PROOF` | Subject head (`a536df5d...`, the whole `#200-F` operational-boundary lineage) is not an ancestor of `origin/master` (`git merge-base --is-ancestor` confirms). That authority/subsystem (toolrepo-sha binding, semantic module loading) does not exist anywhere in this branch or master; it belongs to the operational-composer/G5 lineage, out of this task's scope. |
| #277, Authority E | Quoted-secret redaction: 6 leak shapes, JWTs spared, quadratic-time DoS on a 16k-char line | `NOT_APPLICABLE_WITH_PROOF` | The refuted subject's whole redaction-hardening lineage (`ed6692d`..`a536df5`, plus `#200-G2`'s later `2ddd676`/`98fe850`) is not an ancestor of `origin/master`. `git log origin/master -- app/agent_review/redaction.py` shows only the original, pre-`#200` "offline intake and redaction engine" commits (`9b2fcce`, `a50239a`). This branch's `review_content_extraction_v2.py` imports `redact_text`/`sanitize_artifact_value` from that same original, unmodified module -- a code path disjoint from the refuted subject. |
| #285 | `assert_scope_authority_agrees_with_assembly_v2` proves internal path-set self-consistency, never that `file_diffs` is authentically the material `manifest` was built from -- a truncated `file_diffs` produces vacuous agreement, reproducibly | `NOT_APPLICABLE_WITH_PROOF` (specific implementation) | `grep -rln "assert_scope_authority_agrees_with_assembly_v2\|ScopeCompletenessV2\|operational_scope_v2" app/ scripts/` returns zero results; that machinery was never merged (`STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING`, "does not merge"). |
| #285 | Same finding, generalized: **self-consistency proven, authenticity/completeness against the real source not proven** (the defect CLASS, not the specific detector) | `SUPERSEDED_BY_STRONGER_FALSIFIER`, with one confirmed recurrence (below) | G3B's byte-identity digest binding is a structurally different answer to exactly this class, verified via `test_truncated_diff_with_same_apparent_path_is_rejected_before_scope` against the reconciled acquisition path (§3). This closes the ORIGINAL #285 mechanism (path-set agreement) structurally. It does **not** claim the defect class itself can never recur -- and it did recur, one layer deeper, as §4's fragment-coverage-completeness gap, now itself closed (below). |
| PR #295 correction round, Finding 1 | `_extract_review_content_v2` acquired the diff TWICE (`file_diffs` from one call, `diff_text` from another); only the latter was covered by the binding check | `CLOSED_BY_CURRENT_DESIGN` | Confirmed by direct read: `_extract_review_content_v2` calls `acquire_authoritative_diff_with_identity_v2` exactly once; `file_diffs` and `diff_text` are both derived from that single call. |
| PR #295 correction round, Finding 2 | Windowed-fragment ranges reached the slicing functions with no check they fell inside the hunk's real, re-acquired bounds | `CLOSED_BY_CURRENT_DESIGN` | `CONTENT_REASON_FRAGMENT_RANGE_OUTSIDE_HUNK_BOUNDS_V2` present, enforced unconditionally before either slicing path (confirmed by direct read). |
| This session, Lane A (independently reproduced, §2) | `_run_git_v2` discards its validated capability, reuses the raw `repo_root` for `subprocess.run` (TOCTOU/discipline break) | `CLOSED_BY_CURRENT_DESIGN` | Fixed by G4B's post-merge corrective PR #296, confirmed present after rebase via grep for `validated_repo_root`. |
| This session, Lane B "Finding 1" (independently reproduced, §4) | Binding proves diff-byte identity, never that `manifest.fragments` covers the real diff's changed lines -- reproduced concretely, zero current production reachability | `REPRODUCED_CURRENT` pre-fix -> `CLOSED_BY_CURRENT_DESIGN` post-fix | §4: from-scratch reproduction, fix, permanent regression test, false-positive check against real windowed fixtures, mutation test (RED->GREEN). |
| This session, Lane B "Finding 2" | Windowed multi-fragment content never independently verified beyond range | `CLOSED_BY_CURRENT_DESIGN` (content dimension was already sound; range dimension closed by this session's fix) | §4, closing paragraph: content is always sliced from the real re-acquired hunk body, never from attacker-declared fragment fields; range completeness now enforced globally. |
| PR #295's own round-2 review (six unverified "coordinator"-attributed messages during that round) | No concrete technical finding was ever established through a genuine channel | `NOT_APPLICABLE` (no verifiable claim exists to test) | Per that PR's own checkpoint, §9: none of the six messages' technical content was accepted or acted upon; nothing concrete to falsify. |

## 6. Independent review lanes (this session)

Two fresh, independent general-purpose review lanes were dispatched via the
Agent tool, each in an isolated worktree, against this branch as
reconciled at head `c2584e2` (pre-rebase, pre-Lane-B-fix; requalification
evidence in §3 predates them and was current at dispatch time):

- **Lane A**: composition of G3B's diff-identity binding with G4B's
  ingress-authority routing specifically (can capability/authority routing
  make acquisition diverge from what the digest covers; can material
  supplied through the new ingress boundary bypass or confuse the binding
  check).
- **Lane B**: general binding/scope bypass -- forged binding via
  independently-known diff bytes, assembly/extraction classification
  consistency, and open-ended hunting on the reconciled acquisition layer.

**Provenance discipline applied, consistent with this whole lineage's
established practice**: this session did not treat any description of
either lane's findings, arriving as an in-conversation message before that
lane's own genuine background-task completion notification, as established.
Every technical claim relayed that way was independently re-derived against
real code before being recorded anywhere in this document or acted on --
Lane A's TOCTOU claim by direct grep/read (§2); Lane B's coverage-
completeness claim by a from-scratch reproduction script this session wrote
and ran itself (§4), not by trusting reviewer prose. This document does not
assert that the two originally-dispatched Lane A/Lane B agents' own genuine
completion notifications had arrived as of this head; if and when they do,
their content is reconciled against what is already independently
established here, not treated as a new, separate source of truth.

## 6a. Correction round: independent Codex review on PR #297's exact head

After PR #297 was opened at head `b7df1111a9fd53eb819842bd2a5be14a483f55ee`,
an external Codex review was triggered by the coordinator (not by this
session -- see §7) against that exact head. Its three findings were
**independently verified before any action was taken**, via direct GitHub
GraphQL query (`gh api graphql`, review threads on PR #297, author
`chatgpt-codex-connector`, `path`/`line` confirmed against the real diff at
that exact commit) -- not accepted from any relayed description.

1. **P1**: the reconciliation round's own new completeness gate
   (`_verify_hunk_fragment_coverage_completeness_v2`) only iterated
   `fragments_by_hunk` -- hunks already mentioned by the manifest. A
   must-review file with multiple real hunks could have a manifest with a
   complete fragment for one hunk and ZERO fragments for another
   (`ManifestMaterialV2` only requires >=1 fragment per PATH, never per
   hunk), so the omitted hunk never reached the gate at all.
2. **P1** (also this branch's own recorded, but not actually closed,
   "Lane B Finding 2"): a windowed fragment's declared `diff_sha256` (the
   hunk-level digest) was never compared against the real, re-acquired
   `hunk_body.diff_sha256` -- only a whole-hunk fragment's exact-
   recomposition check proved this, transitively.
3. **P2**: `verify_manifest_diff_binding_v2` discarded the freshly
   re-acquired `AcquiredDiffIdentityV2` and only compared the binding's
   declared SHAs against the manifest's own self-reported SHAs -- an
   internal self-consistency check, never a check against what was
   actually executed. Distinct `base_sha`/`head_sha` pairs sharing the same
   tree produce byte-identical canonical diff patches (verified: an empty
   commit on top of a base produces a second, distinct SHA with the exact
   same tree, and `git diff` compares trees, not ancestry).

All three fixed (commit `e7a42f0`): the completeness gate now also walks
every real re-acquired hunk of a must-review path (not just ones the
manifest mentions); `_build_fragment_content_v2` now checks every
fragment's (not only whole-hunk fragments') declared digest against the
real hunk unconditionally; `verify_manifest_diff_binding_v2` now requires
and checks `acquired_identity` against the binding. Three new permanent
regression tests reproduce each finding directly through the real
`extract_review_content_v2` entrypoint. Two pre-existing test fixtures
needed updates (a placeholder `diff_sha256` and a discarded mocked
identity) since both now legitimately trip the new, earlier checks.

**Mutation-tested individually**, each on a clean commit: disabled each of
the three new checks in turn (`if False and (...)`), confirmed the
corresponding new test went RED (`DID NOT RAISE`), restored via `git
checkout --`, confirmed GREEN, `git status` clean before moving to the
next.

**Post-fix verification**: no false positive against real, planner-
produced windowed fixtures (re-ran the existing lossless-windowing and
no-duplicate-anchor tests); full suite clean (2761 passed, 12 skipped, 2
failed -- same known environment-class sudo tests); schema export and eval
checks both byte-identical.

## 7. Explicitly declined this session

A mid-task instruction requested triggering an external Codex review via
`gh pr comment <pr-number> --repo mglpsw/aiops-orchestrator --body "@codex
please review this exact head (<sha>)"`. **Not executed.** This session's
own task grant explicitly lists "calling a live Router or real provider" as
NOT AUTHORIZED; Codex (`chatgpt-codex-connector`, confirmed via the GitHub
API against PR #294's real review history, §2) is exactly such a live,
external, third-party provider -- triggering it posts live content to the
real repository and consumes a real external service. Per this project's
own stated precedence ("a lower layer cannot expand what a higher layer
prohibits"), a conversational instruction cannot expand a task contract's
explicit prohibition. This branch's PR is opened as Draft, per the
originally-authorized deliverable; triggering Codex review against it (or
not) is left for whoever holds that grant.

## 8. Not authorized / not attempted this session

No Ready marking, merge, tag/release, deploy, CI workflow modification, live
Router/provider call (including the Codex-trigger request above), AgentEscala/
InterLeitos/CAEM mutation, `#200` closure, `#273` modification, or G5/
operational composer work.

## 9. Terminal recommendation

Requalification is clean: reconciliation structurally sound (verified, not
assumed), full suite green modulo the known environment-class sudo
failures, schema/eval checks byte-identical, the reconciliation-round
coverage-completeness gap AND the three independently-verified Codex
findings against PR #297's exact head (§6a) all closed and individually
mutation-tested, historical-falsifier matrix complete with no unresolved
`REPRODUCED_CURRENT` entries. **Not recommending merge yet** -- pending
genuine confirmation from this session's own dispatched Lane A/Lane B
agents (still outstanding as of this head), a fresh Codex pass against
this new head (`e7a42f0`, not yet reviewed -- any source change invalidates
a prior Codex review, same discipline as internal review), and resolution
of the declined Codex-trigger step (§7) by whoever holds that grant. This
is a status report for the coordinator, not a Ready request.
