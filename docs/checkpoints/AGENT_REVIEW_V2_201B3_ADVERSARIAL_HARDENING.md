# `#201-B3` — adversarial hardening of trusted checks

Refs `aiops-orchestrator#201`, third slice, child of the distribution epic
`aiops-orchestrator#199`. Base: `#201-B2` (PR #212, squash `57e0b05`).

## What `#201-B2` left open, and what this slice closes

`#201-B2` proved isolation (network denial, privilege drop, resource
limits, an inventory digest-bound to the plan) but named two gaps as
**mandatory** `#201-B3` acceptance criteria, not follow-ups:

1. **Exit-code forgery.** A PR-controlled `conftest.py`/hook could call
   `os._exit(0)` and the host would observe a genuine `0`.
2. **Process-lifetime containment.** `#201-B2` supervised a process
   *group*; a descendant that called `setsid()` or double-forked escaped
   it and could outlive the executor's return.

## Criterion 1 — the authority boundary (C1)

Closed by refusing the *class*, not by detecting the forgery after the
fact:

```
controls(subject, success_signal) => not authoritative(success_signal)
```

`app/agent_review/trusted_check_authority_v2.py` introduces
`ExecutionClassV2` (`data_only_host_tool` | `subject_code` | `unknown`).
Only `data_only_host_tool` — a host-owned binary consuming the checkout as
data, never as code — is `TRUSTED`-eligible. `AllowlistedCommandSpecV2`
gains `execution_class` (default `unknown`, fail-closed), `host_owned_config`
(necessary, not sufficient — a declaration, not a proof), and
`loads_checkout_plugins` (forces `subject_code` regardless of declaration).
`execute_trusted_check_plan_v2` classifies and refuses **before any process
exists** — a refused check never reaches a spawn at all (asserted directly
by `forbid_spawn` in the test suite).

pytest and any checkout-authored script are `subject_code` and can never
back `TRUSTED`. They remain permitted under `UNTRUSTED_ADVISORY` (refusing
`TRUSTED` is not refusing to execute), but
`promote_trusted_check_to_required_v2` (`#201-A`) already refuses that
authority structurally.

**Residual, named, not hidden:** an application module *imported by* one
item can still call `os._exit(0)` and forge that item's own result. This
is not solved — it cannot be solved by isolating the process further — and
it does not need to be: the class boundary means that forged item can
never be `TRUSTED` regardless. `coverage_authority` for `subject_code`
stays `unavailable` for the same reason: any expected-item enumeration
derived from the PR's own tree cannot prove the PR did not delete a test.

## Criterion 2 — process-lifetime containment (C3)

A real PID namespace (`unshare --pid --fork --kill-child --mount-proc
--net`) replaces process-group supervision. The kernel destroys every
process in the namespace unconditionally the moment its init dies — no
cleanup code of ours is involved once that property holds, and it survives
`setsid()`/double-fork by construction (nothing escapes a PID namespace).

Identity: `app/agent_review/trusted_check_namespace_kernel_v2.py`. Nothing
reported from inside the namespace is trusted — inside it `os.getpid()`
returns `1`, and `1` is exactly the value a privileged kill must never
receive (POSIX broadcast semantics for non-positive targets). The caller
walks its own procfs view for a descendant it spawned whose `NSpid` marks
it as another namespace's init, pins it with `pidfd_open` plus start-time
corroboration, and holds an FD on the namespace itself so the inode cannot
be recycled and misreported as a survivor. `assert_killable_host_pid_v2` is
the single choke point every signal target passes through; `killpg` and
every `-<pgid>`/textual `sudo kill <pid>` form are structurally absent
(asserted by a dedicated grep-style test across the whole family), not
merely unused.

Zero survivors is **proven**, not assumed: the pinned namespace is polled
until genuinely empty, across `SUCCESS`, `FAILURE`, `TIMEOUT` and
`CANCELLED`. The override is asymmetric on purpose — an unproven `SUCCESS`
becomes `INFRA_FAILURE`, but a `FAILURE` is never rewritten (turning a real
regression into an environmental outcome is the exact inversion issue
`#201` forbids, and it would buy nothing since `FAILURE` already blocks
readiness).

## Amendment A1 — the sudo-elevated strategy and the privileged broker

**`STOP_REASON=PID_NAMESPACE_IDENTITY_CANNOT_BE_BOUND_UNDER_SUDO_STRATEGY`**,
raised and resolved within this slice.

The strategy that actually runs in this project's own GitHub Actions CI is
sudo-elevated: the host caller is unprivileged, and the namespace's init
ends up owned by root. Under that strategy the host process cannot itself
read `/proc/<root-owned-pid>/ns/pid` — `PTRACE_MODE_READ` denies it to a
non-root, non-ptracing-capable reader — so it cannot perform the
discovery/containment-verification above directly. Confirmed empirically
under a real unprivileged-with-sudo account, not assumed: 31 of the
account's B3 tests failed with `subject_identity_invalid` before this
amendment, 0 after.

**Two rejected alternatives, and why:**

- *Relax discovery to trust a value the namespace's occupant reports about
  itself.* Rejected outright — that is precisely the class of trust this
  whole subsystem exists to remove, and the namespace-relative value `1`
  reaching a kill path is the specific hazard `assert_killable_host_pid_v2`
  exists to close.
- *Reclassify the sudo-elevated strategy as weak (advisory-only, like the
  unprivileged-userns fallback).* Rejected: sudo-elevated has full uid
  separation between the subject and the host caller (`nobody` vs. the
  real caller identity) — a materially different, and stronger, property
  than the unprivileged-userns fallback's complete absence of uid
  separation. Demoting it would silently degrade every CI run on this
  project's own runner to non-authoritative checks.

**The fix:** `app/agent_review/trusted_check_broker_v2.py`, a small,
host-owned, stdlib-only process launched via `sudo -n python -I <broker>`.
It performs *exactly* the discovery/containment/teardown code above,
because it genuinely is root — same functions, same module, different
caller identity. It answers only a closed, enumerated protocol back to the
unprivileged host: `config` → `ready` → (`cancel` |) → `result`. **No PID
of any kind crosses this protocol** (asserted directly:
`test_broker_reaches_ready_then_result_for_a_natural_success` checks no
message contains a `pid`-shaped key) — the host never needs one, because
every privileged action happens inside the broker.

**Capabilities that remain exclusively inside the broker, never crossing
to the host:** namespace discovery, `pidfd_open`/`pidfd_send_signal`,
containment verification (`/proc` enumeration against the pinned
namespace), and the actual `SIGKILL` delivery. The host only ever sees
`ready`/`result`/its own `cancel` command.

**PID reuse and raw-PID kill remain impossible** for the same reason they
are impossible in the direct path: `assert_killable_host_pid_v2` is the
same shared function, imported by both `isolated_executor_v2.py` and
`trusted_check_broker_v2.py` from `trusted_check_namespace_kernel_v2.py` —
there is exactly one implementation, not two that could drift.

**Broker crash containment (its own explicit, closed checklist item):** if
the broker dies for any reason — crash, OOM, being killed — the kernel,
not broker code, must still guarantee zero survivors. Achieved with
`PR_SET_PDEATHSIG`, applied via `preexec_fn` to the very `unshare`
invocation the broker spawns: that child watches the broker's own life via
a real kernel mechanism and receives `SIGKILL` the instant the broker's
process exits however it exits, cascading into the same `--kill-child`
chain used everywhere else in this subsystem. Proven, not asserted:
`test_broker_crash_still_leaves_zero_survivors` `SIGKILL`s the broker
mid-execution (matched by its own absolute script path, not `process.pid`
— see that test's own comment on why, including a self-inflicted failure
this session hit and fixed: matching by bare filename also matched the
*test file's own* path and killed the test runner) and proves the marker a
surviving subject would have written never appears.

**A real bug this amendment's own tests caught, not review:** the
broker's first draft read the subject's `exit_status` from
`process.returncode` of its own `unshare`/supervisor Popen handle — which
is the *supervisor's own* exit code (always `0` on success), not the
subject's. `test_broker_reports_a_real_failure_and_still_proves_containment`
failed immediately (`0 == 1`), because under root the broker path is never
even selected (root-direct is chosen first), so this was invisible until
run under the sudo-elevated account — exactly why the three-account
discipline is not optional. Fixed by reading the exit status from the
inner supervisor's own `result` record, which the broker now watches
concurrently with the outer process exit and the host's `cancel` command.

**A second, independent finding:** on Python 3.11+, `-I` implies
`safe_path=True` (PEP 706), which *removes* the script's own directory
from `sys.path` — the opposite of the assumption `-I <path>` puts that
directory at `sys.path[0]`, verified empirically before relying on it.
Sibling stdlib-only imports (`trusted_check_namespace_kernel_v2`,
`trusted_check_stream_capture_v2`, `trusted_check_supervisor_v2`) needed
by the broker restore *only* the broker's own compile-time-fixed directory
(`os.path.dirname(os.path.abspath(__file__))`) — never the checkout's cwd,
which stays off `sys.path` throughout, preserving exactly the
anti-shadowing property `-I` exists to provide.

## Bounded output and coexisting failures (C4)

`app/agent_review/trusted_check_stream_capture_v2.py`: stdout/stderr are
drained concurrently, starting at spawn time (not after wait, which
deadlocks against a subject writing more than a pipe buffer holds), capped
at 64 KiB excerpt per stream while the sha256 covers the full stream —
truncation is an explicit, verifiable fact, never a silent loss.

The internal assessment (`ExecutionAssessmentV2`) is genuinely vectorial:
a check that fails on product grounds *and* fails containment records
both, and neither erases the other. `promotion_eligibility` is derived
from the whole conjunction; any `unknown` premise is a false conjunct,
never a maybe.

## What is proven here, and what is `blocked_external: ct104_unavailable`

Proven in this session's sandbox, under three accounts (root/direct;
unprivileged-with-passwordless-sudo/broker; unprivileged-no-sudo/weak
userns): the authority boundary; the private channel; PID-namespace
containment including zero survivors under all four terminal outcomes,
`setsid()`, double-fork, and many descendants/grandchildren; the broker
path end-to-end, including its own crash containment; bounded output;
`ci_validate.sh` sections 7 and 8; byte-identical schema export; the CAEM
F0 pin.

**`blocked_external: ct104_unavailable`**, unchanged from `#201-B2`, and
newly relevant to this amendment specifically: whether CT104's own LXC
profile permits nested PID/user namespace creation at all (the single
largest environmental risk to this whole design — if forbidden there, the
probe selects nothing and every trusted check fails closed, which is
correct behaviour but means the capability itself is unavailable on CT104
until the container profile changes); whether `/proc` there is mounted
with `hidepid`; `harness_digest` against a real pinned image; isolation
under CT104's actual kernel/AppArmor/cgroup layout; zero surviving
descendants proven on the persistent host itself, including under the
sudo-elevated/broker path specifically, which is untested outside this
sandbox. CT102 is never a substitute; none of the above is inferred from
this sandbox's own result.

## Regression discipline

`#201-B2`'s pgid-report temp-file handshake is **deleted**, not hardened
again — that file existed only to name a kill target, and it cost three
independent review rounds. Its replacement proof: a dedicated test fails
if the executor ever creates or `chmod`s anything group/other-writable.
Every other `#201-B2` invariant (inventory↔digest binding, weak-userns
`TRUSTED` refusal, network denial, `sudo` refusal inside the check,
deterministic hashing, `UNTRUSTED_ADVISORY` never promoting, forged
stdout/report ignored, never running unisolated) is preserved and covered
by the unchanged or lightly-adapted `#201-B2` test set.

## What `#201-B3` still does not do

No wiring into `ReviewReadinessV2`/`readiness_decision_v2`/
`review_readiness_emission_v2` (`#201-C`, not started). No change to
`trusted_checks_v2.py`, `contracts_v2.py`, or any exported schema — frozen
throughout, verified byte-identical. No provenance sidecar for CI-sourced
`subject_code` evidence (`#201-C0`, a separate, already-scoped sub-slice —
see the `#201-B3` rev. 4 specification and issue `#217`).
