# AgentReview v2 — typed manifest and lossless chunk planning (partial, #84)

Refs #84. **This delivery is a foundational slice, not the full scope of
issue #84.** It implements the typed manifest contract and a lossless
line-range multi-chunk planner for content already assigned to a single
semantic group. It deliberately does not implement git-diff acquisition,
symbol/AST-aware grouping, or end-to-end propagation into the payload
builder, synthesizer, telemetry, or readiness gate. See "What remains"
below for the precise gap -- this is stated explicitly so partial work is
never mistaken for issue closure.

## What this delivery adds

- `app/agent_review/manifest_v2.py` -- `ManifestV2`, `FragmentV2`,
  `LineRangeV2`, `ManifestChunkV2`, `ManifestDegradationV2`. Strict,
  `frozen=True`, `extra="forbid"` models (via `contracts_v2.ContractV2Model`,
  reused unmodified) with a structural losslessness invariant: every
  `coverage_required` fragment must be referenced by exactly one chunk, or
  explicitly accounted for by a degradation cause -- an unexplained
  omission is a hard `ValidationError`, never a silently accepted partial
  manifest.
- `compute_manifest_hash_v2_for(manifest)` reuses
  `contracts_v2.compute_manifest_hash_v2` (already frozen by PR #81)
  unmodified on the manifest's `model_dump(mode="json")`, so a material
  change to any fragment or chunk flips the hash that would feed
  `RunIdentityV2.manifest_hash`.
- `app/agent_review/planner_v2.py` -- `HunkInputV2`, `plan_lossless_chunks_v2`.
  Given already-parsed hunks and a per-chunk line budget:
  - a hunk within budget becomes one fragment (fallback #2 from the issue's
    own deterministic fallback list: "hunk completo");
  - a hunk larger than budget is split into stable, disjoint line windows
    (fallback #3: "janelas de linha estáveis"), covering it exactly --
    verified by union-equals-input tests, never a shrinker;
  - `must_review` (`coverage_required`) fragments are bin-packed
    (first-fit-decreasing, deterministic tie-break by `fragment_id`) into
    chunks; if they cannot all fit within `max_chunks`, planning returns
    `blocked_pipeline` with a `budget_exhausted` degradation cause
    referencing every required fragment -- never a `planned` result with
    partial required coverage;
  - non-required (auxiliary/context) fragments are packed into leftover
    budget on a best-effort basis and silently dropped if they still do
    not fit, per the issue's own allowance ("podem ser reduzidos somente
    contextos auxiliares declarados") -- this is the only place content is
    ever dropped, and it is never `must_review` content.

`app/agent_review/contracts_v2.py` and `tests/agent_review/test_contracts_v2.py`
are unmodified, exactly as in #83 and #85: `ContractV2Model`,
`RunIdentityV2`, `compute_run_id`, and `compute_manifest_hash_v2` are reused
as-is.

## What remains (not implemented in this delivery)

These are real, substantial pieces of issue #84's stated scope that were
consciously **not** attempted here, to avoid claiming completion that
cannot be honestly demonstrated:

1. **Git-diff acquisition.** Parsing `git diff --no-ext-diff --binary
   BASE...HEAD`, validating name-status/numstat coherence, handling
   renames, deletions, files without a trailing newline, binaries,
   submodules, and generated/minified files by explicit policy, and
   reconstructing patches from blobs when truncated. `HunkInputV2` assumes
   hunks have already been parsed and handed to the planner; no such parser
   exists in this repository yet.
2. **Symbol/AST-aware grouping** (fallback #1 in the issue: "símbolo/AST
   quando disponível e comprovável"). This delivery groups by whatever
   `semantic_group` the caller already decided; it does not analyze
   imports, changed symbols, or which tests exercise them.
3. **`app/agent_review/payload_builder_v2.py`.** Turning a planned
   `ManifestV2` into actual `ChunkPayloadV2` objects (populating each
   chunk's `payload_sha256`) is not implemented.
4. **End-to-end propagation** into the v1 builder, `consumer_v2.py`/
   `parser_v2.py` (#83), synthesizer, telemetry, or the readiness gate to
   accept and preserve N chunks per semantic group. `consumer_v2.py`
   already binds one payload/envelope pair at a time (#83); nothing wires
   a multi-chunk manifest into that flow yet.
5. **JSON Schema export.** `schemas/agent-review/v2/agent-review.manifest.v2.schema.json`
   is not published. Adding it would require editing
   `tests/agent_review/test_contracts_v2.py`'s hard-coded set of five
   exported schema filenames -- exactly the kind of change to the frozen,
   already-reviewed v2 foundation this delivery (like #83 and #85 before
   it) deliberately avoids. Exporting the manifest schema is real,
   contained follow-up work, not a structural blocker.

Because of (1)-(4), the acceptance criteria that require an actual diff
acquisition path, symbol-aware grouping, or full pipeline propagation are
**not** met by this delivery, and issue #84 is not closed by it. What is
implemented (the manifest's losslessness invariant and the line-range
planner) is real, tested, and safe to build on.
