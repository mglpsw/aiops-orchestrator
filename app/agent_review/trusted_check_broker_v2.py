"""Privileged kernel-handle broker for the sudo-elevated isolation strategy
(#201-B3 amendment A1).

Stdlib-only, launched by absolute path under ``-I``, exactly like
``trusted_check_supervisor_v2.py`` -- for the identical reason: no checkout
shadowing, no ``PYTHON*`` influence, no user site, no cwd on ``sys.path``.
This file imports its sibling stdlib-only modules
(``trusted_check_namespace_kernel_v2``, ``trusted_check_stream_capture_v2``,
``trusted_check_supervisor_v2``) by plain module name, which resolves under
``-I`` because ``sys.path[0]`` is this file's own directory.

## Why this exists

Under the sudo-elevated isolation strategy, the HOST process that calls
``execute_trusted_check_plan_v2`` is unprivileged (this is exactly why it
needs ``sudo`` at all) while the namespace's own init process ends up
owned by root. Reading ``/proc/<root-owned-pid>/ns/pid`` -- the primitive
``trusted_check_namespace_kernel_v2`` uses for identity discovery and
containment verification -- requires ``PTRACE_MODE_READ`` permission,
which an unprivileged caller does not have over a root-owned process. The
unprivileged host genuinely cannot perform host-side discovery in this
configuration; no procfs trick closes that gap for a non-root reader.

The fix is not to relax discovery (never trust a value the namespace's own
occupant reports about itself) and it is not to demote the sudo-elevated
strategy to weak/advisory-only (it has full uid separation and is the
strategy this project's own GitHub Actions runner selects -- refusing it
strong isolation would silently degrade every CI run). The fix is to run
the SAME discovery/containment/teardown code this module's sibling already
runs for the root-direct strategy, inside a small process that genuinely
IS root, and report only a closed, enumerated protocol back to the
unprivileged host -- never a PID, never a signal an unprivileged caller
could redirect.

## What the broker is not

It is not a judge of the subject and it grants no authority to the
workload: ``controls(subject, success_signal) => not authoritative(...)``
holds exactly as everywhere else in this codebase. The broker only ever
answers four questions, all host-owned and none of them about what the
subject's code means: is the execution's kernel identity established
(``ready``); please stop it (``cancel``); here is what happened
(``result``); anything else is a protocol violation and gets no answer at
all -- see ``main()``'s fail-closed exits.

## The protocol carries no PID

The host never learns a PID from this protocol, and never needs to: every
privileged action (discovery, containment verification, teardown) happens
INSIDE the broker, which already holds the real kernel identity by having
performed the discovery itself, as root, exactly like the root-direct path
does. There is no ``{"pid": ...}`` field anywhere in this file's messages,
and there is no code path that could construct a ``kill -9 <pid>`` or
``killpg`` invocation from anything the broker receives.

## Crash containment

If the broker dies for ANY reason -- crash, OOM, an operator killing it,
anything -- the kernel itself, not this file's code, must still guarantee
zero surviving descendants. This is achieved with ``PR_SET_PDEATHSIG``,
applied via ``preexec_fn`` to the very ``unshare`` invocation the broker
spawns: that child watches the broker's own life via a real kernel
mechanism, and receives ``SIGKILL`` the instant the broker's process
exits, however it exits. That triggers the SAME ``--kill-child`` cascade
already used everywhere else in this subsystem (unshare's own forked
child -- the namespace's init -- dies, and the kernel destroys the entire
namespace unconditionally the moment its init dies). No broker code needs
to run for this property to hold; that is the point.
"""

from __future__ import annotations

import functools
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# On Python 3.11+, `-I` (isolated mode) implies `safe_path=True` (PEP 706),
# which REMOVES the script's own directory from `sys.path` -- the opposite
# of the older assumption that `-I <path>` puts that directory at
# `sys.path[0]`. Verified empirically, not assumed. Restoring JUST this
# file's own, compile-time-fixed directory (never the checkout's cwd, which
# `-I` correctly keeps off `sys.path` throughout) is what lets the sibling
# stdlib-only modules below be found without reopening the shadowing gap
# `-I` exists to close: `__file__` here is this script's real absolute path
# regardless of cwd, so the only directory ever added is this one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Sibling stdlib-only modules. Two import forms, tried in order: bare (works
# now that this file's own directory is back on sys.path -- see above) and
# package-qualified (works when this module is imported normally, e.g. by
# tests reading its constants without spawning the `-I` process).
try:
    import trusted_check_namespace_kernel_v2 as kernel
    import trusted_check_stream_capture_v2 as streams
    from trusted_check_supervisor_v2 import (
        MAX_MESSAGE_BYTES_V2,
        SUPERVISOR_PATH_V2,
        SUPERVISOR_PROTOCOL_V2,
        canonical_json_bytes_v2,
        strict_json_loads_v2,
    )
except ImportError:
    from app.agent_review import trusted_check_namespace_kernel_v2 as kernel
    from app.agent_review import trusted_check_stream_capture_v2 as streams
    from app.agent_review.trusted_check_supervisor_v2 import (
        MAX_MESSAGE_BYTES_V2,
        SUPERVISOR_PATH_V2,
        SUPERVISOR_PROTOCOL_V2,
        canonical_json_bytes_v2,
        strict_json_loads_v2,
    )

BROKER_PROTOCOL_V2 = "agent-review.trusted-check-broker.v2"
BROKER_PATH_V2 = Path(__file__).resolve()

# Resolved ONCE, using ONLY a fixed, hardcoded absolute search list
# (`os.defpath`, e.g. `/bin:/usr/bin`) -- never the process's own `$PATH`
# environment variable, and never anything relative. A review caught that a
# bare "unshare" handed to `subprocess.Popen(..., cwd=config["cwd"])` (the
# checkout) is resolved via a PATH search performed AFTER this process has
# already `chdir`'d into it -- so a runner whose `$PATH` contains `.` (or
# the checkout itself shipping a same-named file) would let a PR supply the
# "system" binary run as root. `os.defpath` entries are all absolute, so
# resolution here never depends on cwd or on an environment variable the
# runner/PR could influence. `None` if not found -- handled as
# `EXIT_SPAWN_FAILED_V2`, fail-closed, never a bare-name fallback.
UNSHARE_PATH_V2 = shutil.which("unshare", path=os.defpath)

# Broker exit codes -- never the subject's. A missing `result` on the
# channel, for ANY of these, is read by the host as fail-closed.
EXIT_OK_V2 = 0
EXIT_CONFIG_INVALID_V2 = 81
EXIT_PIDFD_UNAVAILABLE_V2 = 82
EXIT_SPAWN_FAILED_V2 = 83
EXIT_IDENTITY_UNAVAILABLE_V2 = 84

_REQUIRED_CONFIG_KEYS_V2 = (
    "kind", "protocol", "nonce", "spec_digest", "argv", "cwd", "max_memory_mb", "max_processes",
)
_POLL_INTERVAL_SECONDS_V2 = 0.05
_IDENTITY_WAIT_SECONDS_V2 = 10.0
_HELLO_WAIT_SECONDS_V2 = 10.0

# Thin aliases onto the SHARED implementation in `trusted_check_namespace_
# kernel_v2` -- the root-direct/weak-userns strategy needs the identical
# parent-death-signal binding (see `isolated_executor_v2._run_isolated_
# direct_v2`), so the logic lives exactly once rather than in two copies
# that could drift. Kept under these names so this module's own tests and
# call sites below are unaffected by the move.
_pdeathsig_still_bound_to_broker_v2 = kernel.pdeathsig_still_bound_v2
_set_pdeathsig_to_broker_v2 = kernel.set_pdeathsig_to_parent_v2


def _canonical_v2(message: dict) -> bytes:
    payload = canonical_json_bytes_v2(message)
    if len(payload) > MAX_MESSAGE_BYTES_V2:  # pragma: no cover - defensive
        raise ValueError("broker message exceeds the protocol bound")
    return payload


def _validate_config_v2(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("config must be a JSON object")
    missing = [key for key in _REQUIRED_CONFIG_KEYS_V2 if key not in payload]
    if missing:
        raise ValueError(f"config missing keys: {missing}")
    if payload["kind"] != "config" or payload["protocol"] != BROKER_PROTOCOL_V2:
        raise ValueError("config kind/protocol mismatch")
    argv = payload["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("argv must be a non-empty list of strings")
    for key in ("nonce", "spec_digest", "cwd"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("max_memory_mb", "max_processes"):
        if not isinstance(payload[key], int) or isinstance(payload[key], bool) or payload[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    return payload


def _recv_nonblocking_v2(channel: socket.socket, *, timeout: float) -> dict | None:
    channel.settimeout(timeout)
    try:
        payload = channel.recv(MAX_MESSAGE_BYTES_V2)
    except (socket.timeout, OSError):
        return None
    if not payload:
        return None
    try:
        record = strict_json_loads_v2(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _host_channel_closed_v2(channel: socket.socket) -> bool:
    """Non-destructive, zero-timeout check for whether the HOST end of this
    socket has closed -- distinct from "nothing sent yet", which
    ``_recv_nonblocking_v2`` (used for the same channel to check for a
    `cancel` command) cannot tell apart from a genuine EOF, since both
    collapse to ``None``. If the unprivileged host process dies while a
    check is still running, this broker's own ``channel`` sees EOF -- but
    nobody is left to ever send `cancel` or read a `result`, and
    ``PR_SET_PDEATHSIG`` does not help here: it ties the SPAWNED unshare
    child's life to THIS broker's life, not to the (now-dead) host's.
    Without this check the broker, and the whole namespace under it, would
    stay alive indefinitely as an unsupervised, privileged, orphaned
    process tree. ``MSG_PEEK`` does not consume the datagram, so this can
    never steal a real message out from under the loop's own reads."""

    channel.settimeout(0.0)
    try:
        payload = channel.recv(1, socket.MSG_PEEK)
    except (BlockingIOError, socket.timeout, OSError):
        return False
    return payload == b""


def _bound_v2(record: dict | None, *, kind: str, nonce: str) -> bool:
    return (
        record is not None
        and record.get("kind") == kind
        and record.get("protocol") in (BROKER_PROTOCOL_V2, SUPERVISOR_PROTOCOL_V2)
        and record.get("nonce") == nonce
    )


def main() -> int:
    channel = socket.socket(fileno=0)
    try:
        raw = channel.recv(MAX_MESSAGE_BYTES_V2)
        config = _validate_config_v2(strict_json_loads_v2(raw.decode("utf-8")))
    except Exception:
        return EXIT_CONFIG_INVALID_V2

    if not kernel.PIDFD_AVAILABLE_V2:
        # No silent fallback to a raw-PID signal path: containment we cannot
        # prove is not containment.
        return EXIT_PIDFD_UNAVAILABLE_V2

    if UNSHARE_PATH_V2 is None:
        # No bare-name fallback: resolution failing closed here is strictly
        # better than falling through to a PATH search performed after this
        # process's cwd is the checkout -- see UNSHARE_PATH_V2's own
        # docstring.
        return EXIT_SPAWN_FAILED_V2

    nonce = config["nonce"]
    inner_host, inner_child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    identity: kernel.ExecutionIdentityV2 | None = None
    process = None
    direct_child_pidfd: int | None = None
    broker_pid = os.getpid()
    try:
        try:
            process = subprocess.Popen(
                [UNSHARE_PATH_V2, *kernel.NAMESPACE_FLAGS_V2, "--", sys.executable, "-I", str(SUPERVISOR_PATH_V2)],
                cwd=config["cwd"],
                stdin=inner_child.fileno(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                # The single line that makes broker-crash containment a
                # kernel guarantee rather than a hope: see the module
                # docstring's "Crash containment" section, and
                # `_set_pdeathsig_to_broker_v2`'s own docstring for the
                # fork-to-prctl race this closes.
                preexec_fn=functools.partial(_set_pdeathsig_to_broker_v2, broker_pid),
            )
        except OSError:
            return EXIT_SPAWN_FAILED_V2
        finally:
            inner_child.close()

        try:
            direct_child_pidfd = os.pidfd_open(process.pid, 0)
        except OSError:  # pragma: no cover - defensive: the process was just spawned by us
            direct_child_pidfd = None

        drain_state, drain_threads = streams.start_drain_v2(process)

        try:
            inner_host.send(_canonical_v2({
                "kind": "config",
                "protocol": SUPERVISOR_PROTOCOL_V2,
                "nonce": nonce,
                "spec_digest": config["spec_digest"],
                "argv": config["argv"],
                "cwd": config["cwd"],
                "max_memory_mb": config["max_memory_mb"],
                "max_processes": config["max_processes"],
                # The broker is root; dropping to an unprivileged uid before
                # the subject execs is always correct here, exactly as it is
                # for the root-direct strategy.
                "drop_to_nobody": True,
            }))
        except OSError:
            return EXIT_SPAWN_FAILED_V2

        hello = _recv_nonblocking_v2(inner_host, timeout=_HELLO_WAIT_SECONDS_V2)
        if not _bound_v2(hello, kind="hello", nonce=nonce):
            kernel.tear_down_execution_v2(
                identity=None, direct_child_pid=process.pid, direct_child_pidfd=direct_child_pidfd,
            )
            return EXIT_IDENTITY_UNAVAILABLE_V2

        # The inner supervisor is now BLOCKED waiting for our `ready`, so its
        # namespace leader is guaranteed alive for the whole of discovery --
        # the identity cannot be lost to a fast-finishing subject. The broker
        # IS root, so -- unlike the unprivileged host in the non-broker
        # path -- this is exactly the root-direct discovery code, unmodified.
        identity = kernel.discover_namespace_leader_v2(
            process.pid, deadline=time.monotonic() + _IDENTITY_WAIT_SECONDS_V2
        )
        if identity is not None and not kernel.is_descendant_of_v2(identity.host_pid, process.pid):
            identity.close()  # pragma: no cover - defensive
            identity = None  # pragma: no cover - defensive

        if identity is None:
            kernel.tear_down_execution_v2(
                identity=None, direct_child_pid=process.pid, direct_child_pidfd=direct_child_pidfd,
            )
            return EXIT_IDENTITY_UNAVAILABLE_V2

        try:
            inner_host.send(_canonical_v2({
                "kind": "ready", "protocol": SUPERVISOR_PROTOCOL_V2, "nonce": nonce,
            }))
        except OSError:
            kernel.tear_down_execution_v2(
                identity=identity, direct_child_pid=process.pid, direct_child_pidfd=direct_child_pidfd,
            )
            return EXIT_SPAWN_FAILED_V2

        try:
            channel.send(_canonical_v2({
                "kind": "ready", "protocol": BROKER_PROTOCOL_V2, "nonce": nonce,
            }))
        except OSError:
            # Host is gone. Tear down anyway -- crash containment does not
            # depend on the host, but there is no reason to wait for a
            # natural exit when nobody will read the result.
            kernel.tear_down_execution_v2(
                identity=identity, direct_child_pid=process.pid, direct_child_pidfd=direct_child_pidfd,
            )
            return EXIT_SPAWN_FAILED_V2

        # Watch THREE things concurrently, each with a short non-blocking
        # poll: the outer unshare/supervisor process's own exit (which only
        # tells us the SUPERVISOR finished, not what the subject did --
        # that fact travels in the supervisor's own `result` record below);
        # that inner `result` record itself, arriving on the channel to the
        # inner supervisor; and a `cancel` from the host on the OUTER
        # channel. The subject's real exit_status/signaled always come from
        # the inner record, never from `process.returncode` -- conflating
        # the two was a real bug caught by this file's own tests: the
        # supervisor exits 0 (its OWN success) regardless of what exit
        # status it is REPORTING for the subject.
        cancelled = False
        inner_result: dict | None = None
        while True:
            inner_result = _recv_nonblocking_v2(inner_host, timeout=_POLL_INTERVAL_SECONDS_V2)
            if inner_result is not None:
                break
            if process.poll() is not None:
                # The supervisor process exited, but SOCK_SEQPACKET data it
                # already wrote before exiting can still be sitting in the
                # channel's receive buffer -- the peer exiting does not
                # discard it. Without this second attempt, a result sent in
                # the narrow window between the timed-out read above and
                # this exit check is missed entirely, and the broker reports
                # a flaky INFRA_FAILURE for what was actually a completed
                # execution. Zero timeout: if the data is already buffered
                # this returns immediately, and if it never arrives at all
                # there is nothing left to wait for -- the process is gone.
                inner_result = _recv_nonblocking_v2(inner_host, timeout=0.0)
                break
            control = _recv_nonblocking_v2(channel, timeout=0.0)
            if control is not None and _bound_v2(control, kind="cancel", nonce=nonce):
                cancelled = True
                break
            if _host_channel_closed_v2(channel):
                # The host is gone, not merely quiet -- treat exactly like
                # an explicit cancel so the SAME teardown path applies; a
                # subsequent `channel.send` below will simply fail (already
                # caught) since nobody is listening.
                cancelled = True
                break

        if cancelled:
            kernel.tear_down_execution_v2(
                identity=identity, direct_child_pid=process.pid, direct_child_pidfd=direct_child_pidfd,
            )
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass

        stdout_capture, stderr_capture = streams.finish_drain_v2(drain_state, drain_threads)
        contained = kernel.verify_containment_v2(identity)

        if cancelled or not _bound_v2(inner_result, kind="result", nonce=nonce):
            # Cancelled/timed-out (host already knows why locally, mirroring
            # the non-broker path), or the supervisor died/misbehaved before
            # producing a valid record -- either way the subject's exit
            # status is not something we can honestly report, and the host
            # does not need it for these outcomes.
            exit_status = None
            signaled = None
            setup_failed = False
        else:
            exit_status = inner_result.get("exit_status")
            signaled = inner_result.get("signaled")
            # Relay the supervisor's own exec-error-pipe signal through --
            # without this, a harness setup failure (missing allowlisted
            # binary, privilege-drop/rlimit failure) inside the namespace
            # would reach the host as an ordinary FAILURE instead of
            # INFRA_FAILURE, exactly the misreporting the host-side check
            # for this field exists to prevent.
            setup_failed = bool(inner_result.get("setup_failed"))

        try:
            channel.send(_canonical_v2({
                "kind": "result",
                "protocol": BROKER_PROTOCOL_V2,
                "nonce": nonce,
                "spec_digest": config["spec_digest"],
                "exit_status": exit_status,
                "signaled": signaled,
                "setup_failed": setup_failed,
                "contained": contained,
                "stdout_bytes": stdout_capture.total_bytes,
                "stdout_sha256": stdout_capture.sha256,
                "stdout_truncated": stdout_capture.truncated,
                "stderr_bytes": stderr_capture.total_bytes,
                "stderr_sha256": stderr_capture.sha256,
                "stderr_truncated": stderr_capture.truncated,
            }))
        except OSError:
            pass
        return EXIT_OK_V2
    finally:
        if identity is not None:
            identity.close()
        if direct_child_pidfd is not None:
            os.close(direct_child_pidfd)
        inner_host.close()


if __name__ == "__main__":
    sys.exit(main())
