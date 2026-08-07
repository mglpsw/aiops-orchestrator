# Checkpoint — AgentReview v2 semantic review content: contract slice (#200-A)

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 200
  slice: 200-A (contract and ADR only)
  branch: feat/200-a-review-content-contract
  base_sha: 273864eaa01dfb708a5a26d3756e16c6cd918a9f   # origin/master at slice start
  head_sha: 4bd7021                                    # second (final) commit of this slice

state:
  contract_frozen: true
  adr_accepted: true
  content_extraction_implemented: false     # #200-B
  redaction_dlp_engine_implemented: false    # #200-B
  router_transport_wired: false              # #200-C
  semantic_review_e2e: false                 # #200-C
  canary_review_of_a_real_repository: false  # AgentEscala#763-A, gated on #200-B/#200-C
  core_synthetic_complete_for_200: false     # requires this slice + #200-B + #200-C together
  existing_v2_schemas_unchanged: true        # verified, not assumed -- see evidence.schema_check

runtime:
  environment: local worktree, CT104-equivalent offline
  router_enabled: false            # no transport implementation exists yet in this slice
  ct102_touched: false
  target_repository_touched: false # no write to AgentEscala or InterLeitos

evidence:
  full_test_suite: "1882 passed, 4 skipped (0 failed)"
  new_tests_this_slice: 54          # 32 review_content_v2 + 22 review_transport_contract_v2
  schema_export_check: "AgentReview v2 schemas are byte-identical."
  ri_b0a_reuse_manifest_check: "33 passed (tests/ri_b0a)"
  payload_sha256_regression: "zero change -- confirmed by full suite + schema --check,
    not merely asserted"
  commits: 2   # split by module: ReviewContentV2+DLP+ADR, then the transport envelope

remaining_for_issue_200:
  - "#200-B: extractor/materializer/redactor + declarative DLP engine,
     acquiring real hunk bytes via diff_acquisition_v2.acquire_authoritative_diff_v2
     and producing a real ReviewContentV2"
  - "#200-C: Router transport wiring, offline-file transport for tests, and a
     dual-target synthetic E2E producing a real ReviewReadinessV2 from real content"
  - "AgentEscala#763-A: the first real canary, gated on both of the above landing
     and a repin to the RC that contains them"
```

## What is proven here

- `ReviewContentV2` (`app/agent_review/review_content_v2.py`) is a frozen,
  strict, self-hashing contract, independently bound to a `ManifestV2` by
  `run_id`/`manifest_hash` (`bind_review_content_to_manifest_v2`), never
  folded into `ChunkPayloadV2`;
- a `coverage_required` fragment cannot be constructed without content --
  proven by a direct negative test
  (`test_fragment_rejects_coverage_required_without_included_policy`), not
  merely documented;
- `ChunkReviewTransportEnvelopeV1`
  (`app/agent_review/review_transport_contract_v2.py`) wraps the unmodified
  v2 response envelope and requires an exact echo of
  `request_sha256`/`content_sha256` before `verify_transport_echo_v1`
  returns anything; proven directly against the exact attack this exists to
  close
  (`test_echo_verification_rejects_a_response_produced_over_a_different_sidecar`);
- `DlpPolicyDeclarationV2` structurally cannot reference target-owned code
  (no `path`/`module`/`import`/`entrypoint` field; `extra="forbid"`), proven
  by `test_load_dlp_policy_rejects_target_owned_code_references`
  (parametrized over all seven forbidden keys);
- every already-published v2 schema
  (`agent-review.chunk-payload.v2`, `agent-review.chunk-response-envelope.v2`,
  and the other eight) is byte-identical before and after this slice
  (`scripts/export-agent-review-v2-schemas.py --check`), and no fixture's
  `payload_sha256` changed anywhere in the 1882-test suite.

## What is NOT proven or claimed here

- no real hunk content has ever been extracted by this code -- there is no
  extractor yet, only the contract the extractor (`#200-B`) will produce
  instances of;
- no DLP engine executes anything -- `DlpPolicyDeclarationV2` is a schema,
  not a running detector;
- no request has ever been sent to the Agent Router, or to any transport --
  `verify_transport_echo_v1` has only been exercised against hand-built
  fixtures in the test suite;
- `ReviewReadinessV2` has not been computed from real semantic content
  anywhere -- that is `#200-C`'s deliverable;
- issue `#200` does not close with this slice. It remains open pending
  `#200-B` and `#200-C`.

## Rollback

Both commits are additive: two new modules, three new schema files, two new
test files, and additive entries in `config/ri/ri-b0a-2-reuse-manifest.json`
+ its generated view. No existing file's public behavior changed. Reverting
either or both commits requires no coordinated change elsewhere -- nothing
in `#200-B`/`#200-C` exists yet to depend on them.
