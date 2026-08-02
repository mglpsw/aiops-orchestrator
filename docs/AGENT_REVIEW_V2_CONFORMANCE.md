# AgentReview v2 — dual-target conformance E2E (#86)

Refs #86, conformance E2E gated directly by C2/#130 (`ReviewReadinessV2` +
gate CLI, merged) and F2/#132 (`PayloadSetV2` emission + CLI, merged), under
trackers #108/#110. Predecessors in the chain (#103, #104, #105, #106, #107,
C1/#127, E1/#128, E2/#129, F1/#131) are already merged.

This issue is exclusively conformance E2E. No core component and no contract
decision is implemented inside it — everything it exercises (`profile_loader_v2`,
`semantic_grouping_policy_v2`, `run_assembly_v2`, `payload_builder_v2`,
`consumer_v2`/`parser_v2`, `synthesis_v2`, `readiness_decision_v2`,
`review_readiness_emission_v2`, `payload_set_emission_v2`) was already merged
by an earlier slice.

## What this proves

The same v2 engine and the same CLIs review two independent, unrelated
targets — a synthetic "AgentEscala" (shift-scheduling) fixture and a
synthetic "InterLeitos" (clinical-operational, multi-tenant) fixture —
differing *only* in `TargetProfileV2`, `SemanticGroupingPolicyV2`, and
on-disk artifact/contract content. No engine module is touched or branched
per target.

```text
profile/policy (real, on-disk, per-target)
  -> run identity + manifest (run_assembly_v2, same code both targets)
  -> chunk payloads (payload_builder_v2, same code both targets)
  -> synthetic provider response (bind_chunk_response_v2/parse_bound_chunk_response_v2)
  -> synthesis (synthesis_v2, same code both targets)
  -> readiness decision (readiness_decision_v2, same code both targets)
  -> ReviewReadinessV2 (review_readiness_emission_v2)
  -> PayloadSetV2 (payload_set_emission_v2)
```

## Fixtures

`tests/agent_review/fixtures/v2/agent_escala/` and
`tests/agent_review/fixtures/v2/interleitos/`, each:

- `.aiops/target-profile.v2.yaml` — a real, valid `TargetProfileV2` document,
  loaded through the real `profile_loader_v2.load_target_profile_v2` (never
  hand-constructed in Python) — the one part of this suite that genuinely
  exercises the on-disk loading path, which every other v2 test suite
  constructs its profile in-memory instead.
- `contracts/domain-contracts.yaml` — a synthetic, illustrative-only domain
  contract, with a real, freshly computed `sha256` recorded in the profile's
  own `contracts[].sha256` field (verified: the profile fails to load if
  either file is edited without updating the other).
- `artifacts/full.diff` — a disclaiming placeholder (required by the
  profile's `artifacts` entry so `payload_builder_v2` has real content to
  sanitize/hash); the actual diff hunks exercised by the pipeline are built
  directly as `ParsedFileDiffV2`/`ParsedHunkV2` objects in the test module
  itself, mirroring E2/#129's own test pattern — this suite's job is to
  prove engine genericity, not to re-prove unified-diff parsing (already
  #103's own suite).

Every fixture file textually, permanently disclaims that it represents real
product logic, patient/institution/clinical data, or PHI — this is written
directly into the files' own content, not merely this document.

The two profiles are deliberately different in every dimension the issue's
own genericity proof requires: different `identity.repo`, different
`must_review.patterns`, different `allowed_semantic_groups`
(`docs_changelog` vs. `api_schema_contract`), and different
`coverage_failure_state` (`blocked_pipeline` vs. `manual_required`).

## Deliberate scope narrowing (recorded, not silently assumed)

- **No new redaction/PHI-detection infrastructure.** The issue's DLP/PHI
  concern is represented via the *existing* `manual_required`/
  `model_uncertainty` readiness path (a chunk result reporting a
  `limitations` entry), never a bespoke detector — inventing one here would
  itself be a core-component implementation the issue explicitly forbids.
- **No real git-based diff acquisition.** `assemble_manifest_from_diff_v2`
  already accepts pre-parsed `ParsedFileDiffV2` tuples directly; this suite
  builds those directly rather than shelling out to `git diff` against a
  throwaway repo, exactly as #129's own suite does.
- **No on-disk `SemanticGroupingPolicyV2` loader.** #106 and #129 both
  deliberately deferred building one; this suite builds the policy object
  directly in Python, per-target, proving the two policies drive different
  `semantic_group` classification without any engine change.
- **Five readiness states are exercised across the two targets combined**,
  not exhaustively 5×2: AgentEscala covers `ready`, `blocked_code` (a
  confirmed P2 finding, round-tripped through a real `prior_lifecycle`
  record), and `blocked_pipeline` (a chunk whose result never arrived);
  InterLeitos covers `manual_required` (the DLP-suspicion proxy) and
  `stale` (an explicit HEAD divergence).

## Cross-cutting regressions proven

- **Anti-branching**: `test_engine_never_branches_on_target_name` greps
  every `app/agent_review/*.py` file for a quoted string literal naming
  either target (`"AgentEscala"`, `"interleitos"`, etc.) — the shape a real
  `if repo == "..."` branch would take. A prose mention of a *consuming
  issue tracker* in a comment (e.g. `telemetry.py`/`schemas.py` documenting
  which real-world issue #111's fix closes) is not flagged. Confirmed
  non-vacuous by mutation testing: injecting a real `if profile.identity.repo
  == "mglpsw/AgentEscala":` branch into `run_assembly_v2.py` makes this test
  fail with the exact literal it found; reverting restores green. Deliberately
  scoped to `app/agent_review/` only, per the issue's own delimitation — the
  legacy v1 `scripts/github_agent_review.py` (`AGENTESCALA_REPO`, `:100`) is
  explicitly out of scope for this regression.
- **Two targets, same engine, different groups/payloads**:
  `test_two_targets_same_engine_different_groups_and_payloads` proves the
  two manifests use disjoint semantic-group sets and produce payloads with
  disjoint `payload_sha256`/`contract_id` sets, from the identical assembly
  code.
- **Cross-target rejection**:
  `test_cross_target_policy_binding_is_rejected` proves InterLeitos's own
  `SemanticGroupingPolicyV2` (using `api_schema_contract`) fails
  `bind_semantic_grouping_policy_to_target_profile_v2` against AgentEscala's
  profile (which does not allow that group), while binding cleanly against
  its own target.
  `test_cross_target_payload_set_binding_is_rejected` proves a `PayloadSetV2`
  emitted for AgentEscala's manifest+payloads is rejected
  (`PayloadSetBindingError`) when cross-checked against InterLeitos's
  manifest/payloads, in both directions (`emit_payload_set_v2` and
  `bind_payload_set_to_payloads_v2` directly).
- **Determinism**: `test_identical_inputs_produce_identical_bytes_and_hashes`
  proves reassembling the identical profile+policy+diffs twice yields
  byte-identical `manifest_hash`/`run_id`/`payload_sha256`.
- **Order-independence**:
  `test_chunk_result_order_does_not_change_synthesis_outcome` proves
  feeding chunk results in reversed order yields an identical coverage
  report hash, identical findings, and identical readiness state.
- **Identity sensitivity**: `test_evidence_hash_change_changes_run_id`
  proves changing only `evidence_hash` (holding profile/policy/diff fixed)
  changes `run_id` while `manifest_hash` stays the same — confirming
  `evidence_hash` is genuinely part of `RunIdentityV2`'s own hash preimage,
  not merely carried alongside it.
- **`must_review` fragmentation never produces `ready`**:
  `test_agent_escala_blocked_pipeline_on_incomplete_coverage` proves a
  missing chunk result resolves to exactly `profile.policies
  .coverage_failure_state` (`blocked_pipeline` for AgentEscala), never
  `ready`.

Every regression above was confirmed non-vacuous by direct mutation testing
(temporarily disabling the guard under test, confirming the corresponding
assertion then fails for the expected reason, then restoring and
re-verifying green) before being accepted as a real proof.

## Conformance matrix artifact

`test_conformance_matrix_is_complete_and_deterministic` builds a
`agent-review.v2-conformance-matrix`-schema JSON document from the REAL
pipeline objects of one full run per target (AgentEscala's `ready` run and
InterLeitos's `manual_required` run), with one entry per target carrying:

```text
target_id, repo
profile_hash, policy_hash, manifest_hash, evidence_hash
expected_head_sha, evaluated_head_sha
expected_chunks, processed_chunks, expected_fragments, processed_fragments
coverage_status, must_review_coverage_complete
binding_result
readiness_state, reason_codes
findings (finding_id/severity/disposition)
evidence_no_network, evidence_no_provider, evidence_no_github_write, evidence_no_ct102
canonical_output_digest
```

The test proves the matrix is byte-identical across two independent builds
from the same inputs, then writes it to a temp path and invokes
`scripts/verify-agent-review-v2-conformance.py` against it via subprocess —
proving the CLI and the artifact genuinely agree, not merely that each was
unit-tested in isolation.

## `scripts/verify-agent-review-v2-conformance.py`

A read-only, offline CLI. Not a core engine component and makes no readiness
or gate decision of its own — `ReviewReadinessV2`/
`compute_readiness_decision_v2` remain the sole authority for that. It only
proves the audit artifact a conformance run produced is:

- schema-tagged (`agent-review.v2-conformance-matrix`) with a non-empty,
  duplicate-free `targets` list;
- structurally complete (every required field present, correctly typed —
  sha256 fields are validated as 64-char lowercase hex, `readiness_state`/
  `reason_codes` are validated against the real `ReadinessStateV2`/
  `ReadinessReasonV2` enums);
- honest about its own no-network/no-provider/no-GitHub-write/no-CT102
  claim — every `evidence_no_*` field must be `true`, or the CLI fails
  closed with `conformance_network_or_provider_evidence_missing`;
- free of anything sanitizable — the artifact is re-run through
  `app.agent_review.redaction.redact_content`, the SAME canonical sanitizer
  the pipeline itself trusts (never a bespoke ad hoc PHI/secret heuristic
  invented for this CLI alone); any difference or nonzero
  `secret_like_values_found` fails closed with
  `conformance_sanitization_leak_detected`.

Fails closed (non-zero exit, reason code on stderr) on any structural
defect — confirmed non-vacuous: a missing required field on one target
entry, and a `false` `evidence_no_network`, were each mutation-tested
directly against `verify_conformance_matrix`/the CLI's own guard and
confirmed to raise the expected `ConformanceVerificationError` before the
guard was restored.

## Deliberately out of scope

- real Router/provider integration;
- publishing any comment or Check Run;
- Codex or any second-opinion review;
- real clinical data, CELK/GERINT;
- auto-approve, auto-merge, deploy, or any remediation.

## Tests

`tests/agent_review/test_v2_dual_target_e2e.py` — 16 tests: two profiles
load and differ; same-engine-different-groups/payloads; two cross-target
rejection proofs; five readiness states across the two targets
(`ready`, `blocked_code`, `blocked_pipeline`, `manual_required`, `stale`);
determinism; order-independence; evidence-hash identity sensitivity; and
three conformance-matrix/CLI tests (complete+deterministic+CLI-verified,
fail-closed on a missing field, fail-closed on a false evidence flag).

The full v1 suite and every pre-existing v2 suite remain green
(`tests/agent_review` — 1050 passed, up from the pre-slice baseline of 1034
by exactly this suite's own 16 new tests, zero regressions elsewhere).
