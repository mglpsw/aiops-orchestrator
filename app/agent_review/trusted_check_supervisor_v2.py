"""Host-owned supervisor for AgentReview v2 trusted checks (#201-B3, rev. 4).

This file is BOTH a module (for the executor to locate by absolute path and
for tests to import constants from) and the script that actually runs inside
the isolation. It is deliberately stdlib-only and imports nothing from
``app.*``: it is invoked as ``python -I <absolute path to this file>``.

## Why an absolute path under ``-I``, and not ``python -m``

With ``-m`` the current working directory is prepended to ``sys.path`` -- and
the cwd during a check is the PR CHECKOUT. A PR that ships its own
``app/agent_review/trusted_check_supervisor_v2.py`` would then hijack the
supervisor and the entire authority argument collapses. Passing the absolute
path makes ``sys.path[0]`` this file's own directory, and ``-I`` additionally
implies ``-E`` (ignore ``PYTHON*`` env vars) and ``-s`` (no user site), so
nothing the subject controls can steer the import.

That is also exactly why every host input arrives over the CHANNEL rather
than the environment: ``-I`` would ignore env anyway, ``/proc/<pid>/cmdline``
is world-readable so argv is not private, and a socket the subject never
holds is.

## The channel: SEQPACKET on fd 0

The host creates ``socketpair(AF_UNIX, SOCK_SEQPACKET)`` and hands one end to
this process as **fd 0**. Two properties earn that specific choice:

- ``sudo`` closes every descriptor above 2, and the sudo-elevated strategy is
  the one this project's own GitHub Actions runner selects -- an inherited
  high fd would simply not survive. fds 0/1/2 do. Probed on both the direct
  and sudo-elevated chains before this design was chosen, not assumed;
- ``SOCK_SEQPACKET`` preserves message boundaries, so a record is one
  ``recv``: no framing, no partial-read state machine, and a truncated write
  cannot be mistaken for a shorter valid record.

The subject NEVER holds this descriptor. Before ``execve`` the child closes
it and receives ``/dev/null`` on stdin, so a forged record cannot be injected
even by code that knows the protocol byte for byte. The subject's own
stdout/stderr remain the host's pipes and are **diagnostic only** -- nothing
read from them ever reaches a verdict.

## The `ready` handshake, and the race it closes

After sending `hello`, this process BLOCKS until the host sends `ready`
before forking the subject. Without this, the subject could start AND FINISH
before the host (or, under the sudo-elevated strategy, the privileged broker
acting on the host's behalf) manages to discover and pin this execution's
kernel identity -- turning a legitimate SUCCESS into an unverifiable
containment failure purely by being fast. Making the handshake explicit
removes the race by construction instead of hoping to win it.

## What this process is, in authority terms

The supervisor runs NO subject code. It forks, and only the CHILD becomes the
subject after dropping privilege and applying resource limits. The supervisor
therefore observes the subject's termination the way the kernel reports it
(``waitpid``), which is the only observation of the subject this design
treats as real -- and it reports that observation over a channel the subject
cannot reach.

It is worth being precise about what that does and does not buy: for
``subject_code`` the exit status is still something the subject chooses, and
no amount of channel privacy changes that. The channel makes the REPORTING
trustworthy, not the reported fact. Authority over subject-code checks comes
from the class boundary in ``trusted_check_authority_v2`` (which refuses
``TRUSTED`` outright) and, later, from deterministic CI in ``#201-C0`` --
never from this supervisor.
"""

from __future__ import annotations

import json
import os
import pwd
import resource
import socket
import sys
from pathlib import Path

SUPERVISOR_PROTOCOL_V2 = "agent-review.trusted-check-supervisor.v2"
SUPERVISOR_PATH_V2 = Path(__file__).resolve()

# One SEQPACKET datagram caps out around 200 KiB on Linux (probed). Every
# message this protocol sends is small by construction; the bound exists so a
# hostile or broken peer cannot make the host allocate without limit.
MAX_MESSAGE_BYTES_V2 = 1 << 20

# Exit codes of the SUPERVISOR itself -- never of the subject. They are only
# ever read as "the protocol did not complete"; the subject's own status
# travels in the `result` record.
EXIT_OK_V2 = 0
EXIT_CONFIG_INVALID_V2 = 71
EXIT_SPAWN_FAILED_V2 = 72

_REQUIRED_CONFIG_KEYS_V2 = (
    "kind",
    "protocol",
    "nonce",
    "spec_digest",
    "argv",
    "cwd",
    "max_memory_mb",
    "max_processes",
    "drop_to_nobody",
)


def _duplicate_keys_v2(pairs):
    """Reject duplicate JSON keys instead of silently letting the last one
    win -- the same discipline `app/caem_consumer/f0.py` applies to CAEM
    documents. A document that says two different things is malformed, not a
    merge."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_v2(value: str):
    raise ValueError(f"non-finite number in JSON: {value}")


def strict_json_loads_v2(data: bytes | str) -> object:
    return json.loads(
        data, object_pairs_hook=_duplicate_keys_v2, parse_constant=_reject_non_finite_v2
    )


def canonical_json_bytes_v2(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def validate_config_v2(payload: object) -> dict:
    """Fail-closed validation of the host's config message. Raises
    ``ValueError`` on anything unexpected -- there is no partial acceptance
    and no defaulting of a missing field."""

    if not isinstance(payload, dict):
        raise ValueError("config must be a JSON object")
    missing = [key for key in _REQUIRED_CONFIG_KEYS_V2 if key not in payload]
    if missing:
        raise ValueError(f"config missing keys: {missing}")
    if payload["kind"] != "config":
        raise ValueError("config kind mismatch")
    if payload["protocol"] != SUPERVISOR_PROTOCOL_V2:
        raise ValueError("config protocol mismatch")
    argv = payload["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("argv must be a non-empty list of strings")
    for key in ("nonce", "spec_digest", "cwd"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"{key} must be a non-empty string")
    for key in ("max_memory_mb", "max_processes"):
        if not isinstance(payload[key], int) or isinstance(payload[key], bool) or payload[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if not isinstance(payload["drop_to_nobody"], bool):
        raise ValueError("drop_to_nobody must be a boolean")
    return payload


def _send_v2(channel: socket.socket, message: dict) -> None:
    payload = canonical_json_bytes_v2(message)
    if len(payload) > MAX_MESSAGE_BYTES_V2:  # pragma: no cover - defensive
        raise ValueError("supervisor message exceeds the protocol bound")
    channel.send(payload)


def _exec_subject_v2(config: dict, channel: socket.socket) -> None:
    """Runs in the forked CHILD and never returns: closes the channel, drops
    privilege, applies limits, and becomes the subject.

    Order matters and is not incidental. The channel is closed FIRST, so the
    descriptor is gone before any subject code exists -- not merely
    close-on-exec, which would still leave a window if the drop or the limits
    raised. Privilege is dropped BEFORE the limits are applied and before
    ``execve``, so the subject never runs with the supervisor's identity."""

    channel.close()
    null_fd = os.open(os.devnull, os.O_RDONLY)
    if null_fd == 0:
        # `os.open` returns a NON-inheritable descriptor (PEP 446), and the
        # channel we just closed WAS fd 0 -- so /dev/null lands right back on
        # fd 0 and `dup2(0, 0)` is a no-op that leaves O_CLOEXEC set. The
        # subject would then exec with no stdin at all: reads fail EBADF
        # instead of returning EOF. Clearing it explicitly is the whole fix,
        # and the branch is not hypothetical -- fd 0 is precisely the
        # descriptor this design frees a moment earlier.
        os.set_inheritable(0, True)
    else:  # pragma: no cover - only when fd 0 was somehow already taken
        os.dup2(null_fd, 0)
        os.close(null_fd)

    if config["drop_to_nobody"]:
        nobody = pwd.getpwnam("nobody")
        os.setgroups([])
        os.setgid(nobody.pw_gid)
        os.setuid(nobody.pw_uid)

    memory_bytes = int(config["max_memory_mb"]) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_NPROC, (config["max_processes"], config["max_processes"]))

    argv = list(config["argv"])
    os.execvp(argv[0], argv)


def main() -> int:
    channel = socket.socket(fileno=0)
    try:
        raw = channel.recv(MAX_MESSAGE_BYTES_V2)
        config = validate_config_v2(strict_json_loads_v2(raw.decode("utf-8")))
    except Exception:
        # Nothing is emitted on the channel: a config we could not validate
        # cannot be answered with a record bound to a nonce we do not trust.
        return EXIT_CONFIG_INVALID_V2

    try:
        os.chdir(config["cwd"])
    except OSError:
        return EXIT_CONFIG_INVALID_V2

    _send_v2(channel, {
        "kind": "hello",
        "protocol": SUPERVISOR_PROTOCOL_V2,
        "nonce": config["nonce"],
        # Namespace-RELATIVE (it is 1 once we are a PID namespace's init).
        # Reported for diagnostics only; the host must never use it as a
        # signal target -- host-side kernel discovery is what actually
        # establishes the killable identity.
        "pid": os.getpid(),
    })

    # Block until the host says it has established this execution's identity.
    # Without this the subject could START AND FINISH before the host managed
    # to look up the namespace leader -- and a fast check would then be
    # reported as "containment unverifiable", turning a legitimate success
    # into an INFRA_FAILURE. Making the handshake explicit removes the race
    # by construction instead of hoping to win it.
    try:
        ready = strict_json_loads_v2(channel.recv(MAX_MESSAGE_BYTES_V2).decode("utf-8"))
    except Exception:
        return EXIT_CONFIG_INVALID_V2
    if (
        not isinstance(ready, dict)
        or ready.get("kind") != "ready"
        or ready.get("protocol") != SUPERVISOR_PROTOCOL_V2
        or ready.get("nonce") != config["nonce"]
    ):
        return EXIT_CONFIG_INVALID_V2

    try:
        child_pid = os.fork()
    except OSError:
        return EXIT_SPAWN_FAILED_V2

    if child_pid == 0:
        try:
            _exec_subject_v2(config, channel)
        except BaseException:
            # The child must never fall back into the supervisor's own code
            # path, and must never run the subject with a partially applied
            # sandbox -- `os._exit` skips every inherited atexit/finally.
            os._exit(EXIT_SPAWN_FAILED_V2)
        os._exit(EXIT_SPAWN_FAILED_V2)  # pragma: no cover - execvp does not return

    _pid, status = os.waitpid(child_pid, 0)
    signaled = os.WIFSIGNALED(status)
    exit_status = -os.WTERMSIG(status) if signaled else os.WEXITSTATUS(status)

    _send_v2(channel, {
        "kind": "result",
        "protocol": SUPERVISOR_PROTOCOL_V2,
        "nonce": config["nonce"],
        "spec_digest": config["spec_digest"],
        "exit_status": exit_status,
        "signaled": signaled,
    })
    return EXIT_OK_V2


if __name__ == "__main__":
    sys.exit(main())
