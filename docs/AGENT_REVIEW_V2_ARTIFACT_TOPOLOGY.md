# AgentReview v2 — the manifest/run/payload artifact topology (#105)

Refs #105, child of the contract-closure epic #102. Blocks #110 (F2: PayloadSet
emission + CLI, which will produce a real `PayloadSetV2` from a real run).

Delivers exactly what the issue asks: the contract, its self-hash, the
manifest cross-validation, and the documentation of a limitation already
decided. No run assembly, no payload construction, no CLI — that is #110's
job, deliberately kept out of this issue.

## Problem this closes

`ManifestChunkV2.payload_sha256` is null-only by type (`manifest_v2.py`)
because of a genuine circularity: `identity.manifest_hash` certifies the
manifest's content, and `RunIdentityV2.run_id` is derived from `identity`
(`compute_run_id`). Populating a chunk's `payload_sha256` would change the
manifest's own content, which would change `manifest_hash`, which would force
a new `run_id` — which the payload material itself embeds
(`ChunkPayloadMaterialV2.identity`, `contracts_v2.py:575`). The hash just
inserted would already be stale the moment it was written. There is no
non-circular way to fold a built payload's hash back into the manifest that
describes it.

Before this issue, nothing linked "the set of payloads a run actually built"
to that run at all.

## Decision taken (per the issue's own recommended alternative: 1 + 2, deferring 3)

1. **Payloads remain deterministic derivatives, outside run identity.**
   `ChunkPayloadV2.payload_sha256` (`contracts_v2.py:594-602`) is already
   reproducible by recomputation from `ChunkPayloadMaterialV2` — this issue
   changes nothing about that.
2. **`PayloadSetV2` is the new attestation artifact**, bound to a run by
   `run_id` + `manifest_hash` (plain fields, not a nested `RunIdentityV2`),
   listing every chunk's `payload_sha256`. It certifies "these are the
   payloads actually built for this run" without touching `RunIdentityV2`,
   `ManifestV2`, or any golden fixture.
3. **A future `RunIdentityV2.1` embedding a `payload_set_hash` inside
   identity itself is explicitly deferred** — it would break v2 identity and
   compatibility already merged, and is only worth doing if (1)+(2) turn out
   not to cover a real need discovered after release preparation (#89).

## What this delivery adds

### `app/agent_review/payload_set_v2.py`

**`PayloadSetEntryV2`** — one entry: `chunk_id` (`SafeIdentifier`),
`payload_sha256` (`Sha256`).

**`PayloadSetMaterialV2`** / **`PayloadSetV2`** — the same
`*Material*`/self-hashing split already established by
`ManifestMaterialV2`/`ManifestV2`, `ChunkPayloadMaterialV2`/`ChunkPayloadV2`,
and `SemanticGroupingPolicyMaterialV2`/`SemanticGroupingPolicyV2`, reused
rather than reinvented. Seven fields total: `schema_id`, `schema_version`,
`source`, `run_id`, `manifest_hash`, `payloads`, `payload_set_sha256`.

Structural validity (self-contained, no external object needed):

- at least one payload entry;
- `chunk_id` unique across the set;
- `payload_set_sha256` covers every other field, validated against
  `compute_payload_set_sha256_v2`.

**Canonical ordering / byte-reproducibility.** `canonical_payload_set_bytes_v2`
re-sorts `payloads` by `chunk_id` before hashing — the same
canonical-ordering discipline `canonical_semantic_grouping_policy_bytes_v2`
and `canonical_effective_policy_bytes_v2` already apply to their own
list-of-entries fields: `sort_keys=True` on the JSON dump already makes
dict key order irrelevant, but says nothing about list element order.
Entries are already validated unique by `chunk_id`, so sorting cannot
silently collapse two distinct entries. Reordering the constructor's input
never changes the canonical bytes.

**Revalidation on every read, not just at construction.**
`verify_payload_set_sha256_v2(payload_set)` mirrors
`contracts_v2.verify_payload_sha256_v2`: it re-serializes the payload set to
canonical JSON and re-runs full model validation through
`PayloadSetV2.model_validate_json`, catching an object that bypassed
`validate_payload_set_hash` (for example via `model_copy(update=...)`, which
does not re-run model validators) rather than trusting that the in-memory
object was built through the normal constructor.

**Two grandezas kept distinct**, matching the rest of v2's discipline of not
conflating "internally coherent" with "matches this specific external
object":

```text
validade estrutural do payload set   = self-consistent on its own (unique
                                         chunk_id, non-empty payloads,
                                         payload_set_sha256 correctness)
validade contra um manifest          = run_id/manifest_hash match a SPECIFIC
                                         ManifestV2, and the payload set's
                                         chunk_id set is EXACTLY the
                                         manifest's chunk_id set
```

A bare `PayloadSetV2` cannot check the second on its own — same reasoning as
`RunFragmentCoverageEntryV2` vs. `bind_coverage_report_to_manifest_v2`
(#104/#115) and `SemanticGroupingPolicyV2` vs.
`bind_semantic_grouping_policy_to_target_profile_v2` (#106): a Pydantic model
has no way to reach out to another object at construction time.

**`bind_payload_set_to_manifest_v2(payload_set, manifest)`** — the binding
function, published as reusable code for #110/F2 to consume, not left as
prose. Raises `PayloadSetBindingError` fail-closed if:

- `run_id` does not match the manifest's own `run_id`
  (`payload_set_run_id_mismatch`);
- `manifest_hash` does not match `manifest.identity.manifest_hash`
  (`payload_set_manifest_hash_mismatch`);
- the payload set's `chunk_id` set is not EXACTLY the manifest's `chunk_id`
  set — neither missing (a chunk the manifest expects a payload for, that
  the set never attests) nor extra (an unknown `chunk_id` the manifest never
  produced) (`payload_set_chunk_set_mismatch`).

## `ManifestChunkV2.payload_sha256` — decision, not limitation

The field's docstring in `manifest_v2.py` previously framed the null-only
typing as "there is no non-circular way to populate this field in advance".
That undersold it: there IS a non-circular resolution, and it is
`PayloadSetV2`, deliberately kept as a separate artifact instead of being
folded back into this field or into `RunIdentityV2`. The docstring has been
updated to say so explicitly.

## Limitation to register (verbatim from the issue, not softened)

> `PayloadSetV2` é ligado ao run, mas NÃO faz parte do `run_id`. A unicidade
> é garantida pela derivação determinística dos payloads e pela
> recomputação no produtor confiável — não por identidade criptográfica
> embutida. Uma ligação DENTRO da identidade pertence a uma futura
> `RunIdentityV2.1`, fora do escopo desta convergência.

Concretely: nothing prevents constructing two textually different
`PayloadSetV2` objects that both validly bind to the same manifest (for
example, entries in a different — but still validly sorted-at-hash-time —
input order still produce byte-identical canonical bytes and therefore the
identical `payload_set_sha256`; but two *semantically different* payload
sets, e.g. built by two different provider runs, are only distinguished by
comparing `payload_set_sha256` values out of band — `run_id` alone does not
disambiguate them, because `PayloadSetV2` is not part of `_RUN_IDENTITY_FIELDS`).

## Contract topology (for #102's shared surface)

```text
consumed by #110/F2 (PayloadSet emission + CLI):
  PayloadSetV2
  bind_payload_set_to_manifest_v2
  compute_payload_set_sha256_v2 / verify_payload_set_sha256_v2

produced by #110/F2, not this issue:
  a real PayloadSetV2 built from a real ManifestV2 + real ChunkPayloadV2 set

untouched, frozen:
  RunIdentityV2 (contracts_v2.py) -- no new field, no change to
    _RUN_IDENTITY_FIELDS
  ManifestV2 -- structure unchanged; only ManifestChunkV2.payload_sha256's
    docstring updated to state the decision explicitly
  ChunkPayloadV2 / compute_payload_sha256_v2 -- unchanged
```

## Deliberately out of scope

- assembling a real run/manifest/payload set end to end (#109/E2, #110/F2);
- any CLI;
- a `RunIdentityV2.1` embedding `payload_set_hash` inside identity (deferred,
  conditioned on a need (1)+(2) do not cover, discovered after #89).

## Tests

`tests/agent_review/test_payload_set_v2.py` — 14 tests covering every
invariant listed above: material/entry internal validity (minimum one entry,
duplicate `chunk_id` rejection), `payload_set_sha256` correctness,
determinism, order-independence, and sensitivity to a real content change;
`verify_payload_set_sha256_v2` accepting a correctly constructed set and
rejecting one that bypassed construction validation via `model_copy`; and
`bind_payload_set_to_manifest_v2` against a real `ManifestV2` built through
`plan_lossless_chunks_v2` (accepted single- and two-chunk cases, `run_id`
mismatch, `manifest_hash` mismatch, a missing chunk, and an extra unknown
chunk).
