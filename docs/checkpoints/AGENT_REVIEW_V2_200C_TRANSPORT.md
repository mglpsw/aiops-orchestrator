# Checkpoint — AgentReview v2 semantic review content: transport and synthetic E2E slice (#200-C)

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 200
  slice: 200-C (transport wiring, offline-file transport, synthetic E2E)
  branch: feat/200-c-review-transport-e2e
  base_sha: c5c07d890800a933d5a0779408a444b366f84635   # origin/master after #200-B merged
  # head_sha: this slice is a single commit; read it from `git log` / the
  # PR, not hand-copied here (see #200-A/#200-B's own checkpoints for why
  # a self-referential SHA in this file goes stale on the very next amend).

state:
  contract_frozen: true                     # inherited from #200-A
  content_extraction_implemented: true       # inherited from #200-B
  redaction_dlp_engine_implemented: true     # inherited from #200-B
  router_transport_wired: true               # this slice
  semantic_review_e2e: true                  # this slice -- OFFLINE/synthetic only
  live_router_call_made: false               # never, in this slice or any prior one
  canary_review_of_a_real_repository: false  # AgentEscala#763-A, gated on repin + Router-enable grant
  core_synthetic_complete_for_200: true       # #200-A + #200-B + #200-C, all landed
  capability_state: contract_readiness_baseline   # STILL NOT semantic_reviewer_shadow -- see #199
  existing_v2_schemas_unchanged: true

runtime:
  environment: GitHub-hosted cloud, clean venv (pydantic 2.11.3 / PyYAML 6.0.2,
    matching requirements-agent-review.lock)
  router_enabled: false            # agent_router_transport_v2 exists and is tested, never called live
  ct102_touched: false
  target_repository_touched: false # no write to AgentEscala or InterLeitos

evidence:
  full_test_suite: "1904 passed, 4 skipped (0 failed)"   # 1896 (post-#200-B) + 8 new
  new_tests_this_slice: 8
  schema_export_check: "AgentReview v2 schemas are byte-identical. (unchanged this slice)"
  ci_validate_sh: "1876 passed, 4 skipped, 28 deselected -- OK"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (unchanged this slice)"
  git_diff_check: clean
  real_e2e_proven:
    - "real git repo -> real diff -> real ManifestV2 -> real ChunkPayloadV2 -> real
       ReviewContentV2 -> real ChunkReviewRequestV2 -> offline transport -> real
       ChunkReviewTransportEnvelopeV1 -> verify_transport_echo_v1 ->
       bind_chunk_response_v2 -> parse_bound_chunk_response_v2 ->
       synthesize_chunk_results_v2 -> compute_readiness_decision_v2 ->
       emit_review_readiness_v2 -> a REAL ReviewReadinessV2 with state=READY"
    - "a tampered content_sha256 echo on one chunk degrades exactly that chunk to
       manual_required and the resulting readiness never reaches READY"
    - "a missing response file and a malformed-JSON response file both degrade to
       manual_required with a stable, typed reason code -- never a crash"
    - "agent_router_transport_v2 refuses immediately (ROUTER_DISABLED_REASON_V2) with
       no api_key, before any network attempt -- proven directly, not assumed"
    - "agent_router_transport_v2 calls EXACTLY {base_url}/v1/chat/completions -- proven
       against a mocked HTTP layer capturing the actual URL requested"
    - "agent_router_transport_v2 maps HTTP 5xx to transport_unavailable, never approval"
  commits: 1

remaining_for_issue_200:
  - "none -- #200-A + #200-B + #200-C together satisfy #200's own
     core_synthetic_complete acceptance criteria. #200 closes with this slice."
  - "AgentEscala#763-A remains open work, but it is now UNBLOCKED at the core
     level: repin to a release containing #200-A/B/C, then a separate grant
     to set AGENT_REVIEW_V2_ROUTER_ENABLED=true for the first live canary."
```

## What is proven here

- the full chain from a real diff to a real `ReviewReadinessV2` works
  end-to-end, offline, synthetically -- not through any single-function
  unit test, but through one real temporary git repository walking every
  documented step in order;
- the fixed order of authority from the #199 execution plan's D2 (content
  -> request -> transport -> envelope -> echo -> binding -> parser ->
  synthesis -> readiness) is not merely documented, it is the literal call
  graph of `execute_chunk_review_v2`/`run_synthetic_review_v2` -- there is
  no code path that reaches a finding without passing through all of it;
- a tampered echo, a missing response, and a malformed response all
  degrade to `manual_required` and are proven (not assumed) to prevent
  `ReadinessStateV2.READY` -- the exact guarantee `#200-A`'s D2 anchor and
  `#200`'s own "resposta tentando promover cobertura" test requirement
  exist for;
- `agent_router_transport_v2` is real, complete, and locked to the single
  allowed endpoint and explicit-flag-only enablement -- proven against a
  mocked HTTP layer, never a live call.

## What is NOT proven or claimed here

- no request has ever been sent to the real Agent Router -- `router_
  enabled: false` throughout this slice and every prior one;
- no canary review of a real repository exists (`AgentEscala#763-A`);
- `#200`'s `core_synthetic_complete` is now `true`, but the epic `#199`'s
  own `semantic_reviewer_shadow` capability state remains `false` --
  those are different, explicitly independent states (see the epic's own
  nomenclature-correction note in its `#199` issue comment thread);
- cross-checking `ChunkContentV2.payload_sha256` byte-for-byte against the
  real `ChunkPayloadV2` object it names is still the caller's
  responsibility, not verified inside this module (unchanged from
  `#200-B`'s own documented split).

## Rollback

One commit, additive: one new module
(`app/agent_review/review_transport_v2.py`) and one new test file. No
schema changed. No existing public function's signature changed. Reverting
this commit requires no coordinated change elsewhere -- nothing outside
this slice depends on it yet.
