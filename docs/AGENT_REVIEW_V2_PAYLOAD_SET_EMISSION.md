# AgentReview v2 — emitting and cross-validating PayloadSetV2 (#132)

Refs #132, child of tracker #110 (payload). Consumes #105's `PayloadSetV2`
contract and its `bind_payload_set_to_manifest_v2` (merged, PR #138), and
#129's real `ManifestV2` assembly (merged, PR #142). Closes tracker #110:
both F1/#131 and F2/#132 are now complete.

Delivers exactly what the issue asks: emission of a real `PayloadSetV2`
from real `ChunkPayloadV2` objects, the full cross-validation chain against
both the manifest and the real payloads, and the CLI. No readiness, no
conformance — both deliberately out of scope.

## What #105 already had, and what was still missing

`payload_set_v2.py` (#105) already defined the contract, its canonical hash
(`compute_payload_set_sha256_v2`, revalidated on read via
`verify_payload_set_sha256_v2`), and `bind_payload_set_to_manifest_v2` —
`run_id`/`manifest_hash`/exact `chunk_id` SET match against a `ManifestV2`.
What #105 explicitly deferred to this issue: cross-validating a
`PayloadSetV2` against the REAL `ChunkPayloadV2` objects it claims to
attest, not merely their `chunk_id`s, and emitting one honestly from real
payloads in the first place.

## `app/agent_review/payload_set_emission_v2.py`

**`bind_payload_set_to_payloads_v2(payload_set, payloads, manifest)`** never
re-derives a check #105 already owns — it calls
`bind_payload_set_to_manifest_v2` first, unmodified, then adds the checks
that require the actual payload objects:

1. `run_id`/`manifest_hash`/exact `chunk_id` set match — delegated to #105.
2. Every entry's `chunk_id` resolves to a real payload — an entry with no
   matching payload is `PAYLOAD_SET_ENTRY_MISSING_PAYLOAD_REASON_V2`.
3. Every supplied payload's `chunk_id` is named by some entry — an extra,
   unattested payload is `PAYLOAD_SET_EXTRA_PAYLOAD_REASON_V2`.
4. Each resolved payload is revalidated with
   `contracts_v2.verify_payload_sha256_v2` (reused, not reimplemented) —
   `PAYLOAD_SET_PAYLOAD_TAMPERED_REASON_V2`.
5. `payload.payload_sha256 == entry.payload_sha256` —
   `PAYLOAD_SET_ENTRY_PAYLOAD_HASH_MISMATCH_REASON_V2`.
6. `payload.run_id`, `payload.chunk_id`, and `payload.identity.manifest_hash`
   are each individually coherent with the manifest and the entry —
   `PAYLOAD_SET_PAYLOAD_RUN_ID_INCOHERENT_REASON_V2`/
   `PAYLOAD_SET_PAYLOAD_CHUNK_ID_INCOHERENT_REASON_V2`/
   `PAYLOAD_SET_PAYLOAD_MANIFEST_HASH_INCOHERENT_REASON_V2`.

Every clause raises a distinct, stable reason code; none is a warning.

**`run_id` and `manifest_hash` incoherence are both genuinely reachable,
independent checks — confirmed by mutation testing, not assumed.**
`plan_lossless_chunks_v2`'s default `chunk_id` numbering (`chunk-0000`,
`chunk-0001`, ...) is NOT globally unique across different runs — two
independent single-chunk manifests built the same way produce the
IDENTICAL `chunk_id`, confirmed directly. A payload that is genuinely
valid on its own (`verify_payload_sha256_v2` passes) but was built for a
DIFFERENT run can therefore be smuggled into this payload set via a
coincidental `chunk_id` collision, with the entry's `payload_sha256`
declared as that foreign payload's real hash — clause 5 alone would pass.
Temporarily disabling the `run_id` check while testing this exact scenario
showed the `manifest_hash` check independently catches the same smuggled
payload — so checking `run_id` first does not make `manifest_hash`
redundant in general, only in that one specific scenario; both stay as
independently meaningful, reachable checks.

`chunk_id` incoherence, by contrast, is provably unreachable through this
function's own call path, kept as defense in depth (mirroring
`readiness_decision_v2.py`'s and `run_assembly_v2.py`'s identical
precedent): `_payloads_by_chunk_id` keys every payload by its OWN
`chunk_id` field, so a successful `payloads_by_chunk_id[entry.chunk_id]`
lookup already guarantees the resolved payload's `chunk_id` equals
`entry.chunk_id` by construction.

**`emit_payload_set_v2(manifest, payloads)`** builds a real `PayloadSetV2`
from a manifest and its actually-built `ChunkPayloadV2` objects, then
immediately runs the full `bind_payload_set_to_payloads_v2` cross-
validation before returning — a producer that (through a bug) built a
tampered or incoherent payload must not silently mint a set that certifies
it as good.

## `scripts/aiops-review-build-payload-set-v2.py`

Thin CLI wiring around `emit_payload_set_v2` — no second implementation of
the cross-validation. Reads a `ManifestV2` JSON file and a directory of
already-built `ChunkPayloadV2` JSON files (one per chunk, any filenames),
writes the resulting `PayloadSetV2` JSON. `--contract-version v2` is
required and explicit; any other value is refused. Fails closed (non-zero
exit, no output file written) on a manifest/payload mismatch, an empty
payloads directory, or invalid JSON — the same reason codes
`emit_payload_set_v2`/`bind_payload_set_to_payloads_v2` raise, printed to
stderr.

Named per the CLI naming decision registered in #102: a brand-new v2 CLI
script (no v1 namesake to collide with), using the established `-v2`
suffix convention (matching `PayloadSetV2.source =
"aiops-review-build-payload-set-v2"`, chosen ahead of this decision when
#105 was implemented).

## Deliberately out of scope

- readiness (C1/C2);
- conformance dual-target (#86);
- any change to `PayloadSetV2`'s own contract (frozen by #105 — a real
  need to change it is a stop condition, not a local edit).

## Tests

`tests/agent_review/test_payload_set_emission_v2.py` — 11 tests: emission
(a valid payload set from real manifest+payloads, byte-reproducible and
order-independent, two different manifests produce different payload
sets), and one regression per cross-validation clause (`run_id` mismatch,
`manifest_hash` mismatch, an extra entry beyond the manifest's chunks, an
entry with no matching payload, an extra unattested payload, a tampered
payload caught by `verify_payload_sha256_v2`, an entry `payload_sha256`
that doesn't match the real payload, and the precise
foreign-run-payload-smuggled-via-chunk_id-collision scenario proving
`run_id` incoherence).

`tests/agent_review/test_aiops_review_build_payload_set_v2_cli.py` — 4
tests: a valid emission via subprocess, refusal without
`--contract-version v2`, fail-closed on a manifest/payload mismatch, and
fail-closed on an empty payloads directory.

**Verification of non-vacuity:** the `run_id` incoherence guard was
temporarily disabled; the corresponding test then failed by falling through
to (and being caught by) the `manifest_hash` incoherence check instead —
which is how the "both independently reachable" claim above was actually
confirmed, not assumed. The guard was restored and the test asserts the
precise `run_id` reason code.
