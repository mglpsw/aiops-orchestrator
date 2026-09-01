# AgentReview v2 -- #200-G1 Checkpoint: Executed Source Identity

**Scope:** issue [#279](https://github.com/mglpsw/aiops-orchestrator/issues/279)
(`#200-G1`, child of `#200`), successor primitive to `#277`'s falsified
`operational_inner_control_v2.py` (Authority B). This is a single primitive
slice per the post-`#200-F` recovery decomposition
(`docs/checkpoints/AGENT_REVIEW_V2_POST_200F_RECOVERY.md`); it does not
attempt G2--G4 or the G5 recomposition.

**Branch:** `feat/200-g1-executed-source-identity`, from live `master` at
`f70af2e635643d1ee96ba431857002ae079b502b` (verified as HEAD before any
change; no drift).

## 1. Design rationale (written before implementation, per process step 2)

### Why the round-1 narrow-root attack closes

`#277` round 1 was forgeable because the *declared subject root* and the
*digested content* were the same untrusted document's two fields, checked
only for mutual consistency ("does this root's hash match this claimed
hash") and for containing the entry script. A caller could narrow the root
to a subdirectory that genuinely contains the entry script while excluding
the real semantic package, and every check in that design would still pass.

This design closes it structurally rather than by adding one more check to
the same shape of design. `verify_executed_source_identity_v2` requires
**full-tree presence**: every non-gitlink path in the resolved commit's
`git ls-tree -r` output must exist under `subject_root` with matching
content, mode, and (for symlinks) target text -- not just the paths a caller
chose to populate. There is no API for materialising a subset of a commit's
tree (`materialise_commit_subject_v2` always writes the whole tree), so a
"narrowed root" is not a smaller *valid* subject with an honest digest; it
is an *incomplete* subject, and incompleteness is refused directly
(`identity_missing_tracked_file`) independent of what got loaded. The loaded-
module check from `#277`'s round-1 fix is kept as an explicit second,
independent signal (`identity_loaded_code_outside_subject`) precisely
because "every tracked path is present" and "every loaded module lives
under the root" are different properties -- the first can hold by
construction while the second would still catch a *different* narrowing
attack against a future design that relaxes full-tree-presence. Neither
check is asked to cover for the other.

### Why the round-2 fabrication attack closes

`#277` round 2 worked because `declared_toolrepo_sha` was validated only as
a 40-hex-character *shape*, and `declared_toolrepo_sha`/`subject_digest`
were both supplied by the same party being verified. An attacker could
tamper code inside a correctly-declared root, recompute the digest honestly
with the module's own public helper over the *tampered* tree, and declare
the real HEAD sha -- nothing in that design ever asked git what the
declared sha's tree actually contains, so an internally-consistent but
factually-false document sailed through.

This design has no field in the trust path that is ever "declared" and then
merely shape- or self-consistency-checked. `verify_executed_source_identity_
v2` takes a commit sha and, on every single call, independently re-derives
that commit's tree straight from `repo_root`'s own object store via
`git ls-tree -r` + `git cat-file --batch` (`list_commit_tree_entries_v2` /
`read_commit_blobs_v2`), then compares that freshly-read content
byte-for-byte against what is actually on disk at `subject_root`. There is
no digest to fabricate, because no pre-computed digest is ever part of the
comparison -- the "expected" side of every comparison is produced by asking
git, fresh, not by trusting a value the party under scrutiny handed over.
Tampering the materialised tree after the fact (this test's TOCTOU-shaped
scenario) or fabricating a document before the fact are the same failure
mode from this design's point of view: on-disk content that disagrees with
git's own answer for that path, caught as `identity_content_mismatch`.

### IDENTITY vs. AUTHORIZATION, kept apart

Per the mission brief, these are two distinct, uncombined outputs:

- `ExecutedSourceIdentityV2` / `verify_executed_source_identity_v2` --
  **IDENTITY**: which commit produced the bytes now on disk. A fact
  derivable entirely from `repo_root`'s own git object store plus what is
  actually materialised. Says nothing about whether that commit was
  *supposed* to run.
- `ExecutedSourceAuthorizationV2` / `authorize_commit_for_execution_v2` --
  **AUTHORIZATION**: whether an already-identified commit is permitted for
  this invocation, via `git merge-base --is-ancestor <commit> <trusted-ref>`.
  Meaningless applied to an sha that was never independently verified as a
  real commit, and does not imply identity: a commit can be a perfectly
  legitimate, unauthorized feature-branch tip.

No function in this module ANDs these into a single boolean. A caller that
wants one accept/refuse decision composes both results itself; that
composition -- and the two-process wiring that would actually spawn an
inner epoch against a verified subject -- is explicitly out of scope for
this primitive (`PORT_AS_CONCEPT`, deferred to G5 recomposition per the
port ledger).

## 2. Port ledger for this slice

| Artifact | New home | Origin | Disposition |
|---|---|---|---|
| Bounded git child environment (allowlist env, `os.defpath` resolution, `-c` config neutralisation, `--no-replace-objects`) | `app/agent_review/bounded_git_v2.py` | `operational_bounded_git_v2.py`, commit `5703e5b` on frozen-forensic `feat/200-f-derivable-operational-boundary` | PORT_WITH_REVALIDATION -- new tests below, no qualification transfer, dropped dependency on the un-ported `ExpectedOperationalRefusalV2` family (kept self-contained: plain `ValueError` + `reason_code`) |
| `ls-tree -r` + `cat-file --batch` commit materialisation (not `git archive`) | `app/agent_review/git_commit_subject_v2.py` (`materialise_commit_subject_v2`, `list_commit_tree_entries_v2`, `read_commit_blobs_v2`) | `operational_subject_v2.py`, same commit | PORT_WITH_REVALIDATION -- same reasoning as upstream (`.gitattributes` export-ignore/export-subst structurally unreachable), new tests, generalised to a single `resolve_commit_v2` + tree-entry API instead of two near-duplicate target/toolrepo dataclasses |
| Content-addressed digest (`compute_subject_digest_v2`, symlinks hashed as target text) | `app/agent_review/git_commit_subject_v2.py` | `operational_inner_control_v2.py` (the digest function itself, not the channel it was embedded in) | PORT_WITH_REVALIDATION -- kept, but its docstring now states explicitly that a value from this function must never be compared against an externally-supplied claim as a substitute for re-deriving expected content from git; only ever compared against another self-produced value (e.g. before/after a materialisation step) |
| `operational_inner_control_v2.py` exclusive outer/inner channel and `declared_toolrepo_sha` shape-only validation | -- | `#277` | **DO_NOT_PORT (authority)** -- not reused in any form; this slice does not build a two-process channel at all, since the two-process wiring is where the falsified trust direction lived |

## 3. Threat scope (stated, not implied)

| Actor / condition | Status | How this primitive treats it |
|---|---|---|
| `host_arbitrary_code_attacker` | **out of scope** | Not defended. Someone who can already run arbitrary code with this process's privileges does not need to forge an identity check. |
| `hostile_target_checkout` | in scope | `bounded_git_v2` neutralises hooks/filters/protocols/fsmonitor at the git-config level; `git_commit_subject_v2` reads via `ls-tree`+`cat-file`, never `archive`, so `.gitattributes` (export-ignore/export-subst) is structurally unreachable. |
| `hostile_environment` | in scope | Child environment is built from nothing (allowlist), never `dict(os.environ)` with deletions; `git` is resolved against `os.defpath`, never the caller's `PATH`. |
| `ordinary_caller_forgery` | in scope | This is the exact class both `#277` falsifiers belong to. Closed by full-tree presence + fresh git-derived content comparison (see §1); no caller-supplied digest or sha is ever trusted past `resolve_commit_v2`'s own re-verification against the repo's object store. |
| `mutable_dev_checkout must_not_define_executed_identity` | in scope, proven | `resolve_commit_v2`/`list_commit_tree_entries_v2`/`read_commit_blobs_v2` read exclusively from git's object database; uncommitted worktree edits, `assume-unchanged`, `skip-worktree` are index/worktree-only state that these calls never consult (see negative corpus below). |

## 4. Test and mutation record

### Process followed

1. **RED** (commit `64fd3d3`): the two `#277` falsifiers written against a
   stub (`NotImplementedError` for both `verify_executed_source_identity_v2`
   and `authorize_commit_for_execution_v2`). Ran before any real logic
   existed; both failed with `NotImplementedError`, confirmed not vacuous.
2. **GREEN** (commit `985c64e`): real implementation. Both falsifiers pass
   without weakening either assertion, plus the full required negative
   corpus, a happy path, and IDENTITY/AUTHORIZATION separation tests. Full
   corpus for this primitive: **25 tests** in
   `test_commit_derived_execution_identity_v2.py`, **16 new** tests for the
   two ported building blocks (`test_bounded_git_v2.py`,
   `test_git_commit_subject_v2.py`) -- **41 total, all green**.
   Writing the full corpus (not just the two falsifiers) caught a real bug
   the falsifiers alone missed: `os.readlink`/`os.access` were used without
   `import os`. Both falsifier scenarios happen to raise before reaching
   that code path (round 1 via a missing-tracked-file on the first
   alphabetically-sorted entry, round 2 via content mismatch on the same),
   so the two RED tests alone would have stayed green with a `NameError`
   time bomb in the mode-check/symlink-check paths. The happy-path and
   mode-mismatch tests, added while building out the corpus, are what
   exposed it.
3. **Mutation testing** (commit `985c64e` as the restore point, confirmed
   clean via `git diff --stat` before and after every mutation): each
   mutation was applied on top of the committed GREEN state, the full
   corpus (41 tests) or the targeted subset was run, the result recorded,
   and the file was reverted to the exact committed text before the next
   mutation. One mutation (defensive gitlink `continue`, see below) was
   kept as a genuine fix and committed separately (`ba8daf7`).

### Mutation matrix

| # | Mutated proposition | File | Result | Classification |
|---|---|---|---|---|
| 1 | Invert content-equality check (`==` for `!=`) | `commit_derived_execution_identity_v2.py` | 15/25 tests failed | **real** -- central to closing round 2 |
| 2 | Disable extra-untracked-file check | same | 1 failed (`test_untracked_shadow_file_in_subject_is_refused`) | **real** |
| 3 | Disable `loaded_module_paths` check | same | 1 failed (`test_module_outside_the_executed_closure_is_refused`); round-1 falsifier stayed green under this mutation alone | **real, but not the sole closer of round-1** -- full-tree-presence (mutation 2/target of the missing-tracked-file check) already refuses round-1's narrowed root before this check would ever run; this check is an independent second signal for a *different* narrowing shape (complete subject, code loaded from elsewhere). Documented in §1 as intentional non-single-point-of-failure design. |
| 4 | Disable gitlink-present check | same | 1 failed (`test_gitlink_in_tree_is_refused`), but via the collateral missing-tracked-file check on an *earlier* alphabetically-sorted entry in an unmaterialised subject, not a clean isolation of the gitlink path itself | **real (still fail-closed), test isolation is imperfect** -- led directly to finding a latent `KeyError` risk (gitlink entries are absent from `expected_content_by_path`); fixed defensively in `ba8daf7` rather than left as a note only |
| 5 | Disable mode-mismatch check | same | 1 failed (`test_mode_mismatch_is_refused`) | **real** |
| 6 | Disable symlink-target check | same | 1 failed (`test_tampered_symlink_target_is_refused`) | **real** |
| 7 | Drop `^{commit}` from `resolve_commit_v2`'s `rev-parse` | `git_commit_subject_v2.py` | 4 failed, incl. `test_resolve_commit_refuses_a_tree_sha` | **real** -- this is the check that keeps a tree/blob sha from being accepted as an identity |
| 8 | Remove `--no-replace-objects` | `bounded_git_v2.py` | 1 failed (`test_git_replace_ref_cannot_substitute_a_different_tree`), reproducing the substituted tree's content exactly | **real** |
| 9 | Build child env from `dict(os.environ)` instead of an allowlist | same | 3 failed, incl. the exact-key-set assertion | **real** -- this is the allowlist-vs-blacklist property from the port ledger |
| 10 | Resolve `git` via caller `PATH` instead of `os.defpath` | same | 2 failed, incl. `test_fake_git_earlier_in_path_is_never_executed` | **real** |
| 11 | Swap ancestor/descendant argument order in `authorize_commit_for_execution_v2`'s `merge-base --is-ancestor` | `commit_derived_execution_identity_v2.py` | 2 failed | **real** -- confirms AUTHORIZATION direction is exercised, not just present |

11/11 mutations killed. No equivalent or redundant-control survivors in the
strict sense; mutation 3 is a *deliberately* non-single-point-of-failure
check (kept even though one specific attack shape no longer needs it, per
the design rationale in §1), and mutation 4's kill is collateral rather than
a clean isolation of the intended check -- both are called out above rather
than reported as clean kills they were not.

## 5. Review rounds and findings

_To be filled in after the two independent adversarial review passes
dispatched via the Agent tool complete, against the exact head frozen below._

## 6. Terminal verdict

_To be filled in once review rounds are complete and (if needed) one bounded
correction round has been applied and re-reviewed._
