"""Tests for the #201-B3 amendment A1 privileged broker.

The broker exists to answer a single question the unprivileged host cannot
answer for itself under the sudo-elevated isolation strategy: is this
execution's kernel identity established, and is it now contained? These
tests drive the broker directly (mirroring how the supervisor tests drive
the supervisor), so they exercise the amendment's protocol without going
through the full executor.

Real subprocesses throughout -- `requires_network`, this repository's own
convention for "spawns a real subprocess" (see `test_diff_acquisition_v2.py`).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time

import pytest

from app.agent_review.trusted_check_broker_v2 import (
    BROKER_PATH_V2,
    BROKER_PROTOCOL_V2,
    _host_channel_closed_v2,
    _recv_nonblocking_v2,
)

pytestmark = pytest.mark.requires_network

NONCE = "e" * 64
SPEC_DIGEST = "f" * 64

# The pdeathsig predicate/setter this module used to define locally moved to
# `trusted_check_namespace_kernel_v2` (see that module and
# `test_trusted_check_namespace_kernel_v2.py` for the pure predicate test) --
# a review caught that the direct isolation strategy needed the identical
# parent-death binding, so the logic now lives exactly once. This module
# still re-exports it under its original name (`_pdeathsig_still_bound_to_
# broker_v2` / `_set_pdeathsig_to_broker_v2`) as a thin alias for its own
# `main()`'s call site.


# Pure, no sudo -- proves the exact kernel-level assumption the fix for the
# "read the final result after broker process exit" review finding relies
# on: SOCK_SEQPACKET data a peer already wrote before closing/exiting stays
# in the receiving socket's buffer and is still readable afterward. Without
# a guaranteed final read attempt after observing the peer's exit, a result
# sent in the narrow window between a timed-out poll and the exit check
# would be missed even though it was sitting right there.
def test_recv_nonblocking_retrieves_data_already_buffered_after_peer_closes():
    host, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        peer.send(json.dumps({
            "kind": "result", "protocol": BROKER_PROTOCOL_V2, "nonce": NONCE, "exit_status": 0,
        }).encode())
        peer.close()  # simulates the supervisor process exiting right after sending
        record = _recv_nonblocking_v2(host, timeout=0.0)
        assert record is not None
        assert record["kind"] == "result"
        assert record["exit_status"] == 0
    finally:
        host.close()


# Pure, no sudo -- proves the fix for the "tear down the broker workload
# when the host channel closes" review finding: EOF on the host-facing
# channel must be distinguishable from "nothing sent yet" (both of which
# `_recv_nonblocking_v2` collapses to `None`), and detecting it must never
# consume a real pending message (MSG_PEEK).
def test_host_channel_closed_detects_eof_without_consuming_pending_data():
    broker_side, host_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        assert _host_channel_closed_v2(broker_side) is False, "host still open, nothing sent yet"

        host_side.send(json.dumps({
            "kind": "cancel", "protocol": BROKER_PROTOCOL_V2, "nonce": NONCE,
        }).encode())
        assert _host_channel_closed_v2(broker_side) is False, "a real pending message must not read as EOF"
        record = _recv_nonblocking_v2(broker_side, timeout=0.0)
        assert record is not None and record["kind"] == "cancel", "MSG_PEEK must not have consumed it"

        host_side.close()
        assert _host_channel_closed_v2(broker_side) is True
    finally:
        broker_side.close()


def _sudo_available() -> bool:
    if shutil.which("sudo") is None:
        return False
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_sudo = pytest.mark.skipif(
    not _sudo_available(), reason="passwordless sudo is not available in this environment"
)


def _spawn_broker(cwd="/tmp"):
    """Spawn a real broker process with a fresh control channel. Returns
    (process, host_channel)."""

    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    process = subprocess.Popen(
        ["sudo", "-n", sys.executable, "-I", str(BROKER_PATH_V2)],
        cwd=cwd, stdin=child.fileno(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    child.close()
    return process, host


def _config(argv, **overrides) -> dict:
    raw = dict(
        kind="config", protocol=BROKER_PROTOCOL_V2, nonce=NONCE, spec_digest=SPEC_DIGEST,
        argv=argv, cwd="/tmp", max_memory_mb=512, max_processes=64,
    )
    raw.update(overrides)
    return raw


def _recv(channel: socket.socket, *, timeout: float = 20.0) -> dict | None:
    channel.settimeout(timeout)
    try:
        payload = channel.recv(1 << 20)
    except (socket.timeout, OSError):
        return None
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


@requires_sudo
def test_broker_reaches_ready_then_result_for_a_natural_success():
    process, host = _spawn_broker()
    try:
        host.send(json.dumps(_config([sys.executable, "-c", "raise SystemExit(0)"])).encode())
        ready = _recv(host)
        assert ready is not None and ready["kind"] == "ready" and ready["nonce"] == NONCE
        result = _recv(host)
        assert result["kind"] == "result"
        assert result["nonce"] == NONCE
        assert result["spec_digest"] == SPEC_DIGEST
        assert result["exit_status"] == 0
        assert result["contained"] is True
        assert result["setup_failed"] is False
        # The single most important negative property of this protocol:
        # no PID field of any kind ever crosses this channel.
        assert "pid" not in result
        assert not any("pid" in str(key).lower() for key in result if key not in ("kind",))
    finally:
        process.wait(timeout=10)
        host.close()


@requires_sudo
def test_broker_reliably_reports_exit_status_for_a_near_instant_subject():
    """A review caught a race: the watch loop only checked `process.poll()`
    after a TIMED-OUT read, with no further attempt to read once the
    supervisor process was observed to have exited -- so a result sent in
    the narrow window between the timed-out poll and the exit check was
    missed, and the broker fell back to reporting `exit_status: None`
    (INFRA_FAILURE downstream) for what was actually a completed,
    successful execution. A subject that exits essentially immediately
    maximises the chance of hitting that window; repeating it several times
    makes a reintroduced race very likely to surface as a flake here rather
    than only intermittently in production."""

    for _ in range(8):
        process, host = _spawn_broker()
        try:
            host.send(json.dumps(_config([sys.executable, "-c", "pass"])).encode())
            ready = _recv(host)
            assert ready is not None and ready["kind"] == "ready"
            result = _recv(host)
            assert result is not None, "the broker reported no result at all"
            assert result["exit_status"] == 0, "a completed success must never be reported as missing"
        finally:
            process.wait(timeout=10)
            host.close()


@requires_sudo
def test_broker_reports_a_real_failure_and_still_proves_containment():
    process, host = _spawn_broker()
    try:
        host.send(json.dumps(_config([sys.executable, "-c", "import sys; sys.exit(1)"])).encode())
        assert _recv(host)["kind"] == "ready"
        result = _recv(host)
        assert result["exit_status"] == 1
        assert result["contained"] is True
    finally:
        process.wait(timeout=10)
        host.close()


@requires_sudo
def test_broker_relays_setup_failed_when_the_allowlisted_binary_is_missing():
    """The supervisor's own exec-error-pipe signal must cross the broker's
    protocol too, not just the direct (non-broker) path -- otherwise a
    harness setup failure under the sudo-elevated strategy specifically
    (this project's own CI strategy) would still be misreported as an
    ordinary FAILURE."""

    process, host = _spawn_broker()
    try:
        host.send(json.dumps(
            _config(["/nonexistent/definitely-not-a-real-binary-v2", "arg"])
        ).encode())
        assert _recv(host)["kind"] == "ready"
        result = _recv(host)
        assert result["setup_failed"] is True
        assert result["exit_status"] == 72
    finally:
        process.wait(timeout=10)
        host.close()


@requires_sudo
def test_broker_cancel_command_tears_down_and_reports_containment(tmp_path):
    marker = tmp_path / "cancel-survivor"
    code = "import time\ntime.sleep(300)\n"
    process, host = _spawn_broker(cwd=str(tmp_path))
    try:
        host.send(json.dumps(_config([sys.executable, "-c", code])).encode())
        assert _recv(host)["kind"] == "ready"
        host.send(json.dumps({"kind": "cancel", "protocol": BROKER_PROTOCOL_V2, "nonce": NONCE}).encode())
        result = _recv(host, timeout=15)
        assert result["kind"] == "result"
        assert result["contained"] is True
        assert not marker.exists()
    finally:
        process.wait(timeout=10)
        host.close()


@requires_sudo
def test_broker_tears_down_and_exits_when_the_host_channel_closes(tmp_path):
    """A review caught that EOF on the broker's own channel to the host was
    collapsed into the same `None` used for 'nothing sent yet', so if the
    unprivileged host process died while a check was still running, the
    broker -- and the whole namespace under it -- would stay alive
    indefinitely as an unsupervised, privileged, orphaned process tree.
    `PR_SET_PDEATHSIG` does not help: it ties the spawned unshare child's
    life to the BROKER's life, not to the (now-dead) host's. Simulates the
    host dying by closing this test's own end without sending `cancel` or
    reading a result -- exactly what a crashed/killed host would leave
    behind."""

    marker = tmp_path / "host-death-survivor"
    code = f"import time\ntime.sleep(6)\nopen({str(marker)!r}, 'w').write('survived')\n"
    process, host = _spawn_broker(cwd=str(tmp_path))
    try:
        host.send(json.dumps(_config([sys.executable, "-c", code])).encode())
        ready = _recv(host, timeout=15)
        assert ready is not None and ready["kind"] == "ready"
        host.close()  # simulates the host process dying mid-check

        process.wait(timeout=10)
        assert process.returncode == 0, "the broker must exit cleanly, not hang forever"

        deadline = time.monotonic() + 9.0
        while time.monotonic() < deadline:
            assert not marker.exists(), "a descendant survived after the host channel closed"
            time.sleep(0.25)
    finally:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass


@requires_sudo
def test_subject_does_not_inherit_the_host_broker_control_channel():
    probe = (
        "import os, json, sys\n"
        "sockets = []\n"
        "for name in os.listdir('/proc/self/fd'):\n"
        "    try:\n"
        "        target = os.readlink('/proc/self/fd/' + name)\n"
        "    except OSError:\n"
        "        continue\n"
        "    if target.startswith('socket:'):\n"
        "        sockets.append(name)\n"
        "sys.stderr.write(json.dumps({'sockets': sockets}))\n"
    )
    process, host = _spawn_broker()
    try:
        host.send(json.dumps(_config([sys.executable, "-c", probe])).encode())
        assert _recv(host)["kind"] == "ready"
        result = _recv(host)
        assert result["exit_status"] == 0
    finally:
        process.wait(timeout=10)
        host.close()


@requires_sudo
def test_broker_refuses_a_malformed_config_and_sends_no_result():
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    process = subprocess.Popen(
        ["sudo", "-n", sys.executable, "-I", str(BROKER_PATH_V2)],
        stdin=child.fileno(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child.close()
    try:
        host.send(b"{not valid json")
        assert _recv(host, timeout=10) is None
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        host.close()


@requires_sudo
def test_broker_refuses_a_config_with_duplicate_json_keys():
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    process = subprocess.Popen(
        ["sudo", "-n", sys.executable, "-I", str(BROKER_PATH_V2)],
        stdin=child.fileno(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    child.close()
    try:
        host.send(b'{"kind": "config", "kind": "config"}')
        assert _recv(host, timeout=10) is None
        process.wait(timeout=10)
        assert process.returncode != 0
    finally:
        host.close()


@requires_sudo
def test_broker_crash_still_leaves_zero_survivors(tmp_path):
    """The property with its own stop condition in the amendment: if the
    broker dies unexpectedly, the kernel -- not broker code -- must still
    guarantee zero surviving descendants.

    This kills the broker itself (SIGKILL, mid-execution, before it has any
    chance to run its own teardown code) while its child (the unshare/
    namespace chain) is still alive, then proves the marker the subject
    would eventually write never appears: PR_SET_PDEATHSIG on the broker's
    direct child cascades into the SAME --kill-child chain used everywhere
    else in this subsystem."""

    marker = tmp_path / "broker-crash-survivor"
    code = f"import time\ntime.sleep(6)\nopen({str(marker)!r}, 'w').write('survived')\n"
    process, host = _spawn_broker(cwd=str(tmp_path))
    try:
        host.send(json.dumps(_config([sys.executable, "-c", code])).encode())
        ready = _recv(host, timeout=15)
        assert ready is not None and ready["kind"] == "ready"

        # The broker (running as root via sudo) is now blocked in its own
        # wait loop with the subject alive. Kill it HARD, with no chance to
        # run its own cleanup code. Matched by its FULL ABSOLUTE path, never
        # a bare filename -- `pkill -f trusted_check_broker_v2.py` would
        # ALSO match THIS TEST FILE's own path
        # (`test_trusted_check_broker_v2.py`, which contains that string as
        # a substring) and kill the pytest process running it. Confirmed the
        # hard way once already: this is exactly the imprecise-matching
        # failure mode this whole subsystem exists to close, now closed in
        # the test tooling too. This is TEST-ONLY convenience, never
        # production code -- the module itself never matches by name.
        broker_path_str = str(BROKER_PATH_V2)
        assert broker_path_str not in __file__, "pattern would also match this test file"
        subprocess.run(
            ["sudo", "-n", "pkill", "-9", "-f", broker_path_str],
            capture_output=True,
        )
        try:
            process.kill()
        except OSError:  # pragma: no cover - defensive
            pass
        process.wait(timeout=10)

        deadline = time.monotonic() + 9.0
        while time.monotonic() < deadline:
            assert not marker.exists(), "a descendant survived the broker's own crash"
            time.sleep(0.25)
    finally:
        host.close()
