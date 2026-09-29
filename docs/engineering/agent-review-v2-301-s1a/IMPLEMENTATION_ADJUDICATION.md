# #301 S1-A — Implementation Adjudication (append-only)

```yaml
repository: mglpsw/aiops-orchestrator
owner_issue: 301
slice: S1-A
pull_request: 361 (Draft)
freeze: docs/engineering/agent-review-v2-301-s1a/ARCHITECTURE_FREEZE.md
freeze_blob: 6e89a64b1a0f4489ab1419189eb2dbf4adbf384b   # preserved; not rewritten
relation_to_freeze: >
  append-only refinement of the implementation contract of S1-A. The freeze
  stays the architectural authority; this record adds the maintainer's
  adjudications after the ba3800e STOP. Where it refines a freeze claim
  (C11), it does so explicitly and names the refined text.
mode: append-only   # later rounds add sections; earlier sections are never edited
```

Chain recorded here, in order:

```text
ba3800e STOP
→ recurrence evidence
→ structural redesign decision
→ refined C11 limitation
→ capability sealing decision
→ duplicate-occurrence semantics
```

---

## 1. ba3800e STOP

```yaml
trigger: STOP_REDESIGN
reason: recurrent_descriptor_ownership_failure_class
stopped_head: ba3800e14d7156a3953a959d5670f4e229fd61ab
base_at_stop: 3aab1aad68167673b46c68639c4a3600d70195ee
exact_head_ci_at_stop: "run 36573101247: Validate repository SUCCESS, AgentReview release gates SUCCESS"
codex_exact_head_findings_at_stop:
  "4133910362": "_StagingWriterV2.__init__: _manifest['.'] inserted after mkdir -> staging/<id> may stay unreported (C11 class)"
  "4133910374": "admit/_register: descriptor exists before its owner is registered (C11 class)"
  "4133910390": "publish admits W by isinstance: a subclass skips the sentinel and hands S1-A caller-chosen descriptors (new class: capability forgery)"
PR_state: remains_Draft
successor_PR: not_required
maintainer_adjudication: "STOP válido; não fazer novos patches site-by-site"   # 2026-09-29
```

The STOP was raised by the author and upheld by the maintainer. No source
change was made between the STOP and this adjudication.

## 2. Recurrence evidence

The same obligation failed three rounds in a row: the freeze's C11
(`todo descriptor tem exatamente um dono de cleanup em todo ponto`).

| Round | Head | What the fix claimed | What came back |
|---|---|---|---|
| 1 | `e46606f` | independent F4: "every dup has an owner before any failure" | — |
| 2 | `6c7ee72` | F4 applied | Codex `4133784308`: the result tuple of `duplicate_authorized_roots` was built outside the cleanup scope |
| 3 | `ba3800e` | `4133784308` fixed | independent focal review: `child`/`admit`/`_register`, `ensure_dir`, the writer constructor, `_PublicationContextV2`, `take_stage_fd`, `from_directory_fd`, `_duplicate_publication_fds` + caller unpack; Codex `4133910362`, `4133910374` |

Recurrence answers: candidate formed — yes; admitted recurrence — yes (same
proposition, same witness shape: an allocating or fallible step between the
creation of a resource and the registration of its owner); outside scope — no.

Mechanical confirmation, gathered after the redesign with the candidate's own
harness (§3.4) run unchanged against the `ba3800e` source:

```yaml
harness: C11 fault-injection sweep (one synchronous fault per executed fault site)
subject: ba3800e (identical app/ and tests/ to its rebased form d41e13b)
scenarios: 8
violating_runs: 1308
distinct_violating_sites: 125      # (function, line, kind)
distinct_functions: 25
focal:
  "4133910362": "_StagingWriterV2.__init__:1178 -> staging residue without any outcome (x32)"
  "4133910374": "admit:964, _register:886-887 -> leaked descriptors"
  others_found: [child, release, close, ensure_dir, write_file, finalize_subdirectories,
                 open_stage, probe_dotgit, read_optional_pointer, read_listed_object,
                 check_listed_candidate, abort, _remove_children_v2,
                 _duplicate_publication_fds, duplicate_authorized_roots,
                 publish_physical_snapshot_v2 (context construction, finally),
                 _OwnedDescriptorV2/_PublicationContextV2 constructors]
```

The six reported siblings were a minimum corpus, as the maintainer stated; the
sweep found the class at 25 functions.

## 3. Structural redesign decision

### 3.1 Rule

```text
owner state exists
→ owner registered
→ resource syscall
→ fd installed into the pre-existing empty slot   (same statement as the call)
```

- Every object that may own a descriptor exists, and is registered where it
  will be found by cleanup, before the acquisition syscall.
- No `list.append`, `dict[...] =`, owner construction or other potentially
  allocating bookkeeping is needed between acquiring a descriptor and it
  having a responsible owner.
- Transfer is a slot move between two owners that both already exist
  (`_FdSlotV2.move_to`), never "release old owner → allocate/register new owner".
  The destination owner (`PublishedSnapshotV2`, `CommittedSnapshotResidualV2`)
  and the whole outcome wrapping it are built first; the move is the last step.
- Release detaches the descriptor from its slot, then closes it: at most once.
- A local owner is released explicitly on the normal path; its `finally` is
  the failure path only.
- Cleanup is idempotent and resumable; an interrupted cleanup step is retried
  once (`_retry_once_v2`), and each settle step runs even if an earlier one
  was interrupted.
- An exception that escapes after an outcome exists carries it
  (`physical_snapshot_outcome`): a committed snapshot is never hidden behind a
  cleanup failure (C10).

### 3.2 Mechanism

| Element | Role |
|---|---|
| `_FdSlotV2` (`__slots__ = ("fd",)`) | the single primitive; `move_to`, `close_once`, `close_quietly`; finalizer is a last resort that no normal path relies on |
| `_SharedFdSlotV2` | same primitive with lock-serialized read/close, for descriptors handed to a caller |
| `_AdmittedDirV2(_FdSlotV2)` | registered in the session before its open |
| `_SourceSessionV2` | built empty; `acquire_roots` installs the root duplicates as one unit; `close()` drains without detaching the registry first; root release by a monotonic counter |
| `_StagingWriterV2` | constructor makes no syscall; directory slots registered before `mkdir`/`open`; `created` recorded by the statement after `mkdir`; `abort()` idempotent and resumable |
| `_PublicationRunV2` | one owner tree per call, built before the first syscall; `settle()` on every exit path |
| `duplicate_authorized_roots` (C2_A, additive) | pre-sized `made` list is the owner; protected region covers the lock release and the return |
| S1-A-local `_open_source_dir_into_v2` / `_open_source_file_into_v2` | acquisition primitives that install straight into the caller's slot |

Consequence for D-PRIVATE-IMPORT: S1-A no longer imports
`_open_regular_file_no_follow_v2` or `_try_open_dir_no_follow_v2`, because both
return a bare descriptor after further fallible steps, which is the "resource
before owner" shape. The observable rules are kept: no-follow, O_NONBLOCK plus
fstat on the same fd, O_NONBLOCK cleared only once S_ISREG holds, and the same
reason-code mapping. O_CLOEXEC is added to directory opens. This also removes
the deviation previously disclosed for `_try_open_dir_no_follow_v2`. G1C is
unchanged.

### 3.3 Structural census (whole write-set)

Every acquisition, transfer and release site in `physical_snapshot_v2.py`,
plus `AuthorizedGitStorageSetV2.duplicate_authorized_roots`. Every other
descriptor-producing primitive is absent, and the static census test enforces
that.

| Site | Resource | Owner that exists first |
|---|---|---|
| `SnapshotPublicationRootV2.from_directory_fd` | root dup, `staging`, `committed` | the W instance's three slots |
| `SnapshotPublicationRootV2._duplicate_publication_fds_into` | staging/committed dups | the run's two slots |
| `_SourceSessionV2.acquire_roots` | root duplicates (tuple) | the session (`_roots`, released by counter) |
| `duplicate_authorized_roots` | each root dup | pre-sized `made` |
| `_SourceSessionV2.admit` (root) | dup of a root | pre-registered `_AdmittedDirV2` |
| `_SourceSessionV2.admit` (components) | each successor | local `successor` slot, then moved |
| `_SourceSessionV2.child` | child dir | pre-registered `_AdmittedDirV2` |
| `_SourceSessionV2.probe_dotgit` | `.git` | pre-registered gitdir `_AdmittedDirV2` |
| `read_optional_pointer` / `read_listed_object` / `check_listed_candidate` | source file | local slot |
| `_scan_directory_v2`, `_remove_children_v2` listing | `os.scandir` internal dup | the C-level iterator (created with its dup), `with`-owned |
| `_StagingWriterV2.create_stage` | `staging/<id>` directory (cleanup obligation) | the writer (`created`) |
| `_StagingWriterV2.open_stage` | stage dir fd | `writer.stage` |
| `_StagingWriterV2.ensure_dir` | subdirectory fd | slot registered in `_dirs` before `mkdir` |
| `_StagingWriterV2.write_file` | destination file | local slot |
| `_remove_children_v2` / `_remove_tree_by_name_v2` | cleanup-time dir fds | local slot |
| `_post_commit_v2` / `_unconfirmed_v2` | stage fd → caller | `PublishedSnapshotV2` / residual built first; `move_to` last |

Out of S1-A scope, recorded: `AuthorizedGitStorageSetV2.from_roots` appends
each opened root to `open_fds` after the open. That is pre-existing C2_A code,
not introduced by S1-A (S1-A only adds a line after the registration); owner
#354/#331. The internals of the G1C helpers are no longer on any S1-A path.

### 3.4 Mechanical discriminators

1. **Static census** (`test_c11_static_census_every_acquisition_installs_into_a_pre_existing_owner`).
   The AST check requires every `os.open`, `fcntl(F_DUPFD_CLOEXEC)` and
   `duplicate_authorized_roots()` to be the entire right-hand side of a
   single-target assignment into an owner (`<slot>.fd`, `made[...]`,
   `self._roots`) on a line marked `fd-install`. `os.scandir` may appear only
   as a `with` context. `os.mkdir` must be followed by its marked
   installation. `os.close` may appear only on lines marked `fd-release`
   inside the release primitives. Any other descriptor source fails the check.
2. **Fault-injection sweep** (`test_c11_every_synchronous_fault_site_leaves_no_unowned_descriptor[*]`,
   `test_c11_publication_root_factory_fault_sites_leave_no_unowned_descriptor`).
   Under opcode tracing of S1-A frames, one synchronous fault is injected
   before an executed fault site: the first and the last occurrence of every
   distinct site, one run per point, across 8 publication shapes (complete,
   bare root, `.git` dir, budget refusal mid-copy, rename collision,
   post-commit sync failure, rename-error-but-committed, unobservable) plus
   the W factory. Each run must meet all of these:
   - no descriptor is left unreleased. This is measured while the exception
     and its frames are still alive and with the cyclic GC off, so a
     finalizer running cannot count as a release;
   - no pre-existing descriptor is closed or replaced;
   - no close hits an unowned number (EBADF);
   - the outcome is truthful: Complete means committed and not staged;
     NotPublished means not committed and any residue is reported; Unconfirmed
     means committed; an exception without an outcome means nothing committed
     or staged.

   The only tolerated exception is a fault on a marked line (§4).
   Candidate result: 0 violations in about 14,700 runs.

Fault model, declared:

- Fault sites are every opcode except a fixed set that cannot raise
  synchronously in CPython 3.11 on the values S1-A uses. The set is
  `_NONFAILING_OPCODES_V311`: loads and stores of locals and constants, stack
  shuffles, jumps, identity tests, `PRECALL`, `RETURN_VALUE`, exception-state
  opcodes.
- The normal-exit `__exit__(None, None, None)` call of a `with` block is
  excluded. Every context manager S1-A uses is C-implemented
  (`threading.Lock`, the `os.scandir` iterator). Injecting a fault there
  models a lock that is never released, which is an artifact of the model and
  not a CPython behaviour. The sweep's first run against the new code found
  exactly this artifact as a deadlock, and it was excluded by name, not
  hidden.
- The sweep also found two real classes, both fixed structurally rather than
  site by site:
  - a fault in `settle()` after the outcome existed lost the outcome of a
    committed snapshot and stranded the rest of the cleanup. Fixed by
    resumable steps, retry-once, the nested `finally`, and outcome attachment;
  - a local owner released only in `finally` stayed pinned to a
    traceback-held frame. Fixed by the normal-path release rule.

Mutation and anti-vacuity:

| Mutant (reintroduced shape) | Intended discriminator | Result |
|---|---|---|
| resource before owner (`child` opens, then builds/registers the owner) | sweep: leak at the owner construction/registration | killed |
| resource before owner (bare `os.open` into a local) | static census: "not installed into a pre-existing owner slot" | killed |
| registration after release (`_post_commit_v2` clears the writer slot, then allocates the snapshot) | sweep: leak at the new owner's construction | killed |
| close outside the release primitives | static census | killed |
| `isinstance` instead of exact type at the W boundary (the ba3800e admission) | forged W (`__class__` spoof; unminted exact-type instance) writes into the victim directory | killed |
| `isinstance` instead of exact type at the A boundary | subclass / spoofed A publishes a directory A never authorized | killed |
| subclass override of the publication capability (seal removed) | definition-time refusal of a subclass | killed |
| `CompletePublicationV2` without the minted-snapshot registry | unminted `PublishedSnapshotV2` accepted | killed |

RED/GREEN with one harness (the candidate's), run unchanged on both trees:
`ba3800e` shows 1308 violating runs, 125 sites, and the subclass-W witness
**accepted** (victim `committed/` written). The candidate shows 0 violating
runs, and the subclass is refused when defined.

## 4. Refined C11 claim and limitation

Refined claim (replaces the universal of freeze §14 `S1A_C11_DESCRIPTOR_OWNERSHIP.proposition`):

```text
for the synchronous and allocation failure paths covered by the observable
Python domain, every FD received by S1-A code is bound to exactly one cleanup
owner before any subsequent operation able to fail, and is closed at most once.
```

```yaml
limitation: PYTHON_FD_OWNERSHIP_INSTALLATION_WINDOW
statement: >
  An asynchronous / interpreter-level interruption (signal handler exception,
  async exception, interpreter teardown) landing between the kernel-side
  creation of a descriptor and its installation into the Python slot -- or
  between detaching a descriptor from its slot and the close(2) -- receives
  no universal close claim in pure Python.
where: >
  exactly the statements marked `fd-install` / `fd-release` in the write-set
  (census §3.3); they contain no operation that can fail synchronously in
  CPython 3.11, so only an asynchronous interruption can land there.
not_promoted_to_success: true
no_positive_inference: "no positive result is inferred from that path"
impact: lifecycle / availability (a descriptor, or an unreported staging/<id>, may leak)
not_impact: "source confinement (C1) and publication truth (C10) are not claimed broken by it without further evidence"
also_declared:
  - "the fault model is CPython 3.11 specific (opcode set, with-exit shape); CI runs 3.11"
  - "C-implemented context-manager exits are modelled as non-failing (§3.4)"
  - "finalizers are a last resort, never counted as release by the discriminator"
  - "the ScandirIterator's internal dup is owned by the C iterator from creation"
```

## 5. Capability sealing decision

`Codex 4133910390` is ESTABLISHED. For the S1-A types that carry authority or
qualified state, the mechanism is exact-type admission, plus no subclass
polymorphism, plus the factory/sentinel invariant.

| Type | Sealed (no subclass) | Sentinel | Factory registry | Admission |
|---|---|---|---|---|
| `SnapshotPublicationRootV2` | yes | yes | yes (minted by `from_directory_fd`) | `type(W) is SnapshotPublicationRootV2` **and** registered |
| `PublishedSnapshotV2` | yes | yes | yes | `CompletePublicationV2` requires exact type **and** registered |
| `CommittedSnapshotResidualV2` | yes | yes | — | `UnconfirmedPublicationV2` requires exact type |
| `PublicationResidualV2`, receipt, binding | yes | — | — | exact type where carried |
| `CompletePublicationV2`, `UnconfirmedPublicationV2`, `IndeterminatePublicationV2`, `NotPublishedV2` | yes | — | — | outcome identity is the type |
| `AuthorizedGitStorageSetV2` (A) | **no** (C2_A unchanged) | — | — | S1-A ingress: `type(A) is AuthorizedGitStorageSetV2` |
| `SourceRepositoryLocatorV2`, `PhysicalWorkBudgetV2`, `DeclaredGitObjectFormatV2` | no | — | — | exact type at ingress |

- `ExactType(A) != Provenance(A)`. The exact-type check at the S1-A ingress
  closes forged-polymorphic A for this consumer. Who produced A remains
  C2_B/#331.
- `isinstance` also trusts a spoofed `__class__`, so every boundary admits by
  `type(x) is T`.
- For W, exact type is subsumed by the registry against the spoof witness. It
  stays as defense in depth and is not claimed as separately discriminated.
- Boundary of the claim: deliberate in-process object surgery is
  same-process tampering and is outside what Python can enforce. Examples are
  `object.__setattr__` on a frozen outcome, `gc`/`ctypes`, and editing the
  private registries. Admission of a `PublishedSnapshotV2` by a future
  consumer is that consumer's boundary (S1-B).
- The binding and receipt of a published snapshot and of a residual are
  read-only properties.

## 6. Duplicate-occurrence semantics

```text
duplicate physical occurrence
→ charge entries_scanned
→ do not reacquire
→ do not copy
```

- First wins (§10) is preserved.
- `symlink_rejected` applies only to a candidate occurrence that would be
  materially opened or acquired. That includes an incomplete pack pair's
  object-looking name (Codex `4133784323`, still classified by a charged
  no-follow open).
- An already-deduplicated occurrence is not followed, not copied and does not
  widen authority. So it needs no open just to learn its filesystem type:
  `SkippedDuplicateOccurrence → no filesystem-type claim`.
- Budget and traversal are unchanged. Witness:
  `test_skipped_duplicate_occurrence_is_not_reacquired_even_as_a_symlink`
  shows no open of the duplicate, and inotify sees no event on the outside
  target.

## 7. Base reconciliation

```yaml
previous_base: 3aab1aad68167673b46c68639c4a3600d70195ee
reconciled_base: c915e2ef3b591395ec076e9dfca9a91ce3f9e1d7
delta: docs/CROSS_REPO_DISSOLUTION_MATRIX.md (#351), adjudicated orthogonal to S1-A
method: rebase of the three S1-A commits, then the redesign on top
freeze_blob_after_rebase: 6e89a64b1a0f4489ab1419189eb2dbf4adbf384b
prior_qualification: historical (every earlier exact-head result is stale)
```

## 8. Qualification of the redesign candidate

A document cannot name the commit that contains it. The exact head, CI run,
independent review and Codex exact-head review of this candidate are recorded
in PR #361, append-only, under that head. What this section fixes is the
required sequence:

```text
new exact head → focal countermodels → recurrence-family census (static + sweep)
→ capability-forgery countermodels → mutation/anti-vacuity (§3.4) → positive controls
→ C2_A/C3/C4-Q regressions → full applicable suite → exact-head CI
→ independent reviewer → Codex exact-head novelty review
```

Out of scope, unchanged: S1-B, `S1_CTX_01`, S1-C, S1-D, S_D, E, #298, #314,
#350; #301 is not concluded. No Ready, merge, release, deploy or provider
action.
