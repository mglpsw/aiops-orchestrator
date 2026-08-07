# Checkpoint — AgentReview v2 trusted checks: contract slice (#201-A)

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 201
  slice: 201-A (contracts only)
  branch: feat/201-a-trusted-check-contracts
  base_sha: 71e0c74c828199fb7ea56a45430a7dff80913150   # origin/master after #200-C merged
  # head_sha: single commit; read from `git log`/the PR, not hand-copied
  # here (see #200-B's own checkpoint for why a self-referential SHA in
  # this file goes stale on the very next amend).

state:
  plan_result_contracts_frozen: true
  offline_simulator_implemented: false    # #201-B1
  isolated_executor_implemented: false    # #201-B2
  adversarial_hardening_proven: false     # #201-B3
  wired_into_synthetic_readiness: false   # #201-C
  existing_v2_schemas_unchanged: true     # verified -- no pre-existing schema file touched
  capability_state: contract_only

runtime:
  environment: GitHub-hosted cloud, clean venv (pydantic 2.11.3 / PyYAML 6.0.2)
  ct104_touched: false
  ct102_touched: false
  target_repository_touched: false

evidence:
  full_test_suite: "1929 passed, 4 skipped (0 failed)"   # 1904 (post-#200-C) + 25 new
  new_tests_this_slice: 25
  schema_export_check: "AgentReview v2 schemas are byte-identical (2 new schemas added,
    zero pre-existing schema touched)"
  ci_validate_sh: "1901 passed, 4 skipped, 28 deselected -- OK"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (2 new not_applicable entries)"
  git_diff_check: clean
  structural_proof: "test_no_required_check_conclusion_value_exists_for_environmental_failure
    asserts RequiredCheckConclusionV2's own member set is exactly
    {success, failure, pending, missing} -- proving environmental-outcome
    promotion refusal is forced by the existing frozen contract's shape,
    not merely this module's policy choice"
  commits: 1

remaining_for_issue_201:
  - "#201-B1: offline simulator producing synthetic TrustedCheckResultV2 instances"
  - "#201-B2: isolated executor -- pinned image/runtime, no network, no sudo,
     no docker socket, result channel the PR cannot write to"
  - "#201-B3: adversarial proof against a malicious conftest.py/reporter/plugin
     attempting to falsify a result"
  - "#201-C: wire a real TrustedCheckResultV2 into run_synthetic_review_v2's
     checks parameter, closing AgentEscala#750"
```

## What is proven here

- `bind_trusted_check_result_to_plan_v2` fail-closes on cross-run,
  stale-HEAD, harness-digest-mismatch, unauthorized-check, and a result
  that bypassed its own constructor via `model_construct` — proven
  directly against each case, not merely documented;
- `promote_trusted_check_to_required_v2` is the ONLY function that may
  construct a `RequiredCheckResultV2` from this sidecar, and refuses both
  `untrusted_advisory` authority and every environmental outcome, even
  from a `trusted` result — proven for all four environmental outcomes
  individually, plus a structural proof that `RequiredCheckConclusionV2`
  itself has no value to represent one;
- `AllowlistedCheckCommandV2.network_allowed` is pinned `Literal[False]`
  at the type level — a plan with network access is not constructible at
  all, proven by a direct `ValidationError` test;
- `TrustedCheckResultMaterialV2`'s own construction validator ties
  `artifact_sha256` presence exactly to a resolved (`success`/`failure`)
  outcome in both directions — proven for all four environmental outcomes
  individually rejecting a present `artifact_sha256`.

## What is NOT proven or claimed here

- no process has ever been spawned by this code;
- no isolation (network/sudo/docker-socket denial) has been tested against
  a real subprocess — that requires a real executor (`#201-B2`);
- no adversarial `conftest.py`/reporter/plugin attack has been attempted
  against real code — only the CONTRACT's inability to represent an
  untrusted result as trusted is proven here;
- no `ReviewReadinessV2` has ever consumed a `RequiredCheckResultV2`
  produced by this module (`#201-C`);
- `#201` does not close with this slice. It remains open pending
  `#201-B1`/`#201-B2`/`#201-B3`/`#201-C`.

## Rollback

One commit, additive: one new module
(`app/agent_review/trusted_checks_v2.py`), one new test file, 2 new
schema files, additive entries in `config/ri/ri-b0a-2-reuse-manifest.json`
+ its generated view, and one additive registration in `schema_export_v2.
py`. No existing schema changed. No existing public function's signature
changed (the one existing-test edit — `test_contracts_v2.py`'s fixed
schema-filename set — adds two filenames to an assertion set, changing no
behavior). Reverting this commit requires no coordinated change elsewhere
— `#201-B1`+ do not exist yet to depend on it.
