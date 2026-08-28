# Checkpoint — `#200-E` controlled subject materialization + executed-source identity

**Status:** Phase 2 in progress. Counterexample ledger, both architecture
spikes (target + toolrepo), and both production subject authorities
(`controlled_subject_v2.py`, `toolrepo_execution_subject_v2.py`) are
complete and qualified. Operational composition, the CLI product path, and
the Router black-box E2E are explicitly **NOT started** — out of scope for
this phase per the grant. Terminal state target: Draft PR, not Ready, not
merged.

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  parent: 200
  roadmap_parent: 80 / 46
  base_sha: f70af2e635643d1ee96ba431857002ae079b502b
  base_tree: 945f3247a9e8ad534a0d35f4450b24446906f30c
  branch: feat/200-e-controlled-subject-materialization
forensic_predecessor:
  pr: 274
  classification: FROZEN_FORENSIC
  state: CLOSED
  merged: false
  head: c37d5b5a3f273dea8e44c60bc3b5a8bb2df13e4b
  qualification_transferred: false
  knowledge_port: required
preflight:
  origin_master_sha_matched: true
  origin_master_tree_matched: true
  issue_200_state: OPEN
  pr_274_state_confirmed: CLOSED, merged=false, head=c37d5b5a..., body markers present
  competing_v2_implementation: none (only #275, explicitly v1 lane, out of scope)
  base_drift: false
```

## Mission

Do not revive `#274`. Do not attempt another round of "seal every Git
behavior inside the target repository" — three rounds of exactly that were
each falsified by the next round finding a sibling target-controlled-
execution vector in the same checkout step (full record:
`#274`'s `docs/checkpoints/AGENT_REVIEW_V2_200D_OPERATIONAL_SUCCESSOR.md`
§ Round 3, and its "Terminal forensic reconciliation").

The authority boundary changes:

```text
OLD (REFUTED):
  target-controlled repository
    -> enumerate/neutralize hooks/config/filters/index/Git behavior
    -> execute semantic Git operations there

NEW:
  untrusted source checkout
    -> object/source input only
    -> reviewer-controlled subject (reviewer-owned config, hooks, index)
    -> semantic diff / blobs / references / content
    -> existing AgentReview v2 pipeline
```

`SourceLocation != SemanticSubjectAuthority`. `DeclaredSHA !=
ExecutedSourceIdentity`. `ObservationThatGitSaysClean != ByteIdentity`.

## Counterexample ledger

Built from the preserved `#274` evidence at `c37d5b5a3f273dea8e44c60bc3b5a8bb2df13e4b`
(`git show c37d5b5:docs/checkpoints/evidence/AGENT_REVIEW_V2_274_ROUND3_ADVERSARIAL/...`),
not from memory. Every witness that mapped to a real, independently
reproduced finding in that corpus is listed. This ledger is the planning
artifact required before any production code exists in this slice; no item
below has yet been tested against `#200-E` code, because none exists yet.
`disposition` therefore records the **plan**, not an achieved result:

- `MUST_START_RED` — the corpus already has (or the architecture spike will
  produce) a harness that reproduces this witness against the OLD/vulnerable
  model; a `#200-E` test using the same harness must fail (RED) before any
  successor mechanism exists, then pass (GREEN) once it does, because the
  authority boundary moved — not because of a new target-specific exception.
- `ALREADY_ELIMINATED_BY_ARCHITECTURE` — reserved for a witness whose
  precondition cannot exist in the new topology at all (there is no target
  object/ref/config path in the semantic operation's dependency graph). Still
  requires a concrete falsifier test per the acceptance rule below; the
  disposition records the topological argument, not a substitute for testing
  it.
- `OUT_OF_SCOPE_WITH_FALSIFIER` — explicitly deferred, with the falsifier
  that justifies deferring it (used here only for the bootstrap-trust
  question in § Bootstrap honesty, which is not one of the 25 witnesses but
  is adjacent to them).

```yaml
COUNTEREXAMPLE_LEDGER:

  - id: CE-01-git-replace-blob-substitution
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      git replace <original> <malicious> makes `git cat-file -p <original>`
      return malicious bytes while `git ls-tree`/`rev-parse` keep reporting
      the original SHA. Closed in #274 round 1 via GIT_NO_REPLACE_OBJECTS=1
      applied to the TARGET's own git process -- a per-process mitigation
      that still shared the target's ref namespace.
    successor_proposition: >-
      The scratch object database is populated by a bounded, explicit object
      import for the declared base_sha/head_sha closure -- it never resolves
      or imports refs/replace/* from the source at all, and the reviewer's
      own scratch git process runs with GIT_NO_REPLACE_OBJECTS=1 as an
      authority-owned value regardless.
    planned_oracle: >-
      Plant a replacement ref in the SOURCE repo mapping the declared
      head_sha's tree/blob to malicious content; materialize the scratch
      subject; assert the scratch diff/blob reads return the ORIGINAL bytes,
      not the replaced ones.
    disposition: MUST_START_RED

  - id: CE-02-ambient-git-env-redirection
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      An ambient GIT_DIR/GIT_OBJECT_DIRECTORY/etc. in the calling process's
      environment silently redirects every git command run in that process,
      independent of cwd/-C. #274 closed this by stripping a fixed list of
      GIT_* names from an inherited os.environ -- a blacklist, falsified in
      round 3 by GIT_CONFIG_PARAMETERS (CE-03) surviving the same list.
    successor_proposition: >-
      The scratch process never inherits the caller's environment. Its child
      environment is constructed from an explicit OS-level allowlist (HOME,
      PATH sufficient to locate the canonical git executable, locale) plus
      authority-owned Git values -- no caller-provided GIT_* variable
      survives unless the new authority explicitly sets it itself.
    planned_oracle: >-
      Set every GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE/GIT_OBJECT_DIRECTORY/
      GIT_ALTERNATE_OBJECT_DIRECTORIES/GIT_COMMON_DIR/GIT_NAMESPACE in the
      calling process's environment before materialization; assert the
      scratch subject's semantic operations are unaffected (the allowlist
      construction means none of these should even reach the child argv/env
      inspection, not merely be neutralized value-by-value).
    disposition: MUST_START_RED

  - id: CE-03-git-config-parameters-leak
    class: CROSS_BOUNDARY
    old_design_falsifier: >-
      GIT_CONFIG_PARAMETERS was absent from #274's env-stripping blacklist
      and is honored by this host's Git; an ambient (not target-local) value
      injects arbitrary config, including an executable filter/hook key,
      into every "sealed" invocation. Independently reproduced in
      #274 round 3 (evidence/.../independent-verification/02).
    successor_proposition: >-
      Same mechanism as CE-02 -- an allowlist child environment has no
      GIT_CONFIG_PARAMETERS entry unless the new authority sets one itself,
      so there is no blacklist to have a gap in.
    planned_oracle: >-
      Set GIT_CONFIG_PARAMETERS in the calling process's environment to
      inject core.hooksPath or a filter definition; assert it has no effect
      on the scratch subject's semantic operations.
    disposition: MUST_START_RED

  - id: CE-04-worktree-gitattributes-mutation
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      An untracked .gitattributes planted in the target's own working tree
      (never committed) changed acquire_diff_v2's output for the identical
      base_sha...head_sha range from text to binary. #274 M4.
    successor_proposition: >-
      Attribute resolution happens inside the scratch checkout at the exact
      declared head_sha, which is checked out FROM the scratch object
      database, never from the target's working tree -- an untracked file in
      the target's working tree is not even read to materialize the scratch
      subject.
    planned_oracle: >-
      Plant an untracked .gitattributes in the target with different
      semantics than the committed one; assert the scratch subject's
      semantic diff reflects only the COMMITTED attributes at head_sha.
    disposition: MUST_START_RED

  - id: CE-05-core-attributesfile-redirect
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      A repository-local core.attributesFile in the target points attribute
      resolution at an arbitrary out-of-tree path; #274's disposable worktree
      (round 1-2) did not cover this at all -- it flipped a text diff to
      binary. Closed in round 2 via `-c core.attributesFile=<devnull>`, an
      override that depended on remembering to add it to every git
      invocation.
    successor_proposition: >-
      The target's repository-local config (including core.attributesFile)
      is never copied or consulted by the scratch subject at all -- there is
      no config-reading step against the target in the materialization path,
      so there is no override to remember.
    planned_oracle: >-
      Set core.attributesFile in the target pointing at a file with
      different rules than the committed .gitattributes; assert scratch
      output matches the committed-attributes-only case (CE-04's oracle).
    disposition: MUST_START_RED

  - id: CE-06-info-attributes-active
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      $GIT_DIR/info/attributes is shared by every worktree of a repository,
      including a disposable one created solely to isolate the working-tree
      .gitattributes vector -- #274 M5, closed by detecting and refusing
      rather than by isolation (no supported Git mechanism excluded it).
    successor_proposition: >-
      The scratch subject has its own git init and its own $GIT_DIR --
      info/attributes there starts empty and reviewer-owned; the target's
      info/attributes is never read to construct it.
    planned_oracle: >-
      Populate the target's $GIT_DIR/info/attributes with an active rule;
      assert the scratch subject's own info/attributes remains empty and
      semantic output is unaffected.
    disposition: MUST_START_RED

  - id: CE-07-info-attributes-nbsp-fifo-witness
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      Lane B, Q4 (#274 round 3): a U+00A0 (NBSP) prefix defeats Python's
      `strip()`-based comment detection in the info/attributes refusal
      helper while Git's own attribute parser treats it as an active rule --
      a corrupted binary patch was returned instead of a refusal. A FIFO at
      the same path was skipped by `Path.is_file()` but read by Git.
    successor_proposition: >-
      Not merely a stricter parser for the SAME detect-and-refuse pattern --
      this witness specifically motivates CE-06's structural fix: an
      always-empty, reviewer-owned info/attributes has no content to
      misparse, and no target-controlled file identity (regular file vs.
      FIFO) to misclassify.
    planned_oracle: >-
      Same fixtures as lane B Q4 (NBSP-prefixed rule, FIFO at info/attributes
      path) run against the scratch subject; assert both have zero semantic
      effect because the scratch info/attributes was never populated from
      the target in the first place, not because a smarter parser classified
      them correctly.
    disposition: MUST_START_RED

  - id: CE-08-hooks-corehookspath
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      git worktree add runs the TARGET's post-checkout hook; a
      repository-local core.hooksPath redirect reaches an arbitrary
      directory the same way. #274 round 2, closed via
      `-c core.hooksPath=<devnull>` on every git invocation in the shared
      runners.
    successor_proposition: >-
      The scratch subject's hooks directory is reviewer-owned (freshly
      initialized, never populated from the target); no checkout of target
      content ever consults a target-controlled hooks path because no
      target-owned .git/hooks or core.hooksPath value is ever read.
    planned_oracle: >-
      Plant a post-checkout hook in the target's .git/hooks AND set a
      repository-local core.hooksPath redirect; assert neither executes
      during materialization (marker-file technique from
      evidence/.../independent-verification/01).
    disposition: MUST_START_RED

  - id: CE-09-filter-clean-toolrepo
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      A repository-local filter.*.clean executes during the bounded-path
      `git diff --name-only HEAD` establish_toolrepo_source_identity_v2
      used as its cleanliness check, and can make materially different
      worktree bytes read as clean against HEAD. Independently reproduced,
      #274 round 3 (evidence/.../independent-verification/03, /04) --
      identity PASSED while the checked file on disk read `# TAMPERED`.
    successor_proposition: >-
      §10's rejected proposition (`git diff HEAD == empty => executed ==
      declared`) is replaced entirely: enumerate the expected executable
      files at the exact declared toolrepo commit tree, read each blob's
      bytes via cat-file, compare directly against the raw filesystem bytes
      actually about to execute. No git-mediated comparison, so no filter
      driver sits between the two byte sources being compared.
    planned_oracle: >-
      Configure filter.*.clean to emit the committed bytes verbatim while
      the actual file on disk is tampered; assert the new toolrepo identity
      authority REFUSES (byte comparison fails) rather than passing.
    disposition: MUST_START_RED

  - id: CE-10-filter-smudge-acquisition
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      A repository-local filter.*.smudge executes during the checkout
      git worktree add performs. #274 round 2 detected and refused this
      (no `-c` closure exists, since the driver name is attacker-chosen);
      round 3 found the detector itself bypassable via includeIf.gitdir
      (CE-13).
    successor_proposition: >-
      Target filter definitions are never copied into the scratch subject's
      config (§8). A committed .gitattributes assigning `filter=<driver>` is
      semantic input (subject bytes), but the DRIVER COMMAND is target-local
      config that is never imported, so there is nothing for Git to execute
      even when the attribute assignment is honored.
    planned_oracle: >-
      Configure filter.*.smudge in the target with a committed
      .gitattributes assigning it; assert materialization completes with the
      marker-file technique showing no execution, and that the committed
      attribute assignment is still visible as semantic metadata (not
      silently dropped).
    disposition: MUST_START_RED

  - id: CE-11-filter-process-toolrepo
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane A (#274 round 3): filter.*.process also executes during toolrepo
      identity observation, same class as CE-09.
    successor_proposition: same as CE-09 -- cat-file-vs-filesystem-bytes
      comparison has no git-mediated step for a process filter to intercept.
    planned_oracle: >-
      Configure filter.*.process instead of .clean with the same tamper
      scenario as CE-09; assert the new authority still refuses.
    disposition: MUST_START_RED

  - id: CE-12-fsmonitor
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      core.fsmonitor holds a command Git executes to enumerate working-tree
      changes; reproduced running during `git status` in the target. #274
      round 2, closed via `-c core.fsmonitor=false`.
    successor_proposition: >-
      Target config, including core.fsmonitor, is never copied into the
      scratch subject (§8); the scratch subject's own fsmonitor is unset by
      default (reviewer-owned, freshly initialized config).
    planned_oracle: >-
      Set core.fsmonitor in the target to an executable marker command;
      assert no semantic operation against the target ever runs `git
      status` or otherwise triggers it, and that materialization does not
      transitively invoke it either.
    disposition: MUST_START_RED

  - id: CE-13-includeif-worktree-bypass
    class: TARGET_ACQUISITION
    old_design_falsifier: >-
      includeIf.gitdir:<repo>/.git/worktrees/** is invisible to #274's
      filter detector before the disposable worktree exists (the pattern
      only matches once a worktree admin dir is present), then activates a
      filter.evil.smudge during the git worktree add it was meant to guard.
      acquire_diff_v2 returned success. Independently reproduced, #274 round
      3, the SHARPEST demonstration that patching this design converges to
      nothing (evidence/.../independent-verification/01) -- it defeats the
      exact detection round 2 added for CE-10.
    successor_proposition: >-
      This witness is the one MOST worth stating precisely why the new
      architecture eliminates the whole CLASS, not just this instance:
      includeIf.gitdir conditions on the PATH of the .git directory doing
      the checkout. The scratch subject's .git directory is a reviewer-
      created path that was never derived from or nested under the target's
      .git at all (no `git worktree add` against the target, ever) -- there
      is no target-owned includeIf condition that can ever match it, by
      construction of the path itself, not by detecting and refusing a
      config key.
    planned_oracle: >-
      Configure includeIf.gitdir:<any pattern, including one matching the
      target's own worktrees admin path> pointing at a file defining a
      filter driver; assert scratch materialization is unaffected because
      the scratch .git path was never derived from the target's.
    disposition: MUST_START_RED

  - id: CE-14-assume-unchanged
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      git update-index --assume-unchanged makes Git itself omit a modified
      tracked file from `git diff --name-only HEAD`, so
      establish_toolrepo_source_identity_v2 PASSED while the file on disk
      was tampered. Independently reproduced through the real function,
      #274 round 3 (evidence/.../independent-verification/04).
    successor_proposition: same as CE-09 -- byte comparison against cat-file
      blobs does not consult the index at all, so assume-unchanged (an
      index flag) has nothing to influence.
    planned_oracle: >-
      Same as CE-09's oracle with assume-unchanged instead of a clean
      filter as the concealment mechanism.
    disposition: MUST_START_RED

  - id: CE-15-skip-worktree
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6 (#274 round 3): git update-index --skip-worktree defeats
      the same cleanliness check via a different index flag, both for a
      modification and a deletion.
    successor_proposition: same as CE-14.
    planned_oracle: >-
      Same as CE-09's oracle with skip-worktree, both for a modify and a
      delete variant.
    disposition: MUST_START_RED

  - id: CE-16-lazy-fetch-promisor-ext
    class: CROSS_BOUNDARY
    old_design_falsifier: >-
      A genuinely missing (not merely --filter-hidden) blob plus a
      repository-local remote.origin.url=ext::<helper> makes the disposable-
      worktree diff trigger Git's lazy object fetch, executing the helper.
      GIT_NO_LAZY_FETCH=1 was absent from #274's sealed environment. A full
      CLI run exited 0 while persisting a NEW PROMISOR PACK into the
      reviewed repository -- this is the #274 P0: execution AND target
      mutation on the declared success path. Independently reproduced,
      #274 round 3 (evidence/.../independent-verification/05); note the
      first reproduction attempt used a `file://`-transport partial clone
      and produced a false negative (no object was actually left missing) --
      the working reproduction deletes a loose object directly.
    successor_proposition: >-
      The scratch object database is populated by an explicit, bounded
      object-closure import BEFORE any semantic operation runs (§6), from a
      source-discovery step that fails closed on any layout whose safe
      semantics aren't established (promisor/partial repositories
      explicitly named in §6 as a fail-closed case). There is no promisor
      remote, no lazy-fetch path, and no `ext::` transport configured in the
      scratch subject at all -- missing objects after import is proven
      complete are a materialization-time refusal (§6: "fail closed, not
      lazy fetch"), never a runtime fetch during a later semantic operation.
    planned_oracle: >-
      Same fixture as evidence/.../05 (loose object deleted, repository-
      local ext:: promisor remote configured) run against source discovery
      and object-closure import; assert either (a) TARGET_OBJECT_ALTERNATES_
      PRESENT / promisor-repository refusal at discovery time with no helper
      execution, or (b) if the object IS present in the source, successful
      import with zero helper invocation and zero write to the target under
      any outcome.
    disposition: MUST_START_RED

  - id: CE-17-target-git-mutation-oracle-blind
    class: TARGET_NONMUTATION
    old_design_falsifier: >-
      test_target_checkout_is_never_mutated and
      test_cli_has_no_filesystem_output_authority relied on `git status
      --porcelain` before/after, which never observes .git contents. A
      scratch-copy mutant writing .git/agent-review-mutant-marker from
      inside prepare_operational_review_v2 passed both tests unmodified.
      Independently reproduced, #274 round 3
      (evidence/.../independent-verification/06); note the first attempt at
      this reproduction used an IN-PLACE mutant and was confounded by
      dirtying the toolrepo's own checkout, tripping an unrelated refusal
      before reaching the oracle question -- the working method mutates a
      disposable scratch CLONE of the toolrepo, never the real checkout.
    successor_proposition: >-
      §9's TARGET_NONMUTATION_INVARIANT: after source discovery/object
      import begins, no semantic operation is permitted to require a
      write-capable Git operation against the target. This is enforced
      structurally (semantic subprocesses use the scratch repo/cwd, never
      the target's), and PROVEN by an oracle that observes the target's
      worktree, .git contents, and ignored/untracked files recursively
      before/after -- not merely `git status --porcelain`.
    planned_oracle: >-
      Port the exact mutant from evidence/.../06 (write
      .git/agent-review-mutant-marker) against the #200-E oracle; it MUST
      fail (the point of the new oracle is to catch what the old one
      missed). Combine with a target made read-only at both worktree and
      .git level -- successful review must still complete.
    disposition: MUST_START_RED

  - id: CE-18-ignored-untracked-executable-source
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      --exclude-standard hid a stray importable app/common/_stray_evil.py
      from the untracked-source check the moment a matching .gitignore line
      existed. #274 round 1 (M6), closed by enumerating all untracked paths
      without --exclude-standard.
    successor_proposition: >-
      If §11's preferred form is adopted (execute directly from the exact
      controlled toolrepo subject materialized from the declared SHA, not
      from an arbitrary development worktree), this class collapses:
      the execution root contains only what is in the declared commit tree
      -- there is no untracked-file universe to enumerate or hide within,
      because nothing untracked is ever copied into the execution subject.
    planned_oracle: >-
      Plant an untracked, .gitignore-hidden importable .py file in the
      TOOLREPO's development checkout used to declare the SHA under test;
      assert it is absent from the materialized toolrepo execution subject
      and cannot be imported by the semantic review process.
    disposition: MUST_START_RED

  - id: CE-19-root-import-shadowing
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6 (#274 round 3): an untracked repository-root pydantic.py
      executed before identity was even established, outside the identity
      authority's declared `app/` + CLI-script bound.
    successor_proposition: same collapse as CE-18, via §13's Python startup
      isolation (isolated mode, PYTHONPATH ignored, user site disabled) on
      top of an execution root containing only declared-commit content.
    planned_oracle: >-
      Same fixture as lane B Q6 (untracked root-level pydantic.py proxy);
      assert it is neither present in the materialized subject nor
      importable given the isolated Python startup configuration.
    disposition: MUST_START_RED

  - id: CE-20-scripts-stdlib-shadowing
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6: an untracked scripts/argparse.py shadowed the stdlib
      module and executed via ordinary Python import resolution before the
      CLI's own argument parsing ran.
    successor_proposition: same as CE-19.
    planned_oracle: >-
      Same fixture with scripts/argparse.py; assert absence from the
      materialized subject and non-importability.
    disposition: MUST_START_RED

  - id: CE-21-pyc-importable-bytecode
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6: an unchecked-hash __pycache__/*.pyc replaced the identity
      checker's own tracked module; PYTHONDONTWRITEBYTECODE=1 does not
      prevent READING existing bytecode.
    successor_proposition: >-
      The materialized toolrepo execution subject is built from exact
      commit blobs (CE-09's mechanism) -- .pyc is never a tracked blob type
      this authority expects, and is not part of the declared commit tree,
      so it is never materialized into the execution subject at all,
      independent of any PYTHONDONTWRITEBYTECODE setting.
    planned_oracle: >-
      Plant __pycache__/*.pyc shadowing a real tracked module in the
      TOOLREPO's development checkout; assert absence from the materialized
      subject and that only the source .py at the declared blob executes.
    disposition: MUST_START_RED

  - id: CE-22-symlinked-import-paths
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6: an untracked symlinked package directory evaded the
      `.py`-suffix filter (Git reports it as a suffixless entry); separately,
      a TRACKED symlink did not bind the executed bytes to the declared
      identity -- changing the external referent changed executed Python
      without changing HEAD, the symlink blob, or git status.
    successor_proposition: >-
      §6 (source discovery) and the toolrepo materialization mechanism must
      make an explicit, positive decision about symlink blobs rather than
      an implicit one: either refuse a declared toolrepo commit containing a
      symlink under the bounded executable-source path, or resolve it AT
      MATERIALIZATION TIME to a concrete blob whose bytes are then compared
      like any other -- never leave it to resolve dynamically against
      whatever the filesystem happens to point at when the process runs.
    planned_oracle: >-
      Both the untracked-symlink-directory and the tracked-symlink-external-
      referent-change fixtures from lane B Q6, run against the new
      materialization; assert either a typed refusal or a byte comparison
      that is provably insensitive to the external referent changing after
      materialization.
    disposition: MUST_START_RED

  - id: CE-23-nested-repo-import-surface
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      Lane B, Q6: an untracked nested Git repository under the bounded
      source path was collapsed by Git to a suffixless directory entry,
      evading the `.py`-suffix filter the same way as CE-22's symlink case.
    successor_proposition: >-
      Same structural fix as CE-18/CE-21: a materialization built from the
      declared commit tree's blobs has no path for an untracked nested
      repository to enter the execution universe, independent of any
      suffix-based filtering.
    planned_oracle: >-
      Plant an untracked nested .git repository with a payload module under
      the bounded toolrepo source path; assert absence from the
      materialized subject.
    disposition: MUST_START_RED

  - id: CE-24-deleted-bounded-source
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      A `.exists()` filesystem prefilter excluded a bounded path DELETED
      from disk (e.g. the CLI script itself) from the pathspec `git diff`
      was even asked about. #274 round 1 (M7/M8), closed by always passing
      the full declared bounded-path set unconditionally and detecting a
      genuinely wrong/empty TOOLREPO_ROOT via `git ls-tree` against HEAD.
    successor_proposition: >-
      The byte-comparison authority (CE-09's mechanism) enumerates the
      EXPECTED files from the declared commit tree first (via ls-tree, not
      via filesystem existence), then reads each one -- a deleted file
      becomes a required-but-missing filesystem read, a refusal by
      construction, not an item that can silently drop out of a pathspec.
    planned_oracle: >-
      Delete a bounded tracked executable source file from the toolrepo
      development checkout used to declare the SHA under test; assert the
      new authority refuses (missing required file), never silently omits
      it from consideration.
    disposition: MUST_START_RED

  - id: CE-25-commit-replacement-toolrepo
    class: TOOLREPO_EXECUTION_IDENTITY
    old_design_falsifier: >-
      The commit-level analogue of CE-01: `git replace` on the toolrepo's
      own declared HEAD commit. #274 round 1 (M2), closed via
      GIT_NO_REPLACE_OBJECTS=1 on the toolrepo's own git process.
    successor_proposition: >-
      Whatever mechanism reads the declared toolrepo commit's tree/blobs
      for CE-09's byte comparison runs with GIT_NO_REPLACE_OBJECTS=1 as an
      authority-owned value (§7), and, if the toolrepo execution subject
      itself is materialized as a bounded object import rather than read
      in place (§11's preferred form), the same argument as CE-01 applies:
      replacement refs are never resolved during import in the first place.
    planned_oracle: >-
      Plant a commit-level replacement for the declared toolrepo HEAD in
      the development checkout; assert the materialized/compared bytes are
      the ORIGINAL commit's, not the replacement's.
    disposition: MUST_START_RED
```

### Ledger totals

```yaml
total: 25
by_class:
  TARGET_ACQUISITION: 10
  TOOLREPO_EXECUTION_IDENTITY: 12
  TARGET_NONMUTATION: 1
  CROSS_BOUNDARY: 2
by_planned_disposition:
  MUST_START_RED: 25
  ALREADY_ELIMINATED_BY_ARCHITECTURE: 0
  OUT_OF_SCOPE_WITH_FALSIFIER: 0
```

No item is marked `ALREADY_ELIMINATED_BY_ARCHITECTURE` at this planning
stage, deliberately: that disposition asserts a topological argument has
been proven, and nothing in `#200-E` has been built or tested yet. CE-13
(`includeIf.gitdir`) and several `TOOLREPO_EXECUTION_IDENTITY` items (CE-18,
CE-19, CE-20, CE-21, CE-23) have unusually strong topological arguments —
their successor propositions above explain why the vulnerable precondition
should not exist at all in the new design, not merely be handled better —
but they stay `MUST_START_RED` until the architecture spike (next section)
and the corresponding falsifier actually prove it, per the acceptance rule.

## Successor acceptance rule

```text
Every material #274 counterexample must either:

A. begin RED against the vulnerable model and become GREEN because the
   authority boundary changed;

or

B. be proven structurally unreachable under the successor with a concrete
   falsifier.

Never: "fixed because we added another target-specific exception."
```

Production implementation does not begin until this ledger exists (it now
does) and the architecture spike below has produced at least one falsified-
or-confirmed result for the `TARGET_SUBJECT_MATERIALIZATION_INVARIANT`.

## Architecture spike — empirical, not assumed

Run interactively before any production module existed, against real
fixture repositories under `mktemp -d`, never against this toolrepo's own
checkout. Every claim below was executed, not reasoned about.

### The tempting shortcut is falsified, per §5's own instruction

`git clone --shared --no-checkout <source> <scratch>` was tried first and
rejected with evidence, not merely distrusted on principle:

```text
$ git clone -q --shared --no-checkout <source> <scratch>
$ cat <scratch>/.git/objects/info/alternates
<source>/.git/objects          # still points at the source object store

$ mv <source> <source>.SEVERED
$ git -C <scratch> cat-file -p <head_sha>:f.txt
error: unable to normalize alternate object path: <source>/.git/objects
fatal: path 'f.txt' does not exist in '<head_sha>'
```

`--shared` is a real, load-bearing dependency on the source's object store,
not a shortcut that happens to look self-contained. This is exactly
`STOP_SUBJECT_NOT_SELF_CONTAINED` reproduced directly, confirming §5's
warning rather than merely restating it.

### The proven mechanism: bounded closure import, not shared/aliased storage

```text
reviewer-owned `git init` (fresh, empty, allowlist child environment)
  -> bounded object enumeration against the source:
       `git rev-list --objects <base_sha> <head_sha>`
       (plumbing read; computes the closure, checks out nothing)
  -> `git pack-objects --stdout` against the source, fed exactly that closure
  -> `git index-pack --stdin` into the scratch object database
  -> refs created in scratch for base/head (objects already local)
  -> scratch has no alternates file, no remote, no shared storage
```

Every step above ran under an explicit allowlist child environment
(`env -i HOME=<scratch-local> PATH=/usr/bin:/bin GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_GLOBAL=/dev/null LC_ALL=C GIT_NO_REPLACE_OBJECTS=1
GIT_NO_LAZY_FETCH=1`) — never the caller's inherited `os.environ`. This is
the §7 allowlist-not-blacklist requirement, and it is what makes the
ambient-injection result below hold without a variable-by-variable
enumeration.

### Decisive results, hostile fixture

Source fixture had ALL of the following planted simultaneously: a
`post-checkout` hook, a `filter.evil.smudge` command (assigned via a
committed `.gitattributes`, so the assignment itself is legitimate subject
metadata), an `includeIf.gitdir:**/.git/worktrees/**.path` pointing at a
second hostile filter definition, a blob-level `git replace` substitution
on the declared head's tracked file, and (in a second run) a genuinely
missing loose object plus a repository-local `remote.origin.url=ext::` promisor
helper.

```yaml
spike_results:
  materialization_hostile_markers_fired: false   # HOOK_RAN, SMUDGE_RAN, INCLUDEIF_RAN all absent throughout
  self_contained_after_source_severed: true       # diff/cat-file/checkout identical after `mv <source> <source>.SEVERED`
  replacement_object_ignored: true                # scratch read the ORIGINAL blob, not the git-replace substitute
  ambient_GIT_DIR_env_ignored: true                # set in the calling shell; zero effect via env -i allowlist
  ambient_GIT_CONFIG_PARAMETERS_ignored: true      # same
  ambient_GIT_OBJECT_DIRECTORY_ignored: true       # same
  missing_object_fails_closed: true                # rev-list --objects rc=128, "fatal: missing blob object ...", GIT_NO_LAZY_FETCH=1 honored
  ext_helper_executed_on_missing_object: false     # zero execution -- the closure step refuses before any pack/import happens, never reaches a later semantic op that could lazy-fetch
  alternates_in_source_detected_pre_closure: true  # trivial check (objects/info/alternates non-empty) maps directly to a TARGET_OBJECT_ALTERNATES_PRESENT refusal per §6
  committed_gitattributes_semantic_effect_preserved: true  # `filter=evil` assignment visible in checkout/diff; the UNDEFINED driver is a silent no-op (Git's own documented behavior for a referenced-but-undefined filter), not corruption and not an error
```

This falsifies, by direct reproduction against the new mechanism, the
ledger items CE-01 (blob replace), CE-02 (ambient `GIT_DIR`/
`GIT_OBJECT_DIRECTORY`), CE-03 (`GIT_CONFIG_PARAMETERS`), CE-04 (worktree
`.gitattributes` — moot here since checkout happens from a clean scratch
tree in the first place), CE-08 (hooks/`core.hooksPath`), CE-10 (smudge),
CE-13 (`includeIf.gitdir`), CE-16 (lazy fetch/`ext::`), CE-25 (commit/blob
replacement on the toolrepo side, same mechanism as CE-01). The §6
alternates-refusal requirement is confirmed trivially checkable at the
discovery boundary, before any object work begins.

### What the spike does NOT yet establish

- The other layouts §6 requires an explicit decision on (linked worktree
  `.git` file, commondir, bare repository, shallow repository) were not yet
  exercised — only an ordinary `.git` directory. Each needs its own
  discovery-boundary test before the production module can claim to cover
  them; until then they should fail closed, per §6's own instruction, not
  be assumed to work.
- Object-closure computation used `git rev-list --objects <base> <head>`
  (both full histories), not `<base>...<head>` boundary syntax — this is
  deliberately conservative (imports more than the diff strictly needs) and
  should be revisited for large repositories once correctness is settled;
  performance was not evaluated at all.
- Renames, submodule gitlinks, and larger real-world object graphs were not
  exercised — the fixture is a single two-commit, single-file repository.
- This spike proves the TARGET-side mechanism. The TOOLREPO-side mechanism
  (§10/§11 — blob-vs-filesystem-bytes identity, and the choice between
  reading in place vs. materializing the toolrepo itself into an exact
  controlled subject) is a related but distinct proposition and has not yet
  been spiked.
- No production module exists yet. This section is evidence for the design
  decision the next section commits to, not a substitute for the module,
  its typed refusals, or its formal test suite.

## Toolrepo execution-subject spike — empirical, Phase 2

Run against a real fixture dev checkout (`git init`, committed baseline,
then untracked/tampered mutations layered on top), never against this
toolrepo's own checkout.

### Import topology, observed directly (not guessed)

```yaml
plain_invocation:  # python3 scripts/probe.py, cwd=repo root
  sys_path_0: "<repo root>/scripts"       # the SCRIPT'S OWN DIRECTORY, auto-inserted by the interpreter
  pythonpath_honored: true
  user_site_enabled: true
isolated_mode:      # python3 -I scripts/probe.py
  sys_path_0: "/usr/lib/python311.zip"    # stdlib zip -- script directory is NOT auto-inserted at all
  pythonpath_honored: false               # confirmed: PYTHONPATH=/some/injected/evil had zero effect
  user_site_enabled: false
```

`-I` is a materially stronger primitive than expected: it does not merely
disable a couple of settings, it removes the automatic script-directory
`sys.path[0]` insertion entirely. The forensic `#274` CLI's own
`sys.path.insert(0, str(REPO_ROOT))` (`scripts/aiops-review-run-v2.py:67-69`,
read via `git show c37d5b5:...`) is what actually made root-level shadowing
possible — `-I` alone does not add a repo root back; only an explicit,
authority-controlled insertion does, and that insertion point is exactly
what the new architecture controls.

### RED baseline (naive/vulnerable model), reproduced directly

A tampered dev checkout (mutated tracked file, untracked `scripts/argparse.py`,
untracked root `pydantic.py`, untracked `.pyc` with different content than
the committed source, `assume-unchanged`/`skip-worktree` set) run as plain
`python3 scripts/probe_entry.py` from the checkout:

```text
SCRIPTS_ARGPARSE_SHADOW_EXECUTED
Traceback ...
AttributeError: module 'app.agent_review.probe_target' has no attribute 'report'
```

The hostile `scripts/argparse.py` executed on `import argparse`, and the
imported `probe_target` module was the TAMPERED on-disk file, not the
committed one — RED, confirming the naive model is vulnerable exactly as
the forensic corpus predicted.

### GREEN result, `git archive` from the declared commit

Materializing via `git archive <declared_sha> | tar -x` into a scratch
directory (never touching the dirty working tree), then running
`python3 -I scripts/probe_entry.py` from inside that scratch directory,
with the subject's own root explicitly inserted at `sys.path[0]` by the
script itself (not auto-inserted):

```text
app.agent_review.probe_target imported from: <subject>/app/agent_review/probe_target.py
MARKER: LEGITIMATE
argparse module file: /usr/lib/python3.11/argparse.py
pydantic import failed: No module named 'pydantic'
```

`MARKER: LEGITIMATE` (not `TAMPERED`, not `PYC_TAMPERED`) confirms the
committed byte content executed, not the tampered/pyc-shadowed on-disk
state. `argparse` resolved to the real stdlib module, not the shadow —
`-I` alone closed it, before any application-level defense. The `.pyc`
in `__pycache__`, the untracked `scripts/argparse.py`, and the untracked
root `pydantic.py` are all simply ABSENT from the materialized subject
(confirmed by direct directory listing), because `git archive` only
extracts tracked, committed blobs — there is no untracked-file universe to
enumerate or hide within.

### Two properties verified directly, not assumed from `git archive`'s
### documentation

```yaml
archive_ignores_index_flags: true    # assume-unchanged + skip-worktree set on the source files;
                                       # archive output still shows the COMMITTED bytes -- archive
                                       # reads the commit tree object directly, never the index
archive_ignores_repo_local_clean_and_smudge_filters: true  # filter.evil.clean/smudge
                                       # configured repository-locally; archived content is the
                                       # plain committed bytes, no filter command executed
```

**Correction, made during production hardening, not left standing:** the
claim below originally also listed CE-11 (`filter.process`) as eliminated
by this same result. That was wrong -- `git archive` does NOT ignore
`filter.*.process` the way it ignores `.clean`/`.smudge`; an isolated
three-way fixture (all three filter hooks configured on the same
repository, only `archive` run) confirmed `.process` fires while
`.clean`/`.smudge` stay silent. CE-11 is refused outright instead (a
production fix, see `1db7e5d`), the same fail-closed shape as the symlink
case below, not eliminated architecturally.

This directly and simultaneously eliminates CE-09 (`filter.clean`), CE-14
(`assume-unchanged`), CE-15 (`skip-worktree`), CE-18 (ignored/untracked
source), CE-19 (root shadowing), CE-20 (scripts-directory shadowing), CE-21
(`.pyc`), CE-23 (untracked nested repository) for the toolrepo side. CE-11
is closed by explicit refusal, not by this elimination.

### One counterexample `git archive` alone does NOT close — found here, not assumed safe

```text
$ git ls-tree -r <sha> | grep symlink
120000 blob <sha>  app/agent_review/symlinked_module.py

$ git archive <sha> | tar -x -C <subject>
$ ls -la <subject>/app/agent_review/symlinked_module.py
lrwxrwxrwx ... symlinked_module.py -> /tmp/.../outside_target/real_module.py

$ cat <subject>/app/agent_review/symlinked_module.py
MALICIOUS_EXTERNAL=1
```

A committed symlink blob (mode `120000`) is extracted by `git archive` as a
REAL filesystem symlink, and reading through it resolves to whatever the
target points at on the actual filesystem — which can be an absolute path
outside the subject entirely, exactly CE-22's finding. `git archive` is
therefore **not sufficient by itself**; the production materializer must
audit the tree first (`git ls-tree -r`, checking the mode column for every
entry under the bounded project-owned path) and **refuse** on any symlink
(`120000`) or gitlink/submodule (`160000`) entry, before calling archive at
all — enumerable cheaply from the same `ls-tree` output already needed for
the byte-identity oracle (§8) and for detecting a deleted bounded path
(CE-24).

### Architecture decision

```yaml
toolrepo_architecture_decision:
  hypotheses_tested:
    - CONTROLLED_EXECUTION_SUBJECT (git archive from declared commit + -I execution)
    - "implicit baseline: RAW_WORKTREE_EQUIVALENCE (plain execution + git-diff-clean proof) -- already refuted by PR 274's own forensic corpus (CE-09, CE-14, CE-15, CE-18..CE-21, CE-23)"
  selected: CONTROLLED_EXECUTION_SUBJECT
  rejected: RAW_WORKTREE_EQUIVALENCE, HYBRID (no evidence found requiring a hybrid --
    the pure controlled-subject form closed every tested class except the symlink
    case, which gets a targeted pre-check rather than a partial fallback to
    worktree execution)
  mechanism:
    materialization: "git archive <declared_sha> -- <bounded pathspec>, from a bounded
      object-closure import (reusing the target-side allowlist-env primitive), never
      the mutable development checkout"
    pre_check: "git ls-tree -r <declared_sha> -- <bounded pathspec>, refuse on any
      mode 120000 (symlink) or 160000 (gitlink/submodule) entry"
    byte_identity_oracle: "cat-file blob bytes at declared_sha vs. raw filesystem
      bytes in the materialized subject, for every enumerated regular-file entry --
      defense in depth layered on top of, not instead of, the archive mechanism"
    execution: "python -I, cwd = subject root, explicit sys.path.insert(0, subject_root),
      no PYTHONPATH, no user site, no bytecode write (-B) where practical"
  reasoning: >-
    Every element above is backed by a reproduced experiment in this
    section, not merely preferred on principle. RAW_WORKTREE_EQUIVALENCE
    was not re-spiked from zero because #274's own forensic corpus already
    falsified it across 7 of the 25 ledger classes; re-running that
    falsification here would not have added evidence, only repeated it.
```

### Bootstrap honesty

```yaml
bootstrap:
  remotely_attested: false
  project_source_identity_after_seal: established   # for the materialized subject's
    # own content, once the symlink/gitlink pre-check and byte-identity oracle both
    # pass -- NOT for the outer launcher that invokes materialization itself
  interpreter_dependency_identity_owner: toolchain_digest  # NOT this authority; a
    # materialized subject deliberately does not carry or vendor third-party
    # dependencies (confirmed: `pydantic` import failed inside the archived subject
    # using the system interpreter with no site-packages access wired in this spike
    # -- the real production execution will use the toolchain's own interpreter/
    # site-packages, a separate, already-existing authority, not something this
    # slice re-derives or collapses into source identity)
```

Forbidden statement not made: this does not claim "no unverified code
executed before review" — the outer launcher (whatever invokes
materialization) necessarily runs first, and its own trust is a
distribution/installer question owned by `#203`→`#205`, not by `#200-E`.

## Phase 2 — production authorities

Implemented: `app/agent_review/_bounded_git_child_env_v2.py` (shared
allowlist child environment, not a blacklist, not a resurrection of
`_sealed_git_execution_v2`), `app/agent_review/controlled_subject_v2.py`
(TARGET), `app/agent_review/toolrepo_execution_subject_v2.py` (TOOLREPO).
Two distinct error families (`ControlledSubjectError`,
`ToolrepoExecutionSubjectError`), never flattened into one.

### Forensic witness mapping (§19)

```yaml
forensic_witness_mapping:
  - id: CE-01
    source: "PR274/independent-verification/README.md, target replace-object finding"
    old_result: "git ls-tree/rev-parse kept reporting the original SHA while cat-file
      returned malicious bytes for the target's own git process"
    successor_result: "test_replacement_object_ignored_ce01 -- scratch reads the
      ORIGINAL blob via the bounded object-closure import, no refs/replace/*
      ever resolved from the source"
    architectural_reason: "bounded rev-list --objects closure never touches
      refs/replace/*; GIT_NO_REPLACE_OBJECTS=1 in the bounded env regardless"
  - id: CE-02/CE-03
    source: "PR274 evidence/independent-verification/02"
    old_result: "GIT_CONFIG_PARAMETERS survived #274's env-stripping blacklist"
    successor_result: "test_ambient_env_has_no_effect_ce02_ce03 -- GIT_DIR,
      GIT_OBJECT_DIRECTORY, GIT_CONFIG_PARAMETERS all set in the calling
      process's environment, zero effect"
    architectural_reason: "allowlist child env: nothing from the caller's
      os.environ reaches the child unless bounded_child_env_v2 named it"
  - id: CE-08
    source: "PR274 evidence/independent-verification/01"
    old_result: "git worktree add ran the target's post-checkout hook"
    successor_result: "test_hostile_hook_never_executes_ce08 -- checkout inside
      scratch never touches the target's .git/hooks at all"
    architectural_reason: "scratch's own .git/hooks is freshly initialized by
      `git init`, never populated from the target"
  - id: CE-09
    source: "PR274 round 3, toolrepo identity filter.clean finding"
    old_result: "filter.clean executed during the toolrepo cleanliness check and
      hid materially dirty tracked source"
    successor_result: "test_filter_clean_has_no_effect_ce09 -- git archive
      does not invoke repository-local filter.clean, verified directly"
    architectural_reason: "git archive reads the commit tree object, not a
      filtered checkout; the .clean/.smudge filter commands are never invoked"
  - id: CE-11
    source: "PR274 lane A, toolrepo identity filter.process finding"
    old_result: "filter.process executed during toolrepo identity observation"
    successor_result: "test_filter_process_is_refused_ce11 -- NOT eliminated by
      the archive mechanism the way CE-09 is; refused outright instead
      (correction found while writing this test: git archive DOES invoke
      filter.process even though .clean/.smudge stay silent, confirmed with
      an isolated three-way fixture -- the checkpoint's spike section
      originally over-generalized the .clean/.smudge result to imply .process
      was covered too, which was wrong, and is corrected in place, not hidden)"
    architectural_reason: "no `-c` override closes an arbitrary attacker-chosen
      driver name, same reasoning as the target-side smudge/CE-10 refusal --
      detected via unscoped `git config --list` (not `--local`, learning
      #274's own include.path lesson) and refused before archive ever runs"
  - id: CE-10
    source: "PR274 M14 (round 2), filter.smudge on checkout"
    old_result: "filter.smudge executed during git worktree add"
    successor_result: "test_hostile_smudge_filter_never_executes_ce10 -- scratch
      checkout never consults the target's filter config"
    architectural_reason: "target filter config is never copied into scratch (§8
      of the grant); a committed .gitattributes assignment is semantic input,
      the DRIVER COMMAND is not imported"
  - id: CE-13
    source: "PR274 evidence/independent-verification/01, the sharpest finding"
    old_result: "includeIf.gitdir:<repo>/.git/worktrees/** bypassed the round-2
      filter detector and executed during git worktree add"
    successor_result: "test_includeif_worktree_pattern_never_matches_ce13 --
      the exact same includeIf pattern configured, zero effect"
    architectural_reason: "the scratch .git path was never derived from or
      nested under the source's .git at all -- there is no target-owned
      includeIf.gitdir condition that can ever match it, by construction of
      the path, not by detecting and refusing a config key"
  - id: CE-14/CE-15
    source: "PR274 evidence/independent-verification/04, lane B Q6"
    old_result: "assume-unchanged/skip-worktree made git diff HEAD omit a
      modified/tampered tracked file; establish_toolrepo_source_identity_v2
      PASSED with tampered bytes on disk"
    successor_result: "test_index_flags_have_no_effect_ce14_ce15 -- both index
      flags set, materialized subject still contains the COMMITTED bytes"
    architectural_reason: "git archive reads the commit tree directly and
      never consults the index at all"
  - id: CE-16
    source: "PR274 lane C Q7, the P0 -- lazy fetch executing an ext:: helper
      and persisting a pack into the target on a declared success path"
    old_result: "a full CLI run exited 0 while executing a hostile transport
      helper and persisting a new promisor pack into the reviewed repository"
    successor_result: "test_genuinely_missing_object_fails_closed_ce16 and
      test_lazy_fetch_helper_never_executes_ce16 -- typed refusal at the
      object-closure step, zero helper execution, zero pack import"
    architectural_reason: "GIT_NO_LAZY_FETCH=1 is part of the bounded
      environment for the closure computation itself, not just later
      operations; a missing object fails the closure step before any
      pack/import work begins"
  - id: CE-17
    source: "PR274 evidence/independent-verification/06"
    old_result: "test_target_checkout_is_never_mutated /
      test_cli_has_no_filesystem_output_authority only observed
      `git status --porcelain`, never .git contents; a scratch-copy mutant
      writing .git/agent-review-mutant-marker passed both unmodified"
    successor_result: "test_target_nonmutation_oracle_catches_a_git_admin_write --
      a recursive before/after filesystem snapshot (worktree + .git + ignored
      + untracked) catches the same mutant"
    architectural_reason: "the oracle itself changed shape (recursive
      filesystem snapshot, not git status), independent of what
      materialization does or does not write"
  - id: CE-18/CE-19/CE-20/CE-21/CE-23
    source: "PR274 lane B Q6, root/scripts shadow, untracked source, .pyc,
      nested repo"
    old_result: "untracked scripts/argparse.py, root pydantic.py, .pyc,
      .gitignore-hidden files all entered the executed-source universe"
    successor_result: "test_untracked_root_and_scripts_shadow_absent_ce18_ce19_ce20,
      test_pyc_shadow_absent_ce21, test_real_subprocess_isolated_mode_
      imports_from_subject_not_devrepo -- none present in the materialized
      subject; -I mode additionally proves the real subprocess cannot resolve
      them even if they existed alongside"
    architectural_reason: "git archive extracts only tracked, committed blobs
      -- there is no untracked-file universe in the subject to hide content
      in; -I removes the interpreter's automatic script-directory sys.path
      insertion and ignores PYTHONPATH/user-site"
  - id: CE-22
    source: "PR274 lane B Q6, tracked symlink"
    old_result: "a tracked symlink's external referent could change without
      changing HEAD, the symlink blob, or git status"
    successor_result: "test_committed_symlink_is_refused_ce22 -- refused at the
      ls-tree mode-audit step, before archive ever runs"
    architectural_reason: "NEW finding this phase, not merely ported: git
      archive alone does not close this (reproduced in the spike, extracting
      a live symlink readable outside the subject) -- required an explicit
      pre-check the spike surfaced, not assumed safe from #274's own closure"
  - id: CE-04/CE-05/CE-06/CE-07
    source: "PR274 M4, round-2 core.attributesFile closure, M5, lane B Q4
      (NBSP/FIFO)"
    old_result: "target-side attribute redirection (untracked worktree
      .gitattributes, core.attributesFile, $GIT_DIR/info/attributes, and the
      NBSP/FIFO bypasses of the round-3 info/attributes detector) each
      changed acquire_diff_v2's semantic output"
    successor_result: "test_source_untracked_gitattributes_has_no_effect_ce04,
      _ce05, and test_source_info_attributes_has_no_effect_ce06_ce07 -- all
      four planted on the source, zero effect on the scratch subject's diff"
    architectural_reason: "the scratch subject's own info/attributes and
      config are freshly initialized and never populated from the source at
      all -- there is no detector to fool (CE-07's specific class), because
      there is nothing read from the source to need detecting in the first
      place"
  - id: CE-12
    source: "PR274 round 2, core.fsmonitor"
    old_result: "core.fsmonitor executed during git status against the target"
    successor_result: "test_source_fsmonitor_never_executes_ce12 -- configured
      on the source, zero execution"
    architectural_reason: "target config, including core.fsmonitor, is never
      copied into scratch"
  - id: CE-24
    source: "PR274 M7/M8 (round 1), deleted bounded source"
    old_result: "a `.exists()` filesystem prefilter silently excluded a
      deleted bounded path from the pathspec"
    successor_result: "test_deleted_bounded_path_is_transparently_absent_ce24
      -- a bounded path absent at the declared SHA contributes zero entries,
      visible via `subj.entries`, never hidden; found and fixed a related
      defect while writing this test: `git archive` fails its ENTIRE
      invocation (exit 128) if any single pathspec matches nothing, even
      alongside valid ones, so the archive call now uses only the
      ls-tree-confirmed-present subset (1db7e5d)"
    architectural_reason: "expected files come from `git ls-tree` against the
      declared SHA, never from a filesystem existence check"
  - id: CE-25
    source: "PR274 M2 (round 1), commit replacement of toolrepo's own HEAD"
    old_result: "git replace on the toolrepo's own declared HEAD commit"
    successor_result: "test_commit_replacement_on_declared_sha_ignored_ce25 --
      ls-tree/archive against the declared SHA return the ORIGINAL commit's
      content"
    architectural_reason: "GIT_NO_REPLACE_OBJECTS=1 is part of the bounded
      environment for every git invocation this module makes"
```

### Phase 2 ledger closure

All 25 ledger items now have a dedicated, passing test -- not the 17 first
committed. The 8 gap items (CE-04, CE-05, CE-06, CE-07, CE-12, CE-24, CE-25,
plus the CE-11 correction) were closed by testing each explicitly rather
than assuming the spike's general-purpose claims covered them, which is
exactly how CE-11 and CE-24 were caught: both were genuinely new findings
made while writing what looked like it should have been a routine coverage
pass, not while looking for bugs specifically. 35 tests total (20 target,
15 toolrepo).

### Mutation non-vacuity (§20), all executed and killed

```yaml
mutation_matrix_phase2:
  - id: M2E-01
    target: controlled_subject_v2
    mutation: "remove the objects/info/alternates refusal check"
    killed_by: test_source_alternates_present_is_refused
  - id: M2E-02
    target: _bounded_git_child_env_v2
    mutation: "drop GIT_NO_LAZY_FETCH from the bounded environment"
    killed_by: test_lazy_fetch_helper_never_executes_ce16
    note: "the hostile ext:: helper ACTUALLY EXECUTED under this mutation,
      confirmed by the marker file existing -- not merely a changed return code"
  - id: M2E-03
    target: controlled_subject_v2
    mutation: "copy the source's .git/config into the scratch repo after init"
    killed_by: test_hostile_smudge_filter_never_executes_ce10
    note: "test_hostile_hook_never_executes_ce08 and the includeIf test
      correctly stayed green under this specific mutation (config alone does
      not reintroduce a hooks directory or a matching includeIf.gitdir
      condition) -- recorded as an honest collateral result, not hidden"
  - id: M2E-04
    target: controlled_subject_v2 (nonmutation oracle)
    mutation: "inject a .git/agent-review-mutant-marker write"
    killed_by: test_target_nonmutation_oracle_catches_a_git_admin_write
    note: "self-contained: the test injects and asserts detection, then
      cleans up and asserts silence on the clean case, in the same test"
  - id: M2E-05
    target: toolrepo_execution_subject_v2
    mutation: "yield the mutable dev checkout root instead of the materialized subject"
    killed_by: [test_index_flags_have_no_effect_ce14_ce15, test_real_subprocess_isolated_mode_imports_from_subject_not_devrepo]
    note: "covers both 'execute original dev checkout' and 'add original repo
      root to sys.path' from the grant's mandatory list -- no separate
      operational composer exists yet to have an independent sys.path bug"
  - id: M2E-06
    target: toolrepo_execution_subject_v2 test's own subprocess invocation
    mutation: "drop -I from the real-subprocess test's own argv"
    killed_by: test_real_subprocess_isolated_mode_imports_from_subject_not_devrepo (against itself)
    note: "found and fixed a real vacuity bug first: the original PYTHONPATH
      assertion pointed at a nonexistent directory and stayed green even with
      -I dropped. Fixed by planting a real hostile module at the PYTHONPATH
      location before re-running this mutation -- see e6abff7"
  - id: M2E-07
    target: toolrepo_execution_subject_v2 (byte-identity oracle)
    mutation: "monkeypatch extraction to write different bytes than the archive produced"
    killed_by: test_tampered_bounded_source_refused_by_byte_identity_oracle
  - id: M2E-08
    target: N/A -- architectural, no mutable logic to mutate
    mutation: "'prefer pyc over exact source' has no corresponding code path:
      git archive extracts only tracked blobs, and .pyc is never tracked in
      the fixtures used"
    killed_by: test_pyc_shadow_absent_ce21 (direct architectural proof, not a
      mutation kill)
```

Every mutant: baseline green -> mutation applied -> confirmed to produce the
intended failure -> reverted -> baseline reconfirmed green, the same
discipline `#274` established.

### Phase 2 qualification

```yaml
qualification_phase2:
  focused_new_tests: "35 passed (test_controlled_subject_v2.py: 20,
    test_toolrepo_execution_subject_v2.py: 15) -- final count, after the
    CE-11/CE-24 gap-closure round added 8 more to the original 27"
  existing_diff_acquisition_tests: "86 passed, untouched"
  compileall: pass
  git_diff_check: pass
  schema_export_check: "byte-identical"
  full_repository_suite_final: "48 failed, 3353 passed, 16 skipped -- rerun
    after the CE-11/CE-24 production fixes; identical 48 failing test names
    to the pre-fix run, +8 passed matching exactly the 8 new tests added,
    zero regressions"
  known_environment_classification:
    reproduced_at_base: true
    method: >-
      Spot-checked the sharpest failure
      (test_telemetry_cli_does_not_call_network_or_provider) against a
      CLEAN worktree of master (f70af2e) with zero #200-E changes applied
      -- byte-identical failure and error message, and its FULL suite run
      matched the exact same 48 failing names with an exact +count delta
      accounted for entirely by #200-E's own new tests. Confirmed none of
      the 48 failing test names reference any #200-E module
      (controlled_subject_v2, toolrepo_execution_subject_v2,
      _bounded_git_child_env_v2).
    classes:
      - class: environment
        count: 46
        description: >-
          `scripts/aiops-review-*-cli.py`'s own `target_repo_write_blocked`
          safety check ("AgentReview artifacts cannot be written inside Git
          worktrees") fires because BOTH this #200-E worktree and the clean
          master baseline used for comparison are `git worktree add`
          checkouts, not plain clones -- a pre-existing property of how this
          multi-worktree host is laid out, unrelated to any change in this
          slice.
      - class: environment
        count: 2
        description: >-
          The two long-known `test_isolated_executor_v2.py` sudo tests
          (fail locally, pass in CI -- established in earlier sessions this
          slice inherited, not rediscovered here).
```
