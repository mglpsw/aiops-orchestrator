# AGENTS.md — app/agent_review

Specializes the root `AGENTS.md`. Does not weaken any hard boundary declared
there; only adds invariants specific to this directory.

## What lives here

The AgentReview engine: intake, redaction, chunking, contracts (v1 and v2),
payload/response binding, synthesis, lifecycle, readiness, and every CLI
under `scripts/aiops-review-*` that wires this code to a real invocation.
CT104-scoped, offline by default — see the root file for the CT102/CT104
distinction.

## v1 and v2 must never be mixed implicitly

`app/agent_review/versioning.py`'s `select_contract_version` is the only
place that decides which contract shape an input has. Any new call site
that accepts a payload/response and could receive either shape MUST call it
before doing anything else with the input. A v1-shaped document fed to a
v2-only path (or vice versa) fails closed as `mixed_contract_versions` —
never silently coerced, never auto-detected by duck-typing.

## Hashing and canonicalization

**Scope of this rule, corrected after an independent Codex review found it
stated too broadly:** every v2 CONTRACT SELF-HASH (`manifest_hash`,
`payload_sha256`, `policy_hash`, `evidence_hash`, `run_id`, and siblings —
the hashes a `ContractV2Model` computes over its OWN structured content) is
computed over:

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
```

This is NOT a universal rule for every hash in this package. Some hashes
are, by design, computed over raw or sanitized bytes instead — e.g.
`payload_references_v2.py` hashes sanitized/raw artifact content directly
(`hashlib.sha256(sanitized.encode(...))`), and `diff_acquisition_v2.py`
hashes real diff text directly. v1's `contract_suggestions.py`/
`false_positive_signatures.py` also use canonical JSON, but a DIFFERENT,
already-frozen preimage (no `allow_nan=False`) — do not treat a difference
from the v2 convention above as a defect in that v1 code, and do not
propose changing a frozen v1 identifier or evidence hash to match it.

`sort_keys=True` only normalizes **dict key order** — it does nothing for
**list element order**. Any list whose element order is not semantically
significant (e.g. a set of rules, a set of required checks) must be sorted
before it enters a hash preimage, explicitly, every time that preimage is
computed. This exact class of bug has been found and fixed more than once
in this codebase (`semantic_grouping_policy_v2.py`, three rounds, across
three different nesting depths) — never assume `sort_keys=True` alone makes
a hash order-independent.

A model that carries its own hash as a field uses the
`*Material*`/self-hashing split pattern (`ManifestMaterialV2`/`ManifestV2`,
`ChunkPayloadMaterialV2`/`ChunkPayloadV2`, `PayloadSetMaterialV2`/
`PayloadSetV2`, `SemanticGroupingPolicyMaterialV2`/
`SemanticGroupingPolicyV2`) so the hash field excludes itself from its own
preimage. Do not invent a new hashing convention for a new contract type —
match this one.

## Lifecycle and coverage

- A finding's `disposition` never advances itself. `NEW` → `CONFIRMED` /
  `DISMISSED` / `FIXED` / `SUPERSEDED` only happens through an already-valid
  `FindingLifecycleRecordV2` supplied as `prior_lifecycle`, revalidated
  against the current HEAD — synthesis and lifecycle aggregation never
  infer confirmation from concordance between chunks or models.
- Coverage bridging (fragment-granular → file-granular) always revalidates
  the coverage report against the manifest it claims to describe
  (`bind_coverage_report_to_manifest_v2`) before trusting it — a
  `RunFragmentCoverageReportV2`/`ParsedChunkResultV2`/`SynthesisResultV2` is
  a freely constructible plain data value with no seal proving it actually
  came from this package's own producers.
- `must_review` coverage that is incomplete must never resolve to `ready`.
  It resolves to exactly `policies.coverage_failure_state`
  (`blocked_pipeline` or `manual_required`, the target's own choice) — never
  silently downgraded to a softer state.

## Determinism

Given identical inputs, every hash, every ordered list, and every emitted
artifact byte must be identical across repeated runs and independent of
chunk-result/retry arrival order. When adding a new aggregation function,
prove this with a direct test (build twice, compare bytes; reorder inputs,
compare outcome) — do not assume it from code review alone.

## Compatibility

v1 golden fixtures (`golden_chunk_payload_hash.json` and siblings) are
frozen. A change to v1 behavior requires a positive, reasoned justification
in the PR — not merely "it was convenient while working on v2".

## Genericity — no target name in the engine

Nothing under this directory may branch on a target repository's name (see
`AgentEscala`/`InterLeitos`, tested by
`tests/agent_review/test_v2_dual_target_e2e.py`). Every target-specific
decision comes from a `TargetProfileV2`/`SemanticGroupingPolicyV2` object
the caller supplies, never from a string comparison against a repo name.

## What a reviewer here must never suggest

- free shell, SSH, `docker exec`, deploy, a direct provider call, or the
  legacy `/v1/chat/ingest` path;
- treating a placeholder/redaction test fixture as if it were a real
  secret, or vice versa;
- promoting a finding's disposition without a real, revalidated
  `FindingLifecycleRecordV2` and evidence;
- inferring `ready` from partial or degraded coverage.
