"""P2 spawn spike -- the dedicated reader (disposable experiment, NOT S1-B code).

One run = one unit: preconditions -> typed fd census -> spawn (mechanism A or B)
-> protocol wait -> teardown on every exit. Prints ONE JSON line on stdout.

Mechanisms:
  A  controlled Popen: reader does subreaper + NNP + fchdir(snapshot fd); Popen with
     close_fds=True, pass_fds=(), explicit env, cwd=None, preexec_fn = setrlimit only.
  B  explicit fork/exec: same reader preconditions; the child dup2s stdio, sets the
     rlimit, closes every fd but stdio and the setup-error pipe, re-checks the exact
     inherited set, then execve; setup failures travel over a CLOEXEC error pipe.

Teardown (both): kernel attribution, never the spawn handle. Scan /proc/*/stat for
ppid == self (reparented grandchildren included, because the reader is a verified
subreaper). pidfd_open each; prove childness with waitid(P_PIDFD, WNOWAIT|WNOHANG),
since a non-child gives ECHILD. Then pidfd_send_signal(SIGKILL) and reap with
waitid(P_PIDFD), bounded by a deadline. Survivors -> unit_teardown_incomplete.

Ablations (discriminators, deliberately broken): handle_only, no_subreaper, no_nnp,
rlimit_after_spawn, path_cwd, no_fd_census, B_no_close, bare_pid.
"""
import ctypes
import fcntl
import json
import os
import resource
import select
import signal
import stat
import subprocess
import sys
import threading
import time

PR_SET_CHILD_SUBREAPER, PR_GET_CHILD_SUBREAPER = 36, 37
PR_SET_NO_NEW_PRIVS, PR_GET_NO_NEW_PRIVS = 38, 39
_LIBC = ctypes.CDLL(None, use_errno=True)
_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C"}


def _prctl(op: int, arg: int = 0) -> int:
    r = _LIBC.prctl(op, arg, 0, 0, 0)
    if r < 0:
        raise OSError(ctypes.get_errno(), f"prctl({op})")
    return r


def _subreaper() -> int:
    v = ctypes.c_int(-1)
    if _LIBC.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(v), 0, 0, 0) < 0:
        raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER")
    return v.value


class Refusal(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason, self.detail = reason, detail


# -- typed fd census (B-CAP) -----------------------------------------------------------------

def _describe(fd: int) -> dict:
    st = os.fstat(fd)
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    return {
        "fd": fd,
        "object_type": "dir" if stat.S_ISDIR(st.st_mode) else "fifo" if stat.S_ISFIFO(st.st_mode)
        else "chr" if stat.S_ISCHR(st.st_mode) else "reg" if stat.S_ISREG(st.st_mode) else "other",
        "access": {os.O_RDONLY: "r", os.O_WRONLY: "w", os.O_RDWR: "rw"}[fl & os.O_ACCMODE],
        "o_path": bool(fl & getattr(os, "O_PATH", 0o10000000)),
        "cloexec": bool(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC),
        "kernel_identity": [st.st_dev, st.st_ino],
    }


def fd_census(snapshot_fd: int) -> list:
    """AllowedFd, typed. Reader closed set: stdin (chr/fifo, r), result + diagnostics
    channels (fifo, w), the admitted snapshot dir (dir, r, never O_PATH, CLOEXEC so it is
    never inherited by Git). Anything else, or a wrong type/access/O_PATH, is refused."""
    allowed = {
        0: ({"chr", "fifo"}, "r"),
        1: ({"fifo"}, "w"),
        2: ({"fifo"}, "w"),
        snapshot_fd: ({"dir"}, "r"),
    }
    seen = []
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        try:
            d = _describe(fd)
        except OSError:
            continue  # the listdir's own transient descriptor
        seen.append(d)
        rule = allowed.get(fd)
        if rule is None:
            raise Refusal("reader_fd_capability_unexpected", f"unexpected fd {d}")
        types, access = rule
        if d["o_path"] or d["object_type"] not in types or d["access"] != access:
            raise Refusal("reader_fd_capability_unexpected", f"fd {fd} is {d}, expected {types}/{access}")
        if fd > 2 and not d["cloexec"]:
            raise Refusal("reader_fd_capability_unexpected", f"fd {fd} would be inherited")
    return seen


# -- teardown by kernel attribution -------------------------------------------------------

def own_children() -> list:
    me, out = os.getpid(), []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            data = open(f"/proc/{name}/stat", "rb").read()
        except OSError:
            continue
        if int(data[data.rindex(b")") + 2:].split()[1]) == me:
            out.append(int(name))
    return out


def teardown(deadline_s: float, handle_pidfd, handle_only: bool) -> dict:
    end = time.monotonic() + deadline_s
    stats = {"signalled": 0, "reaped": 0, "not_child_skipped": 0, "rounds": 0}

    def kill_and_reap(fd: int) -> None:
        try:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            stats["signalled"] += 1
        except ProcessLookupError:
            pass
        while time.monotonic() < end:
            try:
                if os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG) is not None:
                    stats["reaped"] += 1
                    return
            except ChildProcessError:
                return
            time.sleep(0.002)

    if handle_only:
        if handle_pidfd is not None:
            kill_and_reap(handle_pidfd)
    else:
        while time.monotonic() < end:
            stats["rounds"] += 1
            kids = own_children()
            if not kids:
                break
            for pid in kids:
                try:
                    fd = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                try:
                    try:  # childness via the pidfd itself: a non-child gives ECHILD
                        os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    except ChildProcessError:
                        stats["not_child_skipped"] += 1
                        continue
                    kill_and_reap(fd)
                finally:
                    os.close(fd)
    remaining = own_children()
    try:
        os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        any_child = True
    except ChildProcessError:
        any_child = False
    stats.update(remaining=remaining, any_child_or_zombie=any_child)
    return stats


# -- spawn mechanisms ---------------------------------------------------------------------

class Handle:
    def __init__(self, pid, pidfd, out_fd, popen=None, in_fd=None) -> None:
        self.pid, self.pidfd, self.out_fd, self.popen, self.in_fd = pid, pidfd, out_fd, popen, in_fd


def spawn_A(cfg: dict, argv: list, inject: str | None, abl: set) -> Handle:
    limit = cfg["as_limit"]

    def pre() -> None:  # the ONLY pre-exec work in A: the rlimit
        if inject == "setup_exception":
            raise RuntimeError("injected child setup failure")
        if inject == "pre_exec_stall":
            time.sleep(cfg.get("stall_s", 3.0))   # P2d: a child stalled before exec completes
        if "rlimit_after_spawn" not in abl:
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    if inject == "constructor_fails_after_fork":
        # 3.11 binds `from _posixsubprocess import fork_exec as _fork_exec` at import
        # time, so the call site is subprocess._fork_exec (run 2 showed patching the
        # C module attribute never reached it: the row did not inject its own fault).
        real = subprocess._fork_exec

        def failing(*a, **k):
            pid = real(*a, **k)
            raise RuntimeError(f"injected parent failure after fork (child {pid} exists)")

        subprocess._fork_exec = failing
    p = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        close_fds=True, pass_fds=(), env=_ENV, cwd=cfg["snapshot_path"] if "path_cwd" in abl else None,
        preexec_fn=pre,
    )
    if inject == "baseexception_after_spawn":
        raise KeyboardInterrupt("injected right after spawn, before pidfd")
    pidfd = os.pidfd_open(p.pid)
    if "rlimit_after_spawn" in abl:
        time.sleep(0.3)
        resource.prlimit(p.pid, resource.RLIMIT_AS, (limit, limit))
    return Handle(p.pid, pidfd, p.stdout.fileno(), p)


def spawn_B(cfg: dict, argv: list, inject: str | None, abl: set) -> Handle:
    limit = cfg["as_limit"]
    r_out, w_out = os.pipe()          # CLOEXEC (non-inheritable) by default
    r_x, w_x = os.pipe()              # setup-error pipe, CLOEXEC
    devnull = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
    r_in = w_in = None
    if cfg.get("stdin_pipe"):                   # P2d CM-W6: a protocol request channel into the child
        r_in, w_in = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            if inject == "setup_exception":
                raise RuntimeError("injected child setup failure")
            if inject == "pre_exec_stall":
                time.sleep(cfg.get("stall_s", 3.0))   # P2d: a child stalled before exec completes
            os.dup2(r_in if r_in is not None else devnull, 0)
            os.dup2(w_out, 1)
            os.dup2(devnull, 2)
            if "rlimit_after_spawn" not in abl:
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            if "path_cwd" in abl:
                os.chdir(cfg["snapshot_path"])
            if "B_no_close" not in abl:
                for name in os.listdir("/proc/self/fd"):
                    fd = int(name)
                    if fd > 2 and fd != w_x:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                # last pre-exec boundary: the exact inherited set is {0,1,2}
                # (w_x is CLOEXEC and disappears at exec)
                left = set()
                for name in os.listdir("/proc/self/fd"):
                    try:
                        os.fstat(int(name))
                        left.add(int(name))
                    except OSError:
                        pass
                if left != {0, 1, 2, w_x} or not fcntl.fcntl(w_x, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
                    raise RuntimeError(f"pre-exec fd set {sorted(left)}")
            os.execve(argv[0], argv, _ENV)
        except BaseException as exc:  # noqa: BLE001 -- child side, reported and _exit
            try:
                os.write(w_x, f"{type(exc).__name__}:{exc}".encode()[:300])
            finally:
                os._exit(127)
    if inject == "constructor_fails_after_fork":
        raise RuntimeError(f"injected parent failure after fork (child {pid} exists)")
    if inject == "baseexception_after_spawn":
        raise KeyboardInterrupt("injected right after spawn, before pidfd")
    pidfd = os.pidfd_open(pid)
    for fd in (w_out, w_x, devnull) + ((r_in,) if r_in is not None else ()):
        os.close(fd)
    if "rlimit_after_spawn" in abl:
        time.sleep(0.3)
        resource.prlimit(pid, resource.RLIMIT_AS, (limit, limit))
    # P2d (correction round 3): the setup/exec error pipe is watched under the UNIT
    # deadline that started before fork(); the child is already owned (pidfd) here.
    deadline_at = cfg.get("_deadline_at", time.monotonic() + 3600)
    if cfg.get("handshake_deadline_off"):      # P2d ablation: the handshake is NOT under the unit deadline
        deadline_at = time.monotonic() + 3600
    err = b""
    try:
        while True:
            left = deadline_at - time.monotonic()
            if left <= 0:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                os.waitid(os.P_PIDFD, pidfd, os.WEXITED)
                raise Refusal("unit_deadline", "exec handshake did not complete before the unit deadline")
            r, _, _ = select.select([r_x], [], [], left)
            if not r:
                continue
            chunk = os.read(r_x, 512)
            if not chunk:
                break                              # EOF: the CLOEXEC error pipe closed at a successful exec
            err += chunk
    finally:
        os.close(r_x)
    if err:
        raise Refusal("transport_spawn_failed", err.decode(errors="replace"))
    return Handle(pid, pidfd, r_out, in_fd=w_in)


def read_report(h: Handle, deadline_s: float) -> dict:
    end, buf = time.monotonic() + deadline_s, b""
    while b"\n" not in buf:
        left = end - time.monotonic()
        if left <= 0:
            raise Refusal("unit_deadline", "no protocol line before the unit deadline")
        r, _, _ = select.select([h.out_fd], [], [], left)
        if r:
            chunk = os.read(h.out_fd, 65536)
            if not chunk:
                raise Refusal("transport_failed", "EOF before protocol line")
            buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


# -- PID-reuse witness (TF5-G), run as pid 1 of a private user+pid namespace ---------------

def pid_reuse_witness(cfg: dict, spawn, abl: set) -> dict:
    argv = [cfg["python"], "-I", "-S", cfg["child"], "report", cfg["token"]]
    x = spawn(cfg, argv, None, set())
    read_report(x, 5.0)
    stale = x.pidfd                       # kept open past reaping: a stale reference
    os.waitid(os.P_PIDFD, stale, os.WEXITED)
    x_pid = x.pid
    with open("/proc/sys/kernel/ns_last_pid", "w") as f:
        f.write(str(x_pid - 1))
    y = spawn(cfg, [cfg["python"], "-I", "-S", cfg["child"], "hold", cfg["token"]], None, set())
    read_report(y, 5.0)
    if y.pid != x_pid:
        return {"gate": "gate_unavailable", "why": f"pid not reused ({y.pid} != {x_pid})"}
    if "bare_pid" in abl:
        os.kill(x_pid, signal.SIGKILL)    # ablation: identity = bare pid
        via = "bare_pid"
    else:
        try:
            signal.pidfd_send_signal(stale, signal.SIGKILL)
            via = "stale_pidfd_signalled_UNEXPECTEDLY"
        except ProcessLookupError:
            via = "stale_pidfd_ESRCH"
    time.sleep(0.2)
    y_state = os.waitid(os.P_PIDFD, y.pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    return {"gate": "available", "reused_pid": x_pid, "via": via,
            "unrelated_process_killed": y_state is not None}


# -- main -------------------------------------------------------------------------------

def main() -> None:
    cfg = json.loads(sys.argv[1])
    abl, inject, mech = set(cfg.get("ablations", [])), cfg.get("inject"), cfg["mechanism"]
    spawn = spawn_A if mech == "A" else spawn_B
    res = {"mechanism": mech, "scenario": cfg["scenario"], "ablations": sorted(abl)}
    handle, primary, escaped, report = None, None, None, None
    try:
        if threading.active_count() != 1 or len(os.listdir("/proc/self/task")) != 1:
            raise Refusal("reader_process_not_dedicated")
        if "no_subreaper" not in abl:
            _prctl(PR_SET_CHILD_SUBREAPER, 1)
        res["subreaper_verified"] = _subreaper() == 1
        if "no_subreaper" not in abl and not res["subreaper_verified"]:
            raise Refusal("subreaper_required")
        if "no_nnp" not in abl:
            _prctl(PR_SET_NO_NEW_PRIVS, 1)
        res["reader_nnp"] = _prctl(PR_GET_NO_NEW_PRIVS)
        snap = cfg["snapshot_fd"]
        os.fchdir(snap)
        if "no_fd_census" not in abl:
            flags = fcntl.fcntl(snap, fcntl.F_GETFD)
            fcntl.fcntl(snap, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
            res["fd_census"] = fd_census(snap)
        if cfg["scenario"] == "TF5-G":
            res["pid_reuse"] = pid_reuse_witness(cfg, spawn, abl)
        else:
            exe = cfg["python"] if inject != "exec_missing" else "/nonexistent/s1b-spike-exe"
            argv = [exe, "-I", "-S", cfg["child"], cfg["child_mode"], cfg["token"]]
            t_start = time.monotonic()               # the unit deadline starts BEFORE the spawn attempt
            cfg["_deadline_at"] = t_start + cfg.get("unit_deadline", 3.0)
            try:
                handle = spawn(cfg, argv, inject, abl)
            finally:
                res["spawn_control_regained_s"] = round(time.monotonic() - t_start, 3)
            report = read_report(handle, max(0.0, cfg["_deadline_at"] - time.monotonic()))
            res["child_report"] = report
    except Refusal as r:
        primary = {"reason": r.reason, "detail": r.detail[:300]}
    except (subprocess.SubprocessError, OSError, RuntimeError) as e:
        primary = {"reason": "transport_spawn_failed" if not isinstance(e, RuntimeError) or "setup" in str(e)
                   else "injected_parent_failure", "detail": f"{type(e).__name__}: {e}"[:300]}
    except BaseException as e:  # noqa: BLE001 -- recorded, torn down, then reported as escaped
        escaped = f"{type(e).__name__}: {e}"
    finally:
        td = teardown(3.0, handle.pidfd if handle else None, "handle_only" in abl)
    res["teardown"] = td
    res["primary"] = primary
    res["escaped"] = escaped
    if td["remaining"] or td["any_child_or_zombie"]:
        res["outcome"] = "unit_teardown_incomplete"
    elif escaped:
        res["outcome"] = "escaped"
    elif primary:
        res["outcome"] = primary["reason"]
    else:
        res["outcome"] = "completed"
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
