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

**Self-found P1, fixed before external review (commit `548a9f0`):** while
re-reading the implementation before dispatching adversarial review,
`verify_executed_source_identity_v2`'s per-entry comparison loop was found
to join `subject_root / entry.path` directly, without the containment
discipline the *ported* materialisation code
(`git_commit_subject_v2._safe_destination_v2`) already applies. Proven
exploitable, not theoretical: `git mktree` accepts a subtree literally
named `..`, and `git ls-tree -r` on such a tree flattens it into an entry
path like `../evil.py`; demonstrated end-to-end that, unfixed, a
maliciously crafted commit could make `verify_executed_source_identity_v2`
read a file *outside* `subject_root` and report success. Fixed with a
lexical (`posixpath.normpath`-based, not `Path.resolve()`-based --
`resolve()` would dereference an already-materialised symlink entry and
answer a different question) containment check, `_safe_subject_path_v2`,
mutation-tested in isolation to confirm a clean (non-collateral) kill. Full
detail in §4. Recorded here, not hidden in the diff, because the process
contract requires reproducing and disclosing findings rather than folding
them silently into "the implementation" as if they had never been wrong.

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
   corpus for this primitive (after the path-traversal fix in `548a9f0`
   added one more test): **26 tests** in
   `test_commit_derived_execution_identity_v2.py`, **16 new** tests for the
   two ported building blocks (`test_bounded_git_v2.py`,
   `test_git_commit_subject_v2.py`) -- **42 total, all green**.
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
| 12 | Disable `_safe_subject_path_v2`'s containment check (path-traversal fix, commit `548a9f0`) | `commit_derived_execution_identity_v2.py` | 1 failed with "DID NOT RAISE" against a test where a file matching the malicious blob's content was pre-planted at the escape target | **real, clean kill** -- confirms the check is load-bearing and the earlier collateral-kill pattern (mutations 4, and originally this one before the test was strengthened) does not apply here |

12/12 mutations killed. No equivalent or redundant-control survivors in the
strict sense; mutation 3 is a *deliberately* non-single-point-of-failure
check (kept even though one specific attack shape no longer needs it, per
the design rationale in §1), and mutation 4's kill is collateral rather than
a clean isolation of the intended check -- both are called out above rather
than reported as clean kills they were not. Mutation 12 documents a
self-found P1 (path-traversal via a git tree entry literally named `..`)
that was fixed and proven exploitable end-to-end *before* dispatching
adversarial review -- see the note at the top of this document.

### Full adjacent regression suite

Run at commit `985c64e` (before the path-traversal fix; re-run not repeated
after `548a9f0` since that commit only touches this primitive's own two test
files, already green): `python -m pytest tests/agent_review/ -q` ->
**2600 passed, 48 failed, 12 skipped** (277s). The post-`#200-F` recovery
checkpoint recorded **2559 passed, 48 failed, 12 skipped** at `f70af2e6`
(live master, before this branch). `2559 + 41 (this slice's new tests at
that commit) = 2600` exactly. The 48 failures are byte-for-byte the same
named tests as the recovery checkpoint's own list (`test_isolated_
executor_v2.py::test_execute_denies_sudo_inside_the_isolated_check`,
`::test_sudo_path_resolves_to_an_absolute_path_via_a_fixed_search_list`,
plus the `target_repo_write_blocked`-class failures across `test_agent_
review_e2e_contract.py` / `test_aiops_review_build_payloads_cli.py` /
`test_aiops_review_false_positives_cli.py` / `test_aiops_review_telemetry_
cli.py`) -- environment-class (sudo-denial, worktree-write-blocked from
running inside a `git worktree add` checkout), not product regressions, and
not this slice's to fix per the recovery checkpoint's own disposition.
**No new failures introduced by this slice.**

## 5. Review rounds and findings

### Round 1 -- dispatched against head `848bc185cc899bdbc6556e708fb31e0a914971c1`

Two independent lanes, both via the Agent tool (general-purpose subagents,
`isolation: worktree`), told to reproduce before claiming, attempt
fabrication/forgery specifically against the commit->bytes binding, separate
PROVED from SUSPECTED, and not trust this document's claims.

**Lane A -- P0, decisive.** The commit->bytes binding was refuted by a
THIRD mechanism, distinct from both original `#277` falsifier classes
(which lane A confirmed still held). `verify_executed_source_identity_v2`'s
own "no extra file" scan used `Path.rglob("*")`, which does not descend
into a symlinked directory (reports the symlink entry, stops), while the
per-tracked-path comparison used ordinary path joining, which the OS
resolves by transparently following a symlinked directory in an
intermediate component. Exploit: materialise honestly, replace a tracked
directory with a symlink to an attacker directory containing a
byte-identical file (satisfies the tracked-file check) plus an extra
untracked file -> `verify_executed_source_identity_v2` returns SUCCESS
while the extra file sits reachable under `subject_root`, never compared
against git. Lane A also flagged the existing check 5
(`identity_loaded_code_outside_subject`) as only a partial mitigation,
since it reads live `sys.modules` by default and is therefore only useful
if the file happens to already be imported by the time verification runs --
not a "verify before executing" gate.

Confirmed-safe per lane A (attacked and could not break): CLI/argv
injection via `commit_sha`/`ref`, the `..`-traversal fix, gitlink-present
refusal, blob-missing refusal, full ambient-environment poisoning
(`PATH`/`GIT_DIR`/`GIT_SSH_COMMAND`/`LD_PRELOAD`/`GIT_ALTERNATE_OBJECT_
DIRECTORIES`, confirmed via a sentinel-writing fake `git`), and IDENTITY/
AUTHORIZATION non-conflation (a real unmerged-branch repo, identity
succeeds independently of authorization).

**Lane B -- did not refute the commit->bytes binding.** Attacked it with
two new git-plumbing constructions this document's corpus did not cover (a
blob/subtree name-collision tree, and a direct write-time repro of the
`..`-traversal case against materialisation rather than only verification)
and it held both times. Found two lower-severity, real P1s instead:

1. `materialise_commit_subject_v2` crashed with a raw, untyped
   `FileExistsError` (not `SubjectMaterialisationError`) on a hostile tree
   where a blob and a tree share the exact same one-byte name -- git's own
   tree-sort comparator treats a directory entry as if it had a trailing
   `/`, so this is not a duplicate name to git, and `git mktree` accepts
   it. Left a partial write on disk. In this primitive's own declared
   `hostile_target_checkout` threat scope.
2. `ExecutedSourceAuthorizationV2` had no `__bool__`, so `bool(instance)`
   was always `True` regardless of `.authorized` -- not live today (zero
   call sites outside this module/its tests) but a footgun for whatever
   wires this primitive in next.

### Independent reproduction (this document's author, before patching)

Per the process contract, every finding above was personally reproduced
against the real functions before any fix was written -- not accepted from
reviewer prose:

- Lane A P0: reproduced live in a throwaway script
  (`/tmp/g1_repro_script.py` during the session; not committed, scratch
  only) against the actual `verify_executed_source_identity_v2` --
  confirmed `VERIFY RESULT: SUCCESS` with `evil.py` reachable under
  `subject_root`, exactly as reported.
- Lane B P1 #1: reproduced with real `git mktree --missing` plumbing
  (`/tmp/g1_repro_collision.py`) -- confirmed a raw `FileExistsError`
  (not the typed error) and a leftover `collide` file on disk.
- Lane B P1 #2: confirmed `bool(ExecutedSourceAuthorizationV2(..., authorized=False))`
  evaluates to `True` with no `__bool__` defined.

### Correction round (one bounded round, per process step 9)

Commits `334bf51` (RED: three regression tests, confirmed failing against
pre-correction code) and `e4f3c65` (GREEN: fixes). Summary of fixes -- full
rationale in each function's docstring and the commit message:

1. New `_reachable_leaf_paths_v2`: one traversal policy
   (`os.walk(followlinks=False)`), refuses on any symlinked directory
   found anywhere under `subject_root`, and its leaf-path set is reused by
   both the completeness scan and (implicitly, by having already run) made
   safe the per-tracked-path comparison's plain path joins. Re-reproduced
   the exact lane A exploit script against the fixed code: now returns
   `identity_symlinked_directory_in_subject` instead of false success.
2. `materialise_commit_subject_v2` catches the blob/tree name-collision
   (and any `SubjectMaterialisationError` raised mid-write, e.g. the
   existing path-escape check) and cleans up the destination before
   raising a typed `subject_path_collision` error. Re-reproduced lane B's
   script against the fixed code: now raises the typed error and leaves no
   partial write.
3. `ExecutedSourceAuthorizationV2.__bool__` added, tracking `.authorized`.

Mutation-tested all three new/changed checks in isolation (commit before
mutating, mutate, run tests + the standalone repro scripts, confirm both go
red for the right reason, restore, confirm green): mutation 13 (disable the
symlinked-directory raise) reproduced the exact lane A false-success again
via both the test and the standalone script; mutation 14 (force `__bool__`
to always return `True`) killed cleanly by the new test; mutation 15
(prevent the collision `except` clause from matching) reproduced the exact
raw `FileExistsError` from before the fix. 15/15 mutations killed overall
across this primitive (12 from the original round, 3 from this correction).

Full corpus after correction: 45 tests -- 28 in
`test_commit_derived_execution_identity_v2.py` (26 + 2 new), 9 in
`test_git_commit_subject_v2.py` (8 + 1 new), 8 unchanged in
`test_bounded_git_v2.py` -- all green.

### Round 2 -- pending

To be dispatched against the corrected, re-frozen head (see identity block
at the top of this document once updated) per process step 9: two fresh
independent lanes, prior review invalidated by the source change, at least
one lane specifically re-attacking path-containment via symlink
substitution (nested symlinks, symlinks-to-symlinks, symlinked files as
well as directories, case variations) since path-containment is the
abstraction whose second independent refutation would trigger
`STOP_G1_ARCHITECTURE_NOT_CONVERGING`.

## 6. Terminal verdict

_To be filled in once round 2 is complete._
