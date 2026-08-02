# AgentReview v2 — profile-derived payload references (#131)

Refs #131, child of tracker #110 (payload). Consumes #85's `TargetProfileV2`
(loaded by `profile_loader_v2.load_target_profile_v2`). Blocks nothing
structurally — F2/#132 (`PayloadSetV2` emission) only needs a real, already-
built `ChunkPayloadV2` to attest against, which this issue now provides.

Delivers exactly what the issue asks: populates real
`artifact_references`/`contract_references` on `ChunkPayloadV2`, hardcoded
`[]` until now (`payload_builder_v2.py:128-129`). No `PayloadSetV2`
emission, no CLI — both deliberately out of scope.

## Problem this closes

`build_chunk_payload_v2` always set `artifact_references`/`contract_references`
to `[]`. Verified before this issue: this rewrites zero committed fixtures
— `golden_chunk_payload_hash.json` already contains populated references
and is consumed only by `test_contracts_v2.py`'s pure hash test over a
hand-built `_payload()`, which never calls `build_chunk_payload_v2`.

## `app/agent_review/payload_references_v2.py`

Two functions, `build_payload_artifact_references_v2` and
`build_payload_contract_references_v2`, each reading real content from a
target checkout (`repo_root`) per `profile.artifacts`/`profile.contracts`.

**Artifacts and contracts are verified differently, deliberately:**

- **Artifacts** (`TargetArtifactV2`) have no pre-declared hash in the
  profile — there is nothing to "diverge" from. What can fail is presence
  (`required=True` but missing → fail closed,
  `PAYLOAD_REQUIRED_ARTIFACT_MISSING_REASON_V2`) and size (`max_bytes`
  exceeded → refuse, never silently truncate,
  `PAYLOAD_ARTIFACT_EXCEEDS_MAX_BYTES_REASON_V2`). Content is sanitized
  (`redaction.sanitize_artifact_value`) **before** hashing: an artifact's
  raw text could plausibly reach an LLM prompt later (that is what an
  "artifact reference" is *for*), so it must never be referenced by a hash
  computed over unsanitized content.
- **Contracts** (`TargetContractV2`) DO carry a pre-declared `sha256` — the
  whole point of referencing one is proving the real file on disk still
  matches what the profile claims. Comparing a *redacted* hash against
  that pre-declared value would only ever match if the profile's own
  `sha256` had also been computed post-redaction, which cannot be assumed;
  and since only the hash is ever embedded in the resulting reference
  (never raw content), there is no exposure risk in hashing the raw
  bytes. Contract content is therefore never redacted before hashing; a
  mismatch fails closed (`PAYLOAD_CONTRACT_SHA256_MISMATCH_REASON_V2`).

**Missing optional content is documented, never silently absorbed.** An
optional (`required=False`) missing artifact is skipped and named in the
returned `limitations` tuple (`optional_artifact_missing:<artifact_id>`) —
mirroring how #107's `SynthesisResultV2.limitations` already documents
dropped-but-not-fatal facts. A non-required missing contract is simply
omitted — contracts have no analogous "optional" concept to surface,
`TargetContractV2.required` already exists purely to gate the hard failure.

**No kind-aware parsing.** Every declared `kind`
(`json`/`yaml`/`text`/`markdown`/`diff`) is hashed/redacted uniformly as
plain UTF-8 text — `sanitize_artifact_value` already redacts a plain string
via the same regex-based scanning (`redact_text`) any other string field in
this codebase goes through. Structured, kind-specific parsing (e.g.
JSON/YAML-aware field redaction) is unrequested complexity this issue's own
acceptance criteria never asks for.

**`PayloadContractReferenceV2.paths` is always empty.** Nothing in
`TargetContractV2` associates a contract with a specific subset of the
diff's changed files, so no per-file linkage is attempted — a
repository-scoped contract reference applies without needing one.

**Uniform artifact role.** Every artifact is referenced with
`role="primary"` — `TargetArtifactV2` carries no field distinguishing
"primary" from "supporting"/"validation"/"coverage" content, so inventing a
per-artifact role assignment would be a guess this module has no real
signal to base on.

## `payload_builder_v2.py` wiring

`build_chunk_payload_v2`/`build_chunk_payloads_v2` — the two original entry
points — are **unchanged in signature and behavior**: every existing
caller with no `TargetProfileV2`/checkout keeps getting empty references,
exactly as before. `BuiltChunkPayloadV2` gained one new, defaulted field,
`limitations: tuple[str, ...] = ()`, backward compatible (nothing outside
this module ever constructs it directly).

Two new entry points populate real references:

- `build_chunk_payload_from_profile_v2(manifest, chunk, *, profile,
  repo_root) -> BuiltChunkPayloadV2`
- `build_chunk_payloads_from_profile_v2(manifest, *, profile, repo_root) ->
  tuple[BuiltChunkPayloadV2, ...]` — reads artifact/contract content
  exactly once, reused across every chunk: `TargetArtifactV2`/
  `TargetContractV2` are profile-level, not chunk-scoped, and nothing
  associates a specific chunk or semantic group with a reference, so every
  chunk of the same manifest+profile shares the identical reference set.

## Deliberately out of scope

- `PayloadSetV2` emission or validation (F2/#132);
- any CLI;
- kind-aware structured parsing of artifact content;
- per-chunk/per-semantic-group filtering of contract references (nothing
  in `TargetContractV2` provides the information needed to do this
  correctly).

## Tests

`tests/agent_review/test_payload_references_v2.py` — 13 tests: artifact
references (present artifact, content sanitized before hashing —
confirmed the hash differs from a raw-content hash and matches
`sanitize_artifact_value`'s own output, required-missing fails closed,
optional-missing skipped with a limitation, over-`max_bytes` refused);
contract references (present contract with matching `sha256`, mismatch
fails closed, required-missing fails closed, non-required-missing silently
omitted); and the `payload_builder_v2.py` wiring (original entry point
still produces empty references — backward compatibility; the new entry
point populates real references; every chunk of a multi-chunk manifest
shares the identical reference set; two profiles with different artifact
content produce different `payload_sha256` values — the issue's own
acceptance criterion, verbatim).

**Verification of non-vacuity:** the contract `sha256` mismatch guard was
temporarily disabled and the corresponding test confirmed to fail, then
restored.
