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
