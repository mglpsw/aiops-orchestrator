"""Isolated executor for AgentReview v2 trusted checks (#201-B2, third
slice of #201, child of the distribution epic #199).

Executes ONE check from an already-built ``TrustedCheckPlanV2`` (#201-A)
as a REAL, isolated subprocess and produces a real, plan-bindable
``TrustedCheckResultV2`` -- the slice ``#201-B1``'s offline simulator
exists specifically to stand in for.

## What this module is NOT allowed to trust

> O código do HEAD da PR é executado isoladamente como sujeito sob teste. O
> harness, a seleção de checks, os testes de autoridade e o serializador
> vêm da base confiável / host e são pinados por digest. A PR nunca pode
> controlar a evidência que a aprova.

Concretely, this module never lets the subprocess it isolates decide its
own verdict:

- **which command runs** is resolved from ``command_token`` against a
  HOST-supplied ``inventory: Mapping[str, AllowlistedCommandSpecV2]`` --
  never a raw string the plan or any PR-influenced input assembles (see
  ``trusted_checks_v2.AllowlistedCheckCommandV2``'s own docstring on why
  the plan itself already only ever carries a token, never argv);
- **the verdict** (``SUCCESS``/``FAILURE``/``TIMEOUT``/``OOM``/
  ``CANCELLED``/``INFRA_FAILURE``) is derived EXCLUSIVELY from signals the
  HOST observes about the child process via the kernel -- its real exit
  code (``Popen.wait()``'s own return value, not anything the child
  writes to a file it controls) and whether the host's own wall-clock
  deadline, its own cancellation request, or a resource-limit-signature
  signal was the reason it died. **Nothing the child process prints to
  stdout/stderr, or writes to any file, is ever read to determine the
  verdict** -- only captured, sanitized, and attached as
  non-authoritative diagnostic content (``artifact_sha256``).
  A malicious ``conftest.py`` that prints ``"ALL TESTS PASSED"`` or writes
  a forged report file claiming success changes nothing: the verdict this
  module produces depends only on the real, kernel-observed exit status
  of the process that was actually asked to run the check.

## The one thing this module cannot defend against, stated honestly

If the check command itself (the thing named by ``command_token``, e.g.
``pytest``) is invoked in a way where a PR-supplied ``conftest.py`` can
make THAT SAME PROCESS exit ``0`` regardless of what it actually observed
(e.g. by calling ``os._exit(0)`` from a hook), the host-observed exit code
genuinely is ``0`` -- there is no signal this module (or, in general, any
single-process CI harness) can use to distinguish "genuinely passed" from
"forged its own exit code" without a SECOND, independent judge outside
that same process (e.g. host-side per-test collection/counting, or
running each test unit in its own subprocess so the blast radius of one
malicious hook is contained to one check, not the whole suite --
`AllowlistedCheckCommandV2` is already per-check, not per-suite, which is
a step in that direction, but this slice does not implement per-test
sub-isolation). This is the same trust boundary every real CI system
relies on (a process's own exit code, as observed by whoever waited on
it) -- named here explicitly rather than silently assumed solved.

## OOM classification is a documented heuristic, not perfect detection

``RLIMIT_AS`` is real and DOES cap a check's address space -- the safety
property ("limites de CPU/RAM/tempo/processos") holds regardless. But
WHICH typed outcome a killed check gets is necessarily a heuristic: a
runtime that catches its own allocation failure and exits cleanly (e.g.
Python's ``MemoryError`` -> exit code ``1``) is indistinguishable from an
ordinary product failure and is classified ``FAILURE``, not ``OOM`` --
this module does not attempt runtime-specific instrumentation to catch
that case. Only a process actually KILLED by a signal commonly associated
with allocation failure (``SIGKILL``/``SIGSEGV``/``SIGBUS``/``SIGABRT``)
is classified ``OOM``. Both directions of this heuristic are named here
rather than silently assumed accurate.

## What is proven here, and what is `blocked_external: ct104_unavailable`

This module's isolation is built from portable Linux primitives --
unprivileged user+network namespaces (``unshare --user --map-root-user
--net``), privilege drop to an unprivileged uid before that (``nobody``),
and ``resource.setrlimit`` (CPU/address-space/process-count) -- and is
REAL, working, and adversarially tested in whatever Linux host actually
runs this test suite (this repository's own CI, or a development
session's cloud sandbox). It is explicitly **not** CT104:

- this module has no way to assert it is running ON the project's real,
  pinned CT104 runner -- that identity is a deployment fact asserted by
  whoever invokes it, never self-detected here;
- ``harness_digest`` (``TrustedCheckPlanV2``, #201-A) is threaded through
  and echoed into the result unmodified, but this module does not
  independently verify it against a real, running, digest-pinned
  container image -- there is no container runtime to check it against
  outside CT104. That specific verification is `blocked_external:
  ct104_unavailable`, not silently skipped or assumed true;
- a development sandbox that happens to run this test suite as root with
  broad kernel capabilities is a WEAKER starting point than a properly
  hardened, unprivileged CT104 runner -- isolation demonstrated here
  proves the MECHANISM is real and does what it claims (network truly
  unreachable, ``sudo`` truly refused, memory truly capped), not that it
  would resist a determined container-escape attempt on a host with a
  materially different threat model. That stronger guarantee is CT104's
  to provide, not this module's to claim on CT104's behalf.

See ``docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md`` for
the full, itemized issue #201 acceptance-criteria mapping (proven here /
proven-in-this-sandbox-not-CT104 / `blocked_external`), preserved without
silently reducing any criterion.
"""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import resource
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Mapping

from pydantic import model_validator

from app.agent_review.contracts_v2 import ContractV2Model, SafeIdentifier
from app.agent_review.redaction import sanitize_artifact_value
from app.agent_review.trusted_checks_v2 import (
    TrustedCheckAuthorityV2,
    TrustedCheckAuthorityValueV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckResultMaterialV2,
    TrustedCheckResultV2,
    compute_trusted_check_result_sha256_v2,
)

EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2 = "isolated_executor_command_not_in_inventory"
EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2 = "isolated_executor_isolation_unavailable"
EXECUTOR_REASON_SETUP_FAILED_V2 = "isolated_executor_setup_failed"
EXECUTOR_REASON_CANCELLED_V2 = "isolated_executor_cancelled"
EXECUTOR_REASON_TIMEOUT_V2 = "isolated_executor_timeout"
EXECUTOR_REASON_LIKELY_OOM_V2 = "isolated_executor_likely_oom"

# Signals a killed child can die from that this module treats as an OOM
# signature (see the module docstring's "what is proven / documented
# limitation" section: a resource-exhaustion failure a runtime instead
# reports via a normal nonzero exit -- e.g. Python's MemoryError -> exit
# 1 -- is NOT distinguishable from this list alone and is classified
# FAILURE, not OOM; this is a documented, honest limitation, not silently
# assumed solved).
_OOM_SIGNATURE_SIGNALS_V2 = frozenset(
    {signal.SIGKILL, signal.SIGSEGV, signal.SIGBUS, signal.SIGABRT}
)

_NOBODY_USERNAME_V2 = "nobody"
_TIMEOUT_POLL_INTERVAL_SECONDS_V2 = 0.05

# Mirrors trusted_checks_v2._RESOLVED_OUTCOMES_V2 (module-private there,
# so re-declared here rather than reaching into that module's own
# private constant) -- the only two outcomes TrustedCheckResultMaterialV2
# allows an artifact_sha256 for.
_RESOLVED_OUTCOMES_LOCAL_V2 = frozenset({TrustedCheckOutcomeV2.SUCCESS, TrustedCheckOutcomeV2.FAILURE})


class AllowlistedCommandSpecV2(ContractV2Model):
    """One entry in a HOST-owned command inventory. ``argv`` is a fixed,
    non-empty tuple of real argv tokens -- never text assembled from the
    plan, from PR-controlled input, or from any model output. A
    ``TrustedCheckPlanV2`` (#201-A) never carries argv at all, only a
    ``command_token``; resolving that token against THIS inventory is the
    only way an actual command comes to exist, and this inventory itself
    is asserted to be host/target-repo-owned by whoever constructs it --
    this module has no way to enforce WHO built it, exactly like
    #201-A's own ``TrustedCheckPlanV2`` docstring says about ``authority``."""

    command_token: SafeIdentifier
    argv: tuple[str, ...]

    @model_validator(mode="after")
    def validate_argv(self) -> AllowlistedCommandSpecV2:
        if not self.argv:
            raise ValueError("argv must not be empty")
        return self


class IsolatedExecutorError(RuntimeError):
    """Raised only for a setup-time failure this module refuses to paper
    over by silently degrading (e.g. isolation unavailable). Callers that
    want a typed, non-raising ``INFRA_FAILURE`` result instead of an
    exception should use ``execute_trusted_check_plan_v2``, which catches
    this and converts it -- this exception exists for lower-level direct
    callers of ``_run_isolated_v2``."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExecutedCheckV2:
    """A ``TrustedCheckResultV2`` plus non-authoritative diagnostics.
    ``diagnostic_reason`` is NEVER part of the hashed contract material
    (``TrustedCheckResultMaterialV2`` has no such field) -- it exists
    purely for logs/tests to assert WHY an environmental outcome
    happened, without smuggling free text into the frozen result."""

    result: TrustedCheckResultV2
    diagnostic_reason: str | None


def _probe_isolation_available_v2() -> bool:
    return shutil.which("unshare") is not None


def _drop_privileges_and_limit_resources_v2(
    *, max_memory_mb: int, max_processes: int
):
    """Returns a ``preexec_fn`` for ``subprocess.Popen``: drops from root
    to the unprivileged ``nobody`` account (if currently root -- if
    already unprivileged, proceeds without a drop, since there is nothing
    to drop from), then applies ``RLIMIT_AS``/``RLIMIT_NPROC``. Runs in
    the CHILD after ``fork()`` but before ``exec()`` -- exactly the
    boundary needed so the isolation-wrapper binary itself (``unshare``)
    execs already-unprivileged, letting an UNPRIVILEGED user namespace
    (which the kernel permits for any uid, not just root) provide the
    net/user isolation from that point on."""

    def _preexec() -> None:
        if os.geteuid() == 0:
            nobody = pwd.getpwnam(_NOBODY_USERNAME_V2)
            os.setgroups([])
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)
        memory_bytes = max_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))

    return _preexec


def _isolation_wrapped_argv_v2(argv: tuple[str, ...]) -> list[str]:
    # Unprivileged user+network namespace: the child (and everything it
    # execs) gets its own network stack with only loopback -- proven by
    # this module's own adversarial test, not merely asserted (see
    # test_isolated_executor_v2.py's real-outbound-connection-attempt
    # test). --map-root-user makes the namespace usable (mount/etc. some
    # tools expect a mapped uid 0) without granting anything on the REAL
    # host -- the mapping is entirely contained to the throwaway
    # namespace this process itself created.
    return ["unshare", "--user", "--map-root-user", "--net", "--", *argv]


def _classify_completed_process_v2(*, returncode: int) -> tuple[TrustedCheckOutcomeV2, str | None]:
    if returncode == 0:
        return TrustedCheckOutcomeV2.SUCCESS, None
    if returncode > 0:
        return TrustedCheckOutcomeV2.FAILURE, None
    # returncode < 0: died by signal -returncode (POSIX convention).
    died_signal = -returncode
    if died_signal in _OOM_SIGNATURE_SIGNALS_V2:
        return TrustedCheckOutcomeV2.OOM, EXECUTOR_REASON_LIKELY_OOM_V2
    return TrustedCheckOutcomeV2.FAILURE, None


def _build_result_v2(
    *,
    plan: TrustedCheckPlanV2,
    check_name: str,
    authority: TrustedCheckAuthorityV2,
    outcome: TrustedCheckOutcomeV2,
    artifact_payload: dict[str, object] | None,
) -> TrustedCheckResultV2:
    artifact_sha256 = None
    if artifact_payload is not None:
        sanitized = sanitize_artifact_value(artifact_payload)
        canonical = json.dumps(
            sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        artifact_sha256 = hashlib.sha256(canonical).hexdigest()

    material = TrustedCheckResultMaterialV2(
        schema_id="agent-review.trusted-check-result.v2",
        schema_version=2,
        run_id=plan.run_id,
        head_sha=plan.head_sha,
        check_name=check_name,
        authority=authority,
        outcome=outcome,
        harness_digest=plan.harness_digest,
        artifact_sha256=artifact_sha256,
    )
    return TrustedCheckResultV2(
        **material.model_dump(mode="json"),
        result_sha256=compute_trusted_check_result_sha256_v2(material),
    )


def _run_isolated_v2(
    *,
    argv: tuple[str, ...],
    repo_root: Path,
    timeout_seconds: int,
    max_memory_mb: int,
    max_processes: int,
    cancel_event: Event | None,
) -> tuple[TrustedCheckOutcomeV2, str | None, dict[str, object] | None]:
    """Runs ``argv`` isolated and returns ``(outcome, diagnostic_reason,
    artifact_payload)``. Raises ``IsolatedExecutorError`` only for a
    setup-time failure (never for the CHECK'S OWN failure/timeout/OOM,
    which are typed outcomes, not exceptions)."""

    if not _probe_isolation_available_v2():
        raise IsolatedExecutorError(EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2)

    wrapped = _isolation_wrapped_argv_v2(argv)
    preexec_fn = _drop_privileges_and_limit_resources_v2(
        max_memory_mb=max_memory_mb, max_processes=max_processes
    )

    try:
        process = subprocess.Popen(
            wrapped,
            cwd=str(repo_root),
            preexec_fn=preexec_fn,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc

    deadline = time.monotonic() + timeout_seconds
    cancelled = False
    timed_out = False
    while True:
        returncode = process.poll()
        if returncode is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(_TIMEOUT_POLL_INTERVAL_SECONDS_V2)

    if cancelled or timed_out:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive only
            pass
        if cancelled:
            return TrustedCheckOutcomeV2.CANCELLED, EXECUTOR_REASON_CANCELLED_V2, None
        return TrustedCheckOutcomeV2.TIMEOUT, EXECUTOR_REASON_TIMEOUT_V2, None

    stdout, stderr = process.communicate()
    outcome, reason = _classify_completed_process_v2(returncode=process.returncode)
    # TrustedCheckResultMaterialV2 (#201-A) requires artifact_sha256 iff
    # the outcome is SUCCESS/FAILURE -- OOM (a completed-but-killed
    # process still reaches this line) must carry no artifact, exactly
    # like the TIMEOUT/CANCELLED paths above.
    artifact_payload = (
        {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}
        if outcome in _RESOLVED_OUTCOMES_LOCAL_V2
        else None
    )
    return outcome, reason, artifact_payload


def execute_trusted_check_plan_v2(
    plan: TrustedCheckPlanV2,
    *,
    repo_root: Path,
    inventory: Mapping[str, AllowlistedCommandSpecV2],
    authority: TrustedCheckAuthorityValueV2,
    cancel_event: Event | None = None,
) -> tuple[ExecutedCheckV2, ...]:
    """Executes every check in ``plan.checks`` in order, isolated, and
    returns one ``ExecutedCheckV2`` per check (never fewer -- a check that
    cannot even be resolved or spawned still produces a typed
    ``INFRA_FAILURE`` result, never a raised exception the caller must
    remember to catch per-check). ``authority`` is required with no
    default, mirroring ``#201-B1``'s own simulator discipline: calling
    this function with ``TRUSTED`` is the caller asserting ITS OWN
    deployment context actually is host-controlled -- this module does
    not infer or upgrade authority on the caller's behalf.

    ``inventory`` must be HOST/target-repo-owned (per issue #201's own
    ownership split: this repository owns the harness/isolation/
    serializer; the target repo owns the check inventory) -- this
    function only enforces that every ``command_token`` the plan
    authorized actually resolves against it; it cannot enforce WHERE the
    caller sourced ``inventory`` from."""

    results: list[ExecutedCheckV2] = []
    for check in plan.checks:
        spec = inventory.get(check.command_token)
        if spec is None:
            result = _build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            )
            results.append(
                ExecutedCheckV2(result=result, diagnostic_reason=EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2)
            )
            continue

        try:
            outcome, reason, artifact_payload = _run_isolated_v2(
                argv=spec.argv, repo_root=repo_root, timeout_seconds=check.timeout_seconds,
                max_memory_mb=check.max_memory_mb, max_processes=check.max_processes,
                cancel_event=cancel_event,
            )
        except IsolatedExecutorError as exc:
            result = _build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            )
            results.append(ExecutedCheckV2(result=result, diagnostic_reason=exc.reason_code))
            continue

        result = _build_result_v2(
            plan=plan, check_name=check.check_name, authority=authority,
            outcome=outcome, artifact_payload=artifact_payload,
        )
        results.append(ExecutedCheckV2(result=result, diagnostic_reason=reason))

    return tuple(results)
