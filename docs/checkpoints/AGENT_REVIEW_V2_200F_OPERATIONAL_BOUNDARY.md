# AgentReview v2 — `#200-F` derivable operational boundary + explicit review scope

**Parent:** `#200`
**Branch:** `feat/200-f-derivable-operational-boundary`
**Base:** `f70af2e635643d1ee96ba431857002ae079b502b` (tree `945f3247a9e8ad534a0d35f4450b24446906f30c`)
**Class:** implementation in progress. Nothing in this document is qualification.

## Preflight (§1) — recorded, all axes passed

```yaml
origin_master_sha:  f70af2e635643d1ee96ba431857002ae079b502b   # == expected
origin_master_tree: 945f3247a9e8ad534a0d35f4450b24446906f30c   # == expected

pr_276:
  state: CLOSED
  merged: false
  head: 6712cddd37c7e1794a60437af0a634b520eee283
  classification: FROZEN_FORENSIC_OPERATIONAL_BOUNDARY   # marker present in body

issue_200: OPEN

competing_200f_implementation: none
  open_prs: [275]            # fix/agentreview-v1-u2-result-coverage-truth -- v1, unrelated
  remote_branches_matching_200-f|boundary|scope|refusal: none

worktree: /opt/agent-tools/ar-200f-boundary   # fresh, created from master
branch_reuse: false
worktree_reuse: false
qualification_transferred: false
```

## Base-state finding that shapes the whole slice

`master` carries **no operational composer and no product CLI**. Neither
`app/agent_review/operational_run_v2.py` nor `scripts/aiops-review-run-v2.py`
exists at `f70af2e6`. Both were `#274`/`#276` branch artifacts and both died
with those branches.

Consequence: `#200-F` is not a repair of a merged product boundary. It is the
**first** boundary to be built on top of the 75 merged `app/agent_review`
modules. There is no legacy CLI to stay compatible with, so the four
authorities can be primitive rather than retrofitted.

## Port ledger (§2)

Nothing below is qualified by having survived `#276`. Every ported item is
reconstructed selectively from `master` and requalified with new tests.

### `PORT_WITH_REVALIDATION`

| Artifact | Why it survives | Requalification owed |
|---|---|---|
| `ControlledTargetSubjectV2` | source severance held: target source deleted mid-run, artifact byte-identical | new severance + nonmutation tests |
| `ToolrepoExecutionSubjectV2` | committed-byte identity held under forgery attempts | new raw-byte identity test |
| bounded child environment | allowlist env, `os.defpath` pinning, absolute `argv[0]` | new `PATH`/`GIT_*` fake-binary corpus |
| fixed executable/Git resolution | round-2 `PATH` P0 genuinely closed | planted fake `git` never invoked |
| controlled reference material | reference bytes read from the controlled subject, not the live target | new reference-identity test |
| one-synthesis invariant | object identity (`is`) genuine and non-vacuous | new non-interned-value proof |

### `PORT_AS_CONCEPT`

| Concept | Note |
|---|---|
| two-process outer/inner architecture | re-derived; the *channel* between them changes (§5) |
| operational composition order | re-derived from new boundaries, not copied |
| stdout-only readiness product contract | preserved |

### `PORT_AS_RED_TESTS`

The full `#274` + `#276` adversarial corpus is carried as *failing-first*
tests, not as passing inheritance. Enumerated in §15 of the grant; tracked in
this document's red-corpus matrix as it is built.

### `DO_NOT_PORT_AS_AUTHORITY`

| Rejected mechanism | Falsified how |
|---|---|
| CLI exception tuple | needed a 3rd extension after 2 bounded rounds |
| `test_cli_except_tuple_is_complete_by_construction` | **green** while `--delivery-id 'bad id here'` leaked a raw traceback it structurally could not see (enumerated 74/95 `reason_code` classes; `pydantic.ValidationError` is neither) |
| private authority-bearing `--_` argv flags | textual guard bypassed by `argparse` unambiguous abbreviation (`--_inner-d`) |
| textual private-flag blacklist | blacklist over a prefix-matching parser |
| `argparse` last-wins ordering as a defence | ordering is not authority |
| hand-written "unreachable exception" justifications | one such justification was **factually false** and the control passed over it |

### `DO_NOT_PORT_AS_PRODUCT_POLICY`

| Rejected policy | Why |
|---|---|
| `if assembly.excluded_paths: raise` | denies review outright for pure renames, chmod-only, binaries, lockfiles, empty-file adds |
| `operational_run_scope_silently_narrowed` reason code | misnomer: nothing was silently narrowed, the composer refused |

## Authorities this slice must establish

```yaml
A: derivable operational refusal            # replaces the exception tuple
B: exclusive outer -> inner authority channel  # replaces private argv flags
C: explicit changed-scope authority         # replaces abort-on-excluded-path
D: range-aware result binding               # synthesis must not be the discoverer
E: quoted assignment secret redaction       # HARD pre-canary blocker
```

`A`-`E` must survive adversarial qualification **before** the operational
product is reconstructed (§11-§14).

## Protected actions — not taken under this grant

```yaml
ready: false
merge: false
release: false
deploy: false
workflow_mutation: false
live_router: false
provider: false
target_repo_mutation: false
close_200: false
change_273: false
```

---

# Implementation record

## Authorities built

| # | Authority | Module | Status |
|---|---|---|---|
| A | derivable operational refusal | `operational_refusal_v2.py` | built, mutation-qualified |
| — | pre-seal public input (§4) | `operational_ingress_v2.py` | built, mutation-qualified |
| B | exclusive outer→inner channel | `operational_inner_control_v2.py` | built, mutation-qualified |
| C | explicit changed scope | `operational_scope_v2.py` | built |
| D | range-aware result binding | `operational_result_binding_v2.py` | built, mutation-qualified (both paths) |
| E | quoted assignment redaction | `redaction.py` | built, mutation-qualified |
| — | bounded git | `operational_bounded_git_v2.py` | built |
| — | controlled subjects (§11) | `operational_subject_v2.py` | built |
| — | composition (§12/§13) | `operational_run_v2.py` | built |
| — | product CLI (§14) | `scripts/aiops-review-run-v2.py` | built |

## §8 verdict — `STOP_SCOPE_CONTRACT_REQUIRED`

The published `agent-review.review-readiness.v2` contract cannot express
"fragment review complete, total changed scope incomplete" without semantic
distortion. All four candidate routes are **structurally accepted** by the
contracts and semantically false; the spike executes each rather than arguing
about it. ADR: `docs/adr/ADR-200F-SCOPE-COMPLETENESS-CONTRACT.md`.

This blocks **emission only**. The private scope authority still prevents a
false `ready`, so the rest of the slice proceeded.

## Soundness traps found while building the controls

Recorded because each would have produced a control that looked green while
being blind — the exact `#276` failure mode.

| Trap | Consequence if unnoticed | Resolution |
|---|---|---|
| `cls.__module__` is mutable; `_target_pack_epoch_contract_v2.py:50` rewrites it | source lookup reads the wrong file, reports a false violation | resolve definitions from an AST index; fail loudly on an ambiguous name |
| enumeration by instantiation probe | `#276` saw 74/95 classes and called it complete | read the AST, not a constructed instance |
| `git checkout --` on an **untracked** file silently fails | four mutations accumulated; each "discriminator" was measuring the previous mutation too | commit before mutating, always |
| naive import insertion landed inside a parenthesised import | mutation produced an ImportError, not a discriminator — 4 failures that proved nothing | AST-anchored insertion + an explicit import check before running the suite |
| >64 KiB write to a pipe with no reader | test deadlocked | deliver oversized payloads through a file descriptor |
| poisoned `GIT_*` broke the *fixture helper*, not the product | 3 false failures | read fixture state before poisoning |

## Mutation matrix

| Mutation | Intended discriminator | Result |
|---|---|---|
| drop marker from `DiffAcquisitionError` | A1 | RED |
| claim membership without `reason_code` | A2 | RED (exactly 1 failure) |
| undispositioned non-member on product path | A3 | RED (exactly 1) |
| ingress stops translating `ValidationError` | round-4 witness | RED (9) |
| drop subject-root check | root mismatch | RED |
| drop subject digest check | TOCTOU | RED |
| accept smuggled extra keys | document shape | RED |
| add environment fallback | exclusivity | RED |
| remove line-range validation | `#276` P0 | RED (8) |
| skip ranges on Router path only | both-paths rule | **survived**, then RED after the gap was closed |
| restore quote-excluding value class | E witness | RED (16) |
| drop the colon rule | YAML/JSON leak | RED (4) |
| greedy quoted value | multi-secret | RED |

One survivor, recorded rather than explained away: the Router-path mutation
survived the first round because every authority-D test drove the offline
path. A surviving mutant is a coverage statement, so a Router-path test was
added and the mutation re-run.

## Deliberately not claimed

```yaml
bootstrap:
  remotely_attested: false
```

The outer bootstrap necessarily executes from the ordinary checkout before any
subject is sealed, so an untracked module planted beside it can still run
first. The **inner** is not exposed to this: it executes from a subject
materialised from committed bytes, where an untracked shadow cannot exist.
Closing it for the outer requires an attested launcher this slice does not
build.

`declared_toolrepo_sha` is bound to the *bytes* of the subject by digest, and
no argv or environment route can express it. Someone who can already run
arbitrary code on the host can run their own copy of the product; that is not
the boundary, and is not claimed to be.

## Protected actions — still not taken

```yaml
ready: false
merge: false
release: false
deploy: false
workflow_mutation: false
live_router: false
provider: false
network_required: false
target_repo_mutation: false
close_200: false
change_273: false
```

---

# Round 1 — three-lane adversarial review

Subject `0bc8a5c2` / tree `59223d66`. Exact-head CI was green on both jobs.
**All three lanes returned `BLOCK`.** Every P0 and P1 was independently
reproduced by the coordinator before any code changed.

## The finding that mattered most

```yaml
severity: P0
lane: B
claim: a changed path git allows but RelativePath rejects reaches `ready`
```

`src/pages/[id].tsx` — an everyday Next.js/SvelteKit route — was classified
`reviewable`, silently excluded from `expected_files` by the assembly, never
reviewed, did not make scope incomplete, and the composer emitted **`ready`**.

This is **strictly worse than the predecessor**. `#276` over-refused, which is
the safe direction, and its one preserved property was that no false-`ready`
path existed. This slice produced one.

Root cause: `diff_acquisition_v2` decides representability with four
conditions; `operational_scope_v2` reimplemented three. The omitted condition
was a nested closure invisible outside its own function.

## Findings and disposition

| Lane | Sev | Finding | Disposition |
|---|---|---|---|
| B | P0 | false `ready` via unrepresentable path | shared predicate + disagreement detector |
| A | P0 | inner authority forgeable by **narrowing** `subject_root` | every loaded semantic module must be inside the digested root |
| A | P0 | `--profile`/`--grouping-policy` parsed post-seal; secret echoed | documents routed through the ingress authority |
| C | P1 | authority E leaked 6 shapes; 3 claimed `redacted` while leaking | keys matched by pattern; value suspect unless benign |
| B | P1 | git type change denied the whole review | delete+add pair dispositioned as one type change |
| A | P1 | product-path closure blind to 6/8 `operational_*` | closure derived from the CLI entry point |
| A | P1 | A3 exemption proved too little | narrowed to underscore-named; the public one joined the family |
| A | P1 | bare `int()` on control-fd env var | parsed defensively |
| B/C | P2 | five false or overstated written claims | corrected in place |
| C | P2 | mutation matrix cell said RED(6), is RED(8) | corrected above |
| C | P2 | manifest not cross-checked at the choke point | two identity assertions added |
| A | P2 | usage errors collided with the refusal exit code | usage now exits 64 |

## The meta-pattern, which matters more than any single finding

Every one of these shipped with a **green** control:

- `test_no_changed_path_is_ever_dropped` compared `accounted_paths` with
  `changed_paths` — both computed by the same loop over the same input — so it
  could not see a path the *assembly* drops;
- the spike's key assertion was `not in {}`, an empty dict literal, true for
  every input, inside a test written to catch empty claims;
- the duplicate-path test justified its branch with a diff shape git never
  emits;
- the channel test tried `subject_root=/tmp/attacker`, an unrelated directory,
  and never a *narrowing* one;
- the redaction corpus tested sixteen spellings of one shape and none of the
  adjacent shapes;
- the closure list carried a comment claiming it "cannot silently drift
  narrower than the code" while being narrower than the code.

The tests were written from the same mental model as the code, so they could
only confirm it. Widening a corpus fixes instances; the structural answers
adopted here are different in kind — sharing a predicate instead of
reimplementing it, deriving a closure from the entry point instead of listing
it, detecting *disagreement* between two authorities instead of predicting the
next divergence, and matching secret keys by pattern instead of by list.

## An analysis abandoned, and why

A3's exemption originally proved only "raised somewhere, caught somewhere".
The obvious repair — prove statically that every raise is guarded — was
attempted twice and abandoned: a lexical rule rejects the real pattern (a
nested closure raises, the enclosing function catches), and a call-graph rule
must attribute raises to the *innermost* function and then decide external
reachability. The attempts produced false negatives on the clean tree, then
false positives on the mutant.

A control that cannot be got right is worse than a narrower one that can,
because its greenness would mean nothing. The exemption is now decidable by
inspection, and the class that no longer qualifies joined the family instead.
