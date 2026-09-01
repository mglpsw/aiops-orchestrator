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

Two independent lanes dispatched via the Agent tool (general-purpose,
not codex) against head `<to be filled in after dispatch>`, each briefed to
reproduce before claiming and to specifically attempt to reconstruct the
#285-class defect (a path where scope gets classified without the binding
check actually running, or where the "independent" recomputation is not
truly independent).

<Results recorded after both lanes report back.>

## 7. Not authorized / not attempted this round

No Ready marking, merge, tag/release, deploy, CI workflow modification, live
Router/provider call, AgentEscala/InterLeitos/CAEM mutation, `#200` closure,
`#273` modification, or G5/operational composer work. PR #295 remains
Draft.
