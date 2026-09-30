"""P2c (disposable experiment): external termination of the dedicated reader (S1B-REV-02).

Reuses spike_reader.py (its mechanism-A spawn path). Spawns the stand-in Git child with mechanism A in
`grandchild` mode (the child forks a grandchild that calls setsid()), prints
`READY <json>` once the child is alive, then lingers so the harness can signal THIS
process from outside. Options (JSON argv[1]):
  handlers   install SIGTERM/SIGINT/SIGHUP handlers: the FIRST signal raises
             TerminationRequested (BaseException) at the next bytecode boundary; any
             later signal is only recorded, so it cannot interrupt the teardown
  pdeathsig  the child sets PR_SET_PDEATHSIG(SIGKILL) in preexec (plus the rlimit)
  linger     seconds to wait after READY before a normal teardown
"""
import json
import os
import resource
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spike_reader as R  # noqa: E402

PR_SET_PDEATHSIG = 1


class TerminationRequested(BaseException):
    pass


def main() -> None:
    cfg = json.loads(sys.argv[1])
    state = {"signals": []}

    def on_signal(signum, _frame):
        state["signals"].append(signum)
        if len(state["signals"]) == 1:
            raise TerminationRequested(signal.Signals(signum).name)

    if cfg.get("handlers"):
        for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(s, on_signal)
    R._prctl(R.PR_SET_CHILD_SUBREAPER, 1)
    R._prctl(R.PR_SET_NO_NEW_PRIVS, 1)
    limit = 4 * 1024 ** 3

    def pre():
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        if cfg.get("pdeathsig"):
            R._prctl(PR_SET_PDEATHSIG, signal.SIGKILL)

    res, handle, outcome = {"pid_seen_by_self": os.getpid()}, None, "completed"
    try:
        argv = [cfg["python"], "-I", "-S", cfg["child"], "grandchild", cfg["token"]]
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             close_fds=True, pass_fds=(), env={"PATH": "/usr/bin:/bin"}, cwd=None, preexec_fn=pre)
        handle = R.Handle(p.pid, os.pidfd_open(p.pid), p.stdout.fileno(), p)
        R.read_report(handle, 5.0)
        sys.stdout.write("READY " + json.dumps({"reader_pid": os.getpid()}) + "\n")
        sys.stdout.flush()
        time.sleep(cfg.get("linger", 4.0))
    except TerminationRequested as t:
        outcome = f"terminated:{t}"
    finally:
        td = R.teardown(3.0, handle.pidfd if handle else None, False)
    res.update(outcome=outcome, teardown=td, signals=state["signals"])
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
