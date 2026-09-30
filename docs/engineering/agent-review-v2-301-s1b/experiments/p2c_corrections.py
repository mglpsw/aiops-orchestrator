"""P2c (disposable experiment): discriminators for the correction round of PR #364
(independent review of b4a572a). Nonzero exit on any expectation mismatch.

  REV01  what an in-child observation point exists in mechanism A (Popen)?
  REV02  external termination of the reader: SIGTERM/SIGKILL, handler, PDEATHSIG,
         reader as init of a private PID namespace
  REV03  can MSG_PEEK serve on the Popen pipes? what do buffered reads consume?
  REV06  does safe.directory guard S1-B's explicit-GIT_DIR invocation at all?
Usage: p2c_corrections.py <python> <out.json>
"""
import ctypes
import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import termios
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.argv[1]
RESULTS = {}
FAILS = []


def check(name, ok, detail):
    RESULTS[name] = {"verdict": "PASS" if ok else "FAIL", "detail": detail}
    if not ok:
        FAILS.append(name)
    print(f"{'PASS' if ok else 'FAIL':5} {name:44} {json.dumps(detail)[:150]}", flush=True)


# -- REV01 --------------------------------------------------------------------------------

def _stable_fd_census_code() -> str:
    # list candidate numbers, fstat each; the transient enumeration fd is gone by then
    # and is skipped; EVERY other live fd is retained (no range filter)
    return ("import os,json\n"
            "live=[]\n"
            "for n in os.listdir('/proc/self/fd'):\n"
            "  try:\n"
            "    os.fstat(int(n)); live.append(int(n))\n"
            "  except OSError:\n"
            "    pass\n"
            "print(json.dumps(sorted(live)))\n")


def _rev01_run(pass_extra: bool):
    extra = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)   # a known reader-side fd (CLOEXEC)

    def pre():
        live = []
        for n in os.listdir("/proc/self/fd"):
            try:
                os.fstat(int(n))
                live.append(int(n))
            except OSError:
                pass
        os.write(2, json.dumps({"preexec_fds": sorted(live)}).encode() + b"\n")

    p = subprocess.run([PY, "-I", "-S", "-c", _stable_fd_census_code()], capture_output=True, close_fds=True,
                       pass_fds=(extra,) if pass_extra else (), preexec_fn=pre, env={}, stdin=subprocess.DEVNULL)
    os.close(extra)
    preexec = json.loads(p.stderr.decode().splitlines()[0])["preexec_fds"]
    post = json.loads(p.stdout)
    return extra, preexec, post


def rev01() -> None:
    extra, preexec, post = _rev01_run(False)
    check("REV01_preexec_view_precedes_close_fds", extra in preexec and post == [0, 1, 2],
          {"known_reader_fd": extra, "fds_visible_at_preexec": preexec, "fds_after_exec": post,
           "meaning": "the known reader fd is visible at the only in-child hook of A (preexec), so that hook runs BEFORE "
                      "CPython's close_fds; after exec the stable census (no range filter) shows exactly [0,1,2]"})
    extra, preexec, post = _rev01_run(True)
    check("REV01_mutant_extra_inherited_detected", extra in post and post != [0, 1, 2],
          {"mutant": "pass_fds=(extra,)", "known_reader_fd": extra, "fds_after_exec": post,
           "meaning": "the post-exec witness turns RED when a leak is introduced (it discriminates)"})


# -- REV02 --------------------------------------------------------------------------------

def survivors(token):
    found = []
    for n in os.listdir("/proc"):
        if n.isdigit() and int(n) != os.getpid():
            try:
                if token.encode() in open(f"/proc/{n}/cmdline", "rb").read():
                    found.append(int(n))
            except OSError:
                pass
    for pid in found:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return found


def children_of(pid):
    out = []
    for n in os.listdir("/proc"):
        if n.isdigit():
            try:
                d = open(f"/proc/{n}/stat", "rb").read()
            except OSError:
                continue
            if int(d[d.rindex(b")") + 2:].split()[1]) == pid:
                out.append(int(n))
    return out


def term_run(name, *, sig, handlers=False, pdeathsig=False, pidns=False, expect_survivors):
    token = "s1bp2c-" + uuid.uuid4().hex
    cfg = {"python": PY, "child": os.path.join(HERE, "spike_child.py"), "token": token,
           "handlers": handlers, "pdeathsig": pdeathsig, "linger": 4.0}
    argv = [PY, "-I", "-S", os.path.join(HERE, "p2c_term_reader.py"), json.dumps(cfg)]
    if pidns:
        argv = ["unshare", "--user", "--map-root-user", "--pid", "--fork", "--mount-proc"] + argv
    ro = os.open("/dev/null", os.O_RDONLY)
    p = subprocess.Popen(argv, stdin=ro, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    os.close(ro)
    line = p.stdout.readline().decode()
    ready = line.startswith("READY ")
    target = p.pid
    if pidns:
        kids = children_of(p.pid)          # unshare --fork: the reader is unshare's child (pid 1 inside)
        target = kids[0] if kids else None
    time.sleep(0.3)                        # grandchild has called setsid by now
    if ready and target:
        os.kill(target, sig)
    try:
        rest = p.communicate(timeout=20)[0].decode()
    except subprocess.TimeoutExpired:
        p.kill()
        rest = p.communicate()[0].decode()
    time.sleep(0.3)
    surv = survivors(token)
    final = None
    for ln in rest.splitlines():
        if ln.startswith("{"):
            final = json.loads(ln)
    ok = ready and (len(surv) > 0) == expect_survivors
    check(name, ok, {"signal": signal.Signals(sig).name, "handlers": handlers, "pdeathsig": pdeathsig, "pidns": pidns,
                     "survivors": len(surv), "reader_final": final and {k: final[k] for k in ("outcome", "signals")}})


def rev02() -> None:
    term_run("REV02_T1_SIGTERM_no_handler", sig=signal.SIGTERM, expect_survivors=True)
    term_run("REV02_T2_SIGTERM_with_handler", sig=signal.SIGTERM, handlers=True, expect_survivors=False)
    term_run("REV02_T3_SIGKILL_with_handler", sig=signal.SIGKILL, handlers=True, expect_survivors=True)
    term_run("REV02_T4_SIGKILL_child_PDEATHSIG", sig=signal.SIGKILL, pdeathsig=True, expect_survivors=True)
    term_run("REV02_T5_SIGKILL_reader_is_pidns_init", sig=signal.SIGKILL, pidns=True, expect_survivors=False)
    term_run("REV02_T6_SIGTERM_pidns_init_no_handler", sig=signal.SIGTERM, pidns=True, expect_survivors=False)
    term_run("REV02_T7_SIGTERM_pidns_init_with_handler", sig=signal.SIGTERM, pidns=True, handlers=True,
             expect_survivors=False)


# -- REV03 --------------------------------------------------------------------------------

def remaining(fd):
    buf = bytearray(4)
    fcntl.ioctl(fd, termios.FIONREAD, buf)
    return int.from_bytes(buf, sys.byteorder)


def rev03() -> None:
    body = b"x" * 10
    frame = b"a" * 40 + b" blob 10\n" + body + b"\n"
    libc = ctypes.CDLL(None, use_errno=True)
    r, w = os.pipe()
    os.write(w, frame)
    buf = ctypes.create_string_buffer(1)
    rc = libc.recv(r, buf, 1, 0x2)  # MSG_PEEK
    err = ctypes.get_errno()
    check("REV03_msg_peek_on_pipe", rc == -1 and err == errno.ENOTSOCK, {"rc": rc, "errno": errno.errorcode.get(err)})
    os.close(r)
    os.close(w)

    def after(reader_name, fn):
        r, w = os.pipe()
        os.write(w, frame)
        os.close(w)
        header = fn(r)
        left = remaining(r)
        return {"reader": reader_name, "header_ok": header == frame[:frame.index(b"\n") + 1],
                "body_bytes_left_in_pipe": left, "expected_if_zero_prefetch": len(body) + 1}

    def buffered_readline(fd):
        return os.fdopen(os.dup(fd), "rb").readline()

    def big_read(fd):
        data = os.read(fd, 4096)
        return data[:data.index(b"\n") + 1]

    def bytewise(fd):
        out = b""
        while not out.endswith(b"\n") and len(out) < 160:
            out += os.read(fd, 1)
        return out

    for name, fn, zero_prefetch in (("BufferedReader.readline", buffered_readline, False),
                                     ("os.read(4096)", big_read, False),
                                     ("os.read(1) until LF", bytewise, True)):
        d = after(name, fn)
        ok = d["header_ok"] and ((d["body_bytes_left_in_pipe"] == d["expected_if_zero_prefetch"]) == zero_prefetch)
        check(f"REV03_consumption_{name}", ok, d)


# -- REV06 --------------------------------------------------------------------------------

def rev06() -> None:
    t = tempfile.mkdtemp(prefix="s1b-p2c-")
    subprocess.run(["git", "init", "-q", "--bare", f"{t}/r.git"], check=True)
    base = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "HOME": "/dev/null",
            "GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}

    def run(args, extra_env):
        p = subprocess.run(["git", *args], cwd=f"{t}/r.git", env={**base, **extra_env}, input=b"0" * 40 + b"\n",
                           capture_output=True)
        return p.returncode, (p.stdout + p.stderr).decode()[:90]

    disc = run(["rev-parse", "--git-dir"], {})
    expl = run(["cat-file", "--batch-check"], {"GIT_DIR": "."})
    expl_star = run(["-c", "safe.directory=*", "cat-file", "--batch-check"], {"GIT_DIR": "."})
    ver = subprocess.run(["git", "version"], capture_output=True, text=True).stdout.strip()
    check("REV06_safe_directory_scope", disc[0] != 0 and "dubious ownership" in disc[1] and expl[0] == 0
          and "missing" in expl[1] and expl_star[0] == 0,
          {"git": ver, "discovery_without_safe_dir": disc, "explicit_GIT_DIR_without_safe_dir": expl,
           "explicit_GIT_DIR_with_star": expl_star,
           "meaning": "the ownership heuristic guards repository DISCOVERY; S1-B's explicit GIT_DIR=. invocation is "
                      "not subject to it on this Git version"})


def main() -> int:
    rev01()
    rev02()
    rev03()
    rev06()
    json.dump(RESULTS, open(sys.argv[2], "w"), indent=1)
    print(f"checks={len(RESULTS)} failures={len(FAILS)} {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
