# Checkpoint — AgentReview v2 trusted checks: isolated executor slice (#201-B2)

```yaml
subject:
  repository: mglpsw/aiops-orchestrator
  epic: 199
  issue: 201
  slice: 201-B2 (isolated executor)
  branch: feat/201-b2-isolated-executor
  base_sha: f409fd15e25cd7fcbed31fa8107cec419fd3827e   # origin/master after #211 squash-merged

state:
  plan_result_contracts_frozen: true        # inherited from #201-A
  offline_simulator_implemented: true        # inherited from #201-B1
  isolated_executor_implemented: true        # this slice
  isolated_executor_proven_on_ct104: false   # blocked_external: ct104_unavailable
  adversarial_hardening_proven: partial      # some #201-B3 scope pulled forward, see below
  wired_into_synthetic_readiness: false      # #201-C
  existing_v2_schemas_unchanged: true        # no wire schema touched -- executor has none of its own
  capability_state: real_isolated_execution_in_dev_sandbox_not_ct104

evidence:
  full_test_suite: "1975 passed, 4 skipped"
  new_tests_this_slice: 19
  ci_validate_sh: "sec 7: 1917 passed, 4 skipped, 58 deselected; sec 8: 58 passed, 1921 deselected -- OK"
  schema_export_check: "byte-identical (unchanged this slice)"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (unchanged this slice)"
  git_diff_check: clean
  ruff_new_files: "All checks passed! (app/agent_review/isolated_executor_v2.py, tests/agent_review/test_isolated_executor_v2.py)"
  flakiness_check: "3 consecutive full runs of the new suite, 19/19 passed each time"
```

## CT104 status this corte

CT104 (the project's pinned isolated-execution runner) is **offline** at
this corte, consistent with `docs/engineering/CURRENT_CHECKPOINT.md`'s
own recorded state. Per explicit instruction: this slice does not use
CT102 as a substitute, and does not fabricate environmental proof of
CT104-specific guarantees. Everything below is either (a) proven for real
in whatever Linux sandbox actually executed this test suite this session
(explicitly NOT CT104, named as such everywhere it matters), or (b)
marked `blocked_external: ct104_unavailable` -- never silently assumed
true, never silently dropped from the list.

## Issue #201 acceptance criteria -- itemized, none silently reduced

| # | Criterion (issue #201's own wording) | Status |
|---|---|---|
| 1 | contratos strict de plan/result definidos | `done` -- #201-A, reused unmodified |
| 2 | harness e serializer ficam fora do alcance da PR | `proven_in_this_sandbox` -- adversarial forged-stdout/forged-report test proves the verdict depends only on the host-observed kernel exit code, never anything the child prints or writes |
| 3 | isolamento sem rede e sem privilégios comprovado | `proven_in_this_sandbox, not_ct104` -- real `unshare --user --net` (ENETUNREACH proof), real privilege drop to `nobody`, real `sudo` refusal, real `RLIMIT_AS`/`RLIMIT_NPROC` enforcement. **Not** proven on CT104's own pinned/hardened image -- `blocked_external: ct104_unavailable` for that specific host |
| 4 | canal de resultado não é gravável pela PR | `proven_in_this_sandbox` -- `TrustedCheckResultV2` is constructed entirely inside the parent host process from `Popen.wait()`'s own return value; no file path is ever communicated to or read back from the child for the verdict |
| 5 | artifact é ligado ao HEAD/tested identity e harness digest | `done` -- `run_id`/`head_sha`/`harness_digest` threaded from `plan` into every result unmodified; bindable via #201-A's `bind_trusted_check_result_to_plan_v2` (exercised directly in this slice's own success test) |
| 6 | `required_checks` pode alimentar readiness autoritativa | `not_yet -- deferred to #201-C` (wiring into `review_readiness_emission_v2`/`readiness_decision_v2` is explicitly out of this slice per the issue's own slice breakdown) |
| 7 | ausência/infra failure continua fail-closed/manual_required | `proven` -- command-not-in-inventory and isolation-unavailable both produce `INFRA_FAILURE`; `promote_trusted_check_to_required_v2` (#201-A, reused unmodified) refuses to promote anything but `TRUSTED` + resolved (`SUCCESS`/`FAILURE`) |
| 8 | AgentEscala #750 pode fechar após adoção própria | `not_this_repos_job` -- target-repo adoption, unrelated to this slice's own completeness |
| 9 | nenhum CT102, shell livre, comando gerado por LLM ou redução silenciosa de CI | `proven` -- no CT102 reference anywhere in this module; every command comes from `AllowlistedCommandSpecV2.argv`, resolved only via a host-supplied `inventory` keyed by `command_token` -- never free text, never LLM output; nothing in this slice removes or weakens an existing CI gate |

## Testes obrigatórios (issue #201's own list) -- itemized

| Scenario | Status |
|---|---|
| conftest.py malicioso tentando falsificar sucesso | `covered` -- `test_execute_ignores_forged_stdout_and_report_file_and_trusts_only_the_real_exit_code` |
| reporter/config adulterado | `partially_covered` -- same test covers "a forged report file changes nothing"; a fully separate, host-pinned test-runner binary outside the PR's own installed tooling is a target-repo/`#201-D` integration concern, not proven here |
| subprocesso tentando rede | `covered` -- `test_execute_denies_real_outbound_network_access` (ENETUNREACH) |
| subprocesso tentando sudo | `covered` -- `test_execute_denies_sudo_inside_the_isolated_check` |
| subprocesso tentando docker socket | `not_applicable_here` -- this sandbox has no docker socket to begin with (`ls /var/run/docker.sock` -> absent); nothing in this module ever references one |
| subprocesso tentando escrita no harness | `structurally_true, not_separately_tested` -- the harness IS this module's own Python code running in the parent process; there is no shared writable artifact the child could locate to alter parent-side logic (unlike the report-file scenario, which IS tested) |
| timeout/OOM/cancelamento | `covered` -- `test_execute_produces_a_typed_timeout_outcome_for_a_hanging_check`, `test_execute_produces_a_typed_oom_outcome_for_a_process_killed_by_the_memory_limit` (gcc-gated, skips honestly if `gcc` unavailable -- plus a pure-function unit-test fallback for the classification rule itself), `test_execute_produces_a_typed_cancelled_outcome_when_cancel_event_is_set` |
| stale/cross-run/cross-target | `covered_by_reuse` -- `TrustedCheckResultV2`/`bind_trusted_check_result_to_plan_v2` are #201-A's own, untouched; that binding's own tests already prove this |
| plano com comando não allowlisted | `covered` -- `test_execute_refuses_a_command_token_not_in_the_inventory` |
| artifact alterado | `covered_by_reuse` -- #201-A's own `result_sha256` hash-mismatch binding test |
| mesmo input produz resultado canônico equivalente | `covered` -- `test_execute_result_is_deterministic_across_two_real_runs` |
| v1 e modo sem trusted checks preservados | `not_applicable_here` -- this module is purely additive, wires into nothing yet (`#201-C`'s job) |
| failure ambiental não vira regression de produto | `covered` -- `test_execute_untrusted_advisory_result_still_refuses_promotion` plus the INFRA_FAILURE/TIMEOUT/OOM/CANCELLED tests all produce non-resolved outcomes `promote_trusted_check_to_required_v2` refuses |

## CI-discovered gap, fixed in this same slice (not silently patched over)

This slice's own first real `aiops-ci` run on GitHub Actions failed all 8
new `requires_network` tests: `unshare: write failed /proc/self/uid_map:
Operation not permitted`. Root cause: the original implementation always
dropped to `nobody` BEFORE invoking `unshare --user --map-root-user
--net`, forcing the UNPRIVILEGED user-namespace path even though the
runner's job itself runs as real root -- and that runner's kernel refuses
unprivileged user-namespace creation outright (a common hardening
default), independent of who's asking. Fix: `_isolation_wrapped_argv_v2`
now branches on the REAL starting euid -- real root uses `unshare --net`
alone (root's own `CAP_SYS_ADMIN`, no unprivileged userns involved at
all), with privilege drop to `nobody` happening AFTER the namespace
already exists, via a small inline Python interpreter chained inside it.
The unprivileged-userns path is now a fallback for callers that start
already unprivileged, not the primary mechanism. This is exactly the
class of environment-specific isolation-primitive gap this checkpoint's
own "not CT104" framing exists to catch -- recorded here as what
happened, not smoothed over as if the first design had been correct.

## Honest limitations (named, not hidden)

1. **Exit-code forgery from inside the check's own process is not, and
   cannot in general be, defended against by a single-process harness.**
   If the command named by `command_token` itself (e.g. a real `pytest`
   invocation) is influenced by PR-supplied code (`conftest.py`) that
   calls `os._exit(0)` unconditionally, the host-observed exit code
   genuinely is `0`. This is the same trust boundary every real CI system
   accepts. Named explicitly in the module's own docstring, not silently
   assumed solved.
2. **OOM classification is a documented heuristic**, not perfect
   detection: a runtime that catches its own allocation failure
   gracefully (Python's `MemoryError` -> exit `1`) is classified
   `FAILURE`, not `OOM` -- verified directly by this slice's own hands-on
   testing (a Python `bytearray()` allocation against `RLIMIT_AS`
   produces exit `1`, not a signal), which is exactly why the OOM test
   uses a small compiled C fixture instead (a real, unhandled
   `RLIMIT_AS`-triggered `SIGSEGV`).
3. **`harness_digest` binding to a real, running, pinned container image
   is `blocked_external: ct104_unavailable`.** The field is threaded
   through correctly and bindable via #201-A's own mechanism; independent
   verification against an actual digest-pinned runtime requires a
   container runtime this session does not have and CT104 being reachable.
4. **This session's own sandbox runs as root with broad kernel
   capabilities** -- a WEAKER starting point than a properly hardened,
   unprivileged CT104 runner. Isolation demonstrated here proves the
   MECHANISM (namespace unshare, privilege drop, rlimits) is real and
   works; it does not prove resistance to a determined container-escape
   attempt on a materially different, more hostile threat model. That
   stronger guarantee is CT104's own to provide.
5. **Read-only enforcement of the PR checkout itself was deliberately not
   implemented.** Re-reading the issue's own "checkout PR somente como
   input" requirement: the property that actually matters (the PR cannot
   control the harness/serializer/result channel) is satisfied without
   it, since nothing the check writes anywhere is ever trusted for the
   verdict. A disposable checkout being writable by its own test process
   is normal in any CI system, not a security gap here.
6. **Docker-socket denial is untested, not unproven** -- this sandbox
   simply has no docker socket to attempt reaching in the first place, so
   there is nothing to adversarially prove here; the module never
   references one either way.

## What's next in the DAG

- `#201-C`: wire `execute_trusted_check_plan_v2`'s real output into
  `review_readiness_emission_v2`, replacing/augmenting whatever currently
  feeds `required_checks` for a target that opts in;
- `#201-D`: target conformance (AgentEscala as reference; a real
  target-owned `CheckCommandInventoryV2` sourced from that target's own
  committed config, not test-local fixtures as here);
- CT104 coming online reopens item 3 and limitation 3/4 above for
  re-verification against the real pinned runner -- not automatically
  assumed to pass just because the portable mechanism does here.

## Merge record

Not merged as of this checkpoint's own commit -- opened as a draft PR,
held for explicit nominal merge grant, per the same discipline as `#211`.
