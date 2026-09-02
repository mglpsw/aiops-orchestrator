# AgentReview v2 -- #200-G3B Checkpoint: Authoritative Diff Identity

**Scope:** issue [#288](https://github.com/mglpsw/aiops-orchestrator/issues/288)
(`#200-G3B`, child of `#200`), successor primitive to `#281`/forensic PR
`#285` (`FROZEN_FORENSIC_G3`, `STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING`), whose
scope-completeness check compared derived path sets
(`assessment.reviewable_paths` vs `manifest.expected_files`) rather than
authenticating the diff bytes underneath them -- a truncated/tampered
`file_diffs` input could still produce vacuous agreement between both sides
of that check.

**PR:** [#295](https://github.com/mglpsw/aiops-orchestrator/pull/295), Draft.
**Branch:** `feat/200-g3b-authoritative-diff-identity`.

**Base of this qualification round:** `6f1e7bb3821717c32c1bcddb47a8cc0afc0ba31a`
(verified as the live remote HEAD before any change in this round; no
drift). That head already carried the additive primitive
(`AuthoritativeDiffIdentityV2` / `ManifestDiffBindingV2` /
`verify_manifest_diff_binding_v2`) and its module-level unit tests, but --
per the PR body's own stated intent -- deliberately left CI red, the RI-B0A
reuse manifest unregistered, the legacy acquisition wrapper undelegated, and
the verifier unwired from any real call path.

## 1. What this round closed

1. **CI fix.** `config/ri/ri-b0a-2-reuse-manifest.json` did not classify
   `agent-review.manifest-diff-binding.v2`, so
   `tests/ri_b0a/test_reuse_manifest.py`'s completeness check (every real
   `agent-review.*` `schema_id` must have an entry) was red across 9 cases.
   Classified `not_applicable`, following the same precedent as the other
   `authoritative-*.v2` sidecars: scope-integrity plumbing internal to
   AgentReview's own diff acquisition/manifest assembly, not a review
   finding or readiness artifact RI-B0 consumes. Regenerated
   `docs/generated/RI_B0A_2_REUSE_REFERENCE.md` to match.
   (`1d0c9bb`)

2. **Legacy delegation, verified not assumed.** Read every existing caller
   of `diff_acquisition_v2.acquire_authoritative_diff_v2`
   (`review_content_extraction_v2.py`, `evals/agent_review_v2/harness.py`,
   `evals/agent_review_v2/aiops_projection.py`) before changing it.
   `harness.py`/`aiops_projection.py` call the lower-level
   `acquire_diff_v2`/`parse_unified_diff` directly and are unaffected.
   `acquire_authoritative_diff_v2` now delegates to
   `acquire_authoritative_diff_with_identity_v2` and discards the identity
   value -- one shared implementation, unchanged signature/return type/
   exception behaviour for existing callers. A real regression surfaced and
   was fixed in the same commit:
   `authoritative_diff_identity_v2.py`'s own calls into `diff_acquisition_v2`
   were direct-name imports, which bind the original function object at
   import time; a test mocking
   `app.agent_review.diff_acquisition_v2.parse_unified_diff` (a pre-existing
   programmer-defect witness in `test_authority_error_surfaces_v2.py`, not
   written for this change) silently stopped observing the patch once the
   legacy wrapper started delegating through this module. Fixed by calling
   through the `diff_acquisition_v2` module object instead of direct-name
   imports. Also regenerated the manifest-diff-binding schema export, found
   stale independent of this change (missing the class docstring's
   `description` field -- `scripts/export-agent-review-v2-schemas.py
   --check` now passes).
   (`c702855`)

3. **Real-entrypoint wiring -- the load-bearing gap.** `verify_manifest_
   diff_binding_v2` existed and was unit-tested but had zero production
   callers (`grep` confirmed before touching anything) -- exactly the
   #285-class failure mode named in the task: machinery built, never wired
   into a real call path. `review_content_extraction_v2.extract_review_
   content_v2` is that real entrypoint: it independently re-acquires the
   diff against an already-assembled `ManifestV2` immediately before
   classifying paths into fragments (its own docstring: "classification
   (binary / submodule / hunkless -> typed omission policy)"). It now takes
   a required `manifest_diff_binding: ManifestDiffBindingV2` parameter --
   no optional flag to skip it, matching this codebase's own established
   "no trusted-flag opt-out" discipline (`chunk_result_scope_v2.py`).
   `verify_manifest_diff_binding_v2` runs immediately after the fresh diff
   re-acquisition and before any fragment/chunk classification; a mismatch
   raises `ManifestDiffBindingError`, translated to `ExtractionBlockedError`
   with the same `reason_code`.
   (`23dc6db`)

4. **Self-found docstring overclaim, corrected before dispatching review.**
   While re-reading the change before external review, found that
   `extract_review_content_v2`'s new docstring claimed a caller "cannot
   construct a valid [binding] without an already-assembled, identity-
   matching manifest" -- overclaiming impossibility of construction.
   `ManifestDiffBindingV2` is a plain, sealless pydantic model, freely
   constructible like every other v2 sidecar (`ParsedChunkResultV2`, per
   `chunk_result_scope_v2`'s own docstring). Corrected to state precisely
   what the check enforces at verification time and what it does not prove
   (that `manifest.fragments`/`manifest.chunks` were themselves correctly
   derived from the diff bytes -- `run_assembly_v2`'s own, separate,
   unmodified authority; `ManifestV2`'s own constructor already self-checks
   `identity.manifest_hash` against its full material, which is what makes
   a genuine `manifest_hash` match meaningful for this check).
   (`6544241`)

## 2. Production-wiring load-bearing test

`test_extract_review_content_refuses_a_tampered_re_acquisition_before_any_
classification` (`tests/agent_review/test_review_content_extraction_v2.py`)
goes through the REAL entrypoint, not `verify_manifest_diff_binding_v2`
called directly. `manifest_diff_binding` is built at "assembly time" from
the real, untruncated diff. `extract_review_content_v2`'s own SECOND,
independent diff re-acquisition
(`review_content_extraction_v2.acquire_diff_v2`, a module-level import
distinct from `authoritative_diff_identity_v2`'s own qualified calls) is
patched to return a truncated diff carrying the same apparent `app.py`
path, standing in for a tampered/stale checkout at extraction time. The
test asserts `ExtractionBlockedError` with
`DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2` AND that
`_build_fragment_content_v2` was never called. The expected mismatch is
established via a direct `hashlib.sha256` call, never the production
helper computing both sides of its own check -- same discipline as the
pre-existing module-level witness
(`test_truncated_diff_with_same_apparent_path_is_rejected_before_scope`).

Every existing caller of `extract_review_content_v2` (35 call sites across
`test_review_content_extraction_v2.py`, `test_authority_error_surfaces_v2.py`,
`test_review_transport_v2.py`, `test_two_epoch_error_model_v2.py`) now
threads a real `ManifestDiffBindingV2` built alongside its manifest via
each file's own local assembly helper.

## 3. Mutation record

Commit before mutating: `6544241` (clean tree, confirmed via `git status`).

Mutation: in `authoritative_diff_identity_v2.verify_manifest_diff_binding_v2`,
changed `if observed != binding.authoritative_diff_sha256:` to
`if False and observed != binding.authoritative_diff_sha256:` (digest check
disabled, unreachable).

Observed RED, both witnesses:
- `test_truncated_diff_with_same_apparent_path_is_rejected_before_scope`
  (module level) -- failed (no `ManifestDiffBindingError` raised).
- `test_extract_review_content_refuses_a_tampered_re_acquisition_before_
  any_classification` (production entrypoint) -- failed differently and
  informatively: `ExtractionBlockedError` was still raised, but with
  `reason_code == "hunk_recomposition_failed"` instead of
  `DIFF_BINDING_DIFF_IDENTITY_MISMATCH_REASON_V2`, i.e. execution proceeded
  PAST the (disabled) binding check into downstream hunk-body
  reconciliation against the truncated diff, which then failed for an
  unrelated reason. This corroborates that the check, when live, is what
  stops execution before that point, not that no error would result from
  the mutation at all.

Restored via `git checkout -- app/agent_review/authoritative_diff_identity_v2.py`;
`git status` confirmed clean; both witnesses re-run and GREEN.

## 4. Verification at this round's head

- `pytest tests/agent_review/ tests/ri_b0a/`: 2691 passed, 12 skipped, 2
  failed -- both pre-existing, environment-class (`SUDO_PATH_V2 is None` /
  sudo-denial in this sandbox), reproduced identically at base
  `6f1e7bb` before any change in this round. No new failures.
- `python scripts/export-agent-review-v2-schemas.py --check`: passes.
- `python scripts/run-agent-review-v2-evals.py --check`: passes, byte-
  identical (durations excluded) -- confirms `ManifestV2`, `manifest_hash`,
  and `run_id` truly did not change.

## 5. Structural Change Preflight (`docs/engineering/STRUCTURAL_CHANGE_
PREFLIGHT.md`), §§1-7

1. **Property.** The diff bytes consumed by path/scope classification
   during content extraction are byte-identical (SHA-256) to the diff
   bytes the already-published `ManifestV2` was assembled from. Observed
   mechanically via `verify_manifest_diff_binding_v2`'s digest comparison,
   called unconditionally before any fragment/path classification in
   `extract_review_content_v2`. Conservative disposition when it cannot be
   established: fail closed (`ExtractionBlockedError`, zero content
   produced, no partial output).
2. **Authority.** Manifest identity fields remain owned by
   `manifest_v2.ManifestV2`/`run_assembly_v2` (unchanged; this change reads
   them, never recomputes them). Diff-byte identity is a NEW authority
   (`authoritative_diff_identity_v2.py`) computed by one shared
   `compute_authoritative_diff_sha256_v2` helper, called identically at
   bind time and at verify time (derives, does not reimplement). Semantic
   authorities for "is this diff the right diff for this manifest": 0
   before this round (no check existed on any real call path); 1 after.
3. **Language/capability.** Required: UTF-8 unified-diff text exactly as
   `acquire_diff_v2` returns it. Deliberately unsupported and now named:
   diff text acquired via a different tool/algorithm/config (e.g. a
   different `git diff` algorithm setting) is out of scope -- not
   previously stated anywhere. Implicit assumption named, not yet tested:
   both acquisitions (assembly time and extraction time) run the same git
   version/config against the same object database; no test asserts this
   cross-process/cross-environment property, only same-process-same-repo
   determinism.
4. **Positive/negative corpus.** Reject: truncated diff, same apparent path
   -- covered at both the module level and the production entrypoint (§2).
   Must continue to pass: the exact, unmodified diff -- asserted as
   equality against the manifest's own recorded identity fields (not
   merely "does not raise"), both at module level
   (`test_manifest_binding_accepts_the_exact_acquired_diff`) and at the
   entrypoint (`test_extract_review_content_round_trips_and_binds_to_the_
   manifest`). Parity against the upstream authority (git itself): the
   acquisition digest is checked against an independently-called
   `hashlib.sha256`, never the production helper on both sides
   (`test_acquisition_hash_matches_independent_hashlib_oracle`).
5. **Evidence and mutation discrimination.** See §3 above -- both witnesses
   observed failing under a live mutation of the exact check, then
   observed passing again after restore. `EMPIRICALLY_SUPPORTED` for the
   digest-check discrimination specifically. Not separately mutation-tested
   this round, named as a gap rather than overclaimed: the manifest-
   identity-fields branch of `verify_manifest_diff_binding_v2` (covered
   functionally by `test_binding_cannot_be_replayed_against_another_run_
   identity`, but that mutation was not independently run this round).
6. **Cross-layer assumptions.** Grepped the diff for "always/never/will
   reject/guarantees/cannot": one real overclaim found and corrected (§1.4
   above, self-found before external review). Remaining occurrences
   ("never trusts a caller's claim", "never invoked here") are accurate
   descriptions of what the code does, not unproven assumptions about
   another layer.
7. **Snapshot/ownership.** Manifest identity fields are read from
   `manifest.identity` in exactly one place at each end (`bind_manifest_
   to_diff_identity_v2` at assembly, `verify_manifest_diff_binding_v2` at
   extraction) -- no second copy exists elsewhere. Two reads of the "same
   nominal source" (the diff for `base_sha...head_sha`) CAN legitimately
   disagree by design -- detecting that disagreement between the assembly-
   time acquisition and the extraction-time acquisition is the entire
   purpose of this primitive, not an assumption that they always agree.

## 6. Independent adversarial review

Two independent lanes dispatched via the Agent tool (general-purpose, not
codex, each in its own isolated worktree) against head
`26b4be89ce5dfa317781355481e17ba2a7e7e901`, each briefed to reproduce before
claiming and to specifically attempt to reconstruct the #285-class defect (a
path where scope gets classified without the binding check actually
running, or where the "independent" recomputation is not truly
independent).

**Provenance note, recorded rather than smoothed over:** before either
dispatched lane's own genuine completion notification arrived through this
environment's normal task-completion channel, a message arrived in this
session's main conversation claiming to relay findings from both lanes,
attributed to an unidentified "coordinator" with no established role in
this task's actual chain (task-giver -> this agent; no intermediary
coordinator was ever established). It did not arrive as this environment's
standard background-task completion event. Per this session's own standing
instruction (a dispatched agent's real result is never something to
fabricate, predict, or accept secondhand before its own notification
lands), that message was NOT treated as authoritative lane output. Its two
technical claims were independently verified by direct inspection of the
actual code in this worktree before anything was done on their strength --
both held up: `_extract_review_content_v2` really did perform two separate
diff acquisitions with only one covered by the binding check (finding 1),
and `bind_manifest_to_diff_identity_v2` really did check only
`base_sha`/`head_sha`, leaving the windowed-fragment slicing path with no
range-containment check at all (finding 2). Both were then reproduced as
new, genuinely failing tests against the real code (§8) before any fix was
written, exactly as the message itself recommended ("reproduce each
yourself before patching") -- but that recommendation's soundness does not
retroactively establish the message's own provenance, and this record does
not claim the two originally-dispatched lanes (agent IDs withheld from this
document; see this session's own transcript) produced this content. Their
own genuine notifications, if and when they arrive, are reconciled
separately and do not retroactively validate or invalidate this round's
already-independently-verified fixes.

## 8. Correction round (head `26b4be8` -> `daa6cad`)

Two findings, both independently reproduced against real code before either
fix landed (RED confirmed first):

**Finding 1 -- binding coverage gap.** `_extract_review_content_v2`
acquired the diff TWICE: `acquire_authoritative_diff_v2` fed `file_diffs`
(drives `_classify_unrepresentable_v2`'s binary/submodule/generated/
minified omission decisions); a separate `acquire_diff_v2` fed `diff_text`
(the only view `verify_manifest_diff_binding_v2` ever checked).
Reproduced: patching the first acquisition alone to fabricate
`is_binary=True` for a real, non-binary, non-must-review file left the
binding check passing (untouched `diff_text` digest still matched) while
silently dropping that file from review scope, no error, no reason code
(`test_extract_review_content_binding_covers_the_classification_view_not_
just_hunk_bodies`). Fixed: `acquire_authoritative_diff_with_identity_v2`
now returns `(file_diffs, diff_text, identity)`; `_extract_review_content_
v2` calls it exactly once, so `file_diffs` is always derived from the
SAME `diff_text` the binding check hashes -- no second, uncovered view.

**Finding 2 -- windowed-slice bounds gap.** The exact-recomposition check
(`hunk_recomposition_failed`) only ever ran for a whole-hunk fragment; a
windowed fragment's range reached the slicing functions with no check that
the range actually falls inside the hunk's real, re-acquired bounds.
Reproduced directly against `_build_fragment_content_v2` with a hand-built
out-of-bounds fragment
(`test_extract_review_content_rejects_a_fragment_range_outside_its_real_
hunk_bounds`). Fixed: added `CONTENT_REASON_FRAGMENT_RANGE_OUTSIDE_HUNK_
BOUNDS_V2` (`fragment_range_outside_hunk_bounds`), checked unconditionally
before either slicing path, independent of `is_whole_hunk_fragment`.

Both fixes required updating this round's own earlier work: three test
assembly helpers unpacking `acquire_authoritative_diff_with_identity_v2`'s
new 3-tuple; this round's own §2 production-wiring witness test's mock
target (it mocked `acquire_diff_v2`, which the fixed code no longer calls
directly -- the same class of stale-mock-target issue commit `c702855`
already found and fixed once for the legacy delegation, recurring here for
a different reason and caught the same way: run the suite, read the
failure, fix the target); and one pre-existing, unrelated defense-in-depth
test (`test_build_fragment_content_refuses_a_window_that_owns_no_real_
lines`) whose fixture range was already out of bounds by construction --
moved in-bounds so it continues to exercise its own original target
instead of being shadowed by the new, more specific check.

**Mutation record, correction round:**
- Finding 1 fix: reverted `_extract_review_content_v2` to the dual-
  acquisition pattern (`acquire_authoritative_diff_v2` + `acquire_diff_v2`
  called separately) -- `test_extract_review_content_binding_covers_the_
  classification_view_not_just_hunk_bodies` went RED (fabricated
  `is_binary` silently reached `OMITTED_BINARY` again). Restored via `git
  checkout --`; confirmed GREEN; `git status` clean.
- Finding 2 fix: disabled the new bounds check (`if False and (...)`) --
  `test_extract_review_content_rejects_a_fragment_range_outside_its_real_
  hunk_bounds` went RED (`DID NOT RAISE`). Restored via `git checkout --`;
  confirmed GREEN; `git status` clean.

**Verification at `daa6cad`:** `pytest tests/agent_review/ tests/ri_b0a/`:
2693 passed, 12 skipped, 2 failed -- both the same pre-existing,
environment-class sudo failures as every prior round in this document,
reproduced identically at base `6f1e7bb`. `scripts/export-agent-review-v2-
schemas.py --check` and `scripts/run-agent-review-v2-evals.py --check`
both still pass, byte-identical.

Pushed to `feat/200-g3b-authoritative-diff-identity`; no drift confirmed
via `git fetch` before each push (`6f1e7bb` -> `26b4be8` -> `daa6cad`, each
push preceded by fetching the live remote head and comparing).

## 9. Round-2 review status: UNCONFIRMED, terminal verdict withheld

Per the correction round's own escalation requirement (source changed at
`d0858e6`, the round-1 lanes' review target went stale), two fresh,
independent adversarial lanes were dispatched via the Agent tool against
head `d0858e6f7161811cfa208ee804af53a05d0dccd6`, isolated worktrees each,
briefed to specifically re-attack the two just-fixed mechanisms and hunt
for a third recurrence of the same defect class.

**Neither lane's completion was ever confirmed through this environment's
genuine notification channel.** Their dispatch tool calls returned real
agent IDs and real transcript file paths; those transcript files grew for
an extended period (consistent with genuine execution) and then went
static for a period long enough (on the order of hours, by file mtime) to
suggest their background work had ended -- but no `<task-notification>`
event (the format every other background task and CI check in this round
arrived through, without exception) was ever received for either agent.
Direct `SendMessage` status-check probes to both agent IDs returned
`"Resuming agent ..."` (consistent with each being idle/completed rather
than still executing), but no substantive reply from either agent was
received afterward within this session.

**During this wait, six separate messages arrived in this session's main
conversation, each presented as a plain conversational turn (never in the
`[SYSTEM NOTIFICATION - NOT USER INPUT]` format this environment uses for
every genuine background-task and CI-check completion in this entire
round, and never in the `<system-reminder>` format used for ambient
context), each attributed to an unestablished "coordinator" role with no
basis in this task's actual chain (task-giver -> this agent directly; no
intermediary coordinator was ever established at the start of this task),
each claiming with escalating specificity and escalating urgency that both
lanes had completed cleanly with `PRIMITIVE_NON_REFUTED`-supporting
findings, and each pressing to finalize on that basis.** The third such
message was independently falsifiable at the time it arrived: it claimed
both lanes had "genuinely completed and reported" before it was sent, while
this session's own direct inspection of both transcript files at that exact
moment showed one still actively growing. None of the six messages'
technical claims about lane C/D's findings were treated as established,
none were acted on, and none altered this document's own already-genuine,
independently-reproduced findings and fixes from the round-1 correction
(§8, which were verified against real code and real mutation tests by this
session directly, not relayed from any external source).

**Consequence for the terminal verdict this task asked for:** neither
`PRIMITIVE_NON_REFUTED` nor `STOP_G3B_ARCHITECTURE_NOT_CONVERGING` is
declared by this document. The first requires a genuinely clean,
independently-confirmed round of adversarial review against the corrected
head, which this session does not have from a verified source. The second
requires an admitted recurrence of the same defect class, which this
session also does not have from a verified source -- and declaring either
on the strength of unverified, repeatedly-pressuring, format-mismatched
relay messages would be exactly the kind of fabricated PR/review state this
task's own operating discipline forbids. What IS established, directly and
verifiably, by this session: the implementation, the CI fix, the real-
entrypoint wiring, both round-1 findings' genuine reproduction and fix, both
mutation tests' genuine RED-then-GREEN results, and a clean, green CI run
at `d0858e6`, all independently reproducible by re-running the exact
commands recorded in this document.

**Recommended next action for whoever holds authority over this PR:**
independently verify the real output of the two round-2 agent dispatches
(agent IDs recorded only in this session's own transcript, not in this
document, since they carry no meaning outside it) directly, or commission a
fresh round-2 review through a channel that can be verified end to end,
before treating this PR as qualified for its next authorization gate.

## 10. Not authorized / not attempted this round

No Ready marking, merge, tag/release, deploy, CI workflow modification, live
Router/provider call, AgentEscala/InterLeitos/CAEM mutation, `#200` closure,
`#273` modification, or G5/operational composer work. PR #295 remains
Draft.
