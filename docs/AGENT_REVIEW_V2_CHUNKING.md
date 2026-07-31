# AgentReview v2 — typed manifest and lossless chunk planning (#84)

Refs #84. Builds on the v2 binding delivery (#83, `docs/AGENT_REVIEW_V2_BINDING.md`)
and the v2 profile/lock delivery (#85, `docs/AGENT_REVIEW_V2_TARGET_PROFILE.md`).
Delivers every entregável the issue explicitly lists (`manifest_v2.py`,
`planner_v2.py`, `payload_builder_v2.py`, the manifest JSON Schema, and
tests proving N-chunk propagation), plus real git-diff acquisition, which
was in the issue's problem statement even though not in its file list.
One piece is deliberately not attempted: symbol/AST-aware grouping, which
the issue itself frames as optional ("quando disponível e comprovável")
with deterministic fallbacks that are fully implemented instead. See "What
remains" for the precise, honest boundary of this delivery.

## What this delivery adds

### `app/agent_review/manifest_v2.py`

`ManifestMaterialV2`, `ManifestV2`, `FragmentV2`, `LineRangeV2`,
`ManifestChunkV2`, `ManifestDegradationV2`. Strict, `frozen=True`,
`extra="forbid"` models (via `contracts_v2.ContractV2Model`, reused
unmodified) with a structural losslessness invariant: every
`coverage_required` fragment must be referenced by exactly one chunk, or
explicitly accounted for by a degradation cause -- an unexplained omission
is a hard `ValidationError`, never a silently accepted partial manifest.
Every fragment belonging to a `must_review_files` path must itself be
`coverage_required` (not just "at least one" fragment for that path) --
closing the case where a large must-review file is split and only part of
it is actually required. A degradation cause must name exactly the missing
fragments, never an already-covered one. `expected_files`/
`must_review_files` reject duplicates. Chunks reject duplicate
`order_index` values (mirroring the existing v1 precedent in
`chunk_payload_builder.py`).

`compute_manifest_hash_v2_for(material)` reuses
`contracts_v2.compute_manifest_hash_v2` (already frozen by PR #81)
unmodified, so a material change to any fragment or chunk flips the hash
that feeds `RunIdentityV2.manifest_hash`. **`ManifestV2` extends
`ManifestMaterialV2` rather than embedding `run_id`/`identity` in the hash
preimage.** `RunIdentityV2` itself contains `manifest_hash`, and
`ManifestV2` carries `identity: RunIdentityV2` -- hashing the full manifest
(identity included) would require `identity.manifest_hash` to already be
known before it could be computed. `compute_manifest_hash_v2_for` always
excludes `run_id`/`identity` from the preimage (via `model_dump(...,
exclude={"run_id", "identity"})`, safe on either a bare
`ManifestMaterialV2` or a full `ManifestV2`), and `ManifestV2`'s own
validator proves `identity.manifest_hash == compute_manifest_hash_v2_for(self)`
-- so a manifest carrying a stale or foreign `manifest_hash` is rejected,
not silently accepted. This mirrors the `ChunkPayloadMaterialV2`/
`ChunkPayloadV2` split already established in `contracts_v2.py`, applied
one level up since the self-referential field lives on `identity`, not on
`ManifestV2` itself.

### `app/agent_review/diff_acquisition_v2.py`

`parse_unified_diff` -- a pure text parser (no subprocess, no I/O) turning
`git diff --no-ext-diff --binary BASE...HEAD` output into structured
per-file, per-hunk records. Handles additions, deletions, modifications,
renames/copies (with similarity index), binary files (both the
human-readable `Binary files ... differ` marker and the real `GIT binary
patch` base85 format `--binary` actually produces -- verified against real
`git diff --binary` output, since that format has neither `---`/`+++`
markers nor a "differ" line), submodules/gitlinks (mode `160000`, also
verified against real git output), a missing trailing newline on either
side, and a **truncated hunk**: a header declaring more old/new lines than
its body actually supplies, which is flagged rather than trusted at its
declared range. `validate_diff_completeness_v2` reports missing /
unrepresentable (binary, submodule) / truncated paths distinctly, so a
caller routes each to the right remediation (explicit policy vs. blob
reconstruction per the issue's own text) instead of silently treating any
of them as covered.

`acquire_diff_v2` is a thin, fixed-argv subprocess wrapper for the exact
canonical command -- never a shell, never a caller-controlled command
string; `base_sha`/`head_sha` must each be a full lowercase 40-character
commit SHA, rejected otherwise before ever reaching `subprocess.run`.

Never retains or forwards raw hunk content: each hunk's body is hashed
into `diff_sha256` and discarded immediately, matching the same "no raw
diff/payload in artifacts" boundary `contracts_v2`'s own payload/response
hashing already enforces.

### `app/agent_review/planner_v2.py`

`HunkInputV2`, `plan_lossless_chunks_v2`. Given already-parsed hunks (from
`diff_acquisition_v2`, or any other source producing the same shape) and a
per-chunk line budget:

- a hunk within budget becomes one fragment (fallback #2 from the issue's
  own deterministic fallback list: "hunk completo");
- a hunk larger than budget is split into stable, disjoint windows on
  *both* sides proportionally (fallback #3: "janelas de linha estáveis"),
  covering it exactly -- verified by union-equals-input tests, never a
  shrinker. A deletion-only hunk (no new-side content) is windowed by its
  old-side size instead of being under-counted as free; an imbalanced
  hunk (one side much smaller than the number of windows the other side
  needs) anchors the smaller side's excess windows to its last available
  line instead of producing an invalid inverted range;
- `must_review` (`coverage_required`) fragments are packed into chunks
  with an *exact* backtracking bin-packing decision procedure
  (`_pack_fragments_exact`, deterministic tie-break by `fragment_id`,
  admissible suffix-sum pruning at every search node) -- not a greedy
  heuristic like first-fit-decreasing, which is not guaranteed optimal and
  could wrongly report `blocked_pipeline` for content that actually fits.
  **The reason code distinguishes *why*, honestly:** `_pack_fragments_exact`
  returns a three-way `ExactPackingResultV2` (`found` / `proven_infeasible`
  / `search_exhausted` / `input_too_large`), not a boolean, and each maps
  to its own degradation `reason_code` -- `budget_exhausted` only when
  infeasibility is mathematically proven; `packing_search_exhausted` when
  the bounded search could not confirm feasibility either way before its
  state budget ran out (a valid packing may still exist); `planner_limit_exceeded`
  when the fragment count itself exceeds the safe search input size before
  any search is attempted. Collapsing these into a single
  `budget_exhausted` would misrepresent "the search gave up" as "it
  mathematically does not fit". **Known, accepted limitation:** bin
  packing is NP-hard, so no finite search bound can guarantee finding
  every feasible packing; a numerically adversarial exact-fit instance can
  still exhaust the state budget -- reported honestly as
  `packing_search_exhausted`, never as `budget_exhausted`;
- non-required (auxiliary/context) fragments are packed into leftover
  budget on a best-effort basis and silently dropped if they still do not
  fit, per the issue's own allowance ("podem ser reduzidos somente
  contextos auxiliares declarados") -- this is the only place content is
  ever dropped, and it is never `must_review` content.

### `app/agent_review/payload_builder_v2.py`

`build_chunk_payload_v2`/`build_chunk_payloads_v2` turn a `planned`
manifest's chunks into actual `contracts_v2.ChunkPayloadV2` objects,
reusing `compute_payload_sha256_v2` unmodified -- the same hashing
authority `consumer_v2.py` (#83) already binds responses against.

**Documented coverage-granularity gap, not silently papered over:**
`ChunkCoverageV2` (frozen by PR #81) is file-granular; `FragmentV2` (#84)
is line-range-granular. When a file's fragments are split across multiple
chunks -- #84's whole point for large files -- each individual chunk's
payload coverage honestly reports that file as `partially_reviewed`, never
`reviewed`, since no single chunk carries the complete file. Resolving
this fully would mean extending `ChunkCoverageV2` with line-range
awareness (touching the frozen v2 foundation, avoided throughout
#83/#85/#84) or introducing a new payload-level fragment reference
contract; neither is attempted here. `artifact_references`/
`contract_references` are left empty -- populating them with real
target-profile-derived contract references is follow-up work integrating
with #85's profile loader.

### N-chunk propagation through the existing #83 consumer/parser

`tests/agent_review/test_n_chunk_propagation_v2.py` proves, for a 3-chunk
manifest, that manifest → `payload_builder_v2` → `consumer_v2.bind_chunk_response_v2`
→ `parser_v2.parse_bound_chunk_response_v2` preserves fragment/chunk
provenance across all N chunks simultaneously (every parsed chunk's
findings stay within its own fragments' file scope, cross-chunk swaps are
still rejected, and the pipeline is deterministic) -- without changing
`consumer_v2.py`/`parser_v2.py` at all, since their existing single-chunk
design already composes correctly when called once per chunk.

`app/agent_review/contracts_v2.py` is unmodified, exactly as in #83 and
#85: `ContractV2Model`, `RunIdentityV2`, `compute_run_id`,
`compute_manifest_hash_v2`, and `compute_payload_sha256_v2` are reused
as-is. `tests/agent_review/test_contracts_v2.py` has exactly one line
changed: the set of expected exported schema filenames in
`test_exported_json_schemas_are_stable_and_deny_unknown_objects` now
includes `agent-review.manifest.v2.schema.json` -- no other change to
that file or to the frozen v2 foundation it protects.

## What remains (deliberately, explicitly)

1. **Symbol/AST-aware grouping** (fallback #1 in the issue: "símbolo/AST
   quando disponível e comprovável"). The issue itself frames this as an
   enhancement layered over deterministic fallbacks #2/#3, both of which
   are fully implemented. This delivery groups by whatever
   `semantic_group` the caller already decided; it does not analyze
   imports, changed symbols, or which tests exercise them. Implementing
   real per-language AST analysis is a substantially larger, more
   speculative undertaking than the rest of this delivery and was not
   attempted.
2. **Propagation into synthesizer-v2, telemetry-v2, or a readiness-v2
   gate.** These have no concrete implementation anywhere in this
   repository yet -- not in #83, not here. `contracts_v2.ReviewReadinessV2`
   is a frozen *contract* (PR #81); nothing computes one. Extending this
   delivery to "propagate" into components that do not exist would mean
   inventing them, which is out of #84's stated entregáveis and belongs to
   future work once that scope is actually opened.

Everything else the issue's acceptance criteria ask for -- a strict
deny-unknown published manifest schema, large files splitting into
multiple chunks without truncating required content, exact union-of-covered-ranges
equal to union-of-expected-ranges, no undeclared overlap/silent
omission/coverage promotion, incomplete `must_review` blocking readiness
(structurally, via the manifest's own validation), the builder and parser
accepting N chunks per group, and the v1 suite remaining green -- is
implemented and tested.
