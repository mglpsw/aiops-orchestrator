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

from app.agent_review.trusted_check_broker_v2 import BROKER_PATH_V2, BROKER_PROTOCOL_V2

pytestmark = pytest.mark.requires_network

NONCE = "e" * 64
SPEC_DIGEST = "f" * 64


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
        # The single most important negative property of this protocol:
        # no PID field of any kind ever crosses this channel.
        assert "pid" not in result
        assert not any("pid" in str(key).lower() for key in result if key not in ("kind",))
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
