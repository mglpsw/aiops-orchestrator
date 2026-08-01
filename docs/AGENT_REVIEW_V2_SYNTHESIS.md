# AgentReview v2 — synthesis and lifecycle (#107)

Refs #107, child of the epic #80. Depends on the fragment coverage proof and
fail-closed policy frozen by #104 (`app/agent_review/run_fragment_coverage_v2.py`)
and the verified binding delivered by #83
(`app/agent_review/consumer_v2.py`/`parser_v2.py`). Blocks #108
(`ReviewReadinessV2`/quality gate).

Delivers exactly what the issue asks: aggregate N already-bound
`ParsedChunkResultV2` objects for one run into a coverage report and a
deduplicated finding lifecycle, applying #104's fail-closed policy rather
than re-deciding it. No readiness, no quality gate, no CLI, no change to any
v1 module.

## What this delivery adds

### `app/agent_review/synthesis_v2.py`

`synthesize_chunk_results_v2(manifest, chunk_results, evaluated_head_sha,
prior_lifecycle=())` — the single entry point. Rejects, before any
aggregation happens:

- anything that is not a genuine `ParsedChunkResultV2` instance
  (`invalid_chunk_result_type`) — a raw envelope, a v1 result, a hand-built
  dict, or any other object;
- a result whose `run_id` does not match the manifest's own `run_id`
  (`cross_run_chunk_result`);
- two results sharing the same `chunk_id` (`duplicate_chunk_result`).

`_build_coverage_report` derives, per path in `manifest.expected_files`,
which of that path's fragments were assigned, reviewed, partially reviewed,
or missing — from the manifest's own fragment-to-chunk structure crossed
with each bound chunk response's own `ChunkCoverageV2.reviewed_files` claim
(the response's self-reported, already-verified-not-to-exceed-scope
semantic proof). The result is fed straight into
`RunFragmentCoverageEntryV2` (#104), so every one of #104's invariants is
enforced by construction, not re-checked here:

- a path whose fragments land in more than one real chunk
  (`len(affected_chunk_ids) > 1`) can never reach `reviewed` — even when
  every one of its chunks individually reports the path as `reviewed`, this
  module folds that into `partially_reviewed_fragment_ids` instead, with
  `structural_split` in `reason_codes`;
- a fragment named in the manifest's own `degradation_causes` always lands
  in `missing_fragment_ids` with `fragment_degraded`;
- a fragment whose chunk never produced a bound result at all (the upstream
  cause -- an error envelope, a transport failure -- never reaches this
  module, since only a *successful* binding produces a `ParsedChunkResultV2`
  in the first place) lands in `missing_fragment_ids` with
  `chunk_unavailable`.

The produced `RunFragmentCoverageReportV2` is verified, in this delivery's
own tests, to always pass `bind_coverage_report_to_manifest_v2` against the
manifest it was built from — self-consistency proven, not assumed.

**One boundary case is deliberately not resolved here, and fails closed
with a dedicated reason code (`fragmentless_expected_file`):**
`manifest_v2`'s own validator only requires a fragment's path to be a
*subset* of `expected_files`, never that every expected file has at least
one fragment. No code path in this repository constructs such a manifest
today, and `RunFragmentCoverageEntryV2` requires at least one expected
fragment — resolving this is a question about what such a manifest should
even mean at the planning layer (likely #109's concern), not a synthesis
question.

### `app/agent_review/lifecycle_v2.py`

`aggregate_finding_lifecycle_v2(manifest, chunk_results, evaluated_head_sha,
prior_lifecycle=())` — deduplicates every `ChunkFindingV2` across all chunk
results by root cause: exact `(file_path, line_start, line_end,
sorted(contract_ids), severity)`. Two fragments never share a line range
(`planner_v2`'s own disjointness guarantee), so this key is already
fragment-discriminating without needing a `fragment_id` field on
`ChunkFindingV2` itself. Two findings with identical title/evidence text but
different locations are never merged; two findings at the same location
with the same violated contracts, reported by different chunks, always are
— with **every** contributing observation retained in a separate
`FindingProvenanceV2` record (`chunk_id`, derived `fragment_id`, original
`finding_id`), keyed by the synthesized finding's own deterministic ID (a
SHA-256 of the dedup key).

The lifecycle vocabulary is exactly, and only, what `FindingDispositionV2`
defines: `new · confirmed · fixed · dismissed · superseded · stale`. There
is no `rejected` and no `inconclusive` — a caller meaning "rejected" uses
`dismissed` (which the frozen contract already requires justification and
typed evidence for); a caller meaning "inconclusive" represents it as a
run-level limitation or `model_uncertainty`, never as a finding disposition.

**This module never creates `confirmed`, `fixed`, `dismissed`,
`superseded`, or `stale` on its own initiative.** Every freshly observed,
deduplicated finding enters as `new`. A different disposition can only be
*preserved*: a caller passes `prior_lifecycle` records that are already
decided and already revalidated for `evaluated_head_sha`
(`record.observed_at_head_sha == evaluated_head_sha`, checked and rejected
fail-closed — `stale_prior_lifecycle_record` — otherwise). Two chunks, or
two different models, agreeing on the same finding still produces exactly
one `new` record — concordance is never treated as confirmation. A prior
decision not re-observed in a given round still persists in the output
(a human disposition does not silently expire because this particular round
did not re-detect the underlying defect).

`ReadinessStateV2.STALE` (computed later, by #108, from HEAD/identity
divergence at the *run* level) is a different concept entirely from a
finding's own `disposition=stale`. Nothing in this module conflates the
two, and this module never emits `disposition=stale` on its own — only ever
preserves one already decided upstream.

## A small, behavior-preserving refactor of #104's own module

`run_fragment_coverage_v2.py` gained one new pure function,
`compute_fragment_coverage_status_v2`, extracted verbatim from
`RunFragmentCoverageEntryV2.validate_entry`'s own inline status-derivation
logic. The validator now calls this function instead of duplicating the
rule; `synthesis_v2.py` calls the same function to compute a status
guaranteed to satisfy that validator, instead of re-deriving the rule at
the call site and risking the two drifting apart. #104's own full test
suite (35 tests) was re-run unchanged after this refactor and remains
green — this is additive and behavior-preserving, not a contract change.

## What this delivery does not do

- **No readiness, no quality gate.** `#108` is the first consumer that will
  turn a `SynthesisResultV2` into a `ReviewReadinessV2`.
- **No CLI.** `SynthesisResultV2` is a plain, freely constructible
  `dataclass` (matching `ParsedChunkResultV2`'s own precedent) — not a wire
  contract with its own schema, since nothing outside this run-local
  aggregation step consumes it directly today.
- **No `PipelineDegradationCauseV2`/`PipelineAssessmentV2` construction.**
  #104's own documentation is explicit that this projection is #108's job,
  once it exists to consume a coverage report — this module's fragment-level
  `reason_codes` are the detailed evidence; #108 will summarize them.
- **No reuse of v1 heuristics.** `final_synthesizer.py`'s text-based dedup
  (`_finding_key`) and `finding_normalizer.py`'s heuristic severity
  downgrade are both incompatible with fragment-aware, root-cause dedup and
  are not imported anywhere in this delivery.
