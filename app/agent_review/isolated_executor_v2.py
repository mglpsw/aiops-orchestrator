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
  the plan itself already only ever carries a token, never argv). That
  inventory is itself checked against the plan's own commitment before
  anything resolves: ``compute_check_command_inventory_digest_v2(
  inventory) == plan.authority_suite_digest``, refusing the WHOLE plan
  (typed ``INFRA_FAILURE``, nothing attempted) on any mismatch -- closing
  a gap a review of this slice found real: an earlier version accepted
  ANY inventory a caller handed it, proving values but not provenance
  (see that function's own docstring for exactly what this does and does
  not prove);
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

**Practical consequence for any caller, stated as a rule, not a footnote:
do not set ``authority=TRUSTED`` for a check run against real,
untrusted/adversarial PR code until this gap closes (tracked as
``#201-B3``).** Everything else this slice proves -- network denial,
privilege drop, resource limits, the result channel being unwritable by
the check, the inventory being digest-bound to the plan -- holds
regardless of who wrote the code under test. This one property does not.
``#201-C`` (wiring a real result into ``ReviewReadinessV2``) MUST treat
this as an open precondition, not a solved one, before trusting this
module's ``TRUSTED``-authority output against real target-repo PRs.

## OOM is not classified -- named as a limitation, not guessed at

``RLIMIT_AS`` is real and DOES cap a check's address space -- the safety
property ("limites de CPU/RAM/tempo/processos") holds regardless of what
typed outcome a killed check gets. An earlier version of this module
guessed ``OOM`` from a signal-death signature (``SIGKILL``/``SIGSEGV``/
``SIGBUS``/``SIGABRT``), reasoning those commonly follow an ``RLIMIT_AS``
hit. A review of this slice pointed out the real, unresolved ambiguity:
the SAME signals just as commonly follow an ordinary, unrelated crash bug
(a NULL-pointer dereference from a failed ``malloc`` looks identical,
signal-wise, to any other NULL-pointer dereference), and guessing ``OOM``
risked exactly the failure mode issue #201 itself forbids in the OTHER
direction -- a real product regression silently reclassified as
environmental (`TrustedCheckOutcomeV2.OOM` never promotes; a misclassified
regression would vanish from readiness instead of blocking it).
``_classify_completed_process_v2`` now classifies EVERY signal death as
``FAILURE`` (attributable, a promotable candidate) -- the conservative,
safe default. Precisely typing a memory-exhaustion kill as ``OOM``
requires independent evidence (e.g. cgroup ``memory.events``' own
``oom_kill`` counter) this slice does not implement; that gap is named,
not silently patched over with a heuristic that could hide a regression.

## What is proven here, and what is `blocked_external: ct104_unavailable`

This module's isolation is built from portable Linux primitives -- a
network namespace (``unshare --net``), privilege drop to an unprivileged
uid (``nobody``), and ``resource.setrlimit`` (address-space/process-
count) -- and is REAL, working, and adversarially tested in whatever
Linux host actually runs this test suite (this repository's own CI, or a
development session's cloud sandbox). It is explicitly **not** CT104:

**How the namespace is created is PROBED for real, never guessed from
euid alone.** ``_select_isolation_strategy_v2`` tries, in order, real-
root-direct (``unshare --net``), passwordless-``sudo``-elevated
(``sudo -n unshare --net``), then unprivileged-user-namespace
(``unshare --user --map-root-user --net``) -- and only uses whichever
candidate a live ``<prefix> -- true`` invocation actually exits ``0``
for. Privilege drop to ``nobody`` (when the chosen candidate started
privileged) happens AFTER the namespace already exists, via a small
inline Python interpreter chained inside it (see
``_isolation_wrapped_argv_v2``'s own docstring for the full reasoning).
None of the three candidates succeeding at all means fail-closed
(``EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2``), never a fourth guess and
never running unisolated.

This exists because guessing was tried first and was wrong TWICE against
this project's own real GitHub Actions runner: assuming "root implies
`unshare --net` works" failed because that runner's job does not
actually run as root; assuming "unprivileged implies `--user
--map-root-user` works" failed because that runner's kernel/AppArmor
policy refuses unprivileged user-namespace creation outright. Both
failures are recorded in
``docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md``, not
silently patched over -- exactly the kind of environment-specific
isolation-primitive availability gap this module's own "not CT104"
caveat exists to warn about. Probing instead of guessing is what
actually closed it.

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
import shutil
import signal
import subprocess
import sys
import tempfile
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
EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2 = "isolated_executor_inventory_digest_mismatch"
EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2 = "isolated_executor_inventory_key_token_mismatch"
EXECUTOR_REASON_DESCENDANT_HUNG_V2 = "isolated_executor_descendant_hung"

_TIMEOUT_POLL_INTERVAL_SECONDS_V2 = 0.05
_COMMUNICATE_GRACE_SECONDS_V2 = 5.0
_PGID_REPORT_WAIT_SECONDS_V2 = 5.0

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
    only way an actual command comes to exist.

    Being HOST/target-repo-owned is a provenance claim this class alone
    cannot prove -- ``execute_trusted_check_plan_v2`` closes HALF of that
    gap by checking ``compute_check_command_inventory_digest_v2(inventory)
    == plan.authority_suite_digest`` before ever resolving a single token
    (see that function's own docstring for exactly what this does and
    does not prove)."""

    command_token: SafeIdentifier
    argv: tuple[str, ...]

    @model_validator(mode="after")
    def validate_argv(self) -> AllowlistedCommandSpecV2:
        if not self.argv:
            raise ValueError("argv must not be empty")
        return self


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, review_content_v2.py, and trusted_checks_v2.py.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def compute_check_command_inventory_digest_v2(
    inventory: Mapping[str, AllowlistedCommandSpecV2],
) -> str:
    """Canonical sha256 of an entire ``inventory``: ``{command_token:
    argv}`` for every entry, sorted by token, hashed the same way every
    other digest in this codebase is computed.

    Closes the gap an earlier review of this slice found real: accepting
    ``inventory`` as a bare, unverified parameter proved VALUES (each
    ``AllowlistedCommandSpecV2`` is a real, typed, non-empty-argv object)
    but not PROVENANCE -- nothing stopped a caller from resolving
    ``plan.checks``' ``command_token``s against a DIFFERENT inventory than
    the one whose digest the plan's own ``authority_suite_digest`` names.
    ``execute_trusted_check_plan_v2`` now checks this digest against
    ``plan.authority_suite_digest`` before resolving anything, mirroring
    the exact fix `#211` applied to `extract_review_content_v2`'s
    ``target_profile``/``manifest.identity.profile_hash`` binding.

    What this does NOT prove, named explicitly: that the digest recorded
    in ``authority_suite_digest`` when the plan was BUILT was itself
    computed from a legitimately host-owned inventory in the first place
    -- that is the same category of external trust ``harness_digest``
    already carries (see the module docstring's CT104 section). This
    function proves the inventory a caller hands to THIS execution is the
    SAME one the plan already committed to, not that the plan's own
    commitment was honest."""

    entries = {token: spec.model_dump(mode="json") for token, spec in inventory.items()}
    return hashlib.sha256(_canonical_json_bytes_v2(entries)).hexdigest()


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


def _isolation_prefix_candidates_v2() -> tuple[tuple[bool, tuple[str, ...]], ...]:
    """Ordered ``(drop_to_nobody_inside, prefix_argv)`` candidates, most to
    least preferred. ``drop_to_nobody_inside`` tells the dropper script
    whether the namespace was created as REAL root (a subsequent setuid
    to ``nobody`` is meaningful and will succeed) -- true for the
    root-direct and sudo-elevated candidates, false for the unprivileged-
    userns one (see ``_isolation_wrapped_argv_v2``'s own docstring for
    why that one drops nothing)."""

    if os.geteuid() == 0:
        return ((True, ("unshare", "--net", "--")),)
    candidates: list[tuple[bool, tuple[str, ...]]] = []
    if shutil.which("sudo") is not None:
        candidates.append((True, ("sudo", "-n", "unshare", "--net", "--")))
    candidates.append((False, ("unshare", "--user", "--map-root-user", "--net", "--")))
    return tuple(candidates)


def _probe_prefix_works_v2(prefix: tuple[str, ...]) -> bool:
    try:
        completed = subprocess.run([*prefix, "true"], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _select_isolation_strategy_v2() -> tuple[bool, tuple[str, ...]] | None:
    """Actually TESTS each candidate rather than guessing from euid alone
    -- this project's own GitHub Actions runner is neither real root nor
    able to create an unprivileged user namespace by default (kernel/
    AppArmor policy varies by image and changes over time), so guessing
    wrong here means silently either failing every check or, worse,
    silently running unisolated. Returns ``None`` if nothing works --
    the caller must fail closed, never fall back to running unisolated."""

    for drop_to_nobody, prefix in _isolation_prefix_candidates_v2():
        if _probe_prefix_works_v2(prefix):
            return drop_to_nobody, prefix
    return None


def _dropper_script_v2(
    *, drop_to_nobody: bool, max_memory_mb: int, max_processes: int, pgid_report_path: str
) -> str:
    """Source for a small inline Python interpreter that runs INSIDE the
    already-created isolation wrapper (after ``unshare``/``sudo`` have
    already exec'd it, not before): first ``os.setsid()`` -- making THIS
    process its own session AND process-group leader, so its pgid is
    ``os.getpid()`` from this exact point forward, unaffected by
    whatever session/pgroup ``sudo``'s own monitor-process architecture
    may have put it in beforehand (see ``_run_isolated_v2``'s own
    docstring for why that distinction is load-bearing) -- then writes
    that real pid/pgid to ``pgid_report_path`` so the HOST process (which
    cannot otherwise learn it -- ``sudo`` decouples the pid it reports
    for its own invocation from the pid of the command it actually runs)
    can read it back, THEN drops to the unprivileged ``nobody`` account
    when ``drop_to_nobody`` (only meaningful when we entered as REAL
    root), applies ``RLIMIT_AS``/``RLIMIT_NPROC``, then ``execvp``s the
    real check argv (its own ``sys.argv[1:]``). Values are validated
    ``PositiveInt`` contract fields and ``pgid_report_path`` is a host-
    generated ``tempfile`` path (never PR/plan-controlled text), so
    literal interpolation into this source string is safe."""

    memory_bytes = max_memory_mb * 1024 * 1024
    drop_lines = (
        "nobody = pwd.getpwnam('nobody')\n"
        "os.setgroups([])\n"
        "os.setgid(nobody.pw_gid)\n"
        "os.setuid(nobody.pw_uid)\n"
        if drop_to_nobody
        else ""
    )
    return (
        "import os, sys, pwd, resource\n"
        "argv = sys.argv[1:]\n"
        "try:\n"
        "    os.setsid()\n"
        "except OSError:\n"
        # Already a process-group/session leader -- POSIX forbids calling
        # setsid() again in that case (EPERM), but that failure itself
        # PROVES os.getpgrp() already equals this process's own pid
        # (that's what "already a leader" means), so there is nothing
        # left to do. Happens in the non-sudo strategies: the outer
        # subprocess.Popen(start_new_session=True) already made the
        # FIRST spawned process (unshare, or sudo itself) a session
        # leader, and plain execve() (unshare -> this dropper) preserves
        # that identity -- only sudo's own monitor-process architecture
        # forks a genuinely NEW process here, which is why setsid() is
        # attempted at all rather than assumed unnecessary.
        "    pass\n"
        "try:\n"
        f"    with open({pgid_report_path!r}, 'w') as _f:\n"
        "        _f.write(str(os.getpgrp()))\n"
        "except OSError:\n"
        # Reporting the real pgid back to the host is a BEST-EFFORT
        # cleanup aid, never a requirement for the check itself to run
        # and produce a correct verdict -- observed directly on this
        # project's real GitHub Actions runner: this write can fail with
        # a genuine PermissionError under the sudo-elevated strategy
        # (plausibly PAM/AppArmor session-scoped /tmp confinement tied
        # to the ORIGINAL unprivileged caller's identity, not a plain
        # DAC permission-bits problem an elevated root writer would
        # normally bypass) even though nothing else about running the
        # command is affected. Swallowing this and proceeding means the
        # WORST case is a degraded fallback pgid for a later best-effort
        # kill (see _read_real_pgid_v2's own fallback_pid) -- never a
        # crashed, falsely-FAILURE-classified check.
        "    pass\n"
        f"{drop_lines}"
        f"resource.setrlimit(resource.RLIMIT_AS, ({memory_bytes}, {memory_bytes}))\n"
        f"resource.setrlimit(resource.RLIMIT_NPROC, ({max_processes}, {max_processes}))\n"
        "os.execvp(argv[0], argv)\n"
    )


def _isolation_wrapped_argv_v2(
    argv: tuple[str, ...], *, max_memory_mb: int, max_processes: int, pgid_report_path: str
) -> tuple[list[str], bool] | None:
    """Returns ``(wrapped_argv, uses_sudo)``, or ``None`` if
    ``_select_isolation_strategy_v2`` finds nothing that actually works
    (the caller must fail closed, never fall back to running unisolated).
    ``uses_sudo`` tells the caller whether killing the resulting process
    tree later will ALSO need to go through ``sudo`` -- an unprivileged
    caller cannot ``kill()``/``killpg()`` a tree that ``sudo`` elevated to
    root, even though it was the one that spawned it (see
    ``_run_isolated_v2``'s own docstring).

    Wraps ``argv`` so it runs isolated: its own network stack (proven
    by this module's own adversarial test, not merely asserted -- see
    ``test_isolated_executor_v2.py``'s real-outbound-connection-attempt
    test) and, when the chosen strategy started privileged, dropped to an
    unprivileged uid before the real command ever execs. Returns ``None``
    if ``_select_isolation_strategy_v2`` finds nothing that actually
    works -- the caller must fail closed, never fall back to running
    unisolated.

    Three candidate strategies, ACTUALLY PROBED (not guessed from euid
    alone) by ``_select_isolation_strategy_v2``, most to least preferred:

    - **real root, direct**: ``unshare --net`` alone -- real root has
      ``CAP_SYS_ADMIN`` and needs no unprivileged user namespace at all.
    - **elevate via passwordless sudo, then direct**: ``sudo -n unshare
      --net`` -- for a caller that starts unprivileged but has
      passwordless sudo available (true of this project's own GitHub
      Actions runner's default account). Same no-unprivileged-userns
      property as the root-direct case once elevated.
    - **unprivileged user namespace**: ``unshare --user --map-root-user
      --net`` -- the last resort when the caller is unprivileged AND has
      no usable sudo. No privilege drop is attempted inside it: a
      ``--map-root-user`` namespace maps only the ONE calling uid to
      namespace-uid 0, ``nobody``'s real uid has no mapping to ``setuid``
      to from inside it, and there is nothing to drop from in the first
      place since the caller was never privileged.

    Privilege drop (when applicable) always happens INSIDE the already-
    created namespace via the dropper script, never before the isolation
    prefix execs -- a preexec-time drop would force the unprivileged-
    userns path even when a stronger strategy is available.

    This project's own GitHub Actions runner is neither real root NOR
    able to create an unprivileged user namespace by default (observed
    directly across this slice's own first two CI runs: ``unshare: write
    failed /proc/self/uid_map: Operation not permitted`` even after
    trying the root-direct path, because that runner's job process is
    not actually root) -- which is exactly why this is a PROBED chain of
    real candidates, not a single hardcoded mechanism assumed to exist
    everywhere.
    """

    strategy = _select_isolation_strategy_v2()
    if strategy is None:
        return None
    drop_to_nobody, prefix = strategy
    dropper = _dropper_script_v2(
        drop_to_nobody=drop_to_nobody, max_memory_mb=max_memory_mb, max_processes=max_processes,
        pgid_report_path=pgid_report_path,
    )
    uses_sudo = bool(prefix) and prefix[0] == "sudo"
    return [*prefix, sys.executable, "-c", dropper, *argv], uses_sudo


def _classify_completed_process_v2(*, returncode: int) -> tuple[TrustedCheckOutcomeV2, str | None]:
    """``returncode == 0`` -> ``SUCCESS``; anything else the process
    itself produced (a positive exit code, OR a signal death -- e.g.
    ``SIGSEGV``/``SIGBUS``/``SIGABRT``/``SIGKILL``) -> ``FAILURE``, never
    ``OOM``.

    An earlier version of this function guessed ``OOM`` from that same
    signal list, reasoning that ``RLIMIT_AS`` typically kills a process
    this way. A review of this slice pointed out the real, unresolved
    ambiguity that guess carried: a signal death from ``RLIMIT_AS``
    actually hitting its limit is, at the OS level, INDISTINGUISHABLE
    from a genuine, unrelated product crash (both commonly manifest as a
    NULL-pointer-dereference ``SIGSEGV`` after a failed allocation, or an
    unrelated one) without independent evidence (e.g. cgroup ``memory.
    events``' own ``oom_kill`` counter) this slice does not implement.
    Guessing ``OOM`` risked exactly the failure mode issue #201 itself
    names as unacceptable in the OTHER direction: "failure ambiental não
    vira regression de produto" also means, read the other way, a REAL
    product regression must never be silently absolved as environmental.
    Classifying every ambiguous signal death as the attributable, always-
    promotable-candidate ``FAILURE`` is the conservative, safe default;
    the ``RLIMIT_AS`` safety property itself (a check genuinely cannot
    exceed its configured memory) holds regardless of which typed outcome
    the death gets."""

    if returncode == 0:
        return TrustedCheckOutcomeV2.SUCCESS, None
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


def _read_real_pgid_v2(pgid_report_path: str, *, fallback_pid: int) -> int:
    """Reads the REAL pgid the dropper script's own ``os.setsid()``
    established (see ``_dropper_script_v2``'s own docstring for why the
    pgid ``subprocess.Popen`` reports for OUR direct child -- ``sudo``
    or ``unshare`` -- is not reliably the same pgid the actual isolated
    command tree ends up running in, specifically when the sudo-elevated
    strategy is used). Bounded, best-effort: if the file never appears
    (setup failed before the dropper ever got there, or this is a
    genuinely slow environment), falls back to ``fallback_pid`` -- worse
    but no worse than before this fix existed."""

    deadline = time.monotonic() + _PGID_REPORT_WAIT_SECONDS_V2
    while time.monotonic() < deadline:
        try:
            content = Path(pgid_report_path).read_text().strip()
            if content:
                return int(content)
        except (OSError, ValueError):
            pass
        time.sleep(_TIMEOUT_POLL_INTERVAL_SECONDS_V2)
    return fallback_pid


def _kill_process_tree_v2(*, real_pgid: int, uses_sudo: bool) -> None:
    """Best-effort, NEVER RAISES: kills the whole process group
    ``real_pgid``. Plain ``os.killpg`` cannot signal a group ``sudo``
    elevated to root -- an unprivileged caller lacks permission
    (observed directly: a real ``PermissionError`` from exactly this
    call in this slice's own adversarial testing) -- so when the
    isolation strategy that spawned this tree used ``sudo``, the kill
    is ALSO issued via ``sudo`` (which, run fresh for a ``kill``
    command, executes as root and can signal it). Every failure mode
    (already dead, permission denied even via sudo, sudo itself
    unavailable by the time this runs) is swallowed -- this function's
    contract is "try to clean up", never "raise and crash the
    executor" for a best-effort reap."""

    if uses_sudo:
        try:
            subprocess.run(
                ["sudo", "-n", "kill", "-9", "--", f"-{real_pgid}"],
                capture_output=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.killpg(real_pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


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
    which are typed outcomes, not exceptions).

    Tracking the REAL process group to kill later is more subtle than it
    looks, and got this wrong twice before landing here (both failures
    caught by this slice's own adversarial tests, not assumed away):
    ``subprocess.Popen``'s own ``process.pid`` for OUR direct child
    (``sudo`` or ``unshare``) is not reliably the pgid the actual
    isolated command tree ends up in once ``sudo``'s monitor-process
    architecture (or a later ``execve`` chain) reparents things, and a
    plain ``os.killpg`` cannot signal a group ``sudo`` elevated to root
    even though WE spawned it. The dropper script itself calls
    ``os.setsid()`` and reports its own real pid/pgid back to us via a
    host-controlled temp file (``_read_real_pgid_v2``); killing goes
    through ``sudo`` too when the selected strategy used it
    (``_kill_process_tree_v2``)."""

    pgid_report_fd, pgid_report_path = tempfile.mkstemp(prefix="isolated-executor-pgid-")
    os.close(pgid_report_fd)
    # tempfile.mkstemp() creates the file mode 0600, owned by THIS
    # (unprivileged, in the sudo-elevated strategy) process -- but the
    # dropper script writes to it from a DIFFERENT, elevated identity
    # (root via sudo, before its own later privilege drop). Observed
    # directly on this project's real GitHub Actions runner: that write
    # failed with a genuine PermissionError even though the writer was
    # root, most likely due to sudo's own AppArmor/session confinement
    # scoping file access by original ownership rather than a plain DAC
    # permission-bits check. Chmod'ing world-writable sidesteps whatever
    # the exact mechanism is -- this is a tiny, ephemeral scratch file
    # holding nothing but a pid, deleted immediately after use.
    os.chmod(pgid_report_path, 0o666)
    try:
        wrapped_and_sudo = _isolation_wrapped_argv_v2(
            argv, max_memory_mb=max_memory_mb, max_processes=max_processes,
            pgid_report_path=pgid_report_path,
        )
        if wrapped_and_sudo is None:
            raise IsolatedExecutorError(EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2)
        wrapped, uses_sudo = wrapped_and_sudo

        try:
            process = subprocess.Popen(
                wrapped,
                cwd=str(repo_root),
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
            real_pgid = _read_real_pgid_v2(pgid_report_path, fallback_pid=process.pid)
            _kill_process_tree_v2(real_pgid=real_pgid, uses_sudo=uses_sudo)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive only
                pass
            if cancelled:
                return TrustedCheckOutcomeV2.CANCELLED, EXECUTOR_REASON_CANCELLED_V2, None
            return TrustedCheckOutcomeV2.TIMEOUT, EXECUTOR_REASON_TIMEOUT_V2, None

        # The tracked PID has already exited (poll() returned above,
        # which reaps it internally) -- but a DESCENDANT it spawned
        # could still be alive holding the inherited stdout/stderr pipe
        # write-ends open, and a plain communicate() here would then
        # block indefinitely waiting for EOF that never comes, past this
        # check's own logical deadline. Bounded by a grace period; on
        # timeout, kill the whole process group (reaping any such
        # descendant, using the REAL pgid the dropper reported -- not
        # process.pid, which by this point may already be reaped and
        # even RECYCLED by the kernel for an unrelated process) and
        # finish reading, now safe since nothing should be left to
        # write. A second, equally bounded read guards the residual case
        # a descendant escaped the process group entirely (e.g. a
        # double-fork daemonizing itself) and the kill could not reach
        # it -- if even THAT hangs, this is a genuine environment
        # failure, not a verdict this module can safely produce.
        try:
            stdout, stderr = process.communicate(timeout=_COMMUNICATE_GRACE_SECONDS_V2)
        except subprocess.TimeoutExpired:
            real_pgid = _read_real_pgid_v2(pgid_report_path, fallback_pid=process.pid)
            _kill_process_tree_v2(real_pgid=real_pgid, uses_sudo=uses_sudo)
            try:
                stdout, stderr = process.communicate(timeout=_COMMUNICATE_GRACE_SECONDS_V2)
            except subprocess.TimeoutExpired as exc:
                raise IsolatedExecutorError(EXECUTOR_REASON_DESCENDANT_HUNG_V2) from exc
    finally:
        try:
            os.unlink(pgid_report_path)
        except OSError:
            pass

    outcome, reason = _classify_completed_process_v2(returncode=process.returncode)
    # TrustedCheckResultMaterialV2 (#201-A) requires artifact_sha256 iff
    # the outcome is SUCCESS/FAILURE.
    artifact_payload = (
        {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}
        if outcome in _RESOLVED_OUTCOMES_LOCAL_V2
        else None
    )
    return outcome, reason, artifact_payload


def _inventory_keys_match_command_tokens_v2(
    inventory: Mapping[str, AllowlistedCommandSpecV2],
) -> bool:
    return all(token == spec.command_token for token, spec in inventory.items())


def _refuse_whole_plan_v2(
    plan: TrustedCheckPlanV2, *, authority: TrustedCheckAuthorityV2, reason: str
) -> tuple[ExecutedCheckV2, ...]:
    """A structural problem with ``inventory`` itself (not any one
    check) refuses the WHOLE plan: every check gets a typed
    ``INFRA_FAILURE`` with the same ``reason``, none attempted."""

    return tuple(
        ExecutedCheckV2(
            result=_build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            ),
            diagnostic_reason=reason,
        )
        for check in plan.checks
    )


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
    serializer; the target repo owns the check inventory). This is now
    checked, not merely asserted: ``compute_check_command_inventory_
    digest_v2(inventory)`` must equal ``plan.authority_suite_digest``
    before a single ``command_token`` is resolved -- a mismatch refuses
    the WHOLE plan (every check gets a typed ``INFRA_FAILURE``, none are
    attempted) rather than silently resolving tokens against an inventory
    the plan never actually committed to. See that function's own
    docstring for exactly what this proves and does not prove.

    Every entry's own dict key must equal its
    ``AllowlistedCommandSpecV2.command_token`` -- an inventory keyed
    ``{"other_token": spec_whose_own_command_token_is_"token"}`` is
    ambiguous identity (which name does this entry actually answer to?)
    in a system whose whole point is rigorous binding, and is refused
    fail-closed before the digest is even computed, not merely tolerated
    because the digest still commits to *some* bytes either way."""

    if not _inventory_keys_match_command_tokens_v2(inventory):
        return _refuse_whole_plan_v2(
            plan, authority=authority, reason=EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2,
        )

    if compute_check_command_inventory_digest_v2(inventory) != plan.authority_suite_digest:
        return _refuse_whole_plan_v2(
            plan, authority=authority, reason=EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2,
        )

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
