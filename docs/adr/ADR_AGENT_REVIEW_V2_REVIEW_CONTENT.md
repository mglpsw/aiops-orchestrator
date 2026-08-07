# ADR — AgentReview v2 semantic content contract (#200-A)

**Status:** accepted
**Scope:** `aiops-orchestrator#200-A`, first slice of the distribution epic
`aiops-orchestrator#199`
**Decides:** how real, redacted hunk content is represented, hashed, and
carried alongside a chunk payload without touching any already-published v2
contract

## Context

AgentReview v2's offline pipeline (`run_assembly_v2` → `manifest_v2` →
`payload_builder_v2` → `payload_set_emission_v2` → `consumer_v2` →
`parser_v2` → `synthesis_v2` → `readiness_decision_v2`) is complete and
tested end to end, but `ChunkPayloadV2` never carries the actual bytes of a
hunk. `FragmentV2` records `path`/`old_range`/`new_range`/`diff_sha256` --
never the diff text itself. AgentEscala's shadow adoption (`#759`) surfaced
this as the blocker `review_content_extraction_not_implemented`: the v2
pipeline can compute a fully valid, fully bound readiness decision without a
model ever having seen one line of real code.

Closing this requires answering, and freezing, three separable questions:

1. where does real content live relative to `ChunkPayloadV2`;
2. how is a response provably tied to the specific content it was produced
   over, not merely to the chunk it claims to review;
3. how can a target declare a redaction/DLP policy without the analysis job
   ever importing or executing code from the target's own repository.

## Decision 1 — sidecar, not an extension of `ChunkPayloadV2`

**Decision:** define `ReviewContentV2` (`app/agent_review/review_content_v2.py`)
as a new, independently versioned artifact bound to a manifest by
`run_id`/`manifest_hash` -- never as a new field on `ChunkPayloadV2`.

**Why not extend `ChunkPayloadV2`:** `payload_sha256`
(`contracts_v2.compute_payload_sha256_v2`) is computed over every field of
`ChunkPayloadMaterialV2`, and is the exact value `consumer_v2.
validate_response_binding_v2` compares against every incoming response. It
is also already published (`v0.21.0`) and pinned by every consumer of this
toolrepo. Adding a content field would change that hash for every chunk
ever built, invalidating every already-pinned binding and forcing a new
`schema_version` on a contract this codebase has explicitly decided never to
change silently (see `contracts_v2.py`'s own module docstring: "freezes the
data contracts that ... consumers may adopt through explicit future
migrations").

**Precedent reused, not invented:** `payload_set_v2.PayloadSetV2` solves the
identical shaped problem (an artifact that would otherwise create a circular
dependency into `ManifestV2`/`RunIdentityV2` if folded in directly) the same
way -- a separate artifact bound by `run_id` + `manifest_hash`. `ReviewContentV2`
follows that exact shape.

**Verified, not assumed:** every `ChunkPayloadV2` fixture across the
existing test suite (`test_payload_builder_v2.py`, `test_consumer_v2.py`,
`test_v2_dual_target_e2e.py`, and the rest of `tests/agent_review/`) still
produces byte-identical `payload_sha256` values after this change -- the
full suite (1882 tests) and the schema `--check` gate both pass unmodified.

## Decision 2 — a second integrity anchor for content, via a wrapping transport envelope

**Problem:** `payload_sha256` proves a response belongs to a chunk. It does
not prove the response was produced over one *specific* `ReviewContentV2`
sidecar rather than some other, equally `payload_sha256`-matching one built
for the same chunk -- two sidecars for the same chunk are indistinguishable
to `validate_response_binding_v2` alone.

**Decision:** every `FragmentContentV2`/`ChunkContentV2` carries its own
content-derived hash (`content_sha256`, computed from the actual redacted
bytes). A new, independently versioned wire-level wrapper,
`ChunkReviewTransportEnvelopeV1` (`app/agent_review/
review_transport_contract_v2.py`, schema `agent-review.
review-transport-envelope.v1`), carries the **unmodified**
`ChunkResponseEnvelopeValueV2` union plus an echo of `request_sha256` and
`content_sha256` that the far end must return verbatim.
`verify_transport_echo_v1` is the fail-closed gate a caller must pass
*before* the inner response is ever handed to `consumer_v2.
bind_chunk_response_v2`.

**Why a wrapper, not a field on the v2 envelope:**
`ChunkResponseEnvelopeValueV2` (`contracts_v2.py:783-786`) is a closed
(`extra="forbid"`), already-published, discriminated `oneOf` union with no
field reserved for a content hash. Extending it would change what every
already-pinned consumer of that exact union accepts. Wrapping it, instead of
touching it, means not one byte of `ChunkResponseEnvelopeValueV2` or its own
hash preimage (`contracts_v2.canonical_response_envelope_bytes_v2`) changes.

**Verified:** `tests/agent_review/test_review_transport_contract_v2.py`
constructs a syntactically valid, `payload_sha256`-correct response and
proves it is rejected by `verify_transport_echo_v1` when its echoed
`content_sha256` does not match the request that was actually sent --
exactly the attack this decision closes.

## Decision 3 — DLP policy is declarative or host-owned by construction

**Decision:** `DlpPolicyDeclarationV2` is a closed schema
(`agent-review.dlp-policy.v1`) with exactly two ways for a target to declare
policy: inline `DlpPolicyRuleV2` entries (a pattern + action, interpreted by
a host-owned engine -- data, never executed as code), or a named reference
to a host-owned detector pinned by digest (`detector_name` +
`detector_digest`). There is no `path`, `module`, `import`, or `entrypoint`
field anywhere in this schema -- `ContractV2Model`'s `extra="forbid"` means
a document that tries to smuggle one in is rejected outright, and
`load_dlp_policy_declaration_v2` adds an explicit, separately reasoned check
for exactly that forbidden-key set as defense in depth.

**Why this matters:** the analysis job must never import or execute Python
from the repository under review -- doing so would let a PR's own diff
define the policy fiscalizing it. This decision makes that structurally
impossible to represent, not merely discouraged by convention.

**Deferred to #200-B:** loading, allowlisting, and executing a real
host-owned detector by name. This ADR only freezes the shape a target may
declare and the digest that pins it.

## Decision 4 — a `must_review` fragment can never be silently omitted

**Decision:** `FragmentContentV2.coverage_required=True` combined with any
`policy` other than `INCLUDED` is a construction-time `ValidationError`
(`CONTENT_REQUIRED_FRAGMENT_MISSING_REASON_V2`), not a `limitations` entry.
`bind_review_content_to_manifest_v2` re-checks the same invariant against
the manifest's own truth as defense in depth.

**Why a hard construction failure, not a limitation:** a `limitations` entry
is something readiness can still route around (see `ReviewReadinessV2`'s own
`pipeline.degraded` handling). A must-review fragment silently missing its
content is not a degraded-but-continuing state -- it is a fragment the
manifest itself has already decided must be reviewed
(`FragmentV2.coverage_required`, `#84`). Refusing to construct the object at
all, rather than constructing it and hoping every downstream consumer
remembers to check, is the same "the contract itself is the last line of
defense" discipline the rest of `contracts_v2.py` already applies.

**Deferred to #200-B:** replanning a fragment that would otherwise be
dropped for budget (splitting it into more chunks) so it never needs to
reach this refusal in the first place.

## Consequences

- `#200-B` (extraction) and `#200-C` (transport wiring/E2E) build directly
  on these four contracts without needing to revisit them;
- `#200-B`'s cross-check of `ChunkContentV2.payload_sha256` against a real,
  built `ChunkPayloadV2` is explicitly out of scope here (mirrors
  `payload_set_v2` vs. `payload_set_emission_v2`'s own manifest-only vs.
  real-payload split) -- `CONTENT_PAYLOAD_SHA256_MISMATCH_REASON_V2` is
  reserved, not raised, by this module;
- zero already-published v2 schema changed: `agent-review.chunk-payload.v2`
  and `agent-review.chunk-response-envelope.v2` are byte-identical before
  and after this change (`scripts/export-agent-review-v2-schemas.py
  --check`);
- three new schemas are added to the RI-B0a.2 reuse manifest
  (`config/ri/ri-b0a-2-reuse-manifest.json`) and its generated view, keeping
  that completeness registry accurate.

## Alternatives considered and rejected

- **Extend `ChunkPayloadV2` with a content field.** Rejected: breaks every
  pinned `payload_sha256`, forces `schema_version: 3` on a contract this
  codebase has committed not to change silently, invalidates the AgentEscala
  `v0.21.0` shadow adoption's own pins.
- **Add a content hash field directly to `ChunkResponseEnvelopeValueV2`.**
  Rejected: same class of problem -- an already-published, closed,
  discriminated union with real pinned consumers. A wrapping envelope
  achieves the same integrity guarantee without touching it.
- **Trust the target's own DLP module directly (import it).** Rejected
  outright, not merely discouraged: it would let the code under review
  define its own fiscalization, exactly the failure mode `#199`'s hard
  boundaries forbid ("sem código da PR controlando analysis/publisher/
  harness").
