# AgentReview v2 — the readiness decision library (#127)

Refs #127, child of tracker #108 (readiness). Consumes #107's
`synthesis_v2.SynthesisResultV2` (merged, PR #117) and #115's fixed
`bind_coverage_report_to_manifest_v2` (merged, PR #134). Blocks #130/C2,
which will fold this module's decision, together with `pr_state`/GitHub
`checks` acquired from the forge, into a real `ReviewReadinessV2` artifact
and its CLI.

Delivers exactly what the issue asks: a pure function computing the
readiness decision (state, reason codes, blockers, bridged coverage,
pipeline assessment) from `SynthesisResultV2` + `ManifestV2` +
`TargetPoliciesV2`. No artifact emission, no `pr_state`, no GitHub `checks`,
no CLI — all deliberately out of scope, reserved for #130.

## Problem this closes

Two gaps stood between #107's synthesis and a real readiness decision:

1. **Granularity mismatch.** `RunFragmentCoverageReportV2` (#104/#115) is
   fragment-granular (one entry per path, naming exactly which fragments
   were reviewed/partial/missing). `ReviewReadinessV2.coverage` requires
   `ChunkCoverageV2` — file-granular, frozen since #81. Nothing bridged one
   into the other.
2. **No decision logic.** Nothing computed `ReviewReadinessV2`'s `state` /
   `reason_codes` / `blockers` / `pipeline` from a real synthesis result —
   only that dense contract's own internal *validators*, which check that a
   given combination is internally consistent, existed. Producing a
   combination that is both internally consistent AND correctly reflects
   the actual synthesis result is what this issue delivers.

## `app/agent_review/readiness_decision_v2.py`

### `bridge_fragment_coverage_to_chunk_coverage_v2(*, coverage_report, manifest) -> ChunkCoverageV2`

Revalidates `coverage_report` against `manifest` first, via #115's fixed
`bind_coverage_report_to_manifest_v2` — `SynthesisResultV2` is, like
`ParsedChunkResultV2`, "a plain data value, freely constructible" with no
seal proving its `coverage_report` actually matches the given `manifest`, so
this module treats it as untrusted input, the same discipline
`synthesis_v2`/`lifecycle_v2` already apply to `ParsedChunkResultV2`.

Maps each path's fragment-level status (`REVIEWED`/`PARTIAL`/`MISSING`)
directly into `ChunkCoverageV2`'s file buckets. The one genuine design
decision: **a path is classified against a REAL `ManifestDegradationV2`
cause, never merely because it is structurally split across chunks.** If
any non-complete path is backed by a genuine manifest-level degradation
cause, `DEGRADED` status wins over the weaker "just partial" classification
for that path — this is the deterministic precedence the #116 combined
fixture (one path, structurally split AND carrying a genuinely degraded
fragment) proves: `structural_split` and `fragment_degraded` coexist on that
single fragment-level entry, and the bridge correctly resolves the path as
`DEGRADED`, backed by the real cause.

**Known, honestly-declared limitation of the frozen v1 contract (#81):**
`ChunkCoverageV2.status` is a single value governing every affected file in
the whole run. A run mixing a purely structural split (no manifest cause) on
one path with an unrelated genuine degradation (real manifest cause) on a
different path cannot be represented by any single status value — `PARTIAL`
forbids any `degradation_causes`, `DEGRADED` requires every affected file to
be covered by one. This bridge fails closed with
`COVERAGE_BRIDGE_MIXED_DEGRADATION_REASON_V2` rather than silently
misrepresenting either path. Per the issue's own stop condition, altering
`ReviewReadinessV2`/`ChunkCoverageV2` to accommodate this would be a
breaking-contract change, not an implementation task — not attempted here.

Manifest-level degradation reasons (`manifest_v2.DegradationReasonValueV2`,
7 values) are folded into the coarser `CoverageDegradationReasonV2` (5
values): `packing_search_exhausted` and `planner_limit_exceeded` both fold
into `BUDGET_EXHAUSTED` (both are "could not fit within the packer's
constraints" in spirit), documented rather than silently coerced.

### `compute_readiness_decision_v2(*, synthesis, manifest, policies, stale_reason_codes=frozenset()) -> ReadinessDecisionV2`

`ReadinessDecisionV2` is a plain, freely constructible dataclass — like
`SynthesisResultV2`, not a wire contract — carrying `state`, `reason_codes`,
`blockers`, `coverage`, `pipeline`. #130/C2 folds these directly into a real
`ReviewReadinessV2` alongside identity/`pr_state`/`checks`, without
re-deriving any of this module's logic.

Deterministic, total precedence (every input combination reaches exactly one
outcome):

```text
1. caller-supplied stale_reason_codes non-empty  -> STALE
2. >=1 CONFIRMED blocking finding                -> BLOCKED_CODE
3. else synthesis.limitations non-empty          -> MANUAL_REQUIRED
4. else coverage needs attention                 -> policies.coverage_failure_state (exactly)
5. else >=1 NEW blocking finding pending          -> MANUAL_REQUIRED
6. else                                          -> READY
```

**"path fragmentado produz exatamente `policies.coverage_failure_state`"**
(the issue's own acceptance criterion, verbatim) is step 4: a coverage
failure with no confirmed finding and no model uncertainty maps to exactly
whichever state the target's own policy configures — `blocked_pipeline` or
`manual_required` — never anything else.

**Why `model_uncertainty` always forces `MANUAL_REQUIRED`, never
`BLOCKED_PIPELINE`, regardless of `policies.coverage_failure_state`:**
`ReviewReadinessV2.validate_state_invariants`'s `BLOCKED_PIPELINE` branch's
own `allowed` reason set is `{schema_failure, transport_failure,
coverage_failure, policy_failure}` — it structurally excludes
`model_uncertainty`. `MANUAL_REQUIRED`'s allowed set includes it. So step 3
taking precedence over step 4 isn't a policy choice this module makes — it's
the only choice the frozen contract permits.

**`synthesis.limitations` is free-form `SafeIdentifier` text**, not a closed
enum — a grep across this repo's own existing fixtures shows values from
`"model_uncertainty"` to `"chunk_budget_exceeded:api_schema_contract"` to
`"optional_artifact_missing:checks"`. Inventing a bespoke per-string mapping
to `ReadinessReasonV2` would be fragile and permanently incomplete. Any
non-empty `limitations` therefore folds into a single `MODEL_UNCERTAINTY`
pipeline cause — the correct semantic bucket for "the review process itself
self-reported it could not fully complete or trust its work" — with every
raw limitation identifier preserved verbatim, sorted, in that cause's
`detail` field. No information is dropped.

**Known, documented scope limitation:** when step 4 resolves to
`BLOCKED_PIPELINE` (the target's policy choice) and there is ALSO a NEW
blocking finding pending confirmation, that finding is not independently
representable in this decision's `reason_codes` —
`FINDING_CONFIRMATION_REQUIRED` is not in `BLOCKED_PIPELINE`'s allowed
reason set. The finding still exists in `synthesis.findings` and surfaces
once the pipeline degradation clears or the target's policy is
`manual_required`.

**Why `STALE` takes a caller-supplied signal instead of being derived
internally:** detecting that `manifest`'s evaluated identity/HEAD no longer
matches the CURRENT identity/HEAD requires comparing against live state
outside a single synthesis result — that is #130/C2's responsibility, which
holds the live run/identity state this library never touches. Passing the
resulting reason codes short-circuits straight to `STALE`, matching
`ReviewReadinessV2.validate_state_invariants`'s own stale branch, which
ignores coverage/pipeline/findings entirely once stale.

## Deliberately out of scope

- emitting a `ReviewReadinessV2` artifact (#130/C2);
- acquiring or interpreting `pr_state` (#130/C2);
- acquiring or interpreting GitHub `checks` (#130/C2);
- any CLI;
- deriving `stale_reason_codes` itself (requires live current-identity
  state, #130/C2's responsibility).

## Tests

`tests/agent_review/test_readiness_decision_v2.py` — 16 tests: the bridge
(complete/partial/degraded classification, the #116 combined fixture's
deterministic precedence, rejecting a mismatched coverage report, the
mixed-degradation fail-closed case with two different paths); one decision
test per `ReadinessStateV2` value (`READY`, `BLOCKED_CODE`,
`BLOCKED_PIPELINE`, `MANUAL_REQUIRED` via both model-uncertainty and
new-finding routes, `STALE`); the exact-policy-configured-state acceptance
criterion (both `blocked_pipeline` and `manual_required`); precedence tests
proving a confirmed finding and model uncertainty each win over a coexisting
coverage failure; and the two `ReadinessDecisionError` paths (synthesis/manifest
`run_id` mismatch, invalid stale reason codes).

**Verification of non-vacuity:** the mixed-degradation guard was
temporarily disabled and the corresponding test confirmed to fail (with
`ChunkCoverageV2`'s own validator catching the resulting inconsistent
construction as a raw `pydantic.ValidationError` — proving this module's own
typed, actionable `ReadinessDecisionError` is a real improvement over
letting that validation error leak, not a redundant check).
