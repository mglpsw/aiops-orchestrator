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

- `app/agent_review/manifest_v2.py` -- `ManifestMaterialV2`, `ManifestV2`,
  `FragmentV2`, `LineRangeV2`, `ManifestChunkV2`, `ManifestDegradationV2`.
  Strict, `frozen=True`, `extra="forbid"` models (via
  `contracts_v2.ContractV2Model`, reused unmodified) with a structural
  losslessness invariant: every `coverage_required` fragment must be
  referenced by exactly one chunk, or explicitly accounted for by a
  degradation cause -- an unexplained omission is a hard `ValidationError`,
  never a silently accepted partial manifest.
- `compute_manifest_hash_v2_for(material)` reuses
  `contracts_v2.compute_manifest_hash_v2` (already frozen by PR #81)
  unmodified, so a material change to any fragment or chunk flips the hash
  that feeds `RunIdentityV2.manifest_hash`.
  **`ManifestV2` extends `ManifestMaterialV2` rather than embedding
  `run_id`/`identity` in the hash preimage.** `RunIdentityV2` itself
  contains `manifest_hash`, and `ManifestV2` carries `identity:
  RunIdentityV2` -- hashing the full manifest (identity included) would
  require `identity.manifest_hash` to already be known before it could be
  computed. `compute_manifest_hash_v2_for` always excludes `run_id`/
  `identity` from the preimage (via `model_dump(..., exclude={"run_id",
  "identity"})`, safe on either a bare `ManifestMaterialV2` or a full
  `ManifestV2`), and `ManifestV2`'s own validator proves
  `identity.manifest_hash == compute_manifest_hash_v2_for(self)` -- so a
  manifest carrying a stale or foreign `manifest_hash` is rejected, not
  silently accepted. This mirrors the `ChunkPayloadMaterialV2`/
  `ChunkPayloadV2` split already established in `contracts_v2.py`, applied
  one level up since the self-referential field lives on `identity`, not on
  `ManifestV2` itself.
- `app/agent_review/planner_v2.py` -- `HunkInputV2`, `plan_lossless_chunks_v2`.
  Given already-parsed hunks and a per-chunk line budget:
  - a hunk within budget becomes one fragment (fallback #2 from the issue's
    own deterministic fallback list: "hunk completo");
  - a hunk larger than budget is split into stable, disjoint line windows
    (fallback #3: "janelas de linha estáveis"), covering it exactly --
    verified by union-equals-input tests, never a shrinker;
  - `must_review` (`coverage_required`) fragments are packed into chunks
    with an *exact* backtracking bin-packing decision procedure
    (`_pack_fragments_exact`, deterministic tie-break by `fragment_id`,
    bounded by a trivial-infeasibility short-circuit plus a hard cap on
    both search states and input size) -- not a greedy heuristic like
    first-fit-decreasing, which is not guaranteed optimal and could
    wrongly report `blocked_pipeline` for content that actually fits; if
    required fragments genuinely cannot fit within `max_chunks`, planning
    returns `blocked_pipeline` -- never a `planned` result with partial
    required coverage. **The reason code distinguishes *why*, honestly:**
    `_pack_fragments_exact` returns a three-way `ExactPackingResultV2`
    (`found` / `proven_infeasible` / `search_exhausted` / `input_too_large`),
    not a boolean, and each maps to its own degradation `reason_code` --
    `budget_exhausted` only when infeasibility is mathematically proven
    (a single fragment larger than the budget, or total size exceeding
    `capacity * max_chunks`, or the search exhausting every arrangement);
    `packing_search_exhausted` when the bounded search could not confirm
    feasibility either way before its state budget ran out (a valid
    packing may still exist -- see the known limitation below);
    `planner_limit_exceeded` when the fragment count itself exceeds the
    safe search input size before any search is attempted. Collapsing
    these into a single `budget_exhausted` would misrepresent "the search
    gave up" as "it mathematically does not fit". **Known, accepted
    limitation:** bin packing is NP-hard, so no finite search bound can
    guarantee finding every feasible packing. Cheap admissible pruning
    (trivial-infeasibility short-circuits, a suffix-sum room check at
    every search node) narrows the search considerably, but a numerically
    adversarial exact-fit instance (e.g. fragment sizes summing to
    *exactly* `capacity * max_chunks`, forcing zero slack in every bin)
    can still exhaust the state budget before finding a solution that
    does exist -- reported as `packing_search_exhausted`, not
    `budget_exhausted`. The deliberate trade-off is failing fast and safe
    -- `blocked_pipeline`, never a hang, a crash, or a false `planned` --
    over guaranteeing
    optimality on arbitrary input;
  - non-required (auxiliary/context) fragments are packed into leftover
    budget on a best-effort basis (first-fit-decreasing is fine here --
    suboptimal packing only means slightly less optional context fits, not
    a correctness issue) and silently dropped if they still do not fit,
    per the issue's own allowance ("podem ser reduzidos somente contextos
    auxiliares declarados") -- this is the only place content is ever
    dropped, and it is never `must_review` content.

`app/agent_review/contracts_v2.py` is unmodified, exactly as in #83 and #85:
`ContractV2Model`, `RunIdentityV2`, `compute_run_id`, and
`compute_manifest_hash_v2` are reused as-is.
`tests/agent_review/test_contracts_v2.py` has exactly one line changed: the
set of expected exported schema filenames in
`test_exported_json_schemas_are_stable_and_deny_unknown_objects` now
includes `agent-review.manifest.v2.schema.json` (see "JSON Schema export"
below) -- no other change to that file or to the frozen v2 foundation it
protects.

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

**Done, not remaining:** `schemas/agent-review/v2/agent-review.manifest.v2.schema.json`
is now published (`app/agent_review/schema_export_v2.py` renders it from
`ManifestV2`, unmodified from every other v2 contract's export path).

Because of (1)-(4), the acceptance criteria that require an actual diff
acquisition path, symbol-aware grouping, or full pipeline propagation are
**not yet** met by this delivery. What is implemented (the manifest's
losslessness invariant, the line-range planner, and the published schema)
is real, tested, and safe to build on.
