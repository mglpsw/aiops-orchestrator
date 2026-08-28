# Checkpoint — `#200-D` operational composition successor

**Status:** implemented and locally qualified; exact-HEAD CI and independent
review are the next gates. Terminal state is Draft — not Ready, not merged.

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  base_sha: f70af2e635643d1ee96ba431857002ae079b502b
  base_tree: 945f3247a9e8ad534a0d35f4450b24446906f30c
  branch: feat/200-d-operational-composition-successor
forensic_predecessor:
  pr: 271
  head: 58a64f9eb0362b2146da19a9e1bd180f92af0f32
  state: CLOSED, unmerged
  code_cherrypicked_blindly: false
  qualification_transferred: false
architectural_predecessor:
  pr: 272
  squash: f70af2e635643d1ee96ba431857002ae079b502b
  state: MERGED
state:
  published_schema_change: false
  toolrepo_as_product: established
  provider_free_operational_composition: established
  live_router: not_established
  provider: not_established
  target_pack_install: not_established
  release: not_established
```

## Why this PR exists

`run_synthetic_review_v2` (`review_transport_v2.py`) already owned the
entire back half — transport, source-specific proof, binding, parsing,
synthesis, readiness decision, `ReviewReadinessV2` emission — and already
accepted either transport. Verified on live master before any source
change: it had **zero production callers** in `app/` or `scripts/`. That
wiring existed only inside tests.

PR #271 built that missing front half and was reviewed six times, every
round finding the same class of defect: a stage's `except` list narrower
than the exception surface beneath it. It returned
`STOP_ARCHITECTURAL_BOUNDARY` — diagnosing that each authority's surface was
OPEN, so no amount of consumer-side inspection could enumerate it. PR #272
(merged as `f70af2e`) closed those surfaces **at their owners** under a
two-epoch model:

```text
caller / external / environment material
  -> owner validation, parsing, acquisition classification
  -> SEAL
  -> internal derivation
```

Only pre-seal failures convert to a typed refusal; post-seal
`ValidationError`/`TypeError`/etc. is a repository defect and escapes raw.
This PR is the successor built **on top of** that closure. #271's runner is
**not** ported and **not** modified.

## §5 — immutable head-bound reference material (`reference_source_v2.py`)

### The problem, confirmed by reading the call graph, not by assumption

`payload_references_v2.build_payload_artifact_references_v2`/
`build_payload_contract_references_v2` read declared artifact/contract
bytes via `Path.is_file()` / `read_text()` / `read_bytes()` against the
**working tree**. `diff_acquisition_v2.acquire_authoritative_diff_v2` and
`review_content_extraction_v2` read the diff and content from **Git
objects** at `base_sha`/`head_sha`. So identical `(base_sha, head_sha)`
inputs could bind different payload reference bytes depending only on
which revision the checkout happened to sit at, or on staged/untracked/
modified files with no observable difference in those inputs at all.

### The rejected design

A preflight closure (`establish_reference_source_closure_v2(...) -> None`)
followed by letting the payload builder re-read the same mutable
`repo_root` is **check-then-reread**: the working tree can change between
the check and the read. That establishes only
`MaterialObservedDuringPreflight ⊆ MaterialProvenAt(head_sha)`, not the
property actually needed:

```text
TARGET_REFERENCE_SOURCE_INVARIANT
  ConsumedReferenceMaterial ⊆ MaterialProvenAt(head_sha)
```

### The selected design

`resolve_reference_source_v2` materializes every profile-declared
reference path that exists as a **regular Git blob** at `head_sha` into a
private, per-run directory (structural lifetime via a context manager —
cleaned up on success, typed refusal, or unexpected defect), and the
**unmodified** payload owner is pointed at that directory instead of the
target checkout. Enabler, verified by reading the source:
`_build_chunk_payload_from_profile_v2` uses `repo_root` for exactly two
calls, both satisfied transparently by the substituted root — **zero
changes to `payload_builder_v2`/`payload_references_v2`**.

The Git tree entry at `head_sha` is the **sole** decision authority — a
total function with three branches, none of them consulting the working
tree:

```text
regular blob (100644/100755)         -> materialize the exact blob bytes
no entry                             -> not materialized; the EXISTING
                                         payload owner's own missing-
                                         reference semantics apply
                                         (required -> payload_required_
                                         artifact_missing; optional ->
                                         optional_artifact_missing)
symlink / gitlink / tree / other     -> reference_source_material_
non-blob entry                          unverifiable
```

An earlier draft proposed refusing when a declared path was absent from
`head_sha` but present in the working tree (to avoid an operator seeing
"missing" for a file that visibly exists). **Deleted**: it made an
identical immutable subject produce different semantic results from
incidental mutable filesystem state — the exact nondeterminism this design
removes. `checkout HEAD != head_sha` is likewise not a blocking condition:
a call-graph audit found no semantically consumed input anywhere in the
composed run depends on the target's *checked-out* HEAD (only its git
objects). The module reads **no** working-tree state at all and resolves
**no** checkout HEAD — `git rev-parse HEAD` belongs only to
`toolrepo_identity_v2`'s different subject.

```text
SemanticRunInputs = GitObjects(base_sha, head_sha)
                  + trusted profile/policy
                  + explicit bound run authorities

NOT SemanticRunInputs + incidental target working-tree state
```

### Recorded consequence — generated artifacts

Every shipped profile, including
`templates/agentreview-v2-target-pack/target-profile.v2.yaml`, declares
`artifacts/full.diff` as `required: true`; the pack template ships **only**
the profile, with no producer for that file anywhere in the toolrepo. In
the v2 test fixtures it is a committed placeholder, so it is HEAD-bound and
those runs proceed. A target that **generates** `full.diff` after checkout
falls through to the existing `payload_required_artifact_missing` reason —
**no new taxonomy was added** for a condition the payload owner already
names. Runtime-generated-artifact support needs its own producer/digest/
provenance authority; that is **not** built here and belongs to the
distribution/evidence line (`#203`, `#194`–`#198`).

### The decisive control

`test_working_tree_presence_is_irrelevant_to_reference_material`: run A
(declared optional path absent from the working tree) and run B (the same
path present as an untracked/generated file) at the SAME `head_sha` must
produce byte-identical materialized reference sets. Verified as a real
falsifier, not just a passing assertion: a mutant reintroducing a
worktree-sensitive "present but unbound" branch was applied, proven to kill
exactly this one test and no other, then reverted and the baseline
reconfirmed green.

## §18 — toolrepo source identity (`toolrepo_identity_v2.py`)

Precedent: `scripts/install-agent-review-toolrepo.sh` already verifies
`--toolrepo-sha` against `git rev-parse HEAD` and rejects a branch/tag/short
SHA, but accepts a dirty tracked tree because installation copies a
lockfile, not the engine's own importable source. The operational runner
**executes** this toolrepo's Python, so its property is strictly stronger.

```text
TOOLREPO_SOURCE_IDENTITY_INVARIANT
  ReviewRun.toolrepo_sha == S iff
    1. the executing AgentReview package resolves inside TOOLREPO_ROOT;
    2. the executing CLI script belongs to that same root;
    3. TOOLREPO_ROOT is a resolvable Git checkout;
    4. HEAD == S;
    5. tracked executable source in the bounded surface is unmodified;
    6. no untracked executable/importable source exists in that bounded set.
  Caller declaration is never sufficient proof.
```

Claim precision, stated rather than implied: `toolrepo_sha` is the identity
of the AgentReview **toolrepo source checkout**; it does not prove every
executed byte (interpreter, dependencies, site-packages, `.pyc`, native
extensions) — that is `toolchain_digest`'s subject, unchanged by this slice.

**Bounded executable source set**, defined structurally, not by heuristic:
the WHOLE `app/` package tree and `scripts/aiops-review-run-v2.py` (this CLI
only). A dirty, unrelated *toolrepo* file (a doc, an eval fixture, another
script) does not block a run — verified by
`test_dirty_file_outside_bounded_source_set_does_not_block`.

**Correction, found on independent review of the exact head this checkpoint
first described.** The implementation had narrowed this to `app/agent_review`
only — a real divergence from this very paragraph's own stated intent, never
re-verified against the composed call graph. It does import across that
boundary: `review_transport_v2.py`, `required_check_provenance_v2.py`,
`authoritative_ci_snapshot_v2.py`, `authoritative_check_policy_v2.py`,
`authoritative_producer_evidence_v2.py`, `required_check_assembly_v2.py`,
`_router_receipt_v2.py` and `target_pack_receipt_v2.py` all import
`app.common.strict_json`. A dirty `app/common/strict_json.py` could have
executed as part of a review while toolrepo identity still reported clean.
Corrected to the whole `app/` package, with three new discriminating
controls (`test_dirty_tracked_file_outside_agent_review_but_inside_app_is_
refused`, its staged and untracked siblings) mutation-proven: narrowing the
bound back to `app/agent_review` makes exactly those three tests fail.

```text
.git absent / no identity mechanism   -> toolrepo_identity_unavailable
git present + HEAD != declared        -> toolrepo_identity_mismatch
package/script origin outside root    -> toolrepo_identity_mismatch
HEAD correct + bounded source dirty   -> toolrepo_worktree_dirty
untracked importable source present   -> toolrepo_identity_unverifiable
```

**Second-order honesty, recorded rather than hidden**: this module is
itself code that was imported and running before it verified anything. Its
claim is *"review execution was blocked before semantic review/transport"*
— never *"zero unverified code execution"*.

**Gitless toolrepo distribution is out of scope for this slice** and fails
closed:

```yaml
"#200-D":
  supported_toolrepo_execution: {git_checkout: true, gitless_distribution: false}
  gitless: {behavior: fail_closed, reason: toolrepo_identity_unavailable,
            future_owner: "distribution/release (#203 -> #205)"}
```

No `.version`-file fallback was invented; a release-artifact identity model
belongs to the distribution/release line.

## Error surface — measured against the CURRENT call graph, not inherited

```text
toolrepo id    -> ToolrepoIdentityError        (new, this PR)
profile        -> TargetProfileLoadErrorV2     (already closed pre-#272)
grouping       -> SemanticGroupingError        (already closed pre-#272)
diff           -> DiffAcquisitionError
reference src  -> ReferenceSourceError         (new, this PR)
assembly       -> RunAssemblyError
payload        -> PayloadBuilderError          ONLY
payload-set    -> PayloadSetBindingError
content        -> ExtractionBlockedError
readiness      -> wrapped once, see below
```

**Payload family, verified.** `payload_builder_v2.build_chunk_payloads_
from_profile_v2` (plural) and `build_chunk_payload_from_profile_v2`
(singular sibling) both convert their sibling `PayloadReferenceError` into
`PayloadBuilderError`, preserving the reason. This composer catches
`PayloadBuilderError` only; an AST-level structural oracle
(`test_operational_run_v2_never_catches_forbidden_families`) fails the
module if `PayloadReferenceError`, `pydantic.ValidationError`, `OSError`,
`Exception` or `BaseException` is ever caught.

**Back half, wrapped once around the single `run_synthetic_review_v2`
call**, derived from the CURRENT call graph rather than copied from #271's
historical clause list — that list was already stale on current master:

```text
SynthesisErrorV2                 chunk-result scope violation (converted
                                  from ChunkResultScopeError by synthesis_v2
                                  itself — #271's own `except
                                  ChunkResultScopeError` was dead code on the
                                  normal path and is not ported)
LifecycleAggregationError        reached DIRECTLY: synthesis calls the
                                  PRIVATE `_aggregate_finding_lifecycle_
                                  core_v2` and does not convert it. Proven
                                  through the REAL composed run with a
                                  duplicate prior_lifecycle finding_id, not
                                  asserted from reading the source alone.
ReadinessDecisionError           C1 decision refusal (converts its own
                                  FragmentCoverageBindingError internally)
TargetProfileLoadErrorV2         produce_review_readiness_v2 re-loads
                                  target_profile_root a SECOND time,
                                  independently of this module's own
                                  front-half load, inside the required-check
                                  re-verification frontier (#201-C0) — the
                                  IDENTICAL family already caught once is
                                  reachable a second time through a
                                  different call path (#272's own round-3
                                  lesson: closing one entry point is not
                                  closing the authority)
AuthoritativeCheckPolicyErrorV2  the base-checkout policy load/cross-
                                  validate the same C0 frontier performs
RequiredCheckReadinessErrorV2    required-check completeness assessment
RequiredCheckProvenanceErrorV2   the C0 frontier itself — its own module
                                  docstring states it is never caught
                                  "anywhere in between"
ReadinessEmissionError           pre-seal `ready`-precondition refusal
```

`parse_bound_chunk_response_v2` (inside `execute_chunk_review_v2`, itself
inside `run_synthetic_review_v2`'s per-chunk comprehension) raises a raw
`TypeError` for anything not produced by the binder — a **programmer
defect**, proven to still escape this composer raw
(`test_back_half_programmer_defect_escapes_raw`, via a monkeypatched back
half raising a bare `TypeError`).

## Two guards `#271` carried that are `OBSOLETE_AFTER_272`

- `RUN_BUDGET_INVALID_REASON_V2` pre-validation of `max_lines_per_chunk`:
  `assemble_manifest_from_diff_v2` now owns both the type check (wrong type
  → raw `TypeError`, a caller/programmer defect) and the value check
  (non-positive → `RunAssemblyError(run_assembly_budget_invalid)`) itself.
- `if built:` guard before `emit_payload_set_v2`, to avoid a raw pydantic
  `ValidationError` on an empty submission: `emit_payload_set_v2` now
  raises its own `PayloadSetBindingError(payload_set_empty)` for the empty
  case, so the guard would be dead code pretending to be a safeguard.

Neither is ported.

## Canonical synthesis preservation (no persistence)

`run_synthetic_review_v2` already computed `SynthesisResultV2` before
deriving `ReadinessDecisionV2`/`ReviewReadinessV2` from it, then discarded
it — returning only `readiness`/`chunk_outcomes`. The private, non-wire
`SyntheticReviewOutcomeV2` carrier now also returns the exact object it
computed: no second call to `synthesize_chunk_results_v2`. Verified by
**object identity, not equality**
(`test_synthesis_is_computed_exactly_once_and_is_the_object_readiness_used`,
monkeypatching both `synthesize_chunk_results_v2` and
`compute_readiness_decision_v2` to capture what each actually saw). Neither
`SyntheticReviewOutcomeV2` nor `SynthesisResultV2` appears in
`schema_export_v2.py` — confirmed non-wire, no published schema changed.

Lifecycle is not redesigned: `agreement != confirmation` is unchanged, no
persistence or historical lookup is added in this slice. Durable storage
remains owned by `RI-B1`/`#167`; human/external dispositions by `RI-C`/
`#168`.

## `#4`/`#12`/`#13` — provider-free, toolrepo-as-product acceptance

`tests/agent_review/test_operational_run_blackbox_e2e_v2.py` drives
`scripts/aiops-review-run-v2.py` as a **real subprocess** from this
toolrepo against a **real target Git repository created outside the
toolrepo tree** (a `tmp_path`, never imported into the toolrepo tree, no
engine Python copied into it), with the offline transport and a real
pre-placed response envelope. No model, provider, or network dependency.

```text
black_box_e2e:
  separate_toolrepo_target: true
  real_git_repo: true
  real_base_head: true
  real_diff: true
  target_mutated: false            (git tree byte-identical before/after)
  provider_used: false
  network_required: false
  terminal_readiness: manual_required + policy_failure (honest, not forced)
```

Router-format provider-free proof (offline HTTP seam
`_open_agent_router_request_v2`, never live):
`test_secret_in_the_real_target_diff_never_reaches_the_outbound_request`
plants a token-shaped literal in a REAL target diff, drives it through real
acquisition/redaction, and asserts the raw token is absent from the actual
outbound request bytes while the sanitized line survives (non-vacuous:
redaction, not deletion) -- AND completes the full response side through the
real, unpatched `execute_chunk_review_v2`: the mocked HTTP response carries a
genuine `agent-router.inference-receipt.v2` (reusing `test_review_transport_
v2._fixture_receipt`) built from the real outbound request, and the test
asserts `outcome.state == "bound"` with a real `ParsedChunkResultV2` --
receipt verification and Router-result binding, not merely outbound bytes.

**Correction, found on independent review of the exact head this checkpoint
first described.** An earlier version of this test proved the outbound-
redaction half only, then discarded the response side with
`except Exception: pass` against an intentionally invalid fake response, so
`receipt_v2_verified`/`bound_result` were claimed but never actually
exercised. Rewritten as above; proven non-vacuous separately -- a tampered
receipt (wrong `returned_output.sha256`) correctly degrades to
`manual_required`/`router_output_mismatch` rather than silently binding.

Negative oracle: `test_engine_never_branches_on_target_name`
(pre-existing, `test_v2_dual_target_e2e.py`) continues to hold; no new
target-name string literal was introduced anywhere in this PR's modules.

## Honest readiness, not a forced positive state

No fabricated required-check authority is reachable in production today —
`required_check_readiness_v2`'s own module docstring states this. The
acceptance oracle's terminal state is therefore
`manual_required`/`policy_failure`, not `ready`: **semantic result present
+ no authoritative green required-check evidence ⇒ NOT ready**. Forcing a
positive state would have been a weaker test, not a stronger one.

## Git semantic execution closure (correction round, `db00334` onward)

A final exact-head independent review of `2fdedbf` found that `#5`/`#18`'s
own invariants -- reference material and toolrepo identity being functions
of a *declared Git object identity* -- were incomplete in one dimension
neither had addressed: **the Git interpretation environment itself was
unbound**.

```text
ObjectIdentity != InterpretationEnvironment

ClosedGitSubject = ObjectIdentity + DeterministicGitExecutionPolicy
```

`GitSHAIdentity + UnboundGitInterpretationEnvironment` is not a closed
subject: `base_sha`/`head_sha`/`toolrepo_sha` name an object, but Git's
result for a given SHA can still depend on replacement refs, ambient
`GIT_*` environment variables, global/system config, and working-tree
attribute files that are outside any of those three declared identities.

**P0 — replacement objects, reproduced directly.** `git ls-tree <head_sha>
-- path` continues to report the ORIGINAL blob SHA after
`git replace <original> <malicious>`, while `git cat-file -p <original>`
returns the malicious bytes. The identical class affects a commit-level
replacement of a toolrepo's own HEAD. New
`app/agent_review/_sealed_git_execution_v2.py` sets
`GIT_NO_REPLACE_OBJECTS=1` unconditionally for every Git subprocess this
package runs for semantic acquisition or toolrepo identity.

**P0 hardening — ambient environment, reproduced directly.** An ambient
`GIT_DIR` pointing at an unrelated repository silently redirects every Git
command run in the process regardless of `cwd`/`-C`; `GIT_OBJECT_DIRECTORY`
injection broke resolution of the real repository's own HEAD entirely. The
sealed child environment strips `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/
`GIT_OBJECT_DIRECTORY`/`GIT_ALTERNATE_OBJECT_DIRECTORIES`/`GIT_COMMON_DIR`/
`GIT_NAMESPACE`/`GIT_EXTERNAL_DIFF`/`GIT_DIFF_OPTS`/`GIT_ATTR_SOURCE`/
`GIT_CONFIG_COUNT`+`GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*`, and points
`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` at `os.devnull` (supported since
Git 2.32, verified on this host's Git 2.39.5). Repository-local
`.git/config` is deliberately left reachable -- it is part of the checkout
under review, not ambient caller/machine state.

**P1-A — attributes must be subject-bound, reproduced directly.** An
UNTRACKED `.gitattributes` planted in the target's own working tree changed
`acquire_diff_v2`'s output for the identical `base_sha...head_sha` range
from a text hunk to a binary patch. Git's native fix,
`--attr-source=<tree-ish>`, requires Git >= 2.40; **this host's Git (2.39.5,
Debian 12/bookworm stable) does not have it** -- verified directly, the
flag fails with a usage error, not merely absent from `--help`. Both
`acquire_diff_v2` and `acquire_raw_diff_v2` now run inside a disposable,
detached `git worktree` checked out exactly at `head_sha`, so attribute
resolution walks that checkout's own `.gitattributes` -- verified directly
in both directions: an untracked/modified attributes file in the original
checkout has zero effect, while one actually committed at `head_sha` is
correctly applied, and the target's own current checkout HEAD is
irrelevant. `$GIT_DIR/info/attributes` is **not** closed by this (shared by
every worktree, including the disposable one -- reproduced directly) and
is instead explicitly checked and refused
(`diff_info_attributes_active`) before the worktree is even created, since
no supported Git mechanism on this host excludes it.

**P0 — the attribute fix itself executed target-controlled code,
reproduced directly.** Found by the *next* independent review round, whose
subject was this correction rather than the original code. `git worktree
add` runs the TARGET repository's `post-checkout` hook: a hook planted at
`$GIT_DIR/hooks/post-checkout` executed, with the disposable worktree as
its cwd, during the very `git worktree add` introduced above for P1-A. A
repository-local `core.hooksPath` redirect reached an arbitrary directory
the same way. The attribute-source fix was therefore itself a
target-controlled code-execution path, contradicting the "never executes
untrusted code" boundary `diff_acquisition_v2` documents for itself when
it explains `--no-textconv` -- under the SAME threat model the worktree
exists to defeat, since anyone who can plant an untracked `.gitattributes`
can plant a hook.

Neither vector is reachable through the environment: Git has no
`GIT_HOOKS_PATH`, and repository-local `.git/config` is deliberately left
readable (above). Closed on the command line by `sealed_git_argv_v2`,
which splices `-c core.hooksPath=<os.devnull>` between `git` and the
subcommand, where Git requires its own `-c` options to appear -- verified
to override both the default `$GIT_DIR/hooks` lookup and a
repository-local redirect. Applied inside the shared runners as an argv
prefix rather than at each call site, for the same reason
`GIT_NO_REPLACE_OBJECTS` is an environment variable.

This is the concrete reason the round was re-run at all: a correction is
not self-validating, and reviewing only the ORIGINAL defect would have
shipped a fix that reintroduced a strictly worse instance of the class it
closed.

**P0 — the same class again, twice more, reproduced directly.** Applying
that same skepticism to the hook fix found two further target-controlled
execution paths in the identical checkout step:

- `filter.<driver>.smudge`/`.clean`/`.process`, from repository-local
  config, executes during the checkout `git worktree add` performs. Proven
  end-to-end through `acquire_diff_v2` in both directions. There is no `-c`
  closure here, because the driver NAME is chosen by whoever wrote the
  config and cannot be enumerated, and `--no-checkout` is not an
  alternative -- verified: it leaves the worktree empty, so attribute
  resolution stops working entirely, which is the worktree's whole purpose.
  Detected and refused instead (`diff_local_filter_config_active`), the
  same fail-closed shape already used for `$GIT_DIR/info/attributes`.
- `core.fsmonitor` holds a command Git executes to enumerate working-tree
  changes, reproduced running during `git status`. Closed with
  `-c core.fsmonitor=false`.

**Accepted operational cost, recorded rather than hidden.** The filter
refusal rejects any repository whose local config defines a filter driver
-- `git-lfs` being the common legitimate case. That is a deliberate
fail-closed choice in preference to executing a target-controlled command,
and it is the one place in this slice where the closure is strictly
narrower than "works everywhere". `filter.<driver>.required`, which carries
no command, is explicitly NOT treated as executable.

**P1 — the seal broke foreign-owned checkouts, reproduced directly.**
`GIT_CONFIG_GLOBAL=os.devnull` also discards the operator's
`safe.directory`, after which Git refuses outright (`fatal: detected
dubious ownership`) on any checkout owned by a different uid than the
running process -- the ordinary container/CI case, so the seal converted a
working deployment into total acquisition failure. The declared subject is
now named via `-c safe.directory=<repo_root>`; verified to admit exactly
that path while still refusing a DIFFERENT foreign-owned repository, so it
is not a blanket grant, and not a content-trust decision (the subject's
content stays hostile, which is what the rest of the policy is for).

**P1-B — ignore rules are not a source-identity authority, reproduced
directly.** `--exclude-standard` hid a stray `app/common/_stray_evil.py`
from the untracked-source check the moment a matching `.gitignore` line
existed. The check now enumerates all untracked paths (no
`--exclude-standard`) and applies its own explicit SOURCE_IDENTITY filter
(`.py` files, excluding `__pycache__` directory components --
TOOLCHAIN/EXECUTION_ENVIRONMENT, `toolchain_digest`'s subject) rather than
letting ignore configuration decide.

**P1-C — a deleted bounded path must not disappear from the proof,
reproduced directly.** A `.exists()` filesystem prefilter excluded a
bounded path deleted from disk (e.g. the CLI script itself) from the
pathspec `git diff` was even asked about. The full declared
`BOUNDED_SOURCE_RELATIVE_PATHS_V2` is now always passed unconditionally; a
genuinely wrong/empty `TOOLREPO_ROOT` is detected via `git ls-tree` against
HEAD, never filesystem existence.

**P1-D — CLI output must not mutate the target.** `--output <path>`
accepted an arbitrary destination and wrote there directly, so a caller
could request `--output <repo_root>/review.json` -- a mutation the prior
`HEAD^{tree}`-only oracle could not even detect. Removed entirely: the
canonical `ReviewReadinessV2` JSON now goes to **stdout only**; this CLI
never receives or interprets a destination path, so it cannot be pointed at
the target checkout. The black-box oracle itself is corrected to compare
`git status --porcelain=v1 -z -uall --ignored=matching` before/after,
which detects tracked modification, tracked deletion, a new untracked
file, and a new file a `.gitignore` entry would otherwise hide from
`git status` entirely -- proven to actually discriminate what
`HEAD^{tree}` alone would have missed.

**P2 — post-seal defect pinned at the composition boundary.** A genuine
pydantic `ValidationError` forced into `run_assembly_v2`'s post-seal
`ManifestV2` construction is proven to still escape `prepare_operational_
review_v2` raw, verified as a real falsifier (an isolated mutant adding
`except ValidationError` around the assembly call was applied, confirmed to
turn the raw error into `OperationalRunError(run_assembly_identity_
invalid)`, then reverted).

### Mutation/adversarial matrix (M1–M16), all executed and killed

| # | Condition | Killed by |
|---|---|---|
| M1 | blob replacement | `test_sealed_git_execution_v2`, `test_diff_acquisition_v2` |
| M2 | commit replacement | `test_sealed_git_execution_v2`, `test_toolrepo_identity_v2` |
| M3 | `GIT_DIR`/`GIT_OBJECT_DIRECTORY` injection | `test_sealed_git_execution_v2` |
| M4 | worktree `.gitattributes` add/modify | `test_diff_acquisition_v2` |
| M5 | `.git/info/attributes` active | `test_diff_acquisition_v2`, `test_sealed_git_execution_v2` |
| M6 | `.gitignore` hiding stray source | `test_toolrepo_identity_v2` |
| M7 | delete exact runner CLI path | `test_toolrepo_identity_v2` |
| M8 | delete bounded tracked `app/` source | `test_toolrepo_identity_v2` |
| M9 | CLI result path inside target | `test_operational_run_blackbox_e2e_v2` |
| M10 | ignored untracked file mutation | `test_operational_run_blackbox_e2e_v2` |
| M11 | sanitize post-seal `ValidationError` | `test_operational_run_v2` |
| M12 | target `post-checkout` hook during acquisition | `test_sealed_git_execution_v2`, `test_diff_acquisition_v2` |
| M13 | repository-local `core.hooksPath` redirect | `test_sealed_git_execution_v2`, `test_diff_acquisition_v2` |
| M14 | repository-local `filter.*.smudge` on checkout | `test_sealed_git_execution_v2`, `test_diff_acquisition_v2` |
| M15 | repository-local `core.fsmonitor` | `test_sealed_git_execution_v2` |
| M16 | foreign-owned checkout refused by the seal | `test_sealed_git_execution_v2` |

Every mutant: baseline green → mutation applied in an isolated commit →
confirmed to produce the intended failure → reverted → baseline
reconfirmed green. `M2`/`M3`/`M9` used Git-level or process-level
adversarial conditions rather than source mutations, since the property
under test is resistance to an external/ambient condition, not resilience
to a code change -- the discriminating requirement (baseline passes without
the condition, fails or is provably immune with it) is identical.

### CAEM alignment — recorded, not overclaimed

```yaml
caem_alignment:
  git_object_identity_bound: true
  git_interpretation_environment_bound: true
  replacement_objects_disabled: true
  attributes_source_bound: true
  ignored_source_not_authoritative: true
  target_mutation_oracle_complete: true
  target_hook_execution_closed: true
  target_filter_driver_execution_refused: true
  target_fsmonitor_execution_closed: true
  foreign_owned_checkout_supported: true

  formal_caem_f0_f2_conformance:
    established_by_this_slice: false
```

This does **not** claim Git itself is universally deterministic -- only
that the specific Git operations AgentReview's operational composer and
toolrepo identity authority depend on now execute under the bounded policy
this slice proves, on this host's actual Git (2.39.5). The `--attr-source`
gap is recorded as a known, deliberately-unclosed channel with a working
substitute (the disposable worktree), not hidden.

## What is established, and what is not

```yaml
established:
  toolrepo_as_product: true
  provider_free_operational_composition: true
  real_target_checkout: true
  real_diff: true
  immutable_head_bound_reference_material: true
  toolrepo_source_identity: true
  git_semantic_execution_closure: true
  content_redaction: true
  router_receipt_verification_and_binding: true
  payload_content_binding: true
  semantic_response_binding: true
  synthesis: true
  readiness: true
  cli_no_filesystem_output_authority: true

not_established:
  live_router: true
  provider: true
  live_pr_staleness_observation: true
  production_workflow: true
  agentescala_canary: true
  interleitos_canary: true
  target_pack_install: true
  release: true
```

`#19` — this slice operates on explicit immutable `base_sha`/`head_sha`
subjects. It does **not** observe whether a live GitHub PR moved during
execution; independent live-head observation and stale detection belong to
the live-canary grant.

## Scope fence

Not included: mutation of any existing workflow, replacement of
`github_agent_review.py`, `#203`, `#194`–`#198`, live Router, provider,
secrets, AgentEscala/InterLeitos, CT102/CT104, public schema, Ready, merge,
release/tag, deploy.
