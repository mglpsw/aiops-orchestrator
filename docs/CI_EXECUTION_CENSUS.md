# CI_EXECUTION_CENSUS — bounded CI and host-aware validation

Observed base: `ea6a6584b7a4735a753e9a2b61f8f96cb4e3494f` (master).
This DevX/CI slice is independent of C2 and AgentReview v2 implementation.
No merge, deployment, provider call, #370 mutation or Gate A is authorized.

## Before implementation

Base pytest collection: **4,241 IDs**, **138 test files**.

| Directory | Tests |
|---|---:|
| tests/agent_review | 3,478 |
| tests/ (root files) | 529 |
| tests/evals | 99 |
| tests/caem_consumer | 83 |
| tests/ri_b0a | 33 |
| tests/common | 19 |

Marker observations: 154 `requires_network`, 60 `skipif`, 1,027
`parametrize`; no integration/runtime/docker/prometheus-marked IDs in this
base collection. Those external markers remain excluded by contract.

| Property/suite | Validate repository | AgentReview release gates | Executions per PR |
|---|---:|---:|---:|
| Ordinary pytest (4,087 IDs) | test.sh | ci_validate.sh | **2** |
| Real Git/subprocess pytest (154 IDs) | — | ci_validate.sh | **1** |
| Shell syntax | direct | ci_validate.sh | **2** |
| Actions catalog | direct | ci_validate.sh | **2** |
| Compose configs | direct | ci_validate.sh | **2** |
| Schema export, CAEM pin, RI view, target-pack view | — | ci_validate.sh + direct | **2 each** |
| Eval generation and six benchmark checks | — | direct | **1 each** |
| Dangerous-pattern inventory (informational) | direct | — | **1** |

```text
old required CI
  Validate repository → integrity → ordinary FULL
  AgentReview release gates → integrity + four generated checks
                            → ordinary FULL → N
                            → eleven generated/benchmark checks
```

Run `36948059035` on #370 HEAD
`de4a7c2fed2d45c916dc5a1df746e1f2e29feacf`:
Validate repository cancelled during `Run unit tests` at the ten-minute
budget; release-gates job succeeded (repository validation step 10m41s).
`CI_TIMEOUT != TestFailure`: this shows incomplete execution, with no
demonstrated failing test. Run data is historical and must not be promoted to
current PR evidence.

## New execution ownership

```text
new required CI
  Validate repository → repository integrity → causal focused regression
                      → post-Ready guard tests when present on subject
  AgentReview release gates → eleven generated/contract/benchmark checks

local FULL / CI-infrastructure PR + manual + weekly GitHub FULL
  A static/generated → ordinary collection
  B P (host-aware loadfile) → C S (serial) → D N (serial)
```

One duplicate ordinary invocation, three duplicate integrity validators, and
four duplicate generated checks are removed. The remaining full ordinary and
N execution moves to full validation; this is an explicit gate-scope change,
not a claim that fast CI supplies full regression on every commit.

## Partition and resources

P candidates use private tmp_path repositories/artifacts, in-process API
clients, or process-local mocks/environment. `loadfile` reduces interleaving
within a module. The initial S file list is in `scripts/test_lanes.py`:
mount namespace/topology, carrier/epoch and parent-child process identity
observations, plus the new nested pytest qualification harness. N remains
serial because it includes real Git, privilege drop, process-group kill and
supervision; its isolation is not assumed safe for concurrent tests. R stays
outside this slice. No production refactoring was needed.

AgentEscala's `scripts/dev.sh` and `scripts/dev_test_workers.py` served only
as design predecessors: observe capacity before choosing workers, cap auto
parallelism, use loadfile, make exceptions explicit, and never install from
the runner. No frontend/bootstrap/provenance code was copied.

AIOps defaults: reserve 2 GiB, budget 1 GiB/worker, auto ceiling 4; CPU is the
minimum of visible CPU, quota and cpuset. Memory uses host availability and
cgroup spare RAM. Unknown memory means one worker. Metadata reading supports
conventional v1/v2 paths and ancestor limits, without host mutation. Explicit
positive override is an operator decision and may exceed automatic limits.
Lanes execute sequentially, with one budget; hosted FULL auto ceiling is 2.

## Qualification and limitations

PR evidence must carry actual serial/parallel timings, repeated bounded P
runs, S and N outcomes, collection/result equality, host capacity, commit/tree,
and new GitHub CI results. Receipts/logs remain temporary. There is no baked-in
speedup or fabricated expected timing. Fresh new-PR Actions timing is required.

The base lacks `tests/test_post_ready_codex_guard.py` and its implementation;
they belong to frozen #370. Fast CI explicitly records SkippedByScope when
absent and executes the corpus when present. Current classification:
`ABSENT_BY_SUBJECT` / `NOT_APPLICABLE_TO_CURRENT_SUBJECT`, never PASS. After
#371 integrates, #370 must reconcile/rebase onto its master and requalify the
real present corpus through fast CI. No C2 code is imported.

The PR changes the workflow that tests it. Passing those changed jobs is
GitHub CI evidence, not independent workflow provenance or a stronger trust
architecture. LocalReceipt != AutomaticMergeAuthority; push stales receipts.
The existing code under test can implement v2; the new test infrastructure
imports none of its attestation/readiness/broker contracts.

The full workflow also runs for the explicit CI/test-infrastructure pull-request
path list, remaining non-required. Its artifact binds source and tested
identities/trees/run/attempt and retains P/S/N collections, outcomes and skips;
see TESTING.md. Existing local sudo-dependent failures remain classified as
PREEXISTING_ENVIRONMENT_CAPABILITY_LIMITATION pending remote qualification.
#327 is independent and must reconcile its ledger-lint owner if it integrates
after #371. No linter or C2 implementation is imported here.
