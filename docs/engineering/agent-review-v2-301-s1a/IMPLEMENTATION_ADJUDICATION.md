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

---

## 9. Qualification round of `ad1d696` and proportional corrections (append-only)

```yaml
subject: ad1d69628ee52fbe291408df8c31ed1b60083c84
exact_head_ci: "run 36594040811: Validate repository SUCCESS, AgentReview release gates SUCCESS"
codex_exact_head: "Didn't find any major issues (reviewed commit ad1d69628e)"
independent_review: "0 MATERIAL, 5 MINOR, 1 NIT + wording NITs; C11 property of the code confirmed by manual audit of every §3.3 site and by a KeyboardInterrupt re-run of the sweep (14,707 runs, 0 violations)"
recurrence_in_code: none   # the C11 class did not recur in the code; two findings are gaps in its DISCRIMINATORS
disposition: proportional in-slice corrections on a new head; this section supersedes the sentences it names
```

| Finding | Class | Correction |
|---|---|---|
| F-A: an allocating registration written INSIDE a marked statement's target (`self._dirs.setdefault(k, slot).fd = os.open(...)  # fd-install`) passed the census, the sweep and the suite | C11 discriminator gap | census: the target must be a pre-evaluated owner expression (a `Name.attr…` chain ending in `fd`/`_roots`, or `made[<name>]`); sweep: a fault on a marked line is tolerated only at the opcodes the window may contain after the syscall (install: `LOAD_ATTR`, `STORE_ATTR`, `STORE_SUBSCR`; release: detach store, `os.close` call); the `setdefault` mutant is now killed by both |
| F-B: the census was a deny-list | C11 discriminator gap | allow-lists: imports, every `os.*` / `fcntl.*` attribute, `fcntl` commands, no builtin `open`, no dynamic `getattr` on those modules; the raw-syscall table may only name `renameat2`/`statx`; 9 bypass shapes as witnesses |
| F-C: `class X(Quiet, SealedType)` with a non-cooperative `__init_subclass__` escapes the definition-time seal | sealing wording | **§5 and §3.4 are corrected**: the seal is cooperative defense in depth; the load-bearing protection is admission by exact type (W, A) plus the minted-snapshot registry (Complete). Witness: the MRO-bypass subclass is still refused at admission |
| F-D: an interruption after the commit point followed by a second one during `settle` escaped without `physical_snapshot_outcome` | C10 wording / two-fault path | the entry point now remembers the outcome carried by the in-flight interruption and attaches it to a cleanup interruption; §3.1's "a committed snapshot is never hidden behind a cleanup failure" now holds for that path too (witness + mutant of the ad1d696 handler) |
| F-E: a second `abort()` answered False after residue | residue reporting (latent) | `abort()` becomes final only after a successful removal (then `created = False`); until then every call re-attempts and reports what it finds |
| F-F: ScandirIterator faults are clean because its C finalizer closes the dup | wording | **§3.4 is corrected**: "finalizers are never counted as release" applies to S1-A's Python-level owners; the `os.scandir` iterator is a C-level owner of its internal dup by declaration (§4 `also_declared`) |

Wording NITs, corrected here:

- The file opens also gained O_CLOEXEC. The claim in §3.2 is extended to them; behaviour is otherwise unchanged.
- On a `fd-release` line, `os.close` may raise OSError. It does so only after the kernel has released the descriptor, so ownership is not affected.
- `self._roots_released += 1` can allocate once there are more than 256 roots. That happens before the detach, so the descriptor is still owned. The §4 wording "no operation that can fail synchronously" is refined accordingly: nothing that can fail synchronously occurs **between the kernel-side creation or detach and the slot store or close**.
- The sweep gained a ninth shape (`openstage`: `staging/<id>` exists but was never opened, and abort removes it by name).
- Its baseline now checks that the descriptor handed out in Complete and Unconfirmed is the committed tree itself.

RED/GREEN for this round:

- On `ad1d696`, the F-D witness escapes with no carried outcome (`NoneType`), and the F-E witness answers `[True, False, False, True]`: a stale False while the residue exists.
- On the candidate, both are GREEN.
- F-A and F-B are witnessed on the discriminators themselves (mutant and bypass shapes), as the reviewer reproduced them on `ad1d696`.

---

## 10. Re-review of `0672d16`, maintainer adjudication and evidence-contract hardening (append-only)

```yaml
subject_reviewed: 0672d16e65e8be1ee4bd0b57c06fcb49a7d331e0
exact_head_ci: "run 36598493549: Validate repository SUCCESS, AgentReview release gates SUCCESS"
codex_exact_head: "Didn't find any major issues (0672d16e65)"
independent_re_review: "0 MATERIAL; 3 MINOR (N1, N2, N3); 3 NIT; windowless and KeyboardInterrupt sweeps: 0 violations on 9 shapes"
recurrence: "N1 recurs F-A, N2 recurs F-B, N3 recurs F-D -- in the QUALIFICATION layer (discriminators) and in claim wording, not in the production mechanism"
maintainer_adjudication:   # 2026-09-29
  production_mechanism: {status: PRESERVE_0672D16}
  third_production_redesign: {authorized: false}
  qualification_contract_hardening: {authorized: true}
  write_set: [tests/agent_review/test_physical_snapshot_v2.py, docs/engineering/agent-review-v2-301-s1a/IMPLEMENTATION_ADJUDICATION.md]
production_changes_this_round: none   # app/agent_review/*.py byte-identical to 0672d16
```

### 10.1 Superseded historical claims

These sentences stand in their sections as the record of what was claimed. They are **historical claims, superseded** by this section:

- **§3.4 and §9.** "Every descriptor source outside the vetted set fails" and "allow-lists" read as completeness of the static census over Python. That is superseded by §10.3.
- **§9, F-A row.** "Sweep: a fault on a marked line is tolerated only at the opcodes the window may contain after the syscall (… release: detach store, `os.close` call)". The 0672d16 implementation tolerated *any* call on a release-marked line (N1). That is superseded by §10.2.
- **§3.1 and §9, F-D row.** "A committed snapshot is never hidden behind a cleanup failure", and "now holds for that path too" read as universal. That is superseded by §10.4.

### 10.2 N1: marker semantics (`MarkerPresent != MarkerSemanticsSatisfied`)

A marker is not enough. Every marked line must be a statement of one admitted class, checked on the AST:

| Class | Admitted form | Marker | Tolerated opcodes after the syscall |
|---|---|---|---|
| install-call | `<pre-evaluated owner> = <acquiring call>` | `fd-install` | `LOAD_ATTR`, `STORE_ATTR`, `STORE_SUBSCR` |
| install-store | `<chain>.fd = <name>` (slot move), `<name>.created = True` | `fd-install` | `LOAD_ATTR`, `STORE_ATTR` |
| detach | `<chain>.fd = None`, `<name>._roots_released += 1` | either | `LOAD_ATTR`, `STORE_ATTR`, `BINARY_OP` |
| close | `os.close(<name>)` | `fd-release` | `LOAD_GLOBAL`, `LOAD_ATTR`, `LOAD_METHOD`, `CALL` |
| invalid | anything else on a marked line | — | none, and it is a census violation |

A CALL is tolerated only on the `os.close(<name>)` statement itself.

The N1 mutant is an allocating call between the detach and the close, carrying `fd-release`. It is killed by both intended discriminators:
- the census: "marked statement is not an admitted window statement class";
- the sweep: a leak at the `append` CALL on an `invalid` statement.

RED/GREEN with the same mutant:
- the 0672d16 discriminators accept it (census `[]`, sweep 0 violations);
- the candidate's discriminators report the census violation and 6 leak violations.

The classifier's own anti-vacuity is shown by one example of each admitted class being recognised, and by wrong-marker and wrong-shape statements being rejected.

### 10.3 N2: what the static census is (and is not)

```text
Static census        = enforcement of the canonical source forms admitted by S1-A
Behavioral sweep     = evidence over actually exercised qualification paths
Independent review   = novelty / escape detection
StaticCensusCanonicalCoverage != CompleteSemanticCoverageOfDynamicPython
```

Cheap guardrails were added against the families observed. Module handles (`os`, `fcntl`, `ctypes`, `sys`) may appear only as `mod.attr`. `os`/`fcntl` functions may appear only as the callee of a direct call. `sys`/`ctypes` attributes are allow-listed. The libc handle, including every name bound from `ctypes.CDLL(...)` or from another handle, may be used only as `.syscall`. There is no attribute access on an unnamed `CDLL(...)`, and no `vars`/`globals`/`locals`/`eval`/`exec`/`compile`/`__import__`/`open`.

The 8 alias families from the re-review are:
- `_os = os`
- `_open = os.open`
- `sys.modules[...]`
- `vars(os)`
- an extra libc handle
- `ctypes.CDLL(None).open`
- `opener = fcntl.fcntl`
- `[os.open][0]`

The 0672d16 census accepted 8 of 8; the candidate census accepts 0 of 8.

```yaml
limitation: PYTHON_DYNAMIC_INDIRECTION_OUTSIDE_STATIC_CENSUS
statement: >
  forms of dynamic Python indirection beyond the canonical grammar (e.g.
  reflection through the type system: object.__subclasses__, __dict__,
  __getattribute__, fileno of a foreign object, importlib, mmap) are outside
  the static census's completeness claim; they are named, not enforced.
guard_on_exact_source: >
  this limitation never makes a bypass in the exact production source
  acceptable: the exact source must stay inside the canonical grammar
  (census = []), and it is asserted to contain none of the named
  outside-form markers.
witness: test_n2_dynamic_indirection_outside_the_static_census_is_declared_not_claimed
```

### 10.4 N3: qualified fault domain

Refined claim, which replaces the universal sentences listed in §10.1:

```text
Within the declared single-fault synchronous/allocation model and the
explicitly exercised asynchronous-interruption paths, a committed
publication is not hidden by cleanup failure.
```

Exercised asynchronous paths:
- a single KeyboardInterrupt before every executed fault site, in the 9 publication shapes (`test_c11_every_asynchronous_interruption_site_leaves_no_unowned_descriptor[*]`);
- an interruption at the commit point followed by one during cleanup (`test_commit_interruption_then_one_cleanup_interruption_carries_the_outcome`).

```yaml
outside_qualified_fault_model: MULTIPLE_ASYNC_INTERRUPTION_DURING_CLEANUP
boundary_counterexample: >
  test_n3_returned_outcome_then_two_cleanup_interruptions_is_outside_the_qualified_fault_model
  -- a RETURNED Complete outcome, then two interruptions during cleanup:
  committed/<id> exists and no exception in the chain carries an outcome.
  Kept reproducible, labelled OUTSIDE_QUALIFIED_FAULT_MODEL; nothing is
  inferred from it, and no assertion in the suite claims the contrary.
also_declared: >
  an interruption injected into a finalizer is swallowed by CPython
  ("exception ignored"); the sweep collects it via sys.unraisablehook and the
  census still decides whether any descriptor was affected (none was).
```

### 10.5 Evidence of this round (before the exact-head gates)

```yaml
no_tolerance_sweeps:
  MemoryError: {runs: 15673, raw_violations: 293, outside_an_admitted_window_statement: 0}
  KeyboardInterrupt: {runs: 15673, raw_violations: 293, outside_an_admitted_window_statement: 0}
suite_s1a_plus_c2a: "301 passed, 1 skipped (mknod), with -W error::PytestUnraisableExceptionWarning"
qualification_mutants_new: [N1 census, N1 sweep, N2 x8 families, classifier anti-vacuity]
```

The exact head, CI, independent review and Codex review of this round are recorded in PR #361 under that head (a document cannot name its own commit). Terminal rule, per the maintainer: 0 production material findings, 0 declared-domain invalidations, a causal N1 discriminator, calibrated N2/N3 claims, green CI, and no material finding from the reviewer or Codex together give `301_S1A_STRUCTURAL_REDESIGN_CANDIDATE`. A new material refutation of the code or the domain gives `STOP_NEW_FAILURE_CLASS`.
