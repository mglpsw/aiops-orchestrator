# AgentReview v1 — MethodGuidance carrier (V1-C2)

**Status:** `DRAFT_CARRIER_CONTRACT` for `mglpsw/aiops-orchestrator#221` (roadmap `#46`).
**Scope:** AgentReview v1 engine only. The target (AgentEscala) produces the sidecar in its own PR;
nothing here qualifies a target projection (Gate B/C), an exhaustive claim (ClaimV1), or a release.

## Decision

```yaml
MethodGuidanceCarrierDecision:
  producer: >-
    target-owned declarative sidecar `.aiops/method-guidance.yaml`, loaded by `load_repo_profile`
    (same canonical producer as `.aiops/domain-contracts.yaml` / `.aiops/review-packs.yaml`).
    The engine never parses the method document (no second Markdown parser): the target
    projects bounded `selected_text` and binds it to a source blob.
  canonical_source_shape: closed grammar, version 1 (below). Unknown key / version => INVALID.
  typed_carrier: >-
    `TargetProfile.method_guidance` (author document, verbatim) and
    `TargetProfile.method_guidance_verification` (loader-owned, never author-writable:
    source_id -> CURRENT | STALE | UNVERIFIABLE). Both survive `TargetProfile.model_dump` and
    `ReviewIntake.target_profile`. An inline `method_guidance` in repo-profile.yaml is refused
    exactly like the other inline C2 sources.
  serialized_intake: ReviewIntake.target_profile.{method_guidance, method_guidance_verification}
  applicability: >-
    deterministic, per guidance item, from declared `applies_to` only (semantic_groups, exact
    paths, glob patterns, or explicit `scope: global`). At least one member is mandatory: no
    implicit apply-to-all, no inference from Markdown prose. Not applicable => not emitted, no
    limitation, and a chunk with no applicable item carries no method key at all.
  payload_surface: >-
    `chunk_context.method_guidance_context`, a sibling of (never a member of)
    `contracts_context`. Item and context carry `authority_class: METHOD_GUIDANCE`. Emitted only
    when at least one applicable item is CURRENT, so a repository without a sidecar produces
    byte-identical payloads to the previous engine.
  actual_review_input: the ChunkPayload JSON written by `aiops-review-build-payloads` (the request body the
    workflow sends to the Router). Tests assert the literal text in those bytes.
  budget_authority: >-
    `payload_cost_model` (single cost authority). `method_guidance_context()` is called by the
    planner projection and by the builder; the projection floor keeps REQUIRED items intact and
    drops OPTIONAL ones; the builder ladder drops optional method guidance before optional
    contracts and required method guidance before required contracts, always before hunks.
  loss_consequence:
    required: >-
      `required_method_guidance_context_lost:<method_id>/<section_id>` — a REQUIRED_CONTEXT_LOSS
      and critical limitation prefix: the builder refuses to route the affected chunk, the plan is
      `degraded`, the quality gate cannot return `passed`.
    optional: >-
      `optional_method_guidance_context_lost:<method_id>/<section_id>` — typed, informational; not a
      critical prefix, no automatic blocker.
    invalid_source: "`invalid_source_method_guidance:<SUBTYPE>` — critical, fail closed."
    stale_or_unverifiable: >-
      `method_guidance_source_stale:<source_id>` / `method_guidance_source_unverifiable:<source_id>`
      (informational) plus the required/optional loss of every applicable item bound to it. A stale
      reference never receives current authority and its text is never emitted.
  backwards_compatibility: >-
    No sidecar => no new key, no new limitation, no cost change (the projection adds nothing
    unless a method context exists). v1 goldens and the legacy differential oracle are unchanged.
```

## Sidecar grammar (version 1, closed)

```yaml
version: 1                      # required, exactly integer 1
updated: "2026-10-13"           # optional scalar metadata
sources:                        # <= 16
  - source_id: aocm             # [A-Za-z0-9][A-Za-z0-9._:-]{0,63}, unique
    repository: owner/repo
    path: docs/canonical/aocm/METHOD.md   # canonical repo-relative path
    revision: <commit>          # identity, non-empty
    blob: <40 hex git blob oid> # compared against the checkout by the loader
guidance:                       # <= 32
  - method_id: AOCM
    section_id: obligation-matrix
    source_id: aocm             # must resolve to a declared source
    requiredness: optional      # required | optional — explicit, no default
    applies_to:                 # closed; >= 1 non-empty member
      semantic_groups: [primary_backend_logic]
      paths: [backend/api/shifts.py]
      patterns: ["backend/**"]
      scope: global
    selected_text: "..."        # non-empty, <= 4000 chars
```

Source-state vocabulary reuses `PRESENT_VALID | ABSENT | INVALID` from OBL-CL2-03. `ABSENT` is not
`not_relevant`: an absent sidecar declares nothing and the engine claims nothing from it.

## Separation of authorities

| Class | Where it travels | Can it become an invariant? |
|---|---|---|
| ProductContract | `contracts_context` (`domain_contracts`, `review_packs`) | yes — target-declared |
| MethodGuidance | `method_guidance_context` | no — advisory review method |
| PRClaim | PR description/brief | never policy |
| ReviewObservation | model response | advisory |
| ReadinessAuthority | deterministic quality gate | decides |

Method guidance never enters `domain_contracts`, `review_packs` or `contract_bindings`, never feeds
`contract:<id>` resolution, and never changes the effective contract set of a pack.

## Non-claims

No Gate B/C, Control A/B, ClaimV1, chunking/coverage closure (`AR-V1-C2-CHUNKING-COVERAGE-CLOSURE`),
repin, release or v2 change. A model finding that cites a method id as `contract_id` is still an
advisory observation; the carrier adds no contract-id validation (adjacent surface, deliberately
untouched).
