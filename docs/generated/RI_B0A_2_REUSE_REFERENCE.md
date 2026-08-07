<!-- GENERATED VIEW -- DO NOT EDIT BY HAND.
Regenerate: python scripts/generate-ri-b0a-2-reuse-view.py
Source: config/ri/ri-b0a-2-reuse-manifest.json
-->

# RI-B0a.2 — AgentReview/ProjectOps reuse and reference mapping

Generated from `config/ri/ri-b0a-2-reuse-manifest.json`. This view classifies every existing AgentReview contract this session found (10 schemas under `schemas/agent-review/v2/`) plus the ProjectOps track boundary into exactly one of four states: `reuse`, `reference`, `future_adapter`, `not_applicable`. No CAEM or AgentReview schema is copied or redefined here.

## Reuse — consumed as-is by RI-B0 (3)

| Contract ID | Owner | RI-B0 role | Source |
|---|---|---|---|
| `agent-review.review-readiness.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 consumes this as its readiness input; does not redefine readiness semantics. | `schemas/agent-review/v2/agent-review.review-readiness.v2.schema.json` |
| `agent-review.run.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 binds its own proof obligations to this same run identity. | `schemas/agent-review/v2/agent-review.run.v2.schema.json` |
| `agent-review.evidence-bundle.v2` | aiops-orchestrator (app/agent_review/) | RI-B0's own evidence artifacts should reuse this shape rather than defining a second one, until/unless CAEM's own evidence-projection.v1 (currently reserved) is promoted and a migration is decided. | `schemas/agent-review/v2/agent-review.evidence-bundle.v2.schema.json` |

**`agent-review.review-readiness.v2`** — The single deterministic readiness decision this repo already treats as authoritative (docs/engineering/PROJECT_OVERLAY.md: review textual do modelo e advisory, review-readiness determinístico é a decisão consumível). RI-B0's own future readiness output must be built from or consistent with this contract, never a parallel one.

**`agent-review.run.v2`** — RunIdentityV2 binds every finding/payload to (repository, HEAD, run_id). RI-B0's own proof obligations must carry the same identity triple (per docs/RI_A2_THREAT_MODEL.md §1.2) rather than inventing a parallel run-identity concept.

**`agent-review.evidence-bundle.v2`** — AgentReview's own evidence-bundle shape (distinct from CAEM's quarantined .caem/quarantine/caem-2.1/schemas/evidence-bundle.schema.json -- a real naming collision, flagged explicitly per this repo's own established discipline for such collisions, see docs/RI_A2_THREAT_MODEL.md §2). This is the established evidentiary artifact shape already in active use in this repo today.

## Reference — cited for provenance, not consumed directly (6)

| Contract ID | Owner | RI-B0 role | Source |
|---|---|---|---|
| `agent-review.chunk-payload.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 may cite a chunk_id/payload identity for provenance linking, but does not consume payload content directly. | `schemas/agent-review/v2/agent-review.chunk-payload.v2.schema.json` |
| `agent-review.chunk-response-envelope.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 may cite a chunk_id for provenance linking, but does not consume envelope content directly. | `schemas/agent-review/v2/agent-review.chunk-response-envelope.v2.schema.json` |
| `agent-review.target-profile.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 references the profile identity used by a run for provenance; does not consume or redefine profile content. | `schemas/agent-review/v2/agent-review.target-profile.v2.schema.json` |
| `agent-review.manifest.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 references manifest identity (run_id) for provenance; does not consume planning content. | `schemas/agent-review/v2/agent-review.manifest.v2.schema.json` |
| `agent-review.chunk-response.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 may cite a chunk_id/finding identity for provenance linking, but does not consume per-chunk result content directly -- same role as agent-review.chunk-response-envelope.v2. | `schemas/agent-review/v2/agent-review.chunk-response-envelope.v2.schema.json` |
| `agent-review.review-content.v2` | aiops-orchestrator (app/agent_review/) | RI-B0 may cite a chunk_id/content_sha256 for provenance linking, but does not consume reviewable content directly -- same role as agent-review.chunk-payload.v2. | `schemas/agent-review/v2/agent-review.review-content.v2.schema.json` |

**`agent-review.chunk-payload.v2`** — Per-chunk LLM review input, internal to AgentReview's own diff-splitting pipeline. RI-B0 operates one level above individual chunks; it has no direct need to consume raw chunk payloads.

**`agent-review.chunk-response-envelope.v2`** — Per-chunk LLM review output envelope, internal to AgentReview's own aggregation pipeline (feeds lifecycle_v2.aggregate_finding_lifecycle_v2). RI-B0 consumes the aggregated result, not individual chunk responses.

**`agent-review.target-profile.v2`** — Declares which artifacts/paths a review run covers for a given target repository. RI-B0 may need to know which profile produced a given run's evidence, for provenance, but does not own or redefine target-profile semantics itself.

**`agent-review.manifest.v2`** — AgentReview's own run/chunk planning manifest (fragment assignment, expected files). Internal planning artifact; RI-B0 may reference manifest-level identity (run_id) for provenance, but does not consume or redefine planning content.

**`agent-review.chunk-response.v2`** — ChunkReviewResultV2 -- the per-chunk success payload, embedded as a $defs entry inside agent-review.chunk-response-envelope.v2.schema.json (no separate top-level schema file of its own), but carrying its own distinct schema_id literal. Same evidentiary role as its enclosing envelope: internal to AgentReview's own per-chunk review pipeline. Correction, found by an independent Codex review of an earlier draft: this contract was omitted entirely from the manifest, because the manifest's coverage was checked against schema FILES rather than schema_id literals, and this one shares a file with its envelope.

**`agent-review.review-content.v2`** — Sidecar carrying the real, redacted hunk content bound to a chunk payload by content_sha256/payload_sha256 (#200-A, distribution epic #199) -- an internal AgentReview per-chunk artifact, the content-layer counterpart to agent-review.chunk-payload.v2. Same evidentiary role as that contract: RI-B0 operates one level above individual chunks and has no direct need to consume raw reviewable content.

## Future adapter — needs a translation layer once both sides are real (1)

| Contract ID | Owner | RI-B0 role | Source |
|---|---|---|---|
| `agent-review.run-fragment-coverage.v2` | aiops-orchestrator (app/agent_review/) | no current consumer; a future adapter translating fragment-coverage into a completeness proof obligation is the anticipated shape, once RI-B0's proof work and the relevant CAEM contracts are both real. | `schemas/agent-review/v2/agent-review.run-fragment-coverage.v2.schema.json` |

**`agent-review.run-fragment-coverage.v2`** — Proves that every diff fragment assigned to a chunk was actually reviewed (this repo's recent fail-closed coverage-proof work). RI-B0 has no concrete, currently-implemented consumer of raw fragment-coverage records today. Once RI-B0's own completeness/canonicality proof work begins (consuming CAEM's caem.assertion.v1/caem.proof-obligation.v1, both currently reserved), RI-B0 will most likely need a small adapter translating this coverage report into a completeness proof obligation -- not a direct reuse, since the two schemas serve different evidentiary roles (chunk-review completeness vs. a CAEM proof obligation's own resolution states).

## Not applicable — no RI-B0 relevance today (5)

| Contract ID | Owner | RI-B0 role | Source |
|---|---|---|---|
| `agent-review.semantic-grouping-policy.v2` | aiops-orchestrator (app/agent_review/) | none: RI-B0 has no reason to read, cite, or depend on chunk-grouping policy. | `schemas/agent-review/v2/agent-review.semantic-grouping-policy.v2.schema.json` |
| `agent-review.payload-set.v2` | aiops-orchestrator (app/agent_review/) | none: RI-B0 does not touch payload transport. | `schemas/agent-review/v2/agent-review.payload-set.v2.schema.json` |
| `projectops.v1-track` | separate track (not this repository's own code, per CURRENT_CHECKPOINT.md) | none today; if/when ProjectOps produces a concrete, versioned contract this repository consumes, that contract must be added to this manifest by name, not assumed by analogy to AgentReview's shapes. | — |
| `agent-review.review-transport-envelope.v1` | aiops-orchestrator (app/agent_review/) | none: RI-B0 does not touch payload/content transport, same as agent-review.payload-set.v2. | `schemas/agent-review/v2/agent-review.review-transport-envelope.v1.schema.json` |
| `agent-review.dlp-policy.v1` | aiops-orchestrator (app/agent_review/) | none: RI-B0 has no reason to read, cite, or depend on DLP redaction policy. | `schemas/agent-review/v2/agent-review.dlp-policy.v1.schema.json` |

**`agent-review.semantic-grouping-policy.v2`** — Governs how AgentReview groups diff hunks into chunks for LLM review -- an AgentReview-internal execution-planning detail with no proof-obligation or readiness-decision content relevant to RI-B0.

**`agent-review.payload-set.v2`** — Emission artifact bundling a run's chunk payloads for transport to the inference Router. Purely an AgentReview-side transport concern; RI-B0 has no reason to consume or reference it.

**`projectops.v1-track`** — CURRENT_CHECKPOINT.md documents ProjectOps v1 as a separate track: "trilha separada de inteligência de CI, advisory e fail-safe". As of this manifest, this repository contains zero committed ProjectOps schema, code, or artifact under any path (verified: no file or directory matching *projectops* exists in this checkout). There is therefore no concrete contract to classify contract-by-contract yet; this single entry records the track-level boundary instead of inventing hypothetical ProjectOps contract IDs. The one invariant already established (docs/RI_A2_THREAT_MODEL.md §3, this repo's own authority/data matrix) is that ProjectOps readiness must never become AgentReview readiness, and RI-B0 grants it no authority over its own gates.

**`agent-review.review-transport-envelope.v1`** — Wire-level wrapper (#200-A) that carries the UNMODIFIED agent-review.chunk-response-envelope.v2 union plus an echoed request_sha256/content_sha256 pair the far end must return verbatim, closing the gap where payload_sha256 alone cannot distinguish two review-content sidecars for the same chunk. Purely an AgentReview-side transport concern, own schema_version lineage (v1, unrelated to the v2 chunk-response schema version) -- same evidentiary role as agent-review.payload-set.v2.

**`agent-review.dlp-policy.v1`** — A target's declarative DLP policy (#200-A): inline pattern rules or a reference to a host-owned, digest-pinned detector -- never a module path, import string, or entry point naming code inside the target repository (structurally unrepresentable: this schema has no such field). An AgentReview-internal content-redaction execution-planning detail, analogous to agent-review.semantic-grouping-policy.v2, with no proof-obligation or readiness-decision content relevant to RI-B0.
