# Checkpoint — AgentReview v2 trusted checks: offline simulator slice (#201-B1)

**Status:** `CHECKPOINT SNAPSHOT` — registro da slice no corte em que foi escrito. Não é estado atual; ver [`../PROJECT_STATUS.md`](../PROJECT_STATUS.md).

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 201
  slice: 201-B1 (offline simulator)
  branch: feat/201-b1-trusted-check-simulator
  base_sha: f0013358d599a57a1a20275c54d882559997cbc1   # origin/master after #201-A merged

state:
  plan_result_contracts_frozen: true       # inherited from #201-A
  offline_simulator_implemented: true       # this slice
  isolated_executor_implemented: false      # #201-B2
  adversarial_hardening_proven: false       # #201-B3
  wired_into_synthetic_readiness: false     # #201-C
  existing_v2_schemas_unchanged: true       # no schema touched -- simulator has no wire schema
  capability_state: contract_and_offline_simulation_only

evidence:
  full_test_suite: "1942 passed, 4 skipped"    # 1929 (post-#201-A) + 13 new
  new_tests_this_slice: 13
  schema_export_check: "byte-identical (unchanged this slice)"
  ci_validate_sh: "1914 passed, 4 skipped, 28 deselected -- OK"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (unchanged this slice)"
  git_diff_check: clean
  never_spawns_a_process: "proven directly -- subprocess.Popen/run patched to raise;
    simulate_trusted_check_plan_v2 still succeeds"
  commits: 1

remaining_for_issue_201:
  - "#201-B2: isolated executor -- pinned image/runtime, no network, no sudo,
     no docker socket, result channel the PR cannot write to"
  - "#201-B3: adversarial proof against a malicious conftest.py/reporter/plugin
     attempting to falsify a result"
  - "#201-C: wire a real TrustedCheckResultV2 into run_synthetic_review_v2's
     checks parameter, closing AgentEscala#750"
```

## What is proven here

- the simulator never spawns a process, reads a checkout, or touches a
  filesystem — proven, not merely documented (`test_simulate_never_
  touches_the_filesystem_or_spawns_a_process` patches both
  `subprocess.Popen` and `subprocess.run` to raise);
- `authority` has no default — every call site must choose explicitly;
- every simulated result is deterministic (same plan + fixtures + authority
  → byte-identical `result_sha256`/`artifact_sha256`) and independently
  bindable against its plan via the real `bind_trusted_check_result_to_
  plan_v2` from `#201-A`;
- environmental outcomes simulated by this module still refuse to promote
  to `RequiredCheckResultV2`, and `untrusted_advisory`-authority results
  still refuse regardless of outcome — reusing `#201-A`'s own promotion
  authority unmodified, not a parallel check.

## What is NOT proven or claimed here

- no process isolation exists or is tested — nothing here spawns anything
  to isolate (`#201-B2`);
- no adversarial attack against a real subprocess/harness has been
  attempted;
- no `ReviewReadinessV2` has consumed a result from this module;
- `#201` does not close with this slice.

## Rollback

One commit, additive: one new module
(`app/agent_review/trusted_check_simulator_v2.py`), one new test file. No
schema changed (this module defines no wire contract of its own). No
existing public function's signature changed.
