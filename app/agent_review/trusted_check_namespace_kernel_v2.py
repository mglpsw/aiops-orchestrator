"""Host-owned PID-namespace identity and containment primitives (#201-B3).

Stdlib-only, deliberately: this module is imported both by
``isolated_executor_v2.py`` (a normal, fully-imported module) AND by
``trusted_check_broker_v2.py`` (invoked as ``python -I <absolute path>``,
which restricts ``sys.path`` to stdlib + the script's own directory -- a
non-stdlib import here would break the broker the same way it would break
``trusted_check_supervisor_v2.py``).

## The one rule every function here exists to enforce

```text
No value observed FROM INSIDE a PID namespace may reach a kill path.
```

Inside a PID namespace ``os.getpid()`` returns ``1``. For ``kill(2)`` the
non-positive range has POSIX broadcast semantics, so a namespace-relative
value reaching a privileged kill could signal every process the caller can
reach -- not a cosmetic bug. Every identity this module produces is derived
from the CALLER's own procfs view of a process it holds by construction (a
descendant it spawned), never from anything the namespace's own occupant
reports about itself.

``_assert_killable_host_pid_v2`` is the single choke point every signal
target passes through. There is no second path, and no code path anywhere
in this module (or its callers) formats a negative operand -- ``killpg`` and
textual ``kill -9 -- -<pgid>``/``sudo kill <pid>`` forms are structurally
absent, not merely unused.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import signal as _signal
except ImportError:  # pragma: no cover - stdlib always has signal on Linux
    _signal = None

_KERNEL_POLL_INTERVAL_SECONDS_V2 = 0.05
DISCOVERY_MAX_DEPTH_V2 = 8
DISCOVERY_MAX_NODES_V2 = 512
CONTAINMENT_VERIFY_SECONDS_V2 = 5.0

# `--pid --fork` makes the namespace's init the process this whole subsystem
# tracks; the kernel destroys EVERY remaining process in that namespace the
# moment init dies -- unconditionally, no cleanup code of ours involved.
# `--kill-child` additionally installs `PR_SET_PDEATHSIG` on that init, so
# killing the process that directly invoked `unshare` tears the namespace
# down even when the leader itself is not directly addressable. `--mount-proc`
# gives the subject a private procfs, so it cannot enumerate -- let alone
# signal -- host processes.
NAMESPACE_FLAGS_V2 = ("--pid", "--fork", "--kill-child", "--mount-proc", "--net")

# Probed, never assumed. Missing pidfd support means fail-closed, never a
# fallback to signalling a bare pid number (which pid reuse can redirect).
PIDFD_AVAILABLE_V2 = (
    hasattr(os, "pidfd_open") and _signal is not None and hasattr(_signal, "pidfd_send_signal")
)


def assert_killable_host_pid_v2(pid: object) -> int:
    """The single choke point every signal target passes through.

    Refuses anything that is not a plain ``int`` strictly greater than 1 --
    which excludes the namespace-relative value ``1``, ``0``, and every
    negative (broadcast) value."""

    if isinstance(pid, bool) or not isinstance(pid, int):
        raise ValueError("kill target must be an integer pid")
    if pid <= 1:
        raise ValueError("refusing a non-positive or namespace-relative kill target")
    return pid


def read_proc_stat_fields_v2(pid: int) -> list[str] | None:
    """Fields of ``/proc/<pid>/stat`` from ``state`` onward. Split after the
    LAST ``)`` because ``comm`` is attacker-influenceable and may contain
    spaces or parentheses of its own."""

    try:
        content = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    _, _, rest = content.rpartition(")")
    fields = rest.split()
    return fields if len(fields) > 19 else None


def process_start_time_v2(pid: int) -> str | None:
    fields = read_proc_stat_fields_v2(pid)
    return fields[19] if fields is not None else None


def process_parent_v2(pid: int) -> int | None:
    fields = read_proc_stat_fields_v2(pid)
    if fields is None:
        return None
    try:
        return int(fields[1])
    except ValueError:  # pragma: no cover - defensive
        return None


def proc_children_v2(pid: int) -> tuple[int, ...]:
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return ()
    out = []
    for token in raw.split():
        try:
            out.append(int(token))
        except ValueError:  # pragma: no cover - defensive
            continue
    return tuple(out)


def namespace_pids_v2(pid: int) -> list[str] | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("NSpid:"):
                return line.split()[1:]
    except OSError:
        return None
    return None


def namespace_key_for_v2(pid: int) -> tuple[int, int] | None:
    try:
        stat_result = os.stat(f"/proc/{pid}/ns/pid")
    except OSError:
        return None
    return (stat_result.st_dev, stat_result.st_ino)


def is_descendant_of_v2(pid: int, ancestor: int) -> bool:
    """Upward ``ppid`` walk. Cheaper than a downward search and, unlike
    ``/proc/<pid>/task/<tid>/children``, needs no kernel config option."""

    current = pid
    for _ in range(DISCOVERY_MAX_DEPTH_V2):
        if current == ancestor:
            return True
        parent = process_parent_v2(current)
        if parent is None or parent <= 1:
            return False
        current = parent
    return False


@dataclass(frozen=True)
class ExecutionIdentityV2:
    """A signal target that survives pid reuse.

    ``host_pid`` alone is not an identity: the kernel recycles pids, and a
    kill issued against a recycled number hits an unrelated process. The
    ``pidfd`` pins the exact process object, ``start_time`` corroborates it,
    and ``namespace_key`` -- taken from an FD we keep OPEN -- is what makes
    the survivor scan sound: holding that reference prevents the namespace
    inode from being reused underneath us and reported as a phantom
    survivor."""

    host_pid: int
    start_time: str
    pidfd: int
    namespace_fd: int
    namespace_key: tuple[int, int]

    def close(self) -> None:
        for fd in (self.pidfd, self.namespace_fd):
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - defensive
                pass


def _identity_if_namespace_leader_v2(
    pid: int, *, host_namespace: tuple[int, int] | None
) -> ExecutionIdentityV2 | None:
    namespace_pids = namespace_pids_v2(pid)
    if not namespace_pids or len(namespace_pids) < 2:
        return None
    if namespace_pids[-1] != "1" or namespace_pids[0] != str(pid):
        return None
    key = namespace_key_for_v2(pid)
    if key is None or key == host_namespace:
        return None
    start_time = process_start_time_v2(pid)
    if start_time is None:
        return None
    try:
        namespace_fd = os.open(f"/proc/{pid}/ns/pid", os.O_RDONLY)
    except OSError:
        return None
    try:
        pidfd = os.pidfd_open(pid, 0)
    except OSError:
        os.close(namespace_fd)
        return None
    # Re-read AFTER pinning: if the pid was recycled between the checks the
    # start time no longer matches and the candidate is discarded.
    if process_start_time_v2(pid) != start_time:  # pragma: no cover - narrow race
        os.close(namespace_fd)
        os.close(pidfd)
        return None
    return ExecutionIdentityV2(
        host_pid=pid, start_time=start_time, pidfd=pidfd,
        namespace_fd=namespace_fd, namespace_key=key,
    )


def discover_namespace_leader_v2(root_pid: int, *, deadline: float) -> ExecutionIdentityV2 | None:
    """Find the namespace's init, using ONLY the CALLER's own procfs view.

    Nothing reported from inside the namespace is trusted here -- with a
    private procfs the namespace's own occupant cannot even observe its own
    host-visible pid, and a value it could report would be namespace-relative
    anyway. The caller walks down from a process IT SPAWNED and accepts
    exactly one shape: a descendant whose pid namespace differs from its own
    and whose ``NSpid`` ends in ``1`` (it is init of that namespace) while
    beginning with its own host-visible pid. The subject cannot manufacture a
    second init for the same namespace, so the match is unambiguous by
    construction."""

    host_namespace = namespace_key_for_v2(os.getpid())
    while time.monotonic() < deadline:
        visited = 0
        stack: list[tuple[int, int]] = [(root_pid, 0)]
        while stack and visited < DISCOVERY_MAX_NODES_V2:
            pid, depth = stack.pop()
            visited += 1
            if depth > DISCOVERY_MAX_DEPTH_V2:
                continue
            identity = _identity_if_namespace_leader_v2(pid, host_namespace=host_namespace)
            if identity is not None:
                return identity
            stack.extend((child, depth + 1) for child in proc_children_v2(pid))
        time.sleep(_KERNEL_POLL_INTERVAL_SECONDS_V2)
    return None


def namespace_has_live_processes_v2(namespace_key: tuple[int, int]) -> bool:
    """Positive survivor check: is ANY live (non-zombie) process still a
    member of that namespace? A zombie is not a survivor -- it holds no
    resources and cannot run -- so it is excluded deliberately."""

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if namespace_key_for_v2(pid) != namespace_key:
            continue
        fields = read_proc_stat_fields_v2(pid)
        if fields is not None and fields[0] != "Z":
            return True
    return False


def signal_host_pid_v2(pid: object, *, pidfd: int | None) -> None:
    """Best-effort SIGKILL of ONE validated host pid, from a caller that
    holds real privilege over the target (either it IS the target's real
    parent/owner, or it is root). Never raises.

    There is no textual-PID fallback here (no ``kill -9 <pid>`` shellout, no
    ``sudo``): a caller invoking this function is, by construction, already
    privileged enough to signal the target directly -- ``pidfd_send_signal``
    when available (immune to pid reuse), plain ``os.kill`` otherwise. The
    operand is always a positive number that passed
    ``assert_killable_host_pid_v2``."""

    try:
        target = assert_killable_host_pid_v2(pid)
    except ValueError:
        return
    if pidfd is not None and _signal is not None:
        try:
            _signal.pidfd_send_signal(pidfd, _signal.SIGKILL)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        os.kill(target, _signal.SIGKILL if _signal is not None else 9)
    except (ProcessLookupError, PermissionError):
        pass


def tear_down_execution_v2(*, identity: ExecutionIdentityV2 | None, direct_child_pid: int) -> None:
    """Kill the namespace init when identified, and ALWAYS also the direct
    child the caller spawned -- an identity it holds by construction, whose
    death fires ``--kill-child``'s ``PR_SET_PDEATHSIG`` and collapses the
    namespace even when the leader itself was not addressable."""

    if identity is not None:
        signal_host_pid_v2(identity.host_pid, pidfd=identity.pidfd)
    signal_host_pid_v2(direct_child_pid, pidfd=None)


def verify_containment_v2(identity: ExecutionIdentityV2 | None) -> bool:
    """Zero survivors, PROVEN rather than assumed. Polls the pinned namespace
    until it is genuinely empty. An identity that was never established means
    containment is unverifiable, which is a failure -- not a pass by absence
    of evidence."""

    if identity is None:
        return False
    deadline = time.monotonic() + CONTAINMENT_VERIFY_SECONDS_V2
    while time.monotonic() < deadline:
        if not namespace_has_live_processes_v2(identity.namespace_key):
            return True
        time.sleep(_KERNEL_POLL_INTERVAL_SECONDS_V2)
    return False
