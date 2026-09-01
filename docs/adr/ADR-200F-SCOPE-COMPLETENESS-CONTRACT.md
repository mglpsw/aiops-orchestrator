# ADR-200F — representing scope completeness in AgentReview v2 readiness

- **Status:** proposed — blocked, verdict `STOP_SCOPE_CONTRACT_REQUIRED`
- **Slice:** `#200-F` (parent `#200`), grant §8
- **Date:** 2026-09-01
- **Supersedes nothing.** `#276`'s `operational_run_scope_silently_narrowed`
  policy is withdrawn, not amended (see *Rejected*).

## Context

`ChunkCoverageV2` answers *"were the reviewable fragments reviewed?"*. It is
computed over `expected_files` — the paths that produced reviewable fragments.

A changed path that produces no fragment never enters `expected_files`. Pure
renames, mode-only changes, binaries, lockfiles, images, empty-file adds and
deletes, and submodule pointer moves are therefore **invisible** to coverage,
which reports `complete` for a run that never examined part of the change.

This is demonstrated, not asserted, by
`tests/agent_review/test_operational_scope_contract_spike_v2.py::test_coverage_reports_complete_while_a_changed_path_is_invisible`.

The grant requires the two ideas to stay distinct:

| Concept | Question | Owner |
|---|---|---|
| `DiffCoverage` | were the reviewable fragments reviewed? | `run_fragment_coverage_v2` |
| `ScopeCompleteness` | did every changed path get a disposition? | `operational_scope_v2` (new, private) |

and requires this outcome to be expressible:

```text
reviewable fragments fully covered
+ some non-required changed paths unreviewable
→ review may continue
→ total scope incomplete
→ READY impossible
→ explicit manual_required / limitation
```

## Decision

**The internal authority is built now. The emission is blocked.**

`ScopeAssessmentV2` is a private in-process value. It gives every changed path
exactly one disposition (`reviewable` / `metadata_only` / `unsupported`),
computes `scope_complete`, and computes `blocked` for a must-review path that
turned out unreviewable. Nothing about it requires published vocabulary, so
the composer can already refuse to emit `ready` when scope is incomplete.

What is blocked is *recording the distinction in the emitted artifact*.
`agent-review.review-readiness.v2` is published under
`schemas/agent-review/v2/`, and the grant forbids altering a published schema
silently.

## Why each existing channel was rejected

Every route below is **structurally accepted** by the contracts. The obstacle
is semantic in all four cases, which is why the spike had to execute them
rather than reason about them.

### (a) `expected_files` widened, unreviewable paths marked `degraded`

Requires a `CoverageDegradationReasonV2` member. All five existing members
assert something untrue about a pure rename:

| Member | What it would tell an operator | Truth |
|---|---|---|
| `artifact_missing` | go find a missing artifact | nothing is missing |
| `budget_exhausted` | raise the budget | no budget was consumed |
| `transport_failure` | investigate transport | none was involved |
| `schema_failure` | a schema rejected material | none was reached |
| `model_uncertainty` | the model was unsure | no model saw it |

### (b) `status = partial`, unreviewable paths under `missing_files`

Two separate falsehoods. It asserts the fragment review was incomplete when it
was complete, and it collapses `DiffCoverage` into `ScopeCompleteness`,
destroying the distinction the grant requires. After the collapse nobody can
distinguish *"the model failed to review a reviewable file"* from *"this path
had nothing to review"* — opposite situations.

Also unusable: `TargetPoliciesV2.allow_partial_coverage` is `Literal[False]`,
so every ordinary rename would become a policy violation.

### (c) An existing `ReadinessReasonV2` member

The published vocabulary is `schema_failure`, `transport_failure`,
`coverage_failure`, `policy_failure`, `model_uncertainty`,
`finding_confirmation_required`, `confirmed_code_finding`, `head_mismatch`,
`identity_mismatch`. None means "some changed paths carry material this
product cannot represent". `coverage_failure` is nearest and still false:
coverage succeeded over the fragments it was defined on.

### (d) A `limitations` list on readiness

`ReviewReadinessV2` has no such field. `TargetProfileV2` does — which is what
makes the absence a gap rather than a deliberate exclusion. Adding it is
precisely the published-schema change this ADR exists to propose rather than
perform.

### Rejected outright: `#276`'s remedy

```python
if assembly.excluded_paths:
    raise OperationalRunError(OPERATIONAL_RUN_SCOPE_SILENTLY_NARROWED_REASON_V2)
```

Denies review entirely for pure renames, chmod-only changes, binaries,
lockfiles, images and empty-file adds; names the event a *silent narrowing*
when the composer in fact refused loudly; and closes a vector that could not
have produced an emitted `ready` artifact in the first place. Withdrawn.

## Proposed contract change (smallest additive, versioned)

Not implemented under this grant. Recorded so the successor gate has something
concrete to accept or reject.

```yaml
# 1. New coverage degradation vocabulary member -- additive to an existing enum
CoverageDegradationReasonV2:
  + UNREPRESENTABLE_MATERIAL = "unrepresentable_material"
    # the path changed and carries material this product cannot render for
    # review: binary, truncated patch. NOT for paths carrying no material.

# 2. New readiness reason -- additive to an existing enum
ReadinessReasonV2:
  + SCOPE_INCOMPLETE = "scope_incomplete"
    # total changed scope was not fully accounted for; distinct from
    # coverage_failure, which is about reviewable fragments.

# 3. New optional readiness field -- additive, defaulted, back-compatible
ReviewReadinessV2:
  + scope: ScopeCompletenessV2 | None = None

ScopeCompletenessV2:                    # new published object
  complete: StrictBool
  changed_paths: tuple[RelativePath, ...]
  reviewable_paths: tuple[RelativePath, ...]
  metadata_only_paths: tuple[RelativePath, ...]
  unsupported_paths: tuple[RelativePath, ...]
  must_review_blocked_paths: tuple[RelativePath, ...]
```

Enum extension is a consumer-visible change even when additive: a strict
consumer pinned to the current member set will reject an artifact carrying a
new value. So this needs either a schema version bump or an explicit
compatibility decision by the contract owner. **That decision is not mine to
make under this grant**, which is the whole reason this is an ADR and a STOP
rather than a commit.

## Consequences

**Accepted now**

- Every changed path receives a disposition internally; nothing is dropped.
- `ready` is impossible when `scope_complete` is false — enforced in the
  composer, needing no published vocabulary.
- A must-review path that is unreviewable fails closed.
- Ordinary renames, chmod-only changes, binaries and empty files no longer
  deny the whole review, reversing the `#276` regression.

**Deferred, and honestly visible**

- The emitted readiness artifact still cannot *say* "total scope incomplete".
  A downstream consumer reading only the artifact sees `coverage: complete`
  and a non-`ready` state whose reason vocabulary does not explain scope.
- Until the contract lands, the distinction lives in the run's evidence and in
  the composer's refusal to emit `ready` — not in the published artifact.

**Verdict:** `STOP_SCOPE_CONTRACT_REQUIRED`.
