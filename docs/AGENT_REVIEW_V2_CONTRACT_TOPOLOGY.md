# AgentReview v2 — contract topology and CLI/source naming decision (#102)

Refs #102, the contract-closure tracker whose three children are #104 (fragment
coverage proof, closed via PR #114), #105 (`PayloadSetV2`, closed via PR #138),
and #106 (`SemanticGroupingPolicyV2` + effective policy hash, closed via PR
#137). This document is the epic's own direct deliverable: a documentation-only
close-out, no code beyond what its three children already merged.

Two things this document does, per the issue:

1. Publish the explicit list of v2 types that synthesis/readiness (C1/#127,
   C2/#130) and assembly (E1/#128, E2/#129) consume and produce, so both
   branches can proceed in parallel without drift.
2. Decide the CLI-script / `source`-literal naming convention once, so C2,
   E2, and F2 do not each relitigate it.

## Contract topology

### Already frozen, consumed but never modified by any child of this epic

```text
RunIdentityV2 (contracts_v2.py)
  -- _RUN_IDENTITY_FIELDS: repo, pr_number, base_sha, head_sha,
     tested_merge_sha, toolrepo_sha, profile_hash, policy_hash,
     manifest_hash, evidence_hash
  -- compute_run_id(identity) -- unchanged by #105/#106

TargetProfileV2 / TargetPoliciesV2 (contracts_v2.py)
  -- allowed_semantic_groups is an ALLOWLIST only; #106 adds the
     classification RULE on top, never modifies this shape

ManifestV2 / ManifestMaterialV2 / ManifestChunkV2 / FragmentV2
(manifest_v2.py)
  -- ManifestChunkV2.payload_sha256 remains null-only BY DECISION
     (#105's docstring update explains why -- see below)

ChunkPayloadV2 / ChunkPayloadMaterialV2 (contracts_v2.py)
  -- payload_sha256 computed by compute_payload_sha256_v2, unchanged

profile_loader_v2.compute_policy_hash_v2
  -- hashes profile.policies alone; #106 explicitly does NOT redefine this
```

### Produced by #104/#105/#106 (this epic's three children), consumed by later slices

```text
RunFragmentCoverageReportV2 (run_fragment_coverage_v2.py, #104/#115)
  bind_coverage_report_to_manifest_v2(report, manifest)
  -> consumed by C1/#127 (readiness decision: fragment-granular coverage
     revalidated as an untrusted input, never trusted blindly)

SemanticGroupingPolicyV2 (semantic_grouping_policy_v2.py, #106)
  classify_semantic_group_v2(policy, path=...)
  bind_semantic_grouping_policy_to_target_profile_v2(policy, profile)
  EffectivePolicyBundleV2 / compute_effective_policy_hash_v2(target_policies, policy)
  -> consumed by E2/#129 (real run assembly: populates
     ManifestChunkV2.semantic_group instead of a caller-supplied kwarg,
     per #86's ban on repo-name branching in the engine)
  -> compute_effective_policy_hash_v2's return value is the value meant for
     RunIdentityV2.policy_hash once E2 assembles a real run

PayloadSetV2 (payload_set_v2.py, #105)
  bind_payload_set_to_manifest_v2(payload_set, manifest)
  compute_payload_set_sha256_v2 / verify_payload_set_sha256_v2
  -> consumed by F2/#132 (PayloadSet emission + CLI: the only producer),
     never by E1/E2 (E assembles RunIdentityV2/ManifestV2 before any
     payload exists, so it has nothing to attest)
```

### Produced by #107 (PR #117, already merged), consumed by C1/C2

```text
SynthesisResultV2 (synthesis_v2.py)
FindingProvenanceV2 / aggregate_finding_lifecycle_v2 (lifecycle_v2.py)
validate_chunk_results_scope_v2 (chunk_result_scope_v2.py)
  -> consumed by C1/#127: the readiness decision library reads
     SynthesisResultV2 + the manifest + checks, deriving state/reason
     codes/blockers -- never reimplementing lifecycle aggregation
```

### Shared surface for C1/C2 (readiness) vs. E1/E2 (assembly) -- explicit boundary

```text
C1/#127 (readiness decision, library only) consumes:
  SynthesisResultV2, ManifestV2 (read-only), RunFragmentCoverageReportV2
  (via bind_coverage_report_to_manifest_v2), PipelineDegradationCauseV2
  produces:
  a readiness decision (state, reason codes, blockers) -- NOT yet a
  ReviewReadinessV2 artifact; that is C2's job

C2/#130 (ReviewReadinessV2 artifact + quality gate CLI) consumes:
  C1's decision function, RunIdentityV2 (for the artifact's own identity
  fields, including evidence_hash from E1), pr_state/checks acquired from
  the forge
  produces:
  ReviewReadinessV2 (contracts_v2.py, already exported/frozen shape --
  C2 populates it, does not redefine it)

E1/#128 (canonical evidence hash) consumes:
  a sanitized evidence bundle (shape TBD in #128 itself)
  produces:
  the preimage/algorithm for RunIdentityV2.evidence_hash -- inside
  _RUN_IDENTITY_FIELDS, so getting this wrong is irreversible without
  re-hashing everything downstream; #128 owns this alone

E2/#129 (real run/manifest assembly) consumes:
  E1's evidence hash function, SemanticGroupingPolicyV2 (#106),
  acquire_authoritative_diff_v2 (#103), TargetProfileV2.must_review
  produces:
  a real RunIdentityV2 + ManifestV2 from an actual diff -- the first
  producer of either that isn't a test fixture

F1/#131 (profile-derived payload references) consumes:
  TargetProfileV2.artifacts/.contracts via profile_loader_v2
  produces:
  populated artifact_references/contract_references inside
  ChunkPayloadMaterialV2 -- does NOT depend on E2 architecturally
  (ordering after E2 in the serial plan is operational, not a dependency)

F2/#132 (PayloadSet emission + CLI) consumes:
  PayloadSetV2's contract and cross-validators (#105), a real ManifestV2
  (E2) to know the exact expected chunk_id set
  produces:
  a real PayloadSetV2 for a real run -- the only producer
```

## CLI / `source`-literal naming decision (R2)

Verified in code before this decision (measured at `f15fdd0`, current at time
of writing):

```text
contracts_v2.py:573   ChunkPayloadV2.source     = "aiops-review-build-payloads"
                       <-> scripts/aiops-review-build-payloads.py (v1) ALREADY EXISTS
contracts_v2.py:1075  ReviewReadinessV2.source  = "aiops-review-quality-gate"
                       <-> scripts/aiops-review-quality-gate.py (v1) ALREADY EXISTS
manifest_v2.py:194    ManifestV2.source         = "aiops-review-plan-chunks-v2"
                       <-> "-v2" suffix (the one non-colliding pattern already
                           in this repo)
```

**Decision: adopt the "-v2" suffix convention for every NEW v2 `source`
literal / CLI script name going forward.** Concretely:

- `ChunkPayloadV2.source` and `ReviewReadinessV2.source` are **already
  published, frozen contracts** (part of `contracts_v2.py`, merged before
  this convergence effort). Renaming them now would be a breaking schema
  change to an already-shipped artifact for no functional gain — **not done
  here**. Their collision with the pre-existing v1 script names of the same
  string is a known, accepted inconsistency, not retroactively fixed. If it
  ever needs to be fixed, that is a deliberate, separate breaking-change
  issue, gated on real confusion being reported, not a preemptive rename.
- Every `source` literal introduced **after** this decision uses the "-v2"
  suffix, matching `ManifestV2.source`'s existing precedent:
  - `SemanticGroupingPolicyV2.source = "repo-semantic-grouping-policy"`
    (#106) is exempt from the collision concern entirely: it is a
    target-repo-owned artifact, never emitted by a CLI script in *this*
    repository, so there is no script name to collide with.
  - `PayloadSetV2.source = "aiops-review-build-payload-set-v2"` (#105)
    already follows this decision — chosen when #105 was implemented,
    ahead of this document, precisely because "PayloadSet" is a brand-new
    artifact type with no v1 namesake to collide with in the first place.
  - **C2/#130's** quality-gate CLI (if it needs its own script distinct
    from the existing v1 `scripts/aiops-review-quality-gate.py`) MUST use
    a "-v2"-suffixed script name (e.g.
    `scripts/aiops-review-quality-gate-v2.py`). It must NOT reuse or
    overwrite the v1 script.
  - **F2/#132's** payload-set CLI MUST use a "-v2"-suffixed script name
    (e.g. `scripts/aiops-review-build-payload-set-v2.py`), matching
    `PayloadSetV2.source` above.
  - **E2/#129** does not own a CLI in this convergence plan (assembly is a
    library call from a caller, not a standalone script); if a future
    slice adds one, the same "-v2" suffix rule applies.

This closes R2: C2, E2, and F2 inherit this decision and do not relitigate
it.

## Closure checklist (verified explicitly, never by inference)

- [x] #104 concluída e integrada (PR #114).
- [x] #105 concluída e integrada (PR #138, `2fa8d3e`).
- [x] #106 concluída e integrada (PR #137, `f15fdd0`).
- [x] `docs/AGENT_REVIEW_V2_CONTRACT_TOPOLOGY.md` publicada (this document).
- [x] Decisão de nomenclatura CLI v1/v2 registrada no documento acima (R2,
      previous section).
- [x] Superfície de contratos para C1/C2/E1/E2/F1/F2 publicada (previous
      section).
- [x] Nenhum contrato congelado alterado incidentalmente por nenhuma das
      filhas — verified: #104/#115 only added a check inside
      `bind_coverage_report_to_manifest_v2` (no field changes to
      `RunFragmentCoverageEntryV2`); #106 added a new standalone artifact
      (`SemanticGroupingPolicyV2`/`EffectivePolicyBundleV2`), touching
      `TargetProfileV2` and `compute_policy_hash_v2` NOT AT ALL; #105 added
      a new standalone artifact (`PayloadSetV2`), touching `RunIdentityV2`
      and `ManifestV2`'s structure NOT AT ALL (only a docstring on
      `ManifestChunkV2.payload_sha256`, no field/type change).
- [x] Suíte completa verde e `--check` byte-idêntico após cada filha —
      baseline `894`/`1411` at `a01d95e`; current at `2fa8d3e`: `944`
      (`tests/agent_review`) / `1461` (broad marker-excluded selection),
      zero regressions across #104/#115, #105, #106's merges; schema
      export byte-identical throughout (9 schemas currently exported).

**Caveat de governança preservado:** esta epic fecha antes de #86/#87/#88/#89,
que dependem de trabalho posterior (C2, F2). Isso é esperado — #102 cobre
exclusivamente contract closure.
