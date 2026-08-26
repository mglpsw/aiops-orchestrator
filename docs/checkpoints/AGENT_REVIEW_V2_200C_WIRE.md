# Checkpoint — AgentReview v2 Router receipt-v2 wire binding (`#200-C-WIRE`)

**Status:** implementation candidate qualified locally; exact-HEAD forge CI and
independent review remain the next gates. This is a checkpoint of this slice,
not a claim about a future live `master`.

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  issue: 200
  slice: 200-C-WIRE
  branch: codex/200-c-wire-router-receipt-v2
  base_sha: 74d09a544587aa26ab4fd1374d5b527932f691fe
  router_authority:
    repository: mglpsw/agent-router-api
    sha: 80e921dfc28436bd4fed8a4e1fa72ffaa168d10c
    receipt: agent-router.inference-receipt.v2
    capability: F2-A

state:
  public_agentreview_schema_changed: false
  offline_v1_echo_path_preserved: true
  router_f1_http_path_accepted: false
  router_receipt_v2_path_implemented: true
  payload_content_prebind_before_messages: true
  common_file_coverage_contract_scope: true
  live_router_call_made: false
  provider_call_made: false
  target_repository_mutated: false
  ct102_ct104_mutated: false

milestones:
  M1_200C_WIRE: implementation_candidate_complete
  M2_193_198_provenance_products: deferred
  M3_distribution_canary: deferred
  AgentEscala_763_canary: not_executed
  issue_200_formal_closure: pending_real_semantic_canary

acceptance_material:
  router_fixtures: maintainer_supplied_for_this_round
  historical_R1_R28_M1_M12_repository_provenance: UNOBSERVED
```

## Reconciled authority flow

```text
offline envelope v1 -> exact echo proof ------------------┐
                                                          ├-> common scope
messages[] -> Router -> receipt v2 + assistant content ---┘   -> one private
                                                               BoundChunkResponseV2
                                                               constructor
                                                               -> domain parser
```

The HTTP Router path no longer interprets an AgentReview transport envelope or
F1 echo as a current Router response. It requires one non-streaming structured
review response with `agent-router.inference-receipt.v2`, validates the frozen
F2-A route grammar (and optional F2-B grammar when present), binds the receipt's
input digest to the exact sent `messages[]`, binds all six caller declarations,
requires a conclusive `stop`, binds the output digest to the exact public
assistant content, and only then parses `ChunkReviewResultV2`.

The offline path remains source-specific and unchanged at its proof boundary.
Both paths converge only at the common result scope validator and
`_make_bound_chunk_response_v2`, using the same `_BINDING_SENTINEL`. No provider,
model, attempt, or Router request identity is normalized into the AgentReview
domain object; publication of execution provenance remains M2.

## Local evidence on the candidate tree

- focused consumer/transport suite: `56 passed`;
- full suite with the two environment-dependent `sudo` baseline cases excluded:
  `3234 passed, 16 skipped, 2 deselected`;
- unfiltered suite classified the same two failures in
  `tests/agent_review/test_isolated_executor_v2.py`; neither that test file nor
  `app/agent_review/isolated_executor_v2.py` differs from the exact base, and
  this host has no `sudo` executable;
- AgentReview v2 schema export: byte-identical;
- CAEM F0 pin: valid; RI-B0a.2 and target-pack generated views: byte-identical;
- no live Router, provider, target, deploy, release, or runtime mutation.

Four causal mutants were observed RED and then removed:

1. bypassing payload/content equality reached the mocked HTTP opener;
2. ignoring the received-input digest let the adulterated case reach `bound`;
3. parsing the domain before checking output identity changed the required
   `router_output_mismatch` precedence to `router_result_invalid`;
4. removing the contract-ID subset check made both offline and Router results
   escape payload scope.

## Remaining gates and scope fence

The next permitted transition is one Draft PR followed by exact-HEAD CI and an
independent exact-HEAD review. Ready, merge, release, tag, deploy, live Router or
provider use, `#193-#198`, AgentEscala `#763`, and CT102/CT104 mutation remain
outside this slice and require their own grants.
