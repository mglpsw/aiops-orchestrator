"""P2d (disposable experiment): correction round 3 discriminators for PR #364
(post-Ready Codex review 5370887198 on 4343dba). Nonzero exit on any mismatch.

  S  spawn/exec handshake under a unit deadline that starts BEFORE the spawn attempt:
     A = controlled Popen, B = explicit fork/exec (spike_reader.py), A1-A4 / B1-B4
  W  typed signal-wakeup channel (set_wakeup_fd): CM-W1..W6, signal during teardown,
     double signal
  M  controlled signal mask normalization (SIGTERM/SIGINT/SIGHUP blocked at entry)

Every row records: proposition, positive or negative, ablation, observed difference.
Usage:
  p2d_spawn_handshake.py harness <python> <out.json>
  p2d_spawn_handshake.py reader <cfg-json>        (internal role)
"""
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import spike_reader as R  # noqa: E402

CONTROLLED = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
ECHO_CHILD = ("import os,select,sys,json,time\n"
              "print(json.dumps({'fds_post_exec':[]}),flush=True)\n"
              "end=time.monotonic()+2.0; got=b''\n"
              "while time.monotonic()<end:\n"
              "  r,_,_=select.select([0],[],[],max(0,end-time.monotonic()))\n"
              "  if not r: break\n"
              "  b=os.read(0,64)\n"
              "  if not b: break\n"
              "  got+=b\n"
              "print(json.dumps({'stdin_bytes':got.hex()}),flush=True)\n")


class TerminationRequested(BaseException):
    pass


# -- reader role ---------------------------------------------------------------------------

def _wakeup_census(r, w, abl):
    """Typed census including the signal-wakeup roles (unless the old allowlist is ablated)."""
    allowed = {0: ({"chr", "fifo"}, "r"), 1: ({"fifo"}, "w"), 2: ({"fifo"}, "w")}
    if "old_allowlist" not in abl:
        allowed[r] = ({"fifo"}, "r")   # signal_wakeup_read
        allowed[w] = ({"fifo"}, "w")   # signal_wakeup_write
    seen = {}
    for name in os.listdir("/proc/self/fd"):
        fd = int(name)
        try:
            d = R._describe(fd)
        except OSError:
            continue
        rule = allowed.get(fd)
        if rule is None or d["object_type"] not in rule[0] or d["access"] != rule[1] or d["o_path"]:
            raise R.Refusal("reader_fd_capability_unexpected", f"fd {fd}: {d}")
        if fd in (r, w):
            import fcntl
            nb = bool(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_NONBLOCK)
            if not (nb and d["cloexec"]):
                raise R.Refusal("reader_fd_capability_unexpected", f"wakeup fd {fd} nonblock={nb} cloexec={d['cloexec']}")
        seen[fd] = d
    return seen


def reader(cfg):
    abl = set(cfg.get("ablations", []))
    state = {"signals": []}
    res, handle, outcome, primary, r, w = {}, None, "completed", None, None, None

    def on_signal(signum, _frame):          # frozen mechanism: record only, never raise
        state["signals"].append(int(signum))
        if "raising_handler" in abl and len(state["signals"]) == 1:
            raise TerminationRequested(signal.Signals(signum).name)

    try:
        old = signal.set_wakeup_fd(-1)
        if old != -1:
            raise R.Refusal("reader_signal_wakeup_preexisting")          # fail-closed, never adopted
        if "no_mask_normalize" not in abl:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROLLED)      # B-LIF-10
        blocked = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []) if s in CONTROLLED)
        res["controlled_blocked_after_normalization"] = blocked
        if blocked and "no_mask_normalize" not in abl:
            raise R.Refusal("reader_signal_mask_not_normalized")
        flags = os.O_CLOEXEC | (0 if "blocking_wakeup" in abl else os.O_NONBLOCK)
        r, w = os.pipe2(flags)
        if "wakeup_inheritable" in abl:
            os.set_inheritable(w, True)
        for s in CONTROLLED:
            signal.signal(s, on_signal)
        if "shared_channel" not in abl:
            signal.set_wakeup_fd(w, warn_on_full_buffer=False)
        R._prctl(R.PR_SET_CHILD_SUBREAPER, 1)
        R._prctl(R.PR_SET_NO_NEW_PRIVS, 1)
        if "no_census" not in abl:
            _wakeup_census(r, w, abl)
        res["wakeup_fds"] = [r, w]
        cfg["_deadline_at"] = time.monotonic() + cfg.get("unit_deadline", 5.0)
        if cfg.get("echo_child"):
            cfg["stdin_pipe"] = True
            argv = [cfg["python"], "-I", "-S", "-c", ECHO_CHILD, cfg["token"]]
        else:
            argv = [cfg["python"], "-I", "-S", cfg["child"], cfg.get("child_mode", "hold"), cfg["token"]]
        handle = R.spawn_B(cfg, argv, None, abl & {"B_no_close"})
        res["child_report"] = R.read_report(handle, 5.0)
        if "shared_channel" in abl:
            os.set_blocking(handle.in_fd, False)
            signal.set_wakeup_fd(handle.in_fd, warn_on_full_buffer=False)   # CM-W6: wakeup into the protocol
        sys.stdout.write("READY " + json.dumps({"pid": os.getpid()}) + "\n")
        sys.stdout.flush()
        end = time.monotonic() + cfg.get("linger", 3.0)
        watch = [r] if "shared_channel" not in abl else []
        while time.monotonic() < end:
            rr, _, _ = select.select(watch, [], [], max(0.0, end - time.monotonic()))
            if r in rr:
                os.read(r, 4096)
                outcome, primary = "terminated", "unit_terminated_by_signal"
                break
        if cfg.get("echo_child"):
            buf = b""
            while b"stdin_bytes" not in buf:
                rr, _, _ = select.select([handle.out_fd], [], [], 3.0)
                if not rr:
                    break
                chunk = os.read(handle.out_fd, 4096)
                if not chunk:
                    break
                buf += chunk
            for ln in buf.decode().splitlines():
                if "stdin_bytes" in ln:
                    res["child_stdin_bytes"] = json.loads(ln)["stdin_bytes"]
    except TerminationRequested as t:
        outcome = f"terminated_raising:{t}"
    except R.Refusal as e:
        outcome, primary = "refused", e.reason
    except ValueError as e:                  # set_wakeup_fd on a blocking fd
        outcome, primary = "refused", f"ValueError: {e}"
    finally:
        if cfg.get("signal_during_teardown"):
            os.kill(os.getpid(), signal.SIGTERM)       # CM-R3-07: a signal arrives as teardown starts
        td = R.teardown(3.0, handle.pidfd if handle else None, False)
        res["teardown"] = td
        # wakeup channel lifecycle (CM-W5)
        if w is not None:
            if "close_while_registered" not in abl:
                signal.set_wakeup_fd(-1)
            os.close(w)
            sentinel_path = cfg["sentinel"]
            spare = os.open(sentinel_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            if spare != w:
                os.dup2(spare, w, inheritable=False)
                os.close(spare)
            os.kill(os.getpid(), signal.SIGTERM)       # a later signal: where does the wakeup byte go?
            time.sleep(0.1)
            res["sentinel_bytes_after_close"] = os.path.getsize(sentinel_path)
            os.close(w)
        if r is not None:
            os.close(r)
    res.update(outcome=outcome, primary=primary, signals=state["signals"])
    sys.stdout.write(json.dumps(res) + "\n")
    sys.stdout.flush()


# -- harness role --------------------------------------------------------------------------

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


def run_spike_reader(py, mech, inject=None, extra=None):
    token = "s1bp2d-" + uuid.uuid4().hex
    base = tempfile.mkdtemp(prefix="s1b-p2d-")
    os.mkdir(os.path.join(base, "snapshot"))
    snap = os.open(os.path.join(base, "snapshot"), os.O_RDONLY | os.O_DIRECTORY)
    os.set_inheritable(snap, True)
    ro = os.open("/dev/null", os.O_RDONLY)
    cfg = {"mechanism": mech, "scenario": "handshake", "inject": inject, "child_mode": "report", "ablations": [],
           "token": token, "python": py, "child": os.path.join(HERE, "spike_child.py"), "snapshot_fd": snap,
           "snapshot_path": os.path.join(base, "snapshot"), "as_limit": 4 * 1024 ** 3, "unit_deadline": 1.0,
           "stall_s": 3.0}
    cfg.update(extra or {})
    t = time.monotonic()
    p = subprocess.run([py, "-I", "-S", os.path.join(HERE, "spike_reader.py"), json.dumps(cfg)], stdin=ro,
                       capture_output=True, pass_fds=(snap,), timeout=30)
    wall = time.monotonic() - t
    os.close(snap)
    os.close(ro)
    time.sleep(0.2)
    surv = survivors(token)
    res = json.loads(p.stdout.decode().strip().splitlines()[-1])
    return {"outcome": res["outcome"], "primary": (res.get("primary") or {}).get("reason"),
            "spawn_control_regained_s": res.get("spawn_control_regained_s"), "reader_wall_s": round(wall, 3),
            "survivors": len(surv)}


def run_reader(py, *, abl=(), block=None, send=(), echo=False, signal_during_teardown=False, linger=3.0,
               child_mode="hold"):
    token = "s1bp2d-" + uuid.uuid4().hex
    sentinel = tempfile.mktemp(prefix="s1b-p2d-sentinel-")
    cfg = {"python": py, "child": os.path.join(HERE, "spike_child.py"), "token": token, "ablations": list(abl),
           "as_limit": 4 * 1024 ** 3, "unit_deadline": 5.0, "linger": linger, "echo_child": echo,
           "child_mode": child_mode, "signal_during_teardown": signal_during_teardown, "sentinel": sentinel}

    def pre():
        if block:
            signal.pthread_sigmask(signal.SIG_BLOCK, block)    # the launcher starts the reader with these blocked

    ro = os.open("/dev/null", os.O_RDONLY)
    p = subprocess.Popen([py, "-I", "-S", os.path.abspath(__file__), "reader", json.dumps(cfg)], stdin=ro,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=pre)
    os.close(ro)
    first = p.stdout.readline().decode()
    t_sent = None
    if first.startswith("READY") and send:
        time.sleep(0.2)
        t_sent = time.monotonic()
        for s in send:
            os.kill(p.pid, s)
            time.sleep(0.001)
    out, err = p.communicate(timeout=30)
    t_end = time.monotonic()
    lines = [ln for ln in (first + out.decode()).splitlines() if ln.startswith("{")]
    res = json.loads(lines[-1]) if lines else {"outcome": "NO_RESULT", "stderr": err.decode()[-300:]}
    time.sleep(0.2)
    res["survivors"] = len(survivors(token))
    res["seconds_from_signal_to_reader_exit"] = round(t_end - t_sent, 3) if t_sent else None
    return res


RESULTS, FAILS = [], []


def row(rid, proposition, kind, observed, ok, claim_limit=""):
    RESULTS.append({"id": rid, "proposition": proposition, "kind": kind, "verdict": "PASS" if ok else "FAIL",
                    "observed": observed, "claim_limit": claim_limit})
    if not ok:
        FAILS.append(rid)
    print(f"{'PASS' if ok else 'FAIL':5} {rid:34} {kind:9} {json.dumps(observed)[:170]}", flush=True)


def harness(py, out_path):
    # ---- S: spawn/exec handshake under a unit deadline of 1.0s (child pre-exec stall 3.0s)
    for mech in ("A", "B"):
        o = run_spike_reader(py, mech)
        row(f"S-{mech}1-normal", "spawn completes within the unit deadline", "positive", o,
            o["outcome"] == "completed" and o["survivors"] == 0)
        o = run_spike_reader(py, mech, "setup_exception")
        row(f"S-{mech}3-setup-error", "setup failure before exec is bounded and typed", "negative", o,
            o["outcome"] == "transport_spawn_failed" and o["spawn_control_regained_s"] < 1.0 and o["survivors"] == 0)
        o = run_spike_reader(py, mech, "exec_missing")
        row(f"S-{mech}4-exec-missing", "exec failure is bounded and typed", "negative", o,
            o["outcome"] == "transport_spawn_failed" and o["spawn_control_regained_s"] < 1.0 and o["survivors"] == 0)
    o = run_spike_reader(py, "A", "pre_exec_stall")
    row("S-A2-pre-exec-stall", "A: the caller regains control only when the Popen handshake ends", "negative", o,
        o["spawn_control_regained_s"] >= 2.9,
        "demonstrates A does NOT satisfy B-RES-02 standalone: control regained after the stall, not at the 1.0s deadline")
    o = run_spike_reader(py, "B", "pre_exec_stall")
    row("S-B2-pre-exec-stall", "B: the unit deadline covers the exec handshake; child killed and reaped", "negative", o,
        o["outcome"] == "unit_deadline" and o["spawn_control_regained_s"] <= 1.3 and o["survivors"] == 0)
    o = run_spike_reader(py, "B", "pre_exec_stall", {"handshake_deadline_off": True})
    row("S-B2-ablation-no-handshake-deadline", "ablation: B without the handshake deadline waits for the stall",
        "ablation", o, o["spawn_control_regained_s"] >= 2.9)

    # ---- W: typed signal-wakeup channel (dedicated reader, mechanism B)
    o = run_reader(py, send=[signal.SIGTERM])
    row("W-POS-dedicated-channel", "wakeup fds typed+admitted; not inherited; SIGTERM → wakeup → teardown", "positive",
        {k: o.get(k) for k in ("outcome", "survivors", "signals", "sentinel_bytes_after_close")} |
        {"child_fds": [d["fd"] for d in o.get("child_report", {}).get("fds_post_exec", [])]},
        o["outcome"] == "terminated" and o["survivors"] == 0
        and [d["fd"] for d in o["child_report"]["fds_post_exec"]] == [0, 1, 2] and o["sentinel_bytes_after_close"] == 0)
    o = run_reader(py, abl=["old_allowlist"])
    row("CM-W1W2-old-allowlist", "the pre-round-3 closed fd contract refuses a conforming wakeup implementation",
        "negative", {k: o.get(k) for k in ("outcome", "primary")}, o["primary"] == "reader_fd_capability_unexpected")
    o = run_reader(py, abl=["wakeup_inheritable"])
    row("CM-W3-census-refuses-inheritable-wakeup", "the typed census refuses a non-CLOEXEC wakeup endpoint", "negative",
        {k: o.get(k) for k in ("outcome", "primary")}, o["primary"] == "reader_fd_capability_unexpected")
    o = run_reader(py, abl=["wakeup_inheritable", "B_no_close", "no_census"])
    row("CM-W3-wakeup-inherited-by-git", "ablation: non-CLOEXEC wakeup fd + no child-side close leaks into Git",
        "ablation", {"child_fds": [d["fd"] for d in o.get("child_report", {}).get("fds_post_exec", [])],
                     "wakeup_fds": o.get("wakeup_fds")},
        o.get("wakeup_fds", [None, None])[1] in [d["fd"] for d in o.get("child_report", {}).get("fds_post_exec", [])],
        "the census refuses it too when not ablated (non-CLOEXEC wakeup write fd)")
    o = run_reader(py, abl=["blocking_wakeup"])
    row("CM-W4-blocking-wakeup", "a blocking wakeup endpoint is refused", "negative",
        {k: o.get(k) for k in ("outcome", "primary")}, o["outcome"] == "refused")
    o = run_reader(py, abl=["close_while_registered"], send=[signal.SIGTERM])
    row("CM-W5-close-while-registered", "ablation: closing the write end while registered sends later wakeups "
        "into whatever reuses the number", "ablation", {k: o.get(k) for k in ("sentinel_bytes_after_close",)},
        o["sentinel_bytes_after_close"] > 0,
        "the positive row (W-POS) shows set_wakeup_fd(-1) before close leaves the sentinel at 0 bytes")
    o = run_reader(py, echo=True, send=[signal.SIGTERM], linger=1.0)
    row("CM-W6-dedicated-positive", "dedicated channel: no wakeup byte reaches the protocol", "positive",
        {k: o.get(k) for k in ("child_stdin_bytes", "outcome")}, o.get("child_stdin_bytes") == "")
    o = run_reader(py, abl=["shared_channel"], echo=True, send=[signal.SIGTERM], linger=1.0)
    row("CM-W6-shared-with-protocol", "ablation: wakeup shared with the protocol corrupts the request channel",
        "ablation", {k: o.get(k) for k in ("child_stdin_bytes", "outcome")},
        bool(o.get("child_stdin_bytes")) and "0f" in o.get("child_stdin_bytes", ""))
    o = run_reader(py, signal_during_teardown=True)
    row("CM-R3-07-signal-during-teardown", "non-raising handler: a signal at teardown does not abort it", "negative",
        {k: o.get(k) for k in ("survivors", "signals", "outcome")}, o["survivors"] == 0)
    o = run_reader(py, abl=["raising_handler"], signal_during_teardown=True)
    row("CM-R3-07-ablation-raising-handler", "ablation: a raising handler aborts the teardown", "ablation",
        {k: o.get(k) for k in ("survivors", "outcome")}, o.get("survivors", 0) > 0)
    o = run_reader(py, send=[signal.SIGTERM, signal.SIGTERM])
    row("CM-R3-08-double-signal", "double signal: teardown once, zero survivors", "negative",
        {k: o.get(k) for k in ("survivors", "signals", "outcome")},
        o["survivors"] == 0 and o["outcome"] == "terminated" and len(o["signals"]) >= 1)

    # ---- M: controlled signal mask (launcher starts the reader with the signal blocked)
    for s, cm in ((signal.SIGTERM, "CM-R3-04"), (signal.SIGINT, "CM-R3-05"), (signal.SIGHUP, "CM-R3-06")):
        o = run_reader(py, block=[s], send=[s], linger=3.0)
        row(f"{cm}-{s.name}-blocked-normalized", f"{s.name} blocked at entry → normalized → delivered → teardown",
            "negative", {k: o.get(k) for k in ("outcome", "controlled_blocked_after_normalization", "survivors",
                                               "seconds_from_signal_to_reader_exit")},
            o["outcome"] == "terminated" and o["controlled_blocked_after_normalization"] == [] and o["survivors"] == 0
            and o["seconds_from_signal_to_reader_exit"] < 1.5)
        o = run_reader(py, abl=["no_mask_normalize"], block=[s], send=[s], linger=3.0)
        row(f"{cm}-{s.name}-ablation-no-normalize", f"ablation: {s.name} stays pending; no wakeup; reader lingers",
            "ablation", {k: o.get(k) for k in ("outcome", "controlled_blocked_after_normalization",
                                               "seconds_from_signal_to_reader_exit")},
            o["outcome"] == "completed" and o["controlled_blocked_after_normalization"] == [int(s)]
            and o["seconds_from_signal_to_reader_exit"] >= 2.5)

    json.dump(RESULTS, open(out_path, "w"), indent=1)
    print(f"rows={len(RESULTS)} failures={len(FAILS)} {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    if sys.argv[1] == "reader":
        reader(json.loads(sys.argv[2]))
    else:
        sys.exit(harness(sys.argv[2], sys.argv[3]))
