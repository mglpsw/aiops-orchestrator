"""P2 spawn spike -- stand-in for the contained Git child (disposable experiment).

Usage: spike_child.py <mode> <token>
  report      print one JSON observation line, then exit 0
  stall       never speak; sleep (a child that stalls before the protocol)
  grandchild  fork a grandchild that calls setsid() and sleeps, print report, sleep
  hold        print report, then sleep (alive until torn down)

The first thing done is to read /proc/self/limits and /proc/self/status, so a
limit applied after exec is observably absent. The token only marks argv so
the harness can find survivors from outside the reader.
"""
import json
import os
import sys
import time

_EARLY_LIMITS = open("/proc/self/limits").read()
_EARLY_STATUS = open("/proc/self/status").read()


def _as_soft() -> str:
    for line in _EARLY_LIMITS.splitlines():
        if line.startswith("Max address space"):
            return line.split()[3]
    return "?"


def _nnp() -> str:
    for line in _EARLY_STATUS.splitlines():
        if line.startswith("NoNewPrivs:"):
            return line.split()[1]
    return "?"


def _fds() -> list:
    out = []
    for name in sorted(os.listdir("/proc/self/fd"), key=int):
        fd = int(name)
        try:
            flags = int(open(f"/proc/self/fdinfo/{fd}").read().split("flags:")[1].split()[0], 8)
            target = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue  # the listdir descriptor itself
        out.append({"fd": fd, "flags_octal": oct(flags), "target": target})
    return out


def report() -> None:
    st = os.stat(".")
    sys.stdout.write(json.dumps({
        "nnp": _nnp(), "as_soft": _as_soft(), "cwd_dev": st.st_dev, "cwd_ino": st.st_ino,
        "fds_post_exec": _fds(), "pid": os.getpid(),
    }) + "\n")
    sys.stdout.flush()


def main() -> None:
    mode = sys.argv[1]
    if mode == "report":
        report()
        return
    if mode == "stall":
        time.sleep(60)
        return
    if mode == "grandchild":
        pid = os.fork()
        if pid == 0:
            os.setsid()
            time.sleep(60)
            os._exit(0)
        report()
        time.sleep(60)
        return
    if mode == "hold":
        report()
        time.sleep(60)
        return
    raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
