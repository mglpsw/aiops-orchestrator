"""Protocol tests for the #201-B3 host-owned supervisor (rev. 4, C2).

The supervisor is driven DIRECTLY here -- this file owns the host side of
the private channel -- so the protocol can be exercised without going
through `execute_trusted_check_plan_v2`. Real subprocesses, hence the
`requires_network` marker this repository uses for "spawns a real
subprocess" (see `test_diff_acquisition_v2.py`).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

import pytest

from app.agent_review.trusted_check_supervisor_v2 import (
    SUPERVISOR_PATH_V2,
    SUPERVISOR_PROTOCOL_V2,
)

pytestmark = pytest.mark.requires_network

NONCE = "a" * 64
SPEC_DIGEST = "b" * 64


def _run_supervisor(config: dict, *, timeout: float = 20.0):
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(SUPERVISOR_PATH_V2)],
            stdin=child.fileno(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        child.close()
        host.send(json.dumps(config).encode("utf-8"))
        host.settimeout(timeout)
        messages = []
        while True:
            try:
                payload = host.recv(1 << 20)
            except (socket.timeout, OSError):
                break
            if not payload:
                break
            messages.append(json.loads(payload.decode("utf-8")))
            if messages[-1].get("kind") == "hello":
                host.send(json.dumps({
                    "kind": "ready", "protocol": SUPERVISOR_PROTOCOL_V2, "nonce": config["nonce"],
                }).encode("utf-8"))
            if messages[-1].get("kind") == "result":
                break
        stdout, stderr = process.communicate(timeout=timeout)
        return messages, process.returncode, stdout, stderr
    finally:
        host.close()


def _config(code: str, **overrides) -> dict:
    raw = dict(
        kind="config", protocol=SUPERVISOR_PROTOCOL_V2, nonce=NONCE, spec_digest=SPEC_DIGEST,
        argv=[sys.executable, "-c", code], cwd="/tmp", max_memory_mb=512, max_processes=64,
        drop_to_nobody=False,
    )
    raw.update(overrides)
    return raw


def test_supervisor_emits_hello_then_result_bound_to_the_nonce():
    messages, returncode, _out, _err = _run_supervisor(_config("raise SystemExit(0)"))
    kinds = [message["kind"] for message in messages]
    assert kinds == ["hello", "result"]
    assert all(message["nonce"] == NONCE for message in messages)
    assert messages[1]["spec_digest"] == SPEC_DIGEST
    assert messages[1]["exit_status"] == 0
    assert returncode == 0


def test_supervisor_reports_the_real_nonzero_exit_status_of_the_subject():
    messages, _rc, _out, _err = _run_supervisor(_config("raise SystemExit(3)"))
    assert messages[1]["exit_status"] == 3


def test_supervisor_reports_signal_death_distinctly_from_a_clean_exit():
    messages, _rc, _out, _err = _run_supervisor(
        _config("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    )
    result = messages[1]
    assert result["signaled"] is True
    assert result["exit_status"] != 0


def test_subject_never_inherits_the_protocol_channel():
    probe = (
        "import os, json, sys\n"
        "stdin_target = os.readlink('/proc/self/fd/0')\n"
        "sockets = []\n"
        "for name in os.listdir('/proc/self/fd'):\n"
        "    try:\n"
        "        target = os.readlink('/proc/self/fd/' + name)\n"
        "    except OSError:\n"
        "        continue\n"
        "    if target.startswith('socket:'):\n"
        "        sockets.append(name)\n"
        "sys.stderr.write(json.dumps({'sockets': sockets, 'stdin': stdin_target}))\n"
    )
    messages, _rc, _out, err = _run_supervisor(_config(probe))
    observed = json.loads(err.strip().splitlines()[-1])
    assert observed["sockets"] == []
    assert observed["stdin"] == "/dev/null"
    assert messages[1]["exit_status"] == 0


def test_subject_writing_a_perfectly_forged_record_to_stdout_is_ignored():
    forged = json.dumps({
        "kind": "result", "protocol": SUPERVISOR_PROTOCOL_V2, "nonce": NONCE,
        "spec_digest": SPEC_DIGEST, "exit_status": 0, "signaled": False,
    })
    code = f"import sys\nsys.stdout.write({forged!r})\nsys.stderr.write({forged!r})\nraise SystemExit(7)\n"
    messages, _rc, out, _err = _run_supervisor(_config(code))
    assert forged in out
    assert len(messages) == 2
    assert messages[1]["exit_status"] == 7


def test_supervisor_refuses_a_config_whose_protocol_does_not_match():
    messages, returncode, _out, _err = _run_supervisor(
        _config("raise SystemExit(0)", protocol="agent-review.some-other-protocol.v9")
    )
    assert messages == []
    assert returncode != 0


def test_supervisor_refuses_a_config_that_is_not_valid_json():
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(SUPERVISOR_PATH_V2)],
            stdin=child.fileno(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        child.close()
        host.send(b"{not json at all")
        process.communicate(timeout=20)
        assert process.returncode != 0
    finally:
        host.close()


def test_supervisor_refuses_a_config_with_duplicate_json_keys():
    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(SUPERVISOR_PATH_V2)],
            stdin=child.fileno(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        child.close()
        host.send(b'{"kind": "config", "kind": "config", "nonce": "x"}')
        process.communicate(timeout=20)
        assert process.returncode != 0
    finally:
        host.close()


def test_supervisor_reports_setup_failed_when_execvp_cannot_find_the_binary():
    """A review caught that a pre-exec failure (missing allowlisted binary,
    privilege-drop or rlimit failure) exited the child with the same status
    (72) an ordinary subject could also legitimately exit with -- making a
    harness setup failure indistinguishable from a real product FAILURE.
    The exec-error pipe closes that: `execvp` never returning here means the
    write end was never closed by a successful exec, so the parent observes
    the marker this test asserts on."""

    messages, _rc, _out, _err = _run_supervisor(_config(
        "true", argv=["/nonexistent/definitely-not-a-real-binary-v2", "arg"],
    ))
    result = messages[1]
    assert result["setup_failed"] is True
    assert result["exit_status"] == 72


def test_supervisor_reports_setup_failed_as_false_on_an_ordinary_run():
    messages, _rc, _out, _err = _run_supervisor(_config("raise SystemExit(0)"))
    assert messages[1]["setup_failed"] is False


def test_supervisor_drops_privilege_for_the_subject_when_asked():
    if os.geteuid() != 0:
        pytest.skip("privilege drop is only observable when the supervisor starts privileged")
    code = "import os, sys; sys.stderr.write(str(os.geteuid()))"
    messages, _rc, _out, err = _run_supervisor(_config(code, drop_to_nobody=True))
    assert err.strip().splitlines()[-1] != "0"
    assert messages[1]["exit_status"] == 0
