"""EXPERIMENTAL ONLY -- #301 S0 launcher. Not production code.

The launcher is declared TCB: it chooses the interpreter (absolute path, root-owned), the
environment (empty unless a test injects one), the flags (-I -S before the first import), the
descriptors the child inherits (pass_fds only), and the result channel (a socketpair end,
which a same-UID process cannot reopen through /proc/<pid>/fd, unlike a pipe).
"""
from __future__ import annotations

import json
import os
import select
import socket
import struct
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOOTSTRAP_SOURCE = (HERE / "s0_bootstrap.py").read_text()


def _recv_all(sock: socket.socket, deadline: float) -> bytes:
    chunks = []
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        ready, _, _ = select.select([sock], [], [], left)
        if not ready:
            raise TimeoutError
        chunk = sock.recv(1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def launch(interpreter: str, spec: dict, fds: tuple[int, ...], *, env: dict | None = None,
           cwd: str = "/", timeout: float = 120.0, flags: tuple[str, ...] = ("-I", "-S")) -> dict:
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    t0 = time.monotonic()
    try:  # until the child exists, the launcher is the single owner of BOTH endpoints
        argv = [interpreter, *flags, "-c", BOOTSTRAP_SOURCE, json.dumps(dict(spec, result_fd=child.fileno()))]
        proc = subprocess.Popen(
            argv, pass_fds=(*fds, child.fileno()), env=env if env is not None else {}, cwd=cwd,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, ValueError, TypeError) as exc:
        parent.close()
        child.close()
        return {"pid": None, "rc": None, "reply": None, "spawn_error": type(exc).__name__}
    child.close()
    outcome: dict = {"pid": proc.pid}
    try:
        raw = _recv_all(parent, t0 + timeout)
        proc.wait(timeout=max(1.0, t0 + timeout - time.monotonic()))
    except (TimeoutError, subprocess.TimeoutExpired):
        proc.kill()
        proc.wait()
        raw = b""
        outcome["timeout"] = True
    finally:
        parent.close()
    outcome["rc"] = proc.returncode
    outcome["stderr_tail"] = proc.stderr.read().decode(errors="replace")[-600:]
    outcome["stdout_tail"] = proc.stdout.read().decode(errors="replace")[-300:]
    proc.stderr.close()
    proc.stdout.close()
    outcome["elapsed_s"] = round(time.monotonic() - t0, 3)
    if len(raw) >= 8:
        (n,) = struct.unpack(">Q", raw[:8])
        outcome["reply"] = json.loads(raw[8:8 + n]) if len(raw) == 8 + n else {"status": "channel_malformed"}
    else:
        outcome["reply"] = None  # absence is never a valid result
    return outcome
