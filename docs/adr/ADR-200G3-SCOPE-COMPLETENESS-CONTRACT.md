# ADR-200G3 — representing scope completeness in AgentReview v2 readiness

- **Status:** accepted, implemented
- **Slice:** `#200-G3` (issue #281, child of `#200`), successor to `#200-F`'s
  `STOP_SCOPE_CONTRACT_REQUIRED`
- **Date:** 2026-09-01
- **Supersedes:** `#200-F`'s ADR (`docs/adr/ADR-200F-SCOPE-COMPLETENESS-CONTRACT.md`)
  is no longer current for the "contract change" section — the additive
  contract it drafted but explicitly declined to ship is now implemented
  here, essentially as drafted. `#200-F`'s architectural analysis (the
  `FragmentCoverage != ScopeCompleteness` distinction, the rejection of
  every existing channel, the withdrawal of `#276`'s remedy) remains
  correct and is not restated in full.

## Problem, restated

`ChunkCoverageV2` answers *"were the reviewable fragments reviewed?"*,
computed only over `expected_files` — paths that produced a reviewable
fragment. A changed path that never produced a fragment (a pure rename, a
chmod, a binary, an empty file, an unrepresentable path) is invisible to
coverage, which can report `complete` for a run that never accounted for
part of the change. `#200-F`'s spike proved this concretely: a path shaped
like `src/pages/[id].tsx` was certified `reviewable` by an early scope
authority that had reimplemented (and gotten wrong) the same
representability check the assembly used independently — a false `ready`.

`#200-F` built the internal authority (`ScopeAssessmentV2`, private) but
stopped short of the published artifact, concluding: every existing v2
readiness channel is *structurally* accepted and *semantically* dishonest
for this fact. That ADR drafted, but explicitly declined under its own
grant to ship, a smallest additive contract change. This ADR picks that
decision up.

## Decision

**Route (c) from the mission brief: a new, additive, optional field on the
already-published `ReviewReadinessV2` contract.**

```python
class ScopeCompletenessV2(ContractV2Model):
    complete: StrictBool
    changed_paths: tuple[SafeText, ...]
    reviewable_paths: tuple[SafeText, ...]
    metadata_only_paths: tuple[SafeText, ...]
    unsupported_paths: tuple[SafeText, ...]
    must_review_blocked_paths: tuple[SafeText, ...]

class ReviewReadinessV2(ContractV2Model):
    ...
    scope: ScopeCompletenessV2 | None = None
```

Plus one additive enum member, `ReadinessReasonV2.SCOPE_INCOMPLETE`, used
exactly the way `#201-C` already added `POLICY_FAILURE` to the same
already-published enum — direct in-repo precedent that this kind of
additive enum extension has shipped safely before, under its own explicit
compatibility decision. `SCOPE_INCOMPLETE` was added to the allowed-reason
sets for `BLOCKED_CODE`, `BLOCKED_PIPELINE`, and `MANUAL_REQUIRED` in
`ReviewReadinessV2.validate_state_invariants`, and to
`PipelineDegradationCauseV2.validate_reason`'s own allowed set — mirroring
`COVERAGE_FAILURE` at every one of those sites, since the two are proven
structural siblings, not by adding a sixth `ReadinessStateV2` (the five
canonical states — `ready`/`blocked_code`/`blocked_pipeline`/
`manual_required`/`stale` — are normatively fixed by
`docs/engineering/PROJECT_OVERLAY.md` and are not reopened here).

## Why (c) over (a) and (b)

All three candidate routes were prototyped, not merely reasoned about
(`tests/agent_review/test_scope_completeness_contract_v2.py` exercises the
shipped route end to end; the rejected routes below were built as throwaway
scripts during the spike and are not committed, per "prototype, then
implement the chosen one" — their exact failure modes are recorded here in
enough detail to be independently re-derived if this decision is revisited).

**(a) Additive sidecar document, keyed by `run_id`/`head_sha`, published
alongside but not inside the readiness artifact.**

Rejected. A sidecar is trivially and completely ignorable by an existing
consumer — which is exactly the problem. `#200-F`'s own decision record
says the single worst outcome in this whole line of work is reporting
fragment coverage `complete` while total scope status is unknown or
incomplete. A reader of `ReviewReadinessV2` alone (which is what
`state`/`coverage` already are, and what every existing consumer in this
repo — `scripts/aiops-review-quality-gate-v2.py`'s own downstream readers,
per that CLI's own module docstring: *"the decision consumible by any
caller of this CLI is always `readiness.state`, never the exit code
alone"* — reads) has no structural reason to ever fetch the sidecar, and
`state` could still read `ready` while the sidecar says otherwise. The
terminal-readiness relationship this primitive exists to enforce
(`ready` requires coverage AND scope AND checks, see
`readiness_decision_v2.fragment_coverage_scope_and_checks_are_ready_v2`)
has to live where `state` is decided, which means the artifact that
carries `state` also has to carry (or structurally gate on) scope — a
sidecar cannot do that without becoming load-bearing, at which point it is
not actually a sidecar.

**(b) A new versioned readiness contract (`agent-review.review-readiness.v3`).**

Rejected as disproportionate. A new schema/schema_version pair would
require every producer and consumer of `ReviewReadinessV2` (five internal
modules, three CLI scripts, per the grep in `#200-G3`'s implementation) to
be re-pointed, duplicating the entire five-state, five-precondition
invariant surface (`validate_state_invariants` is ~200 lines of proven,
adversarially-reviewed logic) into a parallel v3 copy or an inheritance
hierarchy neither this codebase nor its "no qualification transfer, no
compatibility surface reopened without a decision" convention supports
casually. The actual new information — one fact, "was every changed path
accounted for" — does not need a new schema identity; `schema_id`/
`schema_version` staying `"agent-review.review-readiness.v2"`/`2` is
itself information (this is still, faithfully, a v2 artifact) that a v3
bump would destroy for no benefit.

**(c) Additive field on the existing contract — chosen.**

`ReviewReadinessV2`'s own `ContractV2Model` base already sets
`extra="forbid"`, and the exported JSON Schema already sets
`"additionalProperties": false`. Read naively this looks like a reason
`(c)` is UNSAFE (a strict consumer could reject a new field) — the executed
prototype showed the opposite: `extra="forbid"`/`additionalProperties:
false` make `(c)` SAFE in the specific way this repo's readiness runtime
actually deploys (see "Additive-safety verification" below). A consumer
either does not know the field exists (ignores it) or knows enough to be
`extra="forbid"`-pinned (fails closed, never silently misreads). Since
producer and schema are always pinned together by exact toolrepo SHA in
this architecture (`docs/engineering/CURRENT_CHECKPOINT.md`;
`AgentEscala`'s own dual pins) rather than independently versioned, the
"old schema fed a new artifact" scenario the naive reading worries about
does not arise without a deliberate, separate repin action.

## A real defect the executed prototype found (not a hypothetical)

The first cut of `ScopeCompletenessV2` used `RelativePath` (the same strict
type `FragmentV2.path`/`ChunkCoverageV2.expected_files` use) for every path
field. Building the RED test for the exact false-READY witness
(`src/pages/[id].tsx`) immediately failed at
`ScopeAssessmentV2.to_scope_completeness_v2()` — `ScopeCompletenessV2`
itself refused to construct, because `[id].tsx` fails `RelativePath`'s
glob-metacharacter-forbidding pattern. An `unsupported`-disposition path is,
BY DEFINITION, sometimes exactly a path that fails `RelativePath` — using
the same strict type here would make the contract structurally unable to
name the very path that made scope incomplete. Every path field now uses
`SafeText` (bounded length, no control characters, no secret-like
material — the same type `PipelineDegradationCauseV2.detail` uses) instead.
This is exactly the kind of finding "prototype, then decide" is supposed to
surface that "design, then implement" would not have.

## The internal disposition vocabulary (richer than the wire contract)

`operational_scope_v2.PathDispositionV2` classifies every changed path into
one of nine structural members: `reviewable`, `metadata_only`, `rename`,
`chmod_only`, `type_change`, `empty_file_transition`, `submodule_gitlink`,
`binary_unsupported`, `truncated`, `unrepresentable` (nine, not ten — an
early draft counted `metadata_only` and its four more specific siblings as
separate top-level categories from the issue's own "minimum set" phrasing,
then folded `rename`/`chmod_only`/`empty_file_transition`/
`submodule_gitlink` under the `metadata_only`-shaped umbrella for wire
purposes once it became clear the issue's list names REQUIRED
DISTINCTIONS, not necessarily mutually exclusive top-level enum arities).
`must_review_blocked` is deliberately NOT a tenth structural member — it is
a fact about the COMBINATION of a disposition and the target's must-review
policy (`ScopeAssessmentV2.must_review_blocked_paths`/`.blocked`), computed
once per assessment, not per path in isolation.

`chmod_only` and `type_change` are kept as two distinct members rather than
one merged "chmod/type-change" value (even though the issue's own prose
groups them) because they have OPPOSITE completeness consequences: a pure
permission-bit change (`chmod_only`) carries no content material and is
vacuously representable; a genuine cross-kind change — a regular file
becoming a symlink, git's own delete-plus-add rendering of one coherent
change (`_is_type_change_pair_v2`) — carries real material this product
cannot render for review, and makes scope incomplete. Merging them would
have reintroduced exactly the "collapse two different facts into one
signal" mistake `#200-F`'s ADR named as the root failure mode.

This 9-way vocabulary is internal/evidence-only. The published
`ScopeCompletenessV2` exposes the coarser two-bucket split (`metadata_only_
paths`/`unsupported_paths`) `#200-F`'s own ADR already drafted —
deliberately not the full 9-way split, per "smallest additive
representation": a downstream reader needs to know WHICH bucket a path
falls in for the one decision that matters (does this block `ready`), not
the full structural taxonomy.

## The terminal three-way relationship

Written as a real predicate, not prose:
`readiness_decision_v2.fragment_coverage_scope_and_checks_are_ready_v2`
(`coverage: ChunkCoverageV2, scope: ScopeCompletenessV2, checks: Sequence[
RequiredCheckResultV2]) -> bool`, requiring all three independently:
`coverage.status is COMPLETE and not coverage.missing_must_review_files`,
`scope.complete and not scope.must_review_blocked_paths`, and every
supplied check green with at least one check present. This is the same
relationship `contracts_v2.evaluate_ready_preconditions_v2` enforces as
part of the full five-rule `ready` precondition set (which additionally
covers PR state and blocker/finding absence, orthogonal to this specific
three-way rule) — defined once, standalone, so it is directly testable
without needing a full `ReviewReadinessV2` or the other two preconditions
in scope. `scope` is REQUIRED (not optional) in this standalone predicate,
deliberately unlike `evaluate_ready_preconditions_v2`'s own `scope`
parameter: this function's entire purpose is stating the honest
three-way relationship, and a default treating "not assessed" as "ready"
would undermine exactly that.

## `scope=None`: an honest third value, not "complete"

`ReadinessDecisionV2.scope`, `compute_readiness_decision_v2`'s `scope`
parameter, and `evaluate_ready_preconditions_v2`'s `scope` parameter all
default to `None`, meaning "this caller did not assess total scope" — a
value distinct from both "assessed complete" and "assessed incomplete",
never silently treated as complete. This default exists for exactly one
reason: ~50 pre-existing call sites across six test files test unrelated
precedence rules (confirmed findings, model uncertainty, required-check
folding) and have no reason to also fabricate a scope assessment; forcing
`scope` to be a required parameter everywhere would have meant either
touching all fifty call sites for no behavioral benefit to those tests, or
silently defaulting to "complete" (dishonest by construction). Every
caller producing a REAL readiness artifact for a REAL diff is expected to
compute a real assessment (`operational_scope_v2.assess_changed_scope_v2`)
and pass it through — but whether a given caller actually did so is a
caller-material/authenticity question this module cannot itself verify,
exactly like `review_readiness_emission_v2`'s own module docstring makes
the identical point about `decision`/`findings` more generally. This is a
known, accepted residual gap, stated plainly rather than pretended away.

## Additive-safety verification against a real existing v2 consumer

`scripts/aiops-review-quality-gate-v2.py` is the real, already-shipped v2
consumer/producer checked (not a hypothetical one): it reads a
`--decision` JSON file into `ReadinessDecisionV2` and calls `produce_
review_readiness_v2`. `_load_decision` was extended to parse an OPTIONAL
`scope` key (absent -> `None`, present -> `ScopeCompletenessV2.model_
validate_json`) — an existing `--decision` file from before this slice has
no `scope` key and is parsed unchanged. `scripts/export-agent-review-v2-
schemas.py --check` (the repo's own committed-schema freshness gate,
exercised by `tests/agent_review/test_contracts_v2.py::test_exported_json_
schemas_are_stable_and_deny_unknown_objects`) confirms the regenerated
`agent-review.review-readiness.v2.schema.json` is a PURELY additive diff
against the committed bytes: one new enum member, one new `$defs` entry,
one new optional (`"default": null`, absent from the top-level `"required"`
array) `scope` property — verified by diff, not asserted.

## Consequences

**Accepted now**

- `FragmentCoverage` and `ScopeCompleteness` are two structurally distinct
  facts in the emitted artifact, never collapsed into one signal.
- A run whose fragments are fully covered but whose total scope is
  incomplete now emits a real, explicit, non-`ready` artifact naming
  `SCOPE_INCOMPLETE` — the exact case `#200-F` could only represent
  internally is now externally visible.
- `ready` is impossible when `scope.complete` is `False`, or when any
  `must_review_blocked_paths` exist even with `scope.complete` `True` —
  enforced both pre-seal (`evaluate_ready_preconditions_v2`, consulted by
  `ReviewReadinessV2.validate_state_invariants`) and, independently, by the
  frozen contract's own constructor: `test_the_false_ready_path_stays_
  closed_end_to_end` proves construction of a `ready` artifact from
  scope-incomplete material raises `pydantic.ValidationError`, not merely
  that the composer chooses not to build one.
- Ordinary renames, chmod-only changes, empty-file transitions, and
  submodule pointer moves remain vacuously scope-complete — `#276`'s
  regression (denying ordinary refactors) is not reintroduced.
- The shared predicate defect class (`path_violates_relative_path_contract_
  v2` reimplemented independently in two modules) is closed by construction
  (one function, imported, never restated) and guarded at runtime by
  `assert_scope_authority_agrees_with_assembly_v2`, an anti-recurrence
  check independent of the fuzz corpus that originally proved the current
  divergence closed.

**Deferred, honestly**

- No production composer in this repository currently calls `assess_
  changed_scope_v2` end to end against a live diff and threads the result
  through `produce_review_readiness_v2` automatically — per the recovery
  checkpoint, there is no operational composer/product CLI on `master` at
  all yet (`#200-G5`'s job). `scripts/aiops-review-quality-gate-v2.py`
  accepts `scope` as caller-supplied, optional, unauthenticated material,
  matching its own established trust boundary for every other input
  (`decision`, `identity`, `checks`) — it does not itself compute one.
- The full `#277` differential fuzz corpus (2,592 cases, an 82-repo
  randomized real-git fuzz, a hostile `git mktree` corpus) was not
  reproduced at that scale under this grant. What was reproduced: an
  exhaustive combinatorial walk of `classify_changed_path_v2`'s own
  documented precedence space (384 explicit combinations,
  `test_classification_is_total_and_deterministic`) and a real-git fuzz
  covering six concrete scenarios (rename, chmod, binary, empty-file
  add/delete, symlink type-change) run against actual `git` subprocesses,
  not synthetic diff text. This is real, executed revalidation, not trust
  in the predecessor's claim — but it is smaller in scale than `#277`'s
  own corpus, and that gap is not closed here.

**Verdict:** `PRIMITIVE_NON_REFUTED` (pending the two independent
adversarial review passes required by this slice's process; see the
`#200-G3` checkpoint for their disposition).
