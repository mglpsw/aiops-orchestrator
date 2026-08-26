# Checkpoint — AgentReview v2 Router receipt-v2 wire binding (`#200-C-WIRE`)

**Status:** review-round-4 corrections qualified locally; a new exact-HEAD forge
CI and independent review remain the next gates. This is a checkpoint of this
slice, not a claim about a future live `master`.

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
review response from the sole endpoint, refuses redirects before a second
credentialed request can exist, treats a truncated body as invalid, validates
the frozen F2-A route grammar (and optional F2-B grammar when present), binds
the receipt's input digest to the exact sent `messages[]`, binds all six caller
declarations, requires public, receipt and selected-attempt finish to converge
on `stop`, refuses explicit truncated/incomplete F2-B coverage, binds the output
digest to exact UTF-8 public assistant content, and only then parses
`ChunkReviewResultV2`. Duplicate JSON keys and non-finite numbers are refused
independently in both the outer Router JSON and embedded AgentReview JSON;
recursion-limit failures are typed at each boundary, and the public choice
selector requires the exact integer zero rather than Python equality.

The offline path remains source-specific and unchanged at its proof boundary.
Both paths converge only at the common result scope validator and
`_make_bound_chunk_response_v2`, using the same `_BINDING_SENTINEL`. No provider,
model, attempt, or Router request identity is normalized into the AgentReview
domain object; publication of execution provenance remains M2.

## Local evidence on the candidate tree

- focused consumer/transport suite after review-round-4 corrections: `73 passed`;
- full suite with the two environment-dependent `sudo` baseline cases excluded:
  `3251 passed, 16 skipped, 2 deselected`;
- unfiltered suite classified the same two failures in
  `tests/agent_review/test_isolated_executor_v2.py`; neither that test file nor
  `app/agent_review/isolated_executor_v2.py` differs from the exact base, and
  this host has no `sudo` executable;
- AgentReview v2 schema export: byte-identical;
- CAEM F0 pin: valid; RI-B0a.2 and target-pack generated views: byte-identical;
- no live Router, provider, target, deploy, release, or runtime mutation.

Fourteen causal mutants were observed RED and then removed:

1. bypassing payload/content equality reached the mocked HTTP opener;
2. ignoring the received-input digest let the adulterated case reach `bound`;
3. parsing the domain before checking output identity changed the required
   `router_output_mismatch` precedence to `router_result_invalid`;
4. removing the contract-ID subset check made both offline and Router results
   escape payload scope.
5. ignoring the selected attempt finish let `length` and omitted observations
   reach `bound` while public finishes said `stop`;
6. replacing strict raw JSON parsing with last-key-wins parsing let a duplicated
   receipt identity field reach `bound`.
7. bypassing strict parsing of the embedded assistant JSON let duplicate
   top-level and nested domain keys reach `bound`;
8. removing the Unicode-to-typed conversion let high and low unpaired
   surrogates escape as `UnicodeEncodeError`;
9. bypassing the explicit F2-B completeness gate let `truncated=true` and
   `coverage_incomplete`, independently and together, reach `bound`.
10. restoring equality-only choice selection let `false` and `0.0` reach
    `bound` as index zero;
11. removing the outer recursion conversion let deeply nested Router JSON
    escape as `RecursionError`;
12. removing the inner recursion conversion let deeply nested assistant JSON
    escape as `RecursionError`.
13. restoring the default redirect constructor returned a cross-origin request
    instead of raising before credential forwarding;
14. removing the truncated-body conversion let `IncompleteRead` escape the
    chunk choke point.

Independent exact-HEAD review round 1 on `6af59994dca6f9a4367b7474475c7742c5ec3069`
reported findings 5 and 6 above as P1/P2. Both were reproduced, classified
`VALID` and material, assigned to distinct violated propositions, and corrected
under the precommitted C1 attempt recorded on PR #270. No recurrence candidate
formed in round 1 because no declared correction ran between its same-subject
findings.

Independent exact-HEAD review round 2 on
`b84a76180e5dee4fa7d79bb643c20d797ab1a0ab` reported three further findings:
duplicate keys inside the independently parsed assistant JSON, unpaired
surrogates escaping the UTF-8 output canonicalization, and explicit incomplete
F2-B coverage reaching the domain. All three were reproduced and classified
`VALID` and material. Candidates against C1 were established out of scope:
they violate different propositions, and the inner-JSON witness is outside the
outer-document domain demonstrated before C1. C2 and its causal probes were
recorded on PR #270 before this correction.

Independent exact-HEAD review round 3 on
`fb1ccb955b5a46d2e6445ab5aaa8aa8e0738a619` reported boolean choice-index
coercion and an untyped assistant JSON recursion failure. Both were reproduced
and classified `VALID` and material; bounded sibling search added floating zero
at the same index and recursion-limit nesting at the outer Router JSON boundary.
Candidates against C1/C2 were established out of scope because they violate
different propositions and demonstrated domains; no two corrections were
established as defeated. C3 and its three causal probes were recorded on PR
#270 before this correction.

Independent exact-HEAD review round 4 on
`11e8ce0da3d1efd9902d1968cb10825be43e97e9` reported an escaping
`IncompleteRead` and default urllib cross-origin redirect behavior that retains
the Authorization header. Both were reproduced and classified `VALID` and
material. Candidates against C1-C3 were established out of scope: transport
body acquisition and redirect credential confinement violate different
propositions outside the prior JSON/type domains. C4 and both causal probes
were recorded on PR #270 before this correction.

## Remaining gates and scope fence

The Draft PR remains the only permitted publication. Its next transition is a
new exact-HEAD CI and independent exact-HEAD review. Ready, merge, release, tag,
deploy, live Router or provider use, `#193-#198`, AgentEscala `#763`, and
CT102/CT104 mutation remain outside this slice and require their own grants.
