# AgentReview v2 — the canonical evidence hash (#128)

Refs #128, child of tracker #109 (assembly). Defines and computes
`RunIdentityV2.evidence_hash` (`contracts_v2.py:413`), which until now
existed only as a declared field with no production producer. Blocks
E2/#129 (real run/manifest assembly), which will populate a real
`EvidenceBundleV2` and fold `compute_evidence_hash_v2`'s output into
`RunIdentityV2.evidence_hash`.

Delivers exactly what the issue asks: the contract for a sanitized evidence
bundle, and the canonical hash function over it. No manifest assembly, no
diff adapter, no run-assembly wiring — deliberately E2/#129's job.

## Problem this closes

`evidence_hash` is inside `_RUN_IDENTITY_FIELDS` (`contracts_v2.py:47-58`),
therefore inside the preimage of `run_id` itself. Defining it incorrectly is
irreversible without re-hashing everything downstream (manifest, payloads,
run identity) — this is contract definition, not wiring, and the issue
treats it as its own reviewable unit for exactly that reason.

## What an "evidence bundle" is

A sanitized, minimal list of references to the target-declared inputs a run
actually drew on — artifacts (`TargetArtifactV2`) and contracts
(`TargetContractV2`) — each identified by id and a content `sha256`, never
raw content. This mirrors two existing precedents rather than inventing a
third:

- v2's existing reference-by-hash discipline
  (`PayloadArtifactReferenceV2`/`PayloadContractReferenceV2`,
  `contracts_v2.py:555-568`);
- v1's `evidence_index.py`'s `build_evidence_index`, which already lists
  sanitized artifact presence/validity references rather than embedding
  content.

Computing the actual `sha256` of a real artifact's sanitized content (via
`redaction.sanitize_artifact_value`) requires reading that artifact from a
real target checkout — assembly, deliberately out of scope here. This module
owns only the contract for a bundle of already-computed references; E2
populates real `EvidenceReferenceV2` entries from a real run.

## `app/agent_review/evidence_hash_v2.py`

**`EvidenceReferenceV2`** — `reference_id` (`SafeIdentifier`), `kind`
(`"artifact"` or `"contract"` — deliberately NOT `"check"`: pr_state/checks
belong to C2/#130, not evidence collection), `sha256` (`Sha256`), `detail`
(`SafeText`).

**`EvidenceBundleV2`** — `schema_id`/`schema_version`/`source` plus
`references: list[EvidenceReferenceV2]`. Validates references unique by
`(kind, reference_id)` — the same id can legitimately appear once as an
artifact and once as a contract without collision. An empty `references`
list is valid: a target profile with zero configured artifacts/contracts
still needs a deterministic, non-blocking `evidence_hash`.

**Why no `*Material*`/self-hashing split, unlike every other v2 artifact so
far:** `ManifestMaterialV2`/`ManifestV2`, `ChunkPayloadMaterialV2`/
`ChunkPayloadV2`, `SemanticGroupingPolicyMaterialV2`/
`SemanticGroupingPolicyV2`, and `PayloadSetMaterialV2`/`PayloadSetV2` all
split because the object carries its OWN hash as one of its own fields,
which would otherwise make the hash preimage circular.
`EvidenceBundleV2` does not carry its own hash — `evidence_hash` lives on
`RunIdentityV2`, a *different* object, exactly the shape `manifest_v2.py`'s
own module docstring already describes for `manifest_hash` ("the field
being kept out of the hash preimage lives on a different object"). No split
is needed: the bundle hashes in full, unconditionally.

**`canonical_evidence_bundle_bytes_v2(bundle)`** — hash preimage: every
validated field, with `references` re-sorted by `(kind, reference_id)`
before hashing — the same canonical-ordering discipline every other
list-of-entries hash in this v2 surface already applies
(`canonical_semantic_grouping_policy_bytes_v2`'s `rules`,
`canonical_payload_set_bytes_v2`'s `payloads`). References are already
validated unique by that same key, so sorting cannot silently collapse two
distinct entries, and reordering the constructor's input never changes
these bytes.

**`compute_evidence_hash_v2(bundle)`** — the value meant for
`RunIdentityV2.evidence_hash` once E2/#129 assembles a real run.

## Deliberately out of scope

- manifest assembly (E2/#129);
- the diff adapter (E2/#129);
- any run-assembly wiring (E2/#129);
- reading real artifacts from a target checkout or calling
  `redaction.sanitize_artifact_value` (E2's job — this module never touches
  raw content, only already-computed `sha256` references);
- a `"check"` reference kind (pr_state/checks belong to C2/#130).

## Tests

`tests/agent_review/test_evidence_hash_v2.py` — 12 tests: internal validity
(zero references allowed, duplicate `(kind, reference_id)` rejected, same
`reference_id` across different kinds accepted, unknown `kind` rejected,
unknown `schema_id` rejected), and `compute_evidence_hash_v2` determinism,
order-independence, sensitivity to a changed `sha256`, sensitivity to an
added reference, stability of the empty-bundle hash, mapping-vs-model
equivalence, and rejection of a non-model/non-mapping input.
