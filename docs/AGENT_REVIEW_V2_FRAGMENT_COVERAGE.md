# AgentReview v2 — run fragment coverage proof and fail-closed policy (#104)

Refs #104, child of the contract-closure epic #102. Depends on the manifest and
fragment contract already frozen by #84 (`app/agent_review/manifest_v2.py`).
Blocks #107 (synthesis/lifecycle) and #110 (payload content + CLIs).

Delivers exactly the two things the issue asks for and nothing else: the
`RunFragmentCoverageReportV2`/`RunFragmentCoverageEntryV2` contract, and the
fail-closed policy for structurally divided paths, enforced *in the contract
itself* rather than documented as a convention a caller could ignore.
Synthesis (aggregating real `ParsedChunkResultV2` objects into a report) is
#107's job, not this issue's — see "What this delivery does not do".

## The problem this closes

`ChunkCoverageV2` (frozen in #81) is file-granular. `FragmentV2` (#84) is
line-range-granular. A path whose fragments are split across more than one
chunk is, in every one of those chunks, reported at best as
`partially_reviewed` — `validate_response_binding_v2`
(`contracts_v2.py:858-869`) already forbids a response from promoting that to
`reviewed`. So far so good: no chunk can lie about its own slice. But nothing
upstream could *represent*, across the whole run, which of that path's
fragments were actually touched. `PipelineDegradationCauseV2`
(`contracts_v2.py:1039-1055`) has exactly three fields —
`reason_code`/`component`/`detail` — and `PipelineAssessmentV2` deduplicates
causes by `(reason_code, component)` (`:1066-1068`). It cannot carry a set of
fragment IDs, cannot distinguish two divided paths from each other, and would
silently collapse two distinct causes that happen to share a reason code and
component.

The two grandezas this module keeps apart, always:

```text
escopo estrutural esperado    = the union of a path's fragments in the manifest
cobertura semântica provada   = only the fragments explicitly reported as reviewed
```

The manifest already proves the first exactly (`manifest_v2.py`'s own
losslessness invariant: every `coverage_required` fragment is assigned to
exactly one chunk or explicitly degraded). Nothing proved the second at
run granularity before this issue.

## What this delivery adds

### `app/agent_review/run_fragment_coverage_v2.py`

**`RunFragmentCoverageEntryV2`** — one path, self-validating without needing
a manifest reference: `expected_fragment_ids`, `assigned_fragment_ids`,
`reviewed_fragment_ids`, `partially_reviewed_fragment_ids`,
`missing_fragment_ids`, `affected_chunk_ids`, `status`
(`reviewed`/`partial`/`missing`), `reason_codes`. Its `model_validator`
enforces, unconditionally:

- `reviewed | partially_reviewed | missing` exactly partitions
  `expected_fragment_ids` — no omission, no overlap;
- `reviewed`/`partially_reviewed` are subsets of `assigned`; every
  unassigned fragment is `missing`;
- **the fail-closed policy itself**: if `len(affected_chunk_ids) > 1` (the
  path is structurally divided), `status` can never be `reviewed` —
  regardless of what `reviewed_fragment_ids` claims. A caller supplying
  `reviewed_fragment_ids == expected_fragment_ids` for a divided path is
  rejected by `ValidationError`, not silently downgraded;
- `status` must exactly match the computed partition (no caller-asserted
  status independent of the fragment sets);
- `structural_split` in `reason_codes` if and only if the path is actually
  divided; a `reviewed` path carries zero reason codes; any non-`reviewed`
  path must carry at least one.

**`RunFragmentCoverageReportMaterialV2`/`RunFragmentCoverageReportV2`** — one
artifact per run, carrying `run_id` + `manifest_hash` on the artifact itself
(not on each entry): a report is meaningless detached from the run/manifest
it describes, and per-entry identity would let a report be assembled from
entries proven for *different* runs. Reuses the `*Material*`/hash-carrier
split already established by `ChunkPayloadMaterialV2`/`ChunkPayloadV2`
(`contracts_v2.py:594-626`) and `ManifestMaterialV2`/`ManifestV2`: canonical
bytes cover every field except `coverage_report_sha256` itself, computed with
`paths` sorted by `path` regardless of construction order — reordering the
entries passed to the constructor never changes the hash.

**`bind_coverage_report_to_manifest_v2(report, manifest)`** — the
manifest-aware half of the policy a bare Pydantic model cannot do alone:

- `run_id`/`manifest_hash` equality — cross-run and cross-manifest replay
  rejected, mirroring `validate_response_binding_v2`'s own binding
  discipline;
- the report's path set equals `manifest.expected_files` exactly;
- each entry's `expected_fragment_ids` equals that path's *real* fragment
  set in the manifest — not just internally consistent, actually correct
  relative to this manifest;
- every path's `affected_chunk_ids` is EXACTLY the manifest's real per-path
  chunk set — not merely a subset of chunks that exist somewhere in the
  manifest. `RunFragmentCoverageEntryV2` alone has no manifest to check "is
  this path divided?" against; it computes that purely from the
  caller-supplied `affected_chunk_ids` list. A caller that under-reports
  that list (naming only one of a path's two real chunks) could otherwise
  make a genuinely multi-chunk-split path look undivided — and therefore
  eligible for `status="reviewed"` — at the entry level alone. This exact
  gap was found by independent review before merge: the entry's own
  validator was airtight in isolation, but the fail-closed policy did not
  hold at the system level until this exact-match check closed it;
- a fragment named in the manifest's own `degradation_causes` can never be
  `reviewed`, and must appear in that entry's `missing_fragment_ids` — never
  silently absorbed into `partially_reviewed`.

`PipelineDegradationCauseV2` is untouched and remains exactly what it already
was: a summarized projection for readiness (#108), never the evidence of
record. Nothing here constructs one — that projection is #108's job, once it
exists to consume this report.

### Schema

`schemas/agent-review/v2/agent-review.run-fragment-coverage.v2.schema.json`,
generated by `scripts/export-agent-review-v2-schemas.py`
(`schema_export_v2.py` now also imports `RunFragmentCoverageReportV2`).
`additionalProperties: false` on both the report and the nested entry
definition; byte-identical across repeated `--check` runs.

## Protocol decision — already closed, not reopened here

This issue does not choose a fragment-coverage *protocol*. The convergence
plan already decided: the first v2 release uses the fail-closed fallback
above — a divided path is capped at `partial` — and does **not** introduce a
fragment-aware response protocol (which would require versioning
`ChunkPayload`, `ChunkResponseEnvelope`, the binder, and the parser all
together, since today's payload carries no `fragment_id` the binder could
check a response against). That is a future issue, gated on evidence from
#86/#88 showing the fail-closed fallback makes the system impractical — not
opened speculatively here.

## What this delivery does not do

- **No synthesis.** Nothing here aggregates real `ParsedChunkResultV2`
  objects into a `RunFragmentCoverageReportV2`. `#107` will call
  `RunFragmentCoverageEntryV2`'s constructor (and get its invariants enforced
  for free) once it has real per-chunk results to classify into
  reviewed/partial/missing fragment sets.
- **No readiness/gate integration.** `#108` will be the first consumer that
  projects a `RunFragmentCoverageReportV2` into `PipelineDegradationCauseV2`
  summaries for `ReviewReadinessV2`.
- **No verification that an assigned chunk was actually usable.**
  `bind_coverage_report_to_manifest_v2` proves the report matches the
  manifest's *structure* (which fragments the manifest assigned to which
  chunks) and its degradation causes. Whether a given chunk's response
  actually bound and parsed successfully is a fact that only exists once
  real chunk results exist (#107) — the manifest alone cannot attest to
  processing outcome, only to structural assignment.
- **`coverage_policy_v2.py` as a separate file was not created.** The
  "política fail-closed" *is* `RunFragmentCoverageEntryV2`'s own validator
  plus `bind_coverage_report_to_manifest_v2` — splitting them into a second
  module would separate a contract from the one thing that makes it a
  fail-closed contract, for no real boundary gain. Consolidated into one
  module, matching how `manifest_v2.py` itself keeps its contract and
  invariants together.
