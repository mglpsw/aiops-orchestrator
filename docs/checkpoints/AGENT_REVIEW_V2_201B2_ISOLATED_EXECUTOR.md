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
  full_test_suite: "1979 passed, 4 skipped"
  new_tests_this_slice: 23
  ci_validate_sh: "sec 7: 1917 passed, 4 skipped, 62 deselected; sec 8: 62 passed, 1921 deselected -- OK (run as root, so the new pgid-tamper test executes rather than skips)"
  schema_export_check: "byte-identical (unchanged this slice)"
  caem_f0_pin: "ok"
  ri_b0a_2_reuse_view_check: "byte-identical (unchanged this slice)"
  git_diff_check: clean
  ruff_new_files: "All checks passed! (app/agent_review/isolated_executor_v2.py, tests/agent_review/test_isolated_executor_v2.py)"
  flakiness_check: "root: 23/23 (x1); sudo-elevated via su claude: 23/23 (x2); fully-unprivileged via su ubuntu: 22 passed + 1 correctly skipped (x1) -- every round"
  note: "counts re-measured after the second-review P1 fix (attacker-writable pgid-report file); superseded prior counts (22 tests / 1978) not carried forward"
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
| 2 | harness e serializer ficam fora do alcance da PR | `partially_proven` -- adversarial forged-stdout/forged-report test proves the verdict does NOT depend on anything the child prints or writes. Does NOT prove the child's own EXIT CODE can't be forged by PR-controlled code (e.g. `os._exit(0)` in a `conftest.py` hook) -- an independent review of this slice confirmed this residual gap is real and unresolved; `authority=TRUSTED` must not be used against real adversarial PR code until `#201-B3` closes it (see the module's own docstring and the addendum below) |
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

## CI-discovered gaps, fixed in this same slice (not silently patched over)

This slice's own first two real `aiops-ci` runs on GitHub Actions each
failed all 8 new `requires_network` tests, and each failure taught
something genuinely new about that runner's actual environment rather
than being fixed by guesswork:

**Run 1**: `unshare: write failed /proc/self/uid_map: Operation not
permitted`. The original implementation always dropped to `nobody`
BEFORE invoking `unshare --user --map-root-user --net`, forcing the
unprivileged-user-namespace path. First fix attempt: branch on the REAL
starting euid -- if root, use `unshare --net` alone (no unprivileged
userns needed at all).

**Run 2**: the SAME error, on the SAME `unshare --user --map-root-user`
command -- proving the "if root" branch was never taken, i.e. this
project's own GitHub Actions runner does **not** run the job as real
root (contrary to this slice's own first assumption, stated plainly as a
wrong guess, not hidden). The job is an ordinary unprivileged account,
and that account's unprivileged-user-namespace creation is refused by
the runner's kernel/AppArmor policy.

**Real fix**: `_select_isolation_strategy_v2` now ACTUALLY PROBES a
short, ordered list of real candidates instead of guessing from euid --
real-root-direct, then passwordless-`sudo`-elevated (`sudo -n unshare
--net`, relying on the well-established default that GitHub-hosted
runner accounts have passwordless sudo), then unprivileged-userns as the
last resort -- and only proceeds with whichever candidate a live `<prefix>
-- true` invocation actually returns exit `0` for. Verified locally
under all three real scenarios this sandbox could reproduce (real root;
an unprivileged account with passwordless sudo; an unprivileged account
with neither) -- 19/19 passing in each. If GitHub Actions' runner
identity or policy changes again and NONE of the three candidates work,
this now fails closed with a typed `INFRA_FAILURE`
(`EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2`), same as before -- it does
not regress to a fourth guess.

This whole sequence is exactly the class of environment-specific
isolation-primitive gap this checkpoint's own "not CT104" framing exists
to catch -- recorded here as what actually happened across two real,
observed CI failures, not smoothed over as if the first (or second)
design had been correct on the first try.

## Addendum — independent review, 3 findings closed + 1 confirmed and scoped to `#201-B3`

An independent review of this slice's first CI-green commit (`5f3edc6`)
found the CI status report accurate but raised 5 further points. **3 were
confirmed real and closed here, without opening a new phase; 1
(exit-code forgery, finding 2 below) is an already-known,
architecturally unresolved gap this addendum makes more prominent rather
than pretending to close -- a second pass over this SAME addendum's own
first commit corrected an earlier draft here that had miscounted this as
"4 closed", conflating "documented as an explicit rule" with "fixed".**
Precise accounting: 3 closed (findings 1, 3, 5), 1 confirmed-and-scoped-
open (finding 2), 1 not a code issue (finding 4, cosmetic).

1. **Inventory not bound to the plan's own `authority_suite_digest`
   (confirmed, closed).** `TrustedCheckPlanV2.authority_suite_digest`
   (#201-A) exists specifically to pin the host-owned material a plan
   commits to, but `execute_trusted_check_plan_v2` accepted ANY
   `inventory` a caller handed it -- proving values (each
   `AllowlistedCommandSpecV2` real and typed) but not provenance (nothing
   stopped a caller resolving the plan's `command_token`s against a
   DIFFERENT inventory than the one the plan's own digest names). Fix:
   new `compute_check_command_inventory_digest_v2(inventory)`, checked
   against `plan.authority_suite_digest` before a single token resolves;
   a mismatch refuses the WHOLE plan (every check gets `INFRA_FAILURE`
   with the new `EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2`, none
   attempted). Mirrors `#211`'s own `target_profile`/`manifest.identity.
   profile_hash` fix exactly. Red-tested by
   `test_execute_refuses_an_inventory_that_does_not_match_the_plans_own_
   digest`.
2. **`authority=TRUSTED` is caller-declared, not a property this module
   independently verifies (confirmed, real limitation, made explicit
   rather than "fixed").** #201-A's own docstring assigns "enforce WHO
   sets `TRUSTED`" to `#201-B2`/`#201-B3`. This slice's isolation
   properties (harness/serializer out of the check's reach, result
   channel unwritable by it) ARE part of that enforcement, but they do
   not close the exit-code-forgery gap (finding 4 below) -- so
   `authority=TRUSTED` still cannot be asserted as SAFE for real
   adversarial PR code from this slice alone. Rather than attempt an
   incomplete code fix (a caller-supplied "I promise this is safe" flag
   would be security theater, not a real control), this addendum makes
   the rule explicit and prominent in the module's own docstring: **do
   not use `authority=TRUSTED` against real, untrusted PR code until
   `#201-B3` closes the exit-code-forgery gap.** `#201-C` must treat this
   as an open precondition, not a solved one.
3. **OOM classification risked the exact failure mode #201 forbids, in
   the OTHER direction (confirmed, fixed by REMOVING the heuristic).**
   The original signal-signature heuristic
   (`SIGKILL`/`SIGSEGV`/`SIGBUS`/`SIGABRT` -> `OOM`) could misclassify a
   genuine, unrelated product crash bug as environmental `OOM` -- which
   never promotes to `RequiredCheckResultV2`, silently hiding a real
   regression from readiness instead of surfacing it as `FAILURE`. Fix:
   `_classify_completed_process_v2` no longer guesses `OOM` at all --
   EVERY signal death now classifies as the conservative, attributable
   `FAILURE`. `RLIMIT_AS` enforcement itself (the actual safety property)
   is unchanged and still proven directly (a compiled C fixture that
   overflows the limit is genuinely killed by `SIGSEGV`); precisely
   typing that as `OOM` vs. an unrelated crash would need independent
   evidence (cgroup `memory.events`) this slice does not implement, named
   as a limitation rather than guessed at.
4. **`conftest.py` exit-code forgery is not actually exercised by the
   existing adversarial test (confirmed -- was already a named
   limitation, now made more precise).** The forged-stdout/forged-report
   test proves the verdict ignores what a check PRINTS or WRITES; it does
   NOT and cannot prove PR-controlled code can't forge the check's own
   real exit code (e.g. `os._exit(0)` in a hook). This was already named
   in the module's own "one thing this module cannot defend against"
   section before this review -- strengthened here with an explicit rule
   (see finding 2) and the test's own docstring updated to state plainly
   what it does and does not prove, rather than let its name ("ignores
   forged ... trusts only the real exit code") read as a broader claim
   than it supports.
5. **`process.communicate()` after the poll loop had no timeout
   (confirmed, fixed -- first attempt, then found genuinely broken by a
   second review and fixed for real).** The tracked PID exiting does not
   guarantee no descendant process is still alive holding the inherited
   stdout/stderr pipe open -- a plain `communicate()` there could hang
   past the check's own logical deadline. First fix: bounded by a grace
   period; on timeout, kill the whole process group via `os.killpg(os.
   getpgid(process.pid), ...)`. **A second review caught that this first
   fix was itself unsafe**: by the time it runs, `Popen.poll()` has
   already reaped the tracked pid internally, and the kernel is free to
   RECYCLE that pid number for an unrelated process -- `os.getpgid(
   process.pid)` at that point could resolve to (and `killpg` then
   silently kill) a completely different process group. Real fix: the
   pgid is now captured ONCE, at spawn time (`start_new_session=True`
   guarantees it equals `process.pid` at that exact moment, before any
   reaping can occur), not re-derived later.

   **Verifying that real fix under the sudo-elevated strategy (the one
   this project's own GitHub Actions runner actually uses) surfaced a
   SECOND, deeper bug, caught by this slice's own adversarial testing
   before ever reaching CI**: `sudo`'s monitor-process architecture
   forks a genuinely NEW process for the command it runs, decoupled from
   the pid/pgid `subprocess.Popen` captured for the original `sudo`
   invocation -- so even the spawn-time-captured value was targeting the
   WRONG group once sudo was the strategy in use, and a plain
   `os.killpg()` cannot signal a group `sudo` elevated to root anyway (a
   real `PermissionError`, reproduced directly, not theorized). Real fix,
   round two: the dropper script itself now calls `os.setsid()` (falling
   back to reading the pgid it's already the leader of via
   `os.getpgrp()` if `setsid()` fails with EPERM, which happens
   whenever the OUTER `start_new_session=True` already made this exact
   process a leader through the non-sudo strategies' plain `execve()`
   chain) and reports its own REAL pgid back to the host via a
   host-controlled temp file; killing goes through `sudo -n kill -9 --
   -<pgid>` too when the selected strategy used it. Red-tested by
   `test_execute_does_not_hang_when_a_descendant_outlives_the_leader_
   holding_the_pipe_open`, run in a background thread with a hard join
   timeout so a real regression fails the test cleanly instead of
   hanging the suite -- verified passing under real root, sudo-elevated,
   AND fully-unprivileged accounts, not just the one this session
   happened to start as.

### Second pass — 2 more findings, closed in this same addendum's own commit

6. **Inventory entries admit a contradictory identity (confirmed,
   closed).** Nothing previously required an inventory's dict key
   (`Mapping[str, AllowlistedCommandSpecV2]`) to equal the
   `command_token` field on the `AllowlistedCommandSpecV2` stored under
   it -- `{"other_token": spec_whose_own_command_token_is_"token"}` was
   silently tolerated (its digest still committed to *some* bytes either
   way) despite being ambiguous identity in a system whose whole point is
   rigorous binding. One of this slice's OWN existing tests had exactly
   this contradiction in its own fixture, not caught until this review.
   Fix: `_inventory_keys_match_command_tokens_v2`, checked before the
   digest itself, fail-closed with a new
   `EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2` on any mismatch (the
   whole plan refused, mirroring the digest-mismatch discipline exactly).
   Red-tested by `test_execute_refuses_an_inventory_whose_dict_keys_do_
   not_match_their_own_command_token`.
7. **Duplicated `@pytest.mark.skipif` decorator (confirmed, cosmetic,
   fixed).** No behavioral effect (both decorators evaluated the same
   condition), just noise -- removed.

A `docs/engineering/CURRENT_CHECKPOINT.md` contradiction the same review
flagged ("PRs abertas: nenhuma" recorded alongside this very PR being
open) is also fixed in this addendum's own commit.

## Third real CI failure on this project's own GitHub Actions runner (fixed)

Pushing the second-pass fix (finding 5's real fix, above) failed EVERY
`requires_network` test on the real runner -- not just the
descendant-hang scenario, all of them, including the simplest possible
"check exits 0". Root cause: the new pgid-report file is created by the
CALLING (unprivileged) process, mode `0600`; the dropper script then
tries to WRITE to it from the sudo-elevated (root, before its own later
privilege drop) identity, and that write raised a genuine, UNCAUGHT
`PermissionError` on the real runner -- plausibly PAM/AppArmor session-
scoped `/tmp` confinement tied to the original caller's identity, though
the exact mechanism could not be reproduced in this session's own
sandbox (tried directly; the equivalent write succeeded there). Because
the crash was uncaught, it took down the WHOLE dropper -- and therefore
the check itself -- turning every outcome into `FAILURE`, regardless of
what the check would otherwise have done.

Fix: reporting the real pgid back to the host is a BEST-EFFORT cleanup
aid, never a requirement for the check to run and produce a correct
verdict -- the write is now wrapped in its own `try/except OSError:
pass` inside the dropper, so any failure there degrades to
`_read_real_pgid_v2`'s existing `fallback_pid` behavior (a worse pgid
for a later best-effort kill, never a crashed check). Also chmod's the
report file `0666` right after creation as defense-in-depth, in case the
real cause is closer to a plain ownership/permission-bits problem in
some environments even if not reproducible in this one. This is exactly
the class of "verify on the real target, don't assume the fix that
worked in a dev sandbox actually works everywhere" lesson this
checkpoint's earlier CI-discovered-gaps sections already document twice
over -- recorded here as a third instance, not an exception to the
pattern.

## Fourth real CI failure on this project's own GitHub Actions runner (fixed)

Pushing the third-pass fix (best-effort pgid-report write, above) turned
every OTHER test green, but one new failure appeared:
`test_execute_does_not_hang_when_a_descendant_outlives_the_leader_holding_the_pipe_open`
asserted `SUCCESS` and got `INFRA_FAILURE`
(`isolated_executor_descendant_hung`). Root cause, present since the
original `communicate()`-hang fix and only now exercised by CI's own
timing: by the time `_run_isolated_v2` reaches the post-loop
`communicate()` call, the tracked LEADER has already exited and
`process.returncode` is already known and authoritative -- the only
thing still outstanding is draining stdout/stderr, which can block if a
descendant the leader forked (and never waited on) inherited the pipe
write-end and is still alive. The existing code killed the process
group and retried `communicate()` once, but if THAT also timed out (a
descendant that had already escaped the process group, e.g. via a
double fork), it raised `IsolatedExecutorError` and discarded the
already-known, already-successful returncode -- turning a leader that
exited `0` into `INFRA_FAILURE` purely because of an unrelated orphan.
That is exactly the failure mode issue #201 forbids in the OTHER
direction (real success reported as environmental failure), the mirror
image of the OOM-guessing gap fixed earlier in this same checkpoint.

Fix: the second `communicate()` timeout no longer raises. It falls back
to whatever partial stdout/stderr the `TimeoutExpired` exception itself
carries (`exc.stdout`/`exc.stderr`, best-effort only, may be empty) and
classification proceeds on `process.returncode` exactly as the non-hung
path does -- an orphaned descendant's fate has no bearing on the
leader's own exit code. `EXECUTOR_REASON_DESCENDANT_HUNG_V2` is now
unused by any code path and was removed rather than kept as dead code.
Verified again under all three local privilege scenarios (root,
sudo-elevated x2, fully-unprivileged) -- 22/22 every time -- plus the
full suite (1978 passed, 4 skipped) and `ci_validate.sh` sections 7-8,
before pushing. This is the fourth real CI-only failure this slice has
hit and fixed by verifying on the actual target; recorded here, not
smoothed over.

## Addendum — second independent review, P1 finding: attacker-writable pgid-report file (fixed)

A second full independent review, run against the actual current HEAD
rather than a stale snapshot this time, confirmed all four prior fixes
(inventory-digest binding, key/token identity, OOM-guessing removal, the
descendant-hang mechanism including its own real CI-caught bug) and
correctly agreed `#201-B3` is not scope creep. It then found one new,
real P1: the third-CI-failure fix (best-effort pgid-report write, above)
made the report file world-writable (`0666`) to work around the
sudo-elevated write's own confinement, but nothing ever locked it back
down afterward. It stayed `0666` for the ENTIRE lifetime of the isolated
check -- not just the brief write window -- meaning the untrusted
command itself (running as `nobody`, same `/tmp`, well-known
`isolated-executor-pgid-*` prefix) could locate and overwrite the pgid
the host later trusts for a PRIVILEGED kill (`sudo -n kill -9 --
-<pgid>` under the sudo strategy, or a root-owned `os.killpg` under the
real-root-direct strategy). An attacker-supplied pgid reaching either of
those is privilege-adjacent, not merely a flaky test -- on a persistent
host (CT104, once online) this could kill an arbitrary process group as
root.

Fixed: the dropper script now chmod's the file back to `0600`
immediately after writing it, still under the elevated identity and
strictly before dropping to `nobody` and exec'ing the untrusted
command. The script is single-threaded and sequential, so the untrusted
command never gets a chance to run while the file is still
world-writable -- the window is closed, not narrowed. Attempted
unconditionally, even if the content write itself failed, so a degraded
fallback pgid never also means a still-writable file.

Added the exact adversarial test the review specified:
`test_execute_denies_the_isolated_check_from_tampering_with_its_own_pgid_report_file`
globs for the well-known prefix from INSIDE the isolated, privilege-
dropped check (discovering its own run's report file the same way a
real attacker reading this module's source would, not via any
out-of-band hint) and asserts every overwrite attempt is denied, not
merely that the overall outcome looks fine. It correctly `pytest.skip`s
under the unprivileged-userns fallback strategy specifically, where
there is no real uid separation between the isolated command and the
host caller to begin with (the isolated process only APPEARS as root
inside its own user namespace; the file it would try to overwrite is
already owned by that same real uid) and the cleanup kill is
unprivileged `os.killpg`, never `sudo` -- a different threat model this
finding does not apply to. This project's own GitHub Actions runner is
not in that situation: it has passwordless sudo and always selects the
sudo-elevated (`drop_to_nobody=True`) strategy, so the fix and its test
are exercising the actually-relevant path.

Verified under real root (23/23), sudo-elevated via `su claude` (23/23,
x2), and fully-unprivileged via `su ubuntu` (22 passed, 1 correctly
skipped -- not silently passed) before pushing. Full suite: 1979
passed, 4 skipped. `ci_validate.sh` sections 7-8 green (62
`requires_network` tests, run as root in this session so the new test
executed rather than skipped).

## Addendum — third independent review, two more real findings: fail-open lockdown, weak-isolation TRUSTED gap (both fixed)

A third review, again run against the actual current HEAD, confirmed
every prior fix (PGID-after-death, key/token identity, descendant hang,
and the pgid-report P1 itself) and again correctly agreed `#201-B3` is
not scope creep. It found two more real gaps, both inside the pgid-
report fix from the previous addendum:

**1. The chmod lockdown was itself fail-open.** `os.chmod(path, 0o600)`
was wrapped in a bare `try/except OSError: pass` -- if that SPECIFIC
chmod failed, the untrusted command still ran, against a file that
could still be `0666`, reopening the exact P1 the previous fix closed.
Fixed by reordering the dropper (chmod now happens BEFORE the content
write, not after) and making the chmod failure fail-CLOSED: on
`OSError`, the dropper `os._exit()`s via a dedicated sentinel
(`_PGID_LOCKDOWN_FAILED_EXIT_CODE_V2`) before ever writing content,
dropping privilege, or exec'ing the untrusted command. The host treats
that exit code as a lockdown failure (`INFRA_FAILURE`,
`EXECUTOR_REASON_PGID_LOCKDOWN_FAILED_V2`) ONLY together with the
report file still having no valid pgid content -- never the exit code
alone -- so a legitimate check that happens to independently use the
same exit value, in a run where lockdown succeeded, is never
misclassified: that specific combination (matching exit code AND no
valid content) is only reachable when lockdown genuinely failed and the
untrusted command never got to run at all.

**2. The unprivileged-userns fallback strategy could still back a
`TRUSTED` result.** That fallback has no real uid separation between
the isolated command and the host caller -- it only APPEARS as root
inside its own user namespace, so ANY file the host owns, including the
pgid-report handshake file, is trivially writable by the "isolated"
command regardless of permission bits, since they are the SAME real
uid. Acceptable for `UNTRUSTED_ADVISORY` (never promotable to begin
with -- a weaker isolation guarantee behind it changes nothing about
what it can be used for), but not for `TRUSTED`, which #201-A already
treats as the sole authority a promotable `RequiredCheckResultV2` can
come from. `execute_trusted_check_plan_v2` now refuses
(`EXECUTOR_REASON_ISOLATION_TOO_WEAK_FOR_TRUSTED_V2`) rather than
silently proceeding whenever that fallback is the only thing that
worked and the caller asked for `TRUSTED`; `UNTRUSTED_ADVISORY` can
still use it.

Four new tests: one execs the dropper's own generated source with
`os.chmod` monkeypatched to fail, proving it self-refuses (this exact
OS-level failure mode -- a MAC/session confinement mechanism -- could
not be reproduced directly in this sandbox, same documented limitation
as the sibling write-side fix, so the test targets the dropper's own
logic rather than trying to force a real chmod failure); one proves the
host-side classification of that sentinel; two prove the
TRUSTED-vs-`UNTRUSTED_ADVISORY` gating over weak isolation. Introducing
the new gating surfaced that MANY existing functional tests (exit-code
classification, timeouts, memory limits) implicitly assumed `TRUSTED`
could always be backed by whatever isolation was available -- no longer
true by design. Rather than weakening those tests to `UNTRUSTED_
ADVISORY` (which would also silently reduce TRUSTED-path coverage on
environments that DO have strong isolation, i.e. this project's real
target), they now request a shared `require_strong_isolation` fixture
that skips them cleanly (not silently) on an environment where only the
weak fallback works, mirroring the real target's actual capability
profile (always real root or passwordless sudo, never only this
fallback).

Verified under real root (27/27), sudo-elevated via `su claude`
(27/27), and fully-unprivileged via `su ubuntu` (16 passed, 11 TRUSTED-
mechanics tests correctly skipped -- the 4 new gating/lockdown tests
still ran and passed under `ubuntu` too, since they force their own
scenario via monkeypatch rather than depending on the real account's
capabilities). Full suite: 1983 passed, 4 skipped. `ci_validate.sh`
sections 7-8 green (66 `requires_network` tests, run as root). Pushed
as `e1ff796`, CI green on both jobs.

## Honest limitations (named, not hidden)

1. **Exit-code forgery from inside the check's own process is not, and
   cannot in general be, defended against by a single-process harness.**
   If the command named by `command_token` itself (e.g. a real `pytest`
   invocation) is influenced by PR-supplied code (`conftest.py`) that
   calls `os._exit(0)` unconditionally, the host-observed exit code
   genuinely is `0`. This is the same trust boundary every real CI system
   accepts. **Do not set `authority=TRUSTED` against real adversarial PR
   code until `#201-B3` closes this** (see the addendum above, finding 2).
2. **OOM is not classified at all (changed from an earlier, riskier
   heuristic** -- see the addendum above, finding 3, for why guessing was
   removed rather than kept): every signal death classifies as `FAILURE`.
   `RLIMIT_AS` enforcement itself is still proven directly (a compiled C
   fixture that overflows the limit is genuinely killed by `SIGSEGV`);
   precisely typing that as `OOM` vs. an unrelated crash needs
   independent evidence (cgroup `memory.events`) this slice does not
   implement.
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
6. **A descendant that escapes the tracked process group (e.g. via its
   own `setsid()`/double-fork, detaching from the inherited pipes before
   the leader exits) is not guaranteed to be killed, and can outlive the
   executor's own return.** Confirmed real by a third review of this
   slice: today's cleanup is process-GROUP based (`killpg` on the pgid
   the dropper's own `setsid()` established); a process that calls
   `setsid()` a SECOND time inside itself becomes a new session/group
   leader and is no longer reachable through that same pgid, regardless
   of whether the ORIGINAL leader already exited successfully. On this
   project's actual current target (GitHub Actions' ephemeral runners)
   the blast radius is bounded by the VM's own lifetime; the concern is
   real specifically for a PERSISTENT host (CT104, once online), where
   such a process could linger indefinitely.

   The correct fix is Linux PID namespaces (`unshare --pid --fork`),
   which give an UNCONDITIONAL kernel guarantee no `setsid()`/double-fork
   can escape: when a pid namespace's own PID1 dies (for any reason,
   including a plain successful exit), the kernel immediately SIGKILLs
   every other process still in that namespace, with no cleanup code
   needed for that case at all. Implementing this correctly requires
   also changing how the host learns which pid to actively kill for the
   TIMEOUT/CANCEL path: `os.getpid()`/`os.getpgrp()` called from INSIDE
   a new pid namespace return NAMESPACE-RELATIVE values (e.g. the
   dropper would see itself as pid `1`), not the host-visible value a
   host-side `kill`/`killpg` call needs -- reading `/proc/self/status`'s
   `NSpid` field (which lists the pid at every nesting level, outermost
   first) is the correct way to recover the host-visible value from
   inside the dropper. Getting this specific substitution wrong is not
   a cosmetic bug: a namespace-relative pgid like `1` reaching a
   HOST-side `killpg`/`sudo kill` call has POSIX-defined broadcast
   semantics for pgid/pid values in that range and could signal far more
   than intended.

   This was deliberately NOT implemented in this same round, unlike
   findings 1-2 above: this exact subsystem (process/pgid tracking) has
   already produced five real, CI-or-review-caught bugs across this
   slice, several only reproducible on the real GitHub Actions runner
   and not in this session's own sandbox; CT104 (the actual persistent
   host this finding matters most for) is offline and cannot be used to
   validate real pid-namespace teardown behavior before merge. Rushing
   a kernel-namespace change with a plausible-but-unverified failure
   mode that feeds an attacker-uncontrollable but easily-mistaken value
   into a privileged kill call is a worse risk than shipping this
   named, scoped gap and hardening it as a fast, deliberate follow-up.
6. **Docker-socket denial is untested, not unproven** -- this sandbox
   simply has no docker socket to attempt reaching in the first place, so
   there is nothing to adversarially prove here; the module never
   references one either way.

## What's next in the DAG

- `#201-B3`: adversarial hardening closing the exit-code-forgery gap
  (honest limitation 1 above) -- the actual precondition for ever safely
  setting `authority=TRUSTED` against real, untrusted PR code;
- `#201-C`: wire `execute_trusted_check_plan_v2`'s real output into
  `review_readiness_emission_v2`, replacing/augmenting whatever currently
  feeds `required_checks` for a target that opts in -- MUST NOT treat
  `authority=TRUSTED` as safe for real adversarial PRs before `#201-B3`;
- `#201-D`: target conformance (AgentEscala as reference; a real
  target-owned `CheckCommandInventoryV2` sourced from that target's own
  committed config, not test-local fixtures as here);
- CT104 coming online reopens acceptance criterion 3 and limitations 3/4
  above for re-verification against the real pinned runner -- not
  automatically assumed to pass just because the portable mechanism does
  here.

## Merge record

Not merged as of this checkpoint's own commit -- opened as a draft PR,
held for explicit nominal merge grant, per the same discipline as `#211`.
