# AgentReview v2 — `#200-G3` scope-completeness checkpoint

**Corte temporal:** 2026-09-01, pós-rodada de correção. **Classe:** estado
observado nesta slice; revalidar branch/PR/head antes de qualquer ação
subsequente.

## Identidade viva

```yaml
repository: mglpsw/aiops-orchestrator
branch: feat/200-g3-scope-completeness-contract
head_sha: 1bc7e4bed26501a0de30b1d06ccd208d9a76941e   # post-correction-round head
prior_head_sha: eec5c1c0bdfb2e80f511bbef6cf70afc3840a694  # initial head, REFUTED by round-1 review
base_sha: f70af2e635643d1ee96ba431857002ae079b502b   # live master at slice start
pr: https://github.com/mglpsw/aiops-orchestrator/pull/285
pr_state: draft
issue: https://github.com/mglpsw/aiops-orchestrator/issues/281  (#200-G3)
parent_issue: "#200"
```

## Mission and terminal states

Resolve `STOP_SCOPE_CONTRACT_REQUIRED` left by `#277`'s spike
(`docs/adr/ADR-200F-SCOPE-COMPLETENESS-CONTRACT.md`): represent, honestly
and additively, the distinction `FragmentCoverage != ScopeCompleteness` in
the published `agent-review.review-readiness.v2` contract. Terminal states
per the grant: `PRIMITIVE_NON_REFUTED` | `STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING`
| `STOP_NEW_EXTERNAL_DEPENDENCY`.

## Round 1: initial implementation, REFUTED

Head `eec5c1c0bdfb2e80f511bbef6cf70afc3840a694`. Built `ScopeCompletenessV2`,
`operational_scope_v2` (disposition classification, `assess_changed_scope_v2`,
the disagreement detector), wired `SCOPE_INCOMPLETE` into `readiness_
decision_v2`/`contracts_v2.evaluate_ready_preconditions_v2`, regenerated
the schema, wrote an ADR and two new test files. Dispatched two independent
adversarial review passes via the Agent tool (worktree-isolated).

**Disposition: REFUTED at the decisive point.** Lane A found (P0,
independently reproduced by this agent before any fix): the entire scope-
completeness machinery had zero call sites outside its own new test files.
The real production entrypoint, `review_transport_v2.run_synthetic_
review_v2`, called `compute_readiness_decision_v2` with no `scope=` kwarg
and had no parameter through which a caller could even supply one — so
`#277`'s false-READY hazard remained fully reproducible, unchanged,
through the actual shipped pipeline. This slice's own headline test
(`test_the_false_ready_path_stays_closed_end_to_end`) had called the
library functions manually rather than driving the real entrypoint, so it
did not prove what its docstring claimed. Lane B returned `NON_REFUTED` on
the specific question "can you construct `ready` with scope incomplete"
(the gate held wherever scope was actually threaded through), but found
two real P1 gaps and one P2 fuzz-matrix gap — see below.

## Round 2: correction, current head

Head `1bc7e4bed26501a0de30b1d06ccd208d9a76941e`. Per the grant's "one
bounded correction round" — every finding independently reproduced first,
then fixed, per commit `1bc7e4b`'s own message:

| Finding | Lane | Severity | Fix |
|---|---|---|---|
| Scope machinery never wired into the real entrypoint | A | P0 | `run_synthetic_review_v2` gained optional, additive `file_diffs`/`profile` params (required together); when supplied, computes a real scope assessment from the same material used to build `manifest`, runs the disagreement detector against a real `manifest.expected_files`, threads the result into `compute_readiness_decision_v2`. New test `test_run_synthetic_review_gates_on_scope_completeness_through_the_real_entrypoint` drives this through the actual entrypoint. |
| `ScopeCompletenessV2.complete=True` constructible with nonempty `must_review_blocked_paths` | B | P1 | `complete` now requires BOTH `unsupported_paths` and `must_review_blocked_paths` empty — matches the `ChunkCoverageV2.status is COMPLETE` precedent. |
| `--decision` CLI `scope` key uncross-checked outside the `ready` branch | B | P1 | `ReviewReadinessV2.validate_state_invariants` now cross-checks `scope.complete` against `reason_codes` unconditionally for every non-`STALE` state. New regression tests in `test_contracts_v2.py`. |
| `_is_type_change_pair_v2` `==`→`<=` mutation survived | B | P2 | Regression tests added for two-same-change-type-blocks. |

All four fixes independently mutation-tested against the `1bc7e4b`
baseline (commit before mutating, flip RED, restore GREEN via `git
checkout 1bc7e4b -- <file>`):
1. `run_synthetic_review_v2` forced to always pass `scope=None` → the new
   real-entrypoint test failed on the `SCOPE_INCOMPLETE in reason_codes`
   assertion.
2. Tightened `complete` invariant reverted to the old (looser) form → 3
   tests RED, including the direct regression test for the exact
   dishonest combination Lane B found.
3. New cross-state `reason_codes` check disabled → the new CLI-shaped
   regression test failed to raise as expected.
(Round 1's original five mutations were re-confirmed against this head
too; not re-tabulated here — see the round-1 section of this document's
git history / the ADR for their detail.)

Full `tests/agent_review/` suite at head `1bc7e4b`: see "Verification
performed" below.

## What changed (implementation, both rounds combined)

| File | Change |
|---|---|
| `app/agent_review/diff_acquisition_v2.py` | Extracted `path_violates_relative_path_contract_v2` as a public, single-source-of-truth predicate. |
| `app/agent_review/operational_scope_v2.py` | NEW. `PathDispositionV2` (9 structural members), `classify_changed_path_v2`, `assess_changed_scope_v2`, `ScopeAssessmentV2` (+ `to_scope_completeness_v2`, corrected in round 2 to fold `blocked` into `complete`), `assert_scope_authority_agrees_with_assembly_v2`, `_is_type_change_pair_v2`. |
| `app/agent_review/contracts_v2.py` | NEW `ScopeCompletenessV2` (additive, published; `complete` invariant tightened in round 2). NEW `ReadinessReasonV2.SCOPE_INCOMPLETE`. NEW optional `ReviewReadinessV2.scope` field. `evaluate_ready_preconditions_v2` gains a `scope` parameter and gate. `validate_state_invariants` gains an unconditional (round 2) cross-check of `scope.complete` against `reason_codes` for every non-`STALE` state. |
| `app/agent_review/readiness_decision_v2.py` | `compute_readiness_decision_v2` gains an optional `scope` parameter, folded into the coverage-needs-attention precedence tier. New standalone `fragment_coverage_scope_and_checks_are_ready_v2` predicate. |
| `app/agent_review/review_readiness_emission_v2.py` | Threads `decision.scope` into the sole `ReviewReadinessV2(...)` construction site. |
| `app/agent_review/review_transport_v2.py` | **Round 2.** `run_synthetic_review_v2` gains optional `file_diffs`/`profile` params; wires the real scope assessment + disagreement detector into the real entrypoint. |
| `scripts/aiops-review-quality-gate-v2.py` | `_load_decision` parses an optional `scope` key from the `--decision` JSON file. |
| `schemas/agent-review/v2/agent-review.review-readiness.v2.schema.json` | Regenerated. Purely additive diff (round 1 only; round 2 touched no field shapes, only validators, which do not affect the exported JSON Schema — confirmed via `--check`). |
| `docs/adr/ADR-200G3-SCOPE-COMPLETENESS-CONTRACT.md` | Full rationale, rejected alternatives, the `RelativePath`-vs-`SafeText` prototype finding, and (round 2) the correction-round record with both lanes' findings and fixes. |
| `tests/agent_review/test_operational_scope_v2.py` | Disposition classification (384-case fuzz), disagreement detector, real-git fuzz (6 scenarios). Round 2: added same-change-type-pair regression tests. |
| `tests/agent_review/test_scope_completeness_contract_v2.py` | Contract invariants, ready-gating, terminal predicate, decision-wiring integration, full-pipeline false-READY reproduction (library-level). Round 2: updated two tests for the tightened `complete` invariant, added a direct regression test for the old dishonest combination. |
| `tests/agent_review/test_contracts_v2.py` | Round 2: two new tests for the cross-state `scope`/`reason_codes` invariant (reject when absent, accept when represented). |
| `tests/agent_review/test_review_transport_v2.py` | Round 2: NEW `test_run_synthetic_review_gates_on_scope_completeness_through_the_real_entrypoint` — the real-entrypoint false-READY reproduction Lane A's finding demanded. |

## Contract-work verdict

Three additive routes were evaluated with executable prototypes; a fourth
(sidecar) was also built and rejected. **Chosen: route (c), an additive
optional field on the existing `ReviewReadinessV2` contract.** Full
comparison, including the real defect the prototype found (`RelativePath`
rejecting the exact witness path this contract exists to report), is in
the ADR. A minimal, honest additive representation DOES exist and was
shipped — this is not a `STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING` outcome on
the contract-design question specifically (see below for the overall
verdict, which also depends on round-2 review).

## Verification performed

- Full `tests/agent_review/` suite at round-1 head `eec5c1c0bd`: 2794
  passed, 48 failed (all independently reproduced environment-class:
  `target_repo_write_blocked` from running inside a git worktree, 2 known
  `sudo`-denial), 28 skipped.
- Full `tests/agent_review/` suite at round-2 head `1bc7e4bed2`: re-run
  after the correction; see the live run for this exact head's numbers
  (targeted files — `test_operational_scope_v2.py`,
  `test_scope_completeness_contract_v2.py`, `test_contracts_v2.py`,
  `test_review_transport_v2.py`, `test_required_check_readiness_arch_v2.py`
  — all green, 543 passed + 16 skipped across the scope-specific files
  alone; full-suite run recorded separately).
- `scripts/export-agent-review-v2-schemas.py --check`: clean at both
  heads.
- Mutation testing: 5 propositions in round 1, 3 more in round 2, every
  one confirmed to flip the relevant test(s) RED under mutation and
  restore GREEN.
- Independent adversarial review: round 1, two passes, REFUTED (P0 + 2×P1
  + 1×P2, all fixed in round 2, all fixes independently reproduced by this
  agent before patching). Round 2: two FRESH passes dispatched against
  head `1bc7e4bed2`, per the grant's requirement that source-changed
  review invalidates prior review — disposition recorded below once
  returned.

## Round 2 independent adversarial review disposition

Both lanes dispatched against head `65ba571e1dd510c9c040350465ac75c9057fd9bf`,
independently, worktree-isolated, with at least one lane explicitly briefed
to re-attack "construct a `ready`/non-`SCOPE_INCOMPLETE`-flagged artifact
through the REAL entrypoint while a changed path goes unaccounted for."

**Both lanes independently converged on the same root cause: `REFUTED`.**

- **Lane A, P0.** The round-1 fix (`file_diffs`/`profile` wired into
  `run_synthetic_review_v2`) closes the exact round-1 witness, but
  `assert_scope_authority_agrees_with_assembly_v2` only compares
  `assessment.reviewable_paths` against `manifest.expected_files` -- both
  DERIVED sets. It is blind by construction to a `file_diffs` that
  silently OMITS a changed path's block entirely (as opposed to
  misclassifying a path still present in the list). Repro: build
  `manifest` from the full diff (correctly excluding the `#277` witness
  `src/pages/[id].tsx`), then call the scope assessment with a TRUNCATED
  `file_diffs` that drops that path's block entirely --
  `assessment_truncated.reviewable_paths` is IDENTICAL to the full-diff
  case (`{"app.py"}` either way), so the detector does not raise, and the
  resulting `ScopeCompletenessV2.complete` is `True` with the witness path
  absent from every field, `SCOPE_INCOMPLETE` never reachable.
  **Independently reproduced by this agent** (see below) through the
  exact call shape `run_synthetic_review_v2`'s own docstring claims closes
  the gap.
- **Lane B, same defect, independently found, framed as a confused
  deputy.** The detector never checks that `file_diffs`/`profile` are
  actually the SAME material `manifest` was built from -- only that the
  assessment and the assembly agree given WHATEVER `file_diffs` happens to
  be passed. Repro: build `manifest` from diff A (containing both
  `app.py` and the witness path, correctly excluding the latter), then
  assess scope against a DIFFERENT diff B containing only `app.py` -- no
  disagreement raised, `complete` ends up `True`, silently certifying
  complete scope while diff A's real unrepresentable path was never
  accounted for. Lane B additionally confirmed via mutation: deleting the
  `assert_scope_authority_agrees_with_assembly_v2` call from
  `run_synthetic_review_v2` entirely produces **zero test failures**
  across the full scope-related test suite -- the check has no test
  proving it is load-bearing through the real entrypoint, only through
  direct unit tests of the function itself. This directly falsifies this
  ADR's own claim that the detector is "checked as a runtime invariant
  every run, not merely proven once by a fuzz corpus."

**Independently reproduced by this agent before accepting either finding**
(per this slice's own process discipline): both the truncated-`file_diffs`
scenario (confirmed: `assert_scope_authority_agrees_with_assembly_v2` does
not raise; `ScopeCompletenessV2.complete` is `True` with the witness path
completely absent from `changed_paths`) and Lane B's mutation (confirmed:
removing the detector call from `run_synthetic_review_v2` produces
identical test results -- 301 passed, 16 skipped, no change -- across
`test_review_transport_v2.py`, `test_operational_scope_v2.py`,
`test_scope_completeness_contract_v2.py`).

Both lanes independently confirmed the round-1 `ScopeCompletenessV2.
complete` self-consistency fix and the cross-state `reason_codes` check
hold under fresh adversarial construction and mutation -- that piece is
genuinely `NON_REFUTED` and salvageable (see below).

## Terminal verdict: `STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING`

The scope-completeness ANTI-RECURRENCE WIRING -- the specific abstraction
that the round-1 review already refuted once (unwired entirely) -- was
independently refuted a SECOND time after the one bounded correction round
this grant allows, by two separate review lanes converging on the same
systemic hole: the disagreement detector verifies INTERNAL consistency
between two values both derived from whatever `file_diffs` a caller
happens to supply, never the AUTHENTICITY of that `file_diffs` against the
`manifest` it is supposed to correspond to. Per the grant's own rule ("If
the SAME abstraction is independently refuted a SECOND time: `STOP_G3_
SCOPE_CONTRACT_NOT_CONVERGING`... do not attempt a third fix"), this
primitive stops here. No further code changes were made after this
verdict was reached.

### What is salvageable (port-with-revalidation candidates for whoever
### picks this up next)

- **`ScopeCompletenessV2`'s published contract shape and its
  self-consistency invariants.** Both lanes, across two independent
  rounds, confirm `NON_REFUTED` on: the `complete` flag's tightened
  invariant (requires both `unsupported_paths` and `must_review_blocked_
  paths` empty), the partition/disjointness checks, and the cross-state
  `reason_codes` cross-check in `ReviewReadinessV2.validate_state_
  invariants`. The WIRE SHAPE is sound; only the mechanism that PRODUCES a
  trustworthy `ScopeCompletenessV2` value in the first place is refuted.
- **`path_violates_relative_path_contract_v2`, the shared representability
  predicate.** Unaffected by this verdict -- it is a pure function over a
  single path string, has no dependency on the file_diffs/manifest binding
  problem, and both review rounds left it unchallenged.
- **The disagreement-detector CONCEPT** (composer-level refusal on
  scope-authority/assembly divergence) -- not its current implementation.
  A redesign needs to bind `file_diffs` to `manifest` BY IDENTITY (e.g. a
  content hash of the diff bytes/paths actually used, checked against a
  hash recorded at manifest-assembly time) before comparing DERIVED sets,
  so that supplying inconsistent or truncated `file_diffs` fails closed
  rather than silently agreeing. This is a real, structural fix, but it is
  a NEW design element (an identity-binding mechanism that does not exist
  today anywhere in this codebase's scope-completeness code), not a
  bounded correction -- explicitly out of scope for further work under
  this exhausted grant.
- **Git type-change pairing and the 9-way `PathDispositionV2`
  classification work** (`operational_scope_v2.classify_changed_path_v2`,
  `_is_type_change_pair_v2`). Unaffected by this verdict -- the defect is
  entirely about BINDING `file_diffs` to `manifest`, not about how
  individual paths are classified once a trustworthy `file_diffs` is in
  hand. The 384-case combinatorial fuzz and the real-git fuzz corpus
  remain valid revalidation evidence for this specific piece.

### Recommendation for the next attempt (not authorized here)

Do not reuse this branch/worktree. Start fresh from live `master`, per
this effort's own standing convention (`docs/checkpoints/
AGENT_REVIEW_V2_POST_200F_RECOVERY.md`: "No primitive branch reuses
[prior] branches or worktrees. Each starts fresh from live `master`.").
Carry forward the port ledger above with `PORT_WITH_REVALIDATION` for the
contract shape/predicate/classification work, and `DO_NOT_PORT (authority)`
for the current `assert_scope_authority_agrees_with_assembly_v2`
implementation specifically -- its CONCEPT survives, its mechanism does
not. The next attempt's central design question is exactly: how does a
scope authority prove, structurally, that the `file_diffs` it assessed is
the SAME `file_diffs` (not a superset, not a subset, not a different diff
entirely) that produced the `manifest`/`ChunkCoverageV2` the readiness
artifact's `coverage` field already carries? A content-hash binding
(compute a hash over the exact `file_diffs` at manifest-assembly time,
record it in `ManifestV2` or an adjacent identity object, and require the
scope authority to be constructed only from a `file_diffs` proven to hash
identically) is the leading candidate, matching this codebase's existing
"identity, not merely value equality" discipline elsewhere (e.g.
`RunIdentityV2`/`compute_run_id`, `ReadinessDecisionV2`'s own `run_id`/
`manifest_hash` replay-protection precedent).

## Known limitations (stated plainly, not papered over)

- **`run_synthetic_review_v2`'s scope parameters remain opt-in.** No
  production composer in this repository calls it WITH `file_diffs`/
  `profile` supplied — per the recovery checkpoint, there is no
  operational composer/product CLI on `master` at all (`#200-G5`'s job).
  The gap Lane A found (nothing wired) is closed for any caller that
  supplies both; it is not closed automatically, and nothing in the type
  system forces a future production caller to remember to supply them.
- **`scope=None` remains a real, honest gap for `compute_readiness_
  decision_v2`'s other ~50 pre-existing test call sites and any future
  caller of that function directly** (not through `run_synthetic_
  review_v2`) that forgets to pass a real assessment. `None` is never
  treated as "complete" anywhere in the gating logic (verified). The
  standalone `fragment_coverage_scope_and_checks_are_ready_v2` predicate
  requires `scope` as non-optional for exactly this reason, for callers
  who want the stronger guarantee.
- **Fuzz corpus scale** remains smaller than `#277`'s claimed corpus (see
  ADR "Deferred, honestly"). Not closed in round 2 either — out of scope
  for a bounded correction round.
- **`#232`** and every other item in `docs/engineering/CURRENT_CHECKPOINT.md`'s
  open-issue ledger are unaffected by and unrelated to this slice.

## Grants consumed / not consumed

- Implementation, testing, ADR authorship, schema export, branch push,
  Draft PR creation, correction round: consumed, within this slice's own
  grant.
- NOT consumed / NOT attempted, per explicit prohibition: marking Ready,
  merging, tagging/releasing, deploying, modifying CI workflow files,
  calling a live Router or real LLM provider, mutating AgentEscala/
  InterLeitos/CAEM repos, closing `#200`, modifying `#273`.

## Next minimum action

None, under this grant. Terminal verdict reached:
`STOP_G3_SCOPE_CONTRACT_NOT_CONVERGING` (see above). This primitive's work
under this grant ends here -- no third correction attempted, no further
code changes made after the verdict, PR left in Draft, no merge. The next
minimum action belongs to whoever picks up the successor slice: start
fresh from live `master` (not this branch/worktree), re-derive the
scope-authority/manifest identity-binding design named above as the
central open question, and re-attempt with a NEW grant -- this one is
exhausted.
