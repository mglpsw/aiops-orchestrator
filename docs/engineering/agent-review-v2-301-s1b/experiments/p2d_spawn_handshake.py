"""P2d (disposable experiment): discriminators for PR #364 correction rounds 3 and 3b.
Nonzero exit on any mismatch.

  S    spawn/exec handshake under a unit deadline that starts BEFORE the spawn attempt:
       A = controlled Popen, B = explicit fork/exec (spike_reader.py), A1-A4 / B1-B4
  W    typed signal-wakeup channel (set_wakeup_fd): CM-W1..W6
  M    controlled signal mask (blocked at entry, signal sent after READY): CM-R3-04..06
  R3B  round 3b (#301 5919204387): pending-at-entry signals (S1-S3, pidns), signal to the
       child in the fork->exec window (S4, S4b), signal to the reader during HANDSHAKE (S5),
       signal / double signal INSIDE teardown (S6, S7), SIGCHLD normalization (C1-C3),
       handshake timeout (H1) and a non-completing SIGKILL test double (H2), child
       bootstrap BaseException (U1)

Every row records proposition, kind and observation; every round-3b family is also
recorded as an evidence contract (injection_confirmed, positive control, negative
control, ablation or mutant, observed discriminator, claim boundary). A contract whose
injection is not confirmed is a FAIL, never a PASS.
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


SPAWN_ABL = {"B_no_close", "no_fork_block", "no_child_signal_reset", "child_unwind", "handshake_ignores_wakeup",
             "no_fork_restore_guard", "no_child_sigign_reset"}


def reader(cfg):
    abl = set(cfg.get("ablations", []))
    state = {"signals": [], "phase": "entry"}
    res, handle, outcome, primary, r, w, owner = {"reader_pid": os.getpid()}, None, "completed", None, None, None, {}

    def on_signal(signum, _frame):          # frozen mechanism: record only, never raise
        state["signals"].append([int(signum), state["phase"]])
        if "raising_handler" in abl and len(state["signals"]) == 1:
            raise TerminationRequested(signal.Signals(signum).name)

    if cfg.get("d_state_sim_s"):                      # R3B-O1: non-completing SIGKILL test double
        R._D_STATE["until"] = time.monotonic() + cfg["d_state_sim_s"]
    try:
        # simulated entry state (what a launcher or earlier in-process code left behind);
        # every injection is confirmed by read-back below, never assumed
        for s in cfg.get("self_pend_at_entry", []):
            signal.pthread_sigmask(signal.SIG_BLOCK, {s})
            os.kill(os.getpid(), s)
        if cfg.get("enter_sa_nocldwait"):
            R.set_sigchld_raw(0, R.SA_NOCLDWAIT)
        res["pending_at_entry"] = sorted(int(s) for s in signal.sigpending())
        res["sigchld_at_entry"] = R.sigchld_state()
        old = signal.set_wakeup_fd(-1)
        if old != -1:
            raise R.Refusal("reader_signal_wakeup_preexisting")          # fail-closed, never adopted
        # --- reader signal bootstrap (B-LIF-10 / B-LIF-11), round-3b order
        if "unblock_before_handlers" in abl:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROLLED)      # ablation: the round-3 order
        else:
            signal.pthread_sigmask(signal.SIG_BLOCK, CONTROLLED)        # 1. block CONTROLLED
        flags = os.O_CLOEXEC | (0 if "blocking_wakeup" in abl else os.O_NONBLOCK)
        r, w = os.pipe2(flags)                                          # 2. dedicated wakeup pipe
        if "wakeup_inheritable" in abl:
            os.set_inheritable(w, True)
        for s in CONTROLLED:
            signal.signal(s, on_signal)                                 # 3. non-raising handlers
        if "shared_channel" not in abl:
            signal.set_wakeup_fd(w, warn_on_full_buffer=False)          # 4. wakeup registration
        res["sigchld_after_normalization"] = R.normalize_sigchld(abl)   # 5. B-LIF-11 (+ read-back)
        R._prctl(R.PR_SET_CHILD_SUBREAPER, 1)
        R._prctl(R.PR_SET_NO_NEW_PRIVS, 1)
        if "no_census" not in abl:
            _wakeup_census(r, w, abl)
        res["wakeup_fds"] = [r, w]
        state["phase"] = "ready"                                        # 6. termination state ready
        if "no_mask_normalize" not in abl:
            signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROLLED)      # 7. unblock
        blocked = sorted(int(s) for s in signal.pthread_sigmask(signal.SIG_BLOCK, []) if s in CONTROLLED)
        res["controlled_blocked_after_normalization"] = blocked         # 8. read back
        if blocked and "no_mask_normalize" not in abl:
            raise R.Refusal("reader_signal_mask_not_normalized")
        if select.select([r], [], [], 0)[0]:                            # 9. inspect wakeup before any work
            os.read(r, 4096)
            raise R.Refusal("unit_terminated_by_signal", "controlled signal pending at reader entry")
        state["phase"] = "spawn"                                        # 10. admit spawn
        cfg["_deadline_at"] = time.monotonic() + cfg.get("unit_deadline", 5.0)
        cfg["_hard_end"] = cfg["_deadline_at"] + cfg.get("teardown_reserve", 3.0)   # unit envelope, fixed before fork
        cfg["_wakeup_read"], cfg["_wakeup_fds"] = r, (r, w)
        if cfg.get("echo_child"):
            cfg["stdin_pipe"] = True
            argv = [cfg["python"], "-I", "-S", "-c", ECHO_CHILD, cfg["token"]]
        else:
            argv = [cfg["python"], "-I", "-S", cfg["child"], cfg.get("child_mode", "hold"), cfg["token"]]
        handle = R.spawn_B(cfg, argv, cfg.get("inject"), abl & SPAWN_ABL, owner)
        state["phase"] = "running"
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
        primary = e.reason
        outcome = "terminated" if e.reason == "unit_terminated_by_signal" else "refused"
        res["primary_detail"] = e.detail[:200]
    except ValueError as e:                  # set_wakeup_fd on a blocking fd
        outcome, primary = "refused", f"ValueError: {e}"
    except OSError as e:                     # e.g. fork() failure (R3B-F1)
        outcome, primary = "refused", "transport_spawn_failed"
        res["primary_detail"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        if cfg.get("cleanup_marker"):
            with open(cfg["cleanup_marker"], "a") as f:          # R3B-U1: who runs the reader cleanup?
                f.write(f"{os.getpid()}\n")
        res["controlled_blocked_at_teardown"] = sorted(
            int(x) for x in signal.pthread_sigmask(signal.SIG_BLOCK, []) if x in CONTROLLED)
        if owner.get("pidfd") is not None:          # how did the child end, before teardown touches it?
            st = R._REAL_WAITID(os.P_PIDFD, owner["pidfd"], os.WEXITED | os.WNOHANG | os.WNOWAIT)
            res["child_status_before_teardown"] = None if st is None else {
                "code": {os.CLD_EXITED: "CLD_EXITED", os.CLD_KILLED: "CLD_KILLED", os.CLD_DUMPED: "CLD_DUMPED"}
                .get(st.si_code, st.si_code), "status": st.si_status}
        state["phase"] = "teardown"

        def on_phase(ph):
            state["phase"] = f"teardown:{ph}"
            if cfg.get("teardown_marker"):
                sys.stdout.write(f"TEARDOWN_PHASE {ph}\n")
                sys.stdout.flush()
                time.sleep(cfg.get("teardown_pause_s", 0.5))   # the harness signals while we are HERE

        try:
            budget = cfg["_hard_end"] - time.monotonic() if "_hard_end" in cfg else cfg.get("teardown_reserve", 3.0)
            td = R.teardown(max(0.0, budget), owner.get("pidfd"), False, on_phase=on_phase)
            res["teardown"] = td
            if (td["remaining"] or td["any_child_or_zombie"]) and "outcome_ignores_teardown" not in abl:
                res["dominant_reason"] = "unit_teardown_incomplete"       # §15: teardown dominates; primary kept
                outcome = "unit_teardown_incomplete"
            if "_hard_end" in cfg:
                res["unit_overrun_s"] = round(time.monotonic() - cfg["_hard_end"], 3)
        finally:
            state["phase"] = "cleanup"
            # wakeup channel lifecycle (CM-W5): unregister before close, even if teardown raised
            if w is not None:
                if "close_while_registered" not in abl:
                    signal.set_wakeup_fd(-1)
                os.close(w)
                sentinel_path = cfg["sentinel"]
                spare = os.open(sentinel_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                if spare != w:
                    os.dup2(spare, w, inheritable=False)
                    os.close(spare)
                state["phase"] = "probe_after_close"
                os.kill(os.getpid(), signal.SIGTERM)       # a later signal: where does the wakeup byte go?
                time.sleep(0.1)
                res["sentinel_bytes_after_close"] = os.path.getsize(sentinel_path)
                os.close(w)
            if r is not None:
                os.close(r)
    res.update(outcome=outcome, primary=primary, signals=state["signals"],
               owner={k: v for k, v in owner.items() if k != "pidfd"},
               d_state_double=dict(R._D_STATE, until=None) if cfg.get("d_state_sim_s") else None)
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
            "survivors": len(surv), "owner": res.get("owner"), "d_state_double": res.get("d_state_double"),
            "teardown_remaining": len(res["teardown"]["remaining"]), "teardown_signalled": res["teardown"]["signalled"],
            "pidfds_open_after": res["teardown"].get("pidfds_open_after"),
            "owner_pidfd_closed": res["teardown"].get("owner_pidfd_closed"),
            "unit_envelope_s": res.get("unit_envelope_s"), "unit_elapsed_s": res.get("unit_elapsed_s"),
            "child_sig_ign": (res.get("child_report") or {}).get("sig_ign")}


GREP = next(x for x in ("/usr/bin/grep", "/bin/grep") if os.path.exists(x))
SIGIGN_ARGV = [GREP, "-m1", "SigIgn", "/proc/self/status"]   # C program: reports what it inherited


def run_reader(py, *, abl=(), block=None, pend=None, sigchld_ign=False, send=(), send_on="READY",
               send_to="reader", echo=False, linger=3.0, child_mode="hold", pidns=False, extra=None):
    token = "s1bp2d-" + uuid.uuid4().hex
    sentinel = tempfile.mktemp(prefix="s1b-p2d-sentinel-")
    marker = tempfile.mktemp(prefix="s1b-p2d-cleanup-")
    cfg = {"python": py, "child": os.path.join(HERE, "spike_child.py"), "token": token, "ablations": list(abl),
           "as_limit": 4 * 1024 ** 3, "unit_deadline": 5.0, "linger": linger, "echo_child": echo,
           "child_mode": child_mode, "sentinel": sentinel, "cleanup_marker": marker}
    cfg.update(extra or {})

    def pre():
        # the launcher's state at the reader's entry; mask, pending set and SIG_IGN survive execve
        if block:
            signal.pthread_sigmask(signal.SIG_BLOCK, block)
        if pend:
            signal.pthread_sigmask(signal.SIG_BLOCK, pend)
            for s in pend:
                os.kill(os.getpid(), s)
        if sigchld_ign:
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    argv = [py, "-I", "-S", os.path.abspath(__file__), "reader", json.dumps(cfg)]
    if pidns:
        argv = ["unshare", "--user", "--map-root-user", "--pid", "--fork", "--mount-proc"] + argv
    ro = os.open("/dev/null", os.O_RDONLY)
    p = subprocess.Popen(argv, stdin=ro, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=pre)
    os.close(ro)
    seen, t_sent = "", None
    if send:
        while True:
            ln = p.stdout.readline().decode()
            seen += ln
            if not ln or ln.startswith("{"):
                break
            if ln.startswith(send_on):
                target = int(ln.split()[1]) if send_to == "child" else p.pid
                if send_on == "READY":
                    time.sleep(0.2)
                t_sent = time.monotonic()
                for s in send:
                    os.kill(target, s)
                    time.sleep(0.001)
                break
    out, err = p.communicate(timeout=40)
    t_end = time.monotonic()
    want = 1 if pidns else p.pid
    lines = [json.loads(ln) for ln in (seen + out.decode()).splitlines() if ln.startswith("{")]
    mine = [d for d in lines if d.get("reader_pid") == want]
    res = mine[-1] if mine else {"outcome": "NO_RESULT", "stderr_tail": err.decode()[-160:]}
    res["reader_returncode"] = p.returncode
    time.sleep(0.2)
    res["survivors"] = len(survivors(token))
    res["seconds_from_signal_to_reader_exit"] = round(t_end - t_sent, 3) if t_sent else None
    try:
        res["cleanup_marker_pids"] = [int(x) for x in open(marker).read().split()]
        os.unlink(marker)
    except OSError:
        res["cleanup_marker_pids"] = []
    res["reader_signals"] = [sg for sg, ph in res.get("signals", []) if ph != "probe_after_close"]
    res["reader_signal_phases"] = [ph for sg, ph in res.get("signals", []) if ph != "probe_after_close"]
    return res


RESULTS, FAILS, CONTRACTS = [], [], []


def row(rid, proposition, kind, observed, ok, claim_limit=""):
    RESULTS.append({"id": rid, "proposition": proposition, "kind": kind, "verdict": "PASS" if ok else "FAIL",
                    "observed": observed, "claim_limit": claim_limit})
    if not ok:
        FAILS.append(rid)
    print(f"{'PASS' if ok else 'FAIL':5} {rid:44} {kind:9} {json.dumps(observed)[:150]}", flush=True)


def contract(cid, proposition, injection_confirmed, positive, negative, ablation, discriminator, boundary):
    verdict = {r["id"]: r["verdict"] for r in RESULTS}
    refs = [x for x in (positive, negative, ablation) if x]
    ok = bool(injection_confirmed) and all(verdict.get(x) == "PASS" for x in refs)
    CONTRACTS.append({"id": cid, "proposition": proposition, "injection_confirmed": bool(injection_confirmed),
                      "positive_control": positive, "negative_control": negative, "ablation_or_mutant": ablation,
                      "observed_discriminator": discriminator, "claim_boundary": boundary,
                      "verdict": "PASS" if ok else "FAIL"})
    if not ok:
        FAILS.append(cid)
    print(f"{'PASS' if ok else 'FAIL':5} contract {cid:35} injection_confirmed={bool(injection_confirmed)}", flush=True)


def pick(o, *keys):
    return {k: o.get(k) for k in keys}


def child_fds(o):
    return [d["fd"] for d in o.get("child_report", {}).get("fds_post_exec", [])]


def harness(py, out_path):
    # ---- S: spawn/exec handshake under a unit deadline of 1.0s (child pre-exec stall 3.0s)
    for mech in ("A", "B"):
        o = run_spike_reader(py, mech)
        row(f"S-{mech}1-normal", "spawn completes within the unit deadline; the owner pidfd is closed once and no "
            "pidfd is left open", "positive", o,
            o["outcome"] == "completed" and o["survivors"] == 0 and o["pidfds_open_after"] == 0
            and o["owner_pidfd_closed"] is True)
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
    h1 = o = run_spike_reader(py, "B", "pre_exec_stall")
    row("S-B2-pre-exec-stall (R3B-H1)", "B: the unit deadline covers the exec handshake; SIGKILL via pidfd, then the "
        "bounded teardown reaps; zero survivors", "negative", o,
        o["outcome"] == "unit_deadline" and o["spawn_control_regained_s"] <= 1.3 and o["survivors"] == 0
        and o["teardown_remaining"] == 0)
    o = run_spike_reader(py, "B", "pre_exec_stall", {"handshake_deadline_off": True})
    row("S-B2-ablation-no-handshake-deadline", "ablation: B without the handshake deadline waits for the stall",
        "ablation", o, o["spawn_control_regained_s"] >= 2.9)
    contract("R3B-H1", "handshake timeout with a killable child is bounded by the unit deadline and torn down",
             h1["owner"] and h1["owner"].get("pidfd_open") == "ok", "S-B1-normal", "S-B2-pre-exec-stall (R3B-H1)",
             "S-B2-ablation-no-handshake-deadline", "control regained ~1.0s vs >=2.9s without the handshake deadline",
             "killable child (sleep); the stall is an injected pre-exec delay")
    h2 = o = run_spike_reader(py, "B", "pre_exec_stall", {"d_state_sim_s": 8.0, "teardown_reserve": 1.5})
    row("R3B-H2-non-completing-SIGKILL-bounded", "after the deadline, a child whose SIGKILL does not complete is "
        "reported unit_teardown_incomplete INSIDE the unit envelope fixed before fork (work 1.0s + teardown reserve "
        "1.5s); no blocking wait", "negative", o,
        o["outcome"] == "unit_teardown_incomplete" and o["primary"] == "unit_deadline"
        and o["unit_elapsed_s"] <= o["unit_envelope_s"] + 0.15
        and o["d_state_double"]["nohang_hidden"] > 0 and o["d_state_double"]["blocking_waits"] == 0,
        "test double: pidfd WNOHANG waits report 'still running' for 8s; qualifies the bound logic only, not real "
        "D-state or filesystem behaviour")
    o = run_spike_reader(py, "B", "pre_exec_stall", {"d_state_sim_s": 8.0, "teardown_reserve": 1.5,
                                                     "ablations": ["blocking_reap_after_deadline"]})
    row("R3B-H2-ablation-blocking-reap", "ablation: a blocking waitid after the deadline hangs for as long as the "
        "SIGKILL does not complete, far outside the unit envelope", "ablation", o,
        o["unit_elapsed_s"] >= 7.5 and o["d_state_double"]["blocking_waits"] >= 1)
    contract("R3B-H2", "no blocking wait after the handshake deadline; ownership passes to the bounded teardown",
             h2["d_state_double"] and h2["d_state_double"]["nohang_hidden"] > 0 and h2["teardown_signalled"] >= 1,
             "S-B2-pre-exec-stall (R3B-H1)", "R3B-H2-non-completing-SIGKILL-bounded", "R3B-H2-ablation-blocking-reap",
             "unit elapsed <= envelope (2.5s) with unit_teardown_incomplete vs >= 7.5s with the blocking reap",
             "test double for a non-completing SIGKILL; no real D-state process is fabricated")

    g1 = run_spike_reader(py, "B", None, {"raw_argv": SIGIGN_ARGV})
    o = run_spike_reader(py, "B", None, {"raw_argv": SIGIGN_ARGV, "ablations": ["no_child_sigign_reset"]})
    row("R3B-G1-child-sigign-reset", "the child bootstrap resets every SIG_IGN disposition: the exec'd C program "
        "starts with SigIgn=0 (CPython ignores SIGPIPE/SIGXFSZ at start-up)", "negative",
        pick(g1, "child_sig_ign", "outcome"), g1["child_sig_ign"] == "0000000000000000")
    row("R3B-G1-ablation-no-sigign-reset", "ablation: without the reset Git inherits SIGPIPE (bit 13) and SIGXFSZ "
        "(bit 25) as ignored", "ablation", pick(o, "child_sig_ign", "outcome"),
        o["child_sig_ign"] == "0000000001001000")
    contract("R3B-G1", "ChildBootstrapSignalMachinery: no inherited SIG_IGN reaches Git",
             g1["child_sig_ign"] is not None and o["child_sig_ign"] is not None, "S-B1-normal",
             "R3B-G1-child-sigign-reset",
             "R3B-G1-ablation-no-sigign-reset", "SigIgn 0 vs 0x1001000 after exec",
             "the exec target is grep (a CPython stand-in would re-ignore SIGPIPE/SIGXFSZ at its own start-up and "
             "mask the result); only the reader's own SIG_IGN set (CPython defaults) is exercised; a launcher-set "
             "SIG_IGN is reset by the same loop but is not injected here")
    o = run_spike_reader(py, "B", "fork_fails")
    row("R3B-F1-fork-failure-typed", "fork() failure: no child, typed transport_spawn_failed, mask restored",
        "negative", pick(o, "outcome", "primary", "survivors", "owner"),
        o["outcome"] == "transport_spawn_failed" and o["survivors"] == 0 and o["owner"] == {})

    # ---- W: typed signal-wakeup channel (dedicated reader, mechanism B)
    o = run_reader(py, send=[signal.SIGTERM])
    row("W-POS-dedicated-channel", "wakeup fds typed+admitted; not inherited; SIGTERM → wakeup → teardown", "positive",
        pick(o, "outcome", "survivors", "reader_signals", "sentinel_bytes_after_close") | {"child_fds": child_fds(o)},
        o["outcome"] == "terminated" and o["survivors"] == 0 and child_fds(o) == [0, 1, 2]
        and o["sentinel_bytes_after_close"] == 0 and o["teardown"]["pidfds_open_after"] == 0
        and o["teardown"].get("owner_pidfd_closed") is True)
    o = run_reader(py, abl=["old_allowlist"])
    row("CM-W1W2-old-allowlist", "the pre-round-3 closed fd contract refuses a conforming wakeup implementation",
        "negative", pick(o, "outcome", "primary"), o["primary"] == "reader_fd_capability_unexpected")
    o = run_reader(py, abl=["wakeup_inheritable"])
    row("CM-W3-census-refuses-inheritable-wakeup", "the typed census refuses a non-CLOEXEC wakeup endpoint", "negative",
        pick(o, "outcome", "primary"), o["primary"] == "reader_fd_capability_unexpected")
    def wakeup_leaked(o):
        wf = o.get("wakeup_fds") or [None, None]
        return wf[0] in child_fds(o) or wf[1] in child_fds(o)

    # three independent barriers keep the wakeup endpoints out of Git: CLOEXEC, the child-bootstrap
    # reset (explicit close of the child's copies) and the child pre-exec close loop; each row keeps ONE
    for rid, keep, abls in (
            ("CM-W3a-close-loop-alone", "the child pre-exec close loop",
             ["wakeup_inheritable", "no_child_signal_reset", "no_census"]),
            ("CM-W3b-cloexec-alone", "CLOEXEC", ["B_no_close", "no_child_signal_reset", "no_census"]),
            ("CM-W3d-reset-close-alone", "the child-bootstrap signal reset (explicit close)",
             ["wakeup_inheritable", "B_no_close", "no_census"])):
        o = run_reader(py, abl=abls)
        row(rid, f"barrier kept: {keep}; the wakeup endpoints do not reach Git", "negative",
            {"child_fds": child_fds(o), "wakeup_fds": o.get("wakeup_fds"), "ablations": abls},
            bool(child_fds(o)) and not wakeup_leaked(o))
    o = run_reader(py, abl=["wakeup_inheritable", "B_no_close", "no_child_signal_reset", "no_census"])
    row("CM-W3c-all-barriers-removed", "ablation: only with ALL three barriers (and the census) removed does a wakeup "
        "endpoint reach Git", "ablation", {"child_fds": child_fds(o), "wakeup_fds": o.get("wakeup_fds")},
        (o.get("wakeup_fds") or [None, None])[1] in child_fds(o),
        "the census refuses the non-CLOEXEC endpoint when not ablated (CM-W3)")
    o = run_reader(py, abl=["blocking_wakeup"])
    row("CM-W4-blocking-wakeup", "a blocking wakeup endpoint is refused", "negative",
        pick(o, "outcome", "primary"), o["outcome"] == "refused")
    o = run_reader(py, abl=["close_while_registered"], send=[signal.SIGTERM])
    row("CM-W5-close-while-registered", "ablation: closing the write end while registered sends later wakeups "
        "into whatever reuses the number", "ablation", pick(o, "sentinel_bytes_after_close"),
        o["sentinel_bytes_after_close"] > 0,
        "the positive row (W-POS) shows set_wakeup_fd(-1) before close leaves the sentinel at 0 bytes")
    o = run_reader(py, echo=True, send=[signal.SIGTERM], linger=1.0)
    row("CM-W6-dedicated-positive", "dedicated channel: no wakeup byte reaches the protocol", "positive",
        pick(o, "child_stdin_bytes", "outcome"), o.get("child_stdin_bytes") == "")
    o = run_reader(py, abl=["shared_channel"], echo=True, send=[signal.SIGTERM], linger=1.0)
    row("CM-W6-shared-with-protocol", "ablation: wakeup shared with the protocol corrupts the request channel",
        "ablation", pick(o, "child_stdin_bytes", "outcome"),
        bool(o.get("child_stdin_bytes")) and "0f" in o.get("child_stdin_bytes", ""))
    o = run_reader(py, send=[signal.SIGTERM, signal.SIGTERM])
    row("R3-double-signal-running", "double signal while RUNNING: one teardown, zero survivors", "negative",
        pick(o, "survivors", "reader_signals", "outcome"),
        o["survivors"] == 0 and o["outcome"] == "terminated" and len(o["reader_signals"]) >= 1)

    # ---- M: controlled signal blocked at entry, sent AFTER READY (round 3)
    for s, cm in ((signal.SIGTERM, "CM-R3-04"), (signal.SIGINT, "CM-R3-05"), (signal.SIGHUP, "CM-R3-06")):
        o = run_reader(py, block=[s], send=[s], linger=3.0)
        row(f"{cm}-{s.name}-blocked-normalized", f"{s.name} blocked at entry → normalized → delivered → teardown",
            "negative", pick(o, "outcome", "controlled_blocked_after_normalization", "survivors",
                             "seconds_from_signal_to_reader_exit"),
            o["outcome"] == "terminated" and o["controlled_blocked_after_normalization"] == [] and o["survivors"] == 0
            and o["seconds_from_signal_to_reader_exit"] < 1.5)
        o = run_reader(py, abl=["no_mask_normalize"], block=[s], send=[s], linger=3.0)
        row(f"{cm}-{s.name}-ablation-no-normalize", f"ablation: {s.name} stays pending; no wakeup; reader lingers",
            "ablation", pick(o, "outcome", "controlled_blocked_after_normalization",
                             "seconds_from_signal_to_reader_exit"),
            o["outcome"] == "completed" and int(s) in o["controlled_blocked_after_normalization"]
            and o["seconds_from_signal_to_reader_exit"] >= 2.5,
            "round 3b: bootstrap step 1 blocks all of CONTROLLED, so without step 7 the whole set stays blocked")

    # ---- R3B-S1..S3: controlled signal ALREADY PENDING at entry (the launcher blocked and sent it)
    for s, rid in ((signal.SIGTERM, "R3B-S1"), (signal.SIGINT, "R3B-S2"), (signal.SIGHUP, "R3B-S3")):
        pos = run_reader(py, pend=[s], linger=1.0)
        row(f"{rid}-{s.name}-pending-at-entry", f"{s.name} pending at entry is delivered only after handlers + wakeup "
            "exist → controlled termination before any spawn", "negative",
            pick(pos, "pending_at_entry", "outcome", "primary", "reader_signals", "reader_signal_phases", "owner",
                 "survivors"),
            int(s) in pos.get("pending_at_entry", []) and pos["outcome"] == "terminated"
            and pos["primary"] == "unit_terminated_by_signal" and pos["reader_signals"] == [int(s)]
            and pos["owner"] == {} and pos["survivors"] == 0)
        abl = run_reader(py, pend=[s], linger=1.0, abl=["unblock_before_handlers"])
        row(f"{rid}-{s.name}-ablation-unblock-before-handlers", f"ablation (round-3 order): the pending {s.name} is "
            "delivered to the default/Python disposition → uncontrolled exit", "ablation",
            pick(abl, "outcome", "reader_returncode", "stderr_tail"),
            abl["outcome"] in ("NO_RESULT",) or abl.get("outcome", "").startswith("escaped"))
        contract(rid, f"{s.name} pending at reader entry → handler/wakeup → controlled termination",
                 int(s) in pos.get("pending_at_entry", []), None, f"{rid}-{s.name}-pending-at-entry",
                 f"{rid}-{s.name}-ablation-unblock-before-handlers",
                 f"controlled 'terminated' vs uncontrolled exit (returncode {abl['reader_returncode']})",
                 "plain process domain; the pending signal is created by the launcher before execve")
    pos = run_reader(py, pidns=True, linger=1.0, extra={"self_pend_at_entry": [int(signal.SIGTERM)]})
    row("R3B-S1-pidns-pending-at-entry", "reader as PID-namespace init: pending SIGTERM with handlers installed first "
        "→ delivered → controlled termination", "negative",
        pick(pos, "pending_at_entry", "outcome", "reader_signals", "survivors"),
        15 in pos.get("pending_at_entry", []) and pos["outcome"] == "terminated" and pos["reader_signals"] == [15]
        and pos["survivors"] == 0)
    abl = run_reader(py, pidns=True, linger=1.0, abl=["unblock_before_handlers"],
                     extra={"self_pend_at_entry": [int(signal.SIGTERM)]})
    row("R3B-S1-pidns-ablation-silent-discard", "ablation (round-3 order) as PID-namespace init: the kernel discards "
        "the SIG_DFL SIGTERM; cancellation is silently lost and work proceeds", "ablation",
        pick(abl, "pending_at_entry", "outcome", "reader_signals"),
        15 in abl.get("pending_at_entry", []) and abl["outcome"] == "completed" and abl["reader_signals"] == [])
    contract("R3B-S1-pidns", "the V1 domain (reader = pidns init) needs handlers before unblock",
             15 in pos.get("pending_at_entry", []) and 15 in abl.get("pending_at_entry", []), None,
             "R3B-S1-pidns-pending-at-entry", "R3B-S1-pidns-ablation-silent-discard",
             "terminated vs completed with no signal recorded (silent discard)",
             "unprivileged userns + pidns; the pending signal is raised by the reader against itself at entry "
             "(fork() clears the pending set, so the launcher cannot pre-pend it through unshare --fork)")

    # ---- R3B-S4 / S4b: a signal addressed to the CHILD before exec must not reach the reader's machinery
    pos = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", send_to="child", linger=0.5,
                     extra={"inject": "pre_exec_stall", "stall_s": 0.8, "announce_fork": True})
    killed15 = {"code": "CLD_KILLED", "status": 15}
    row("R3B-S4-child-signal-after-reset", "SIGTERM to the child in its pre-exec bootstrap (after the reset): the child "
        "dies by SIG_DFL SIGTERM; no wakeup byte in the reader, no misattributed termination", "negative",
        pick(pos, "outcome", "primary", "reader_signals", "survivors", "child_status_before_teardown"),
        pos["primary"] == "transport_failed" and pos["reader_signals"] == [] and pos["survivors"] == 0
        and pos.get("child_status_before_teardown") == killed15)
    abl = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", send_to="child", linger=0.5,
                     abl=["no_child_signal_reset"], extra={"inject": "pre_exec_stall", "stall_s": 0.8,
                                                           "announce_fork": True})
    row("R3B-S4-ablation-no-child-reset", "ablation: the child runs the inherited reader handler and writes 0f into "
        "the reader's wakeup pipe → reader reports a termination it never received", "ablation",
        pick(abl, "outcome", "primary", "reader_signals"),
        abl["outcome"] == "terminated" and abl["reader_signals"] == [])
    contract("R3B-S4", "ReaderSignalMachinery != ChildBootstrapSignalMachinery (reset step)",
             pos.get("child_status_before_teardown") == killed15, None, "R3B-S4-child-signal-after-reset",
             "R3B-S4-ablation-no-child-reset", "no termination vs misattributed 'terminated' with zero reader signals",
             "the pre-exec stall is injected to make the window observable")
    pos = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", send_to="child", linger=0.5,
                     extra={"inject": "stall_before_reset", "stall_s": 0.8, "announce_fork": True})
    row("R3B-S4b-fork-window-blocked", "SIGTERM to the child BEFORE its reset: CONTROLLED is blocked across fork, so it "
        "stays pending until the reset and then takes SIG_DFL (the child dies by SIGTERM)", "negative",
        pick(pos, "outcome", "primary", "reader_signals", "survivors", "child_status_before_teardown"),
        pos["primary"] == "transport_failed" and pos["reader_signals"] == [] and pos["survivors"] == 0
        and pos.get("child_status_before_teardown") == killed15)
    abl = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", send_to="child", linger=0.5,
                     abl=["no_fork_block"], extra={"inject": "stall_before_reset", "stall_s": 0.8,
                                                   "announce_fork": True})
    row("R3B-S4b-ablation-no-fork-block", "ablation: without the fork critical section the inherited handler runs in "
        "the fork→reset window and writes into the reader's wakeup pipe", "ablation",
        pick(abl, "outcome", "primary", "reader_signals"),
        abl["outcome"] == "terminated" and abl["reader_signals"] == [])
    contract("R3B-S4b", "NoChildCanRunInheritedReaderHandlerBeforeReset (fork critical section)",
             pos.get("child_status_before_teardown") == killed15, None, "R3B-S4b-fork-window-blocked",
             "R3B-S4b-ablation-no-fork-block", "no termination vs misattributed 'terminated'",
             "the fork→reset window is widened artificially (stall_before_reset); structural claim, not a timing race")

    # ---- R3B-S5: a signal to the READER during HANDSHAKE preserves its cause
    pos = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", linger=0.5,
                     extra={"inject": "pre_exec_stall", "stall_s": 3.0, "unit_deadline": 2.5, "announce_fork": True})
    row("R3B-S5-reader-signal-in-handshake", "the handshake select watches signal_wakeup_read: termination_requested, "
        "not unit_deadline", "negative",
        pick(pos, "outcome", "primary", "reader_signals", "survivors", "seconds_from_signal_to_reader_exit"),
        pos["primary"] == "unit_terminated_by_signal" and pos["reader_signals"] == [15] and pos["survivors"] == 0
        and pos["seconds_from_signal_to_reader_exit"] < 1.5)
    abl = run_reader(py, send=[signal.SIGTERM], send_on="FORKED", linger=0.5, abl=["handshake_ignores_wakeup"],
                     extra={"inject": "pre_exec_stall", "stall_s": 3.0, "unit_deadline": 2.5, "announce_fork": True})
    row("R3B-S5-ablation-handshake-ignores-wakeup", "ablation: the signal is recorded but the cause becomes "
        "unit_deadline", "ablation", pick(abl, "primary", "reader_signals", "seconds_from_signal_to_reader_exit"),
        abl["primary"] == "unit_deadline" and abl["reader_signals"] == [15])
    contract("R3B-S5", "cause preservation: a controlled signal during HANDSHAKE is termination_requested",
             pos.get("reader_signal_phases") == ["spawn"], None, "R3B-S5-reader-signal-in-handshake",
             "R3B-S5-ablation-handshake-ignores-wakeup", "unit_terminated_by_signal vs unit_deadline",
             "phase 'spawn' = between fork and exec confirmation")

    # ---- R3B-S8: a signal arriving while CONTROLLED is blocked in FORK_CRITICAL
    pos = run_reader(py, linger=0.5, extra={"inject": "pre_exec_stall", "stall_s": 3.0, "unit_deadline": 2.0,
                                            "signal_in_fork_critical": True})
    row("R3B-S8-signal-in-fork-critical", "a signal raised while CONTROLLED is blocked (after fork, before pidfd) stays "
        "pending, is delivered at mask restore and is observed by the handshake", "negative",
        pick(pos, "primary", "reader_signals", "reader_signal_phases", "owner", "survivors"),
        pos["primary"] == "unit_terminated_by_signal" and pos["reader_signal_phases"] == ["spawn"]
        and 15 in pos["owner"].get("pending_before_restore", []) and pos["survivors"] == 0)
    abl = run_reader(py, linger=0.5, abl=["handshake_ignores_wakeup"],
                     extra={"inject": "pre_exec_stall", "stall_s": 3.0, "unit_deadline": 2.0,
                            "signal_in_fork_critical": True})
    row("R3B-S8-ablation-handshake-ignores-wakeup", "ablation: the delivered signal is recorded but the cause becomes "
        "unit_deadline", "ablation", pick(abl, "primary", "reader_signals"),
        abl["primary"] == "unit_deadline" and abl["reader_signals"] == [15])
    contract("R3B-S8", "READY_TO_FORK/FORK_CRITICAL → TERMINATION_REQUESTED is observed (at mask restore, by HANDSHAKE)",
             15 in pos["owner"].get("pending_before_restore", []), None, "R3B-S8-signal-in-fork-critical",
             "R3B-S8-ablation-handshake-ignores-wakeup", "unit_terminated_by_signal vs unit_deadline",
             "the signal is raised by the reader against itself while blocked; sigpending() confirms it before restore")

    # ---- R3B-F1: fork() failure restores the controlled mask
    pos = run_reader(py, linger=0.2, extra={"inject": "fork_fails"})
    row("R3B-F1-fork-failure-mask-restored", "fork() fails inside the critical section: typed refusal, no child, "
        "CONTROLLED unblocked again", "negative",
        pick(pos, "outcome", "primary", "primary_detail", "controlled_blocked_at_teardown", "owner"),
        pos["primary"] == "transport_spawn_failed" and "fork" in pos.get("primary_detail", "")
        and pos["controlled_blocked_at_teardown"] == [] and pos["owner"] == {})
    abl = run_reader(py, linger=0.2, abl=["no_fork_restore_guard"], extra={"inject": "fork_fails"})
    row("R3B-F1-ablation-no-restore-guard", "ablation: without the guard the mask stays blocked after the failed fork",
        "ablation", pick(abl, "primary", "controlled_blocked_at_teardown"),
        abl["controlled_blocked_at_teardown"] == [1, 2, 15])
    contract("R3B-F1", "a failed fork() leaves no blocked CONTROLLED mask behind",
             "injected fork() failure" in pos.get("primary_detail", ""), "R3B-F1-fork-failure-typed",
             "R3B-F1-fork-failure-mask-restored", "R3B-F1-ablation-no-restore-guard", "[] vs [1, 2, 15] blocked",
             "fork() failure is injected (EAGAIN) at the call site")

    # ---- R3B-O1: the final outcome is derived from the teardown result (§15 precedence)
    pos = run_reader(py, send=[signal.SIGTERM], linger=3.0, extra={"d_state_sim_s": 8.0, "teardown_reserve": 1.0})
    row("R3B-O1-teardown-dominates", "terminated by signal + a child whose SIGKILL does not complete: outcome "
        "unit_teardown_incomplete, primary unit_terminated_by_signal kept", "negative",
        pick(pos, "outcome", "primary", "dominant_reason", "unit_overrun_s", "survivors"),
        pos["outcome"] == "unit_teardown_incomplete" and pos["primary"] == "unit_terminated_by_signal"
        and pos.get("unit_overrun_s", 9) <= 0.15)
    abl = run_reader(py, send=[signal.SIGTERM], linger=3.0, abl=["outcome_ignores_teardown"],
                     extra={"d_state_sim_s": 8.0, "teardown_reserve": 1.0})
    row("R3B-O1-ablation-outcome-ignores-teardown", "ablation: the teardown failure is recorded but the outcome stays "
        "'terminated'", "ablation", pick(abl, "outcome", "primary"),
        abl["outcome"] == "terminated" and abl["teardown"]["remaining"] != [])
    contract("R3B-O1", "DominantOutcome derives from teardown; primary preserved",
             bool(pos.get("d_state_double") and pos["d_state_double"]["nohang_hidden"] > 0), None,
             "R3B-O1-teardown-dominates", "R3B-O1-ablation-outcome-ignores-teardown",
             "unit_teardown_incomplete vs terminated", "test double for a non-completing SIGKILL")

    # ---- R3B-S6 / S7: signals INSIDE teardown (synchronised on an observable teardown phase)
    pos = run_reader(py, send=[signal.SIGTERM], send_on="TEARDOWN_PHASE", linger=0.3,
                     extra={"teardown_marker": True})
    row("R3B-S6-signal-inside-teardown", "a signal delivered during the teardown scan does not abort teardown",
        "negative", pick(pos, "reader_signal_phases", "survivors", "outcome"),
        pos["reader_signal_phases"] == ["teardown:scan"] and pos["survivors"] == 0
        and pos.get("teardown", {}).get("remaining") == [])
    abl = run_reader(py, send=[signal.SIGTERM], send_on="TEARDOWN_PHASE", linger=0.3, abl=["raising_handler"],
                     extra={"teardown_marker": True})
    row("R3B-S6-ablation-raising-handler", "ablation: a raising handler aborts the teardown scan → survivor",
        "ablation", pick(abl, "survivors", "outcome", "reader_returncode"), abl["survivors"] > 0)
    contract("R3B-S6", "signal during TEARDOWN (inside the scan phase)", pos["reader_signal_phases"] == ["teardown:scan"],
             None, "R3B-S6-signal-inside-teardown", "R3B-S6-ablation-raising-handler",
             "0 survivors vs >0 survivors", "the teardown pauses at an observable marker so the signal lands inside it")
    pos = run_reader(py, send=[signal.SIGTERM, signal.SIGTERM], send_on="TEARDOWN_PHASE", linger=0.3,
                     extra={"teardown_marker": True})
    row("R3B-S7-double-signal-inside-teardown", "two signals during the teardown scan: teardown still completes once",
        "negative", pick(pos, "reader_signal_phases", "survivors"),
        len(pos["reader_signal_phases"]) >= 1 and set(pos["reader_signal_phases"]) == {"teardown:scan"}
        and pos["survivors"] == 0)
    abl = run_reader(py, send=[signal.SIGTERM, signal.SIGTERM], send_on="TEARDOWN_PHASE", linger=0.3,
                     abl=["raising_handler"], extra={"teardown_marker": True})
    row("R3B-S7-ablation-raising-handler", "ablation: a raising handler aborts on the first of the two", "ablation",
        pick(abl, "survivors", "outcome"), abl["survivors"] > 0)
    contract("R3B-S7", "double signal during TEARDOWN", set(pos["reader_signal_phases"]) == {"teardown:scan"}, None,
             "R3B-S7-double-signal-inside-teardown", "R3B-S7-ablation-raising-handler", "0 vs >0 survivors",
             f"two SIGTERMs 1ms apart; {len(pos['reader_signal_phases'])} handler invocation(s) observed "
             "(standard signals may coalesce while pending)")

    # ---- R3B-C: SIGCHLD reaping state (B-LIF-11); the child exits immediately, pidfd_open is delayed
    cexit = {"inject": "child_exit_immediately", "pidfd_delay_s": 0.2}
    base = run_reader(py, linger=0.2, extra=cexit)
    row("R3B-C3-child-exits-before-pidfd", "normalized reaping state: a child that exits before pidfd_open (0.2s "
        "parent delay) stays a zombie; pidfd_open works and waitid(WNOWAIT) observes the exit", "positive",
        pick(base, "owner", "sigchld_after_normalization"),
        base["owner"].get("pidfd_open") == "ok" and base["owner"].get("exited_before_handshake") is True)
    for rid, kw, desc in (("R3B-C1", {"sigchld_ign": True}, "SIGCHLD=SIG_IGN inherited across execve"),
                          ("R3B-C2", {"extra": {"enter_sa_nocldwait": True}}, "SA_NOCLDWAIT at reader entry")):
        extra = dict(kw.get("extra", {}))
        def runc(abl=()):
            return run_reader(py, linger=0.2, abl=list(abl), sigchld_ign=kw.get("sigchld_ign", False),
                              extra=extra | cexit)
        pos = runc()
        row(f"{rid}-normalized", f"{desc} → normalized (SIG_DFL, no SA_NOCLDWAIT, read back) → pidfd_open ok, "
            "exit status observable", "negative", pick(pos, "sigchld_at_entry", "sigchld_after_normalization", "owner"),
            pos["sigchld_after_normalization"] == {"handler": "SIG_DFL", "sa_nocldwait": False}
            and pos["owner"].get("pidfd_open") == "ok" and pos["owner"].get("exited_before_handshake") is True)
        abl = runc(["no_sigchld_normalize"])
        row(f"{rid}-ablation-not-normalized", f"ablation: {desc} kept → the child is auto-reaped → pidfd_open ESRCH",
            "ablation", pick(abl, "sigchld_at_entry", "owner", "primary"), abl["owner"].get("pidfd_open") == "ESRCH")
        at_entry = pos["sigchld_at_entry"]
        confirmed = at_entry["handler"] == "SIG_IGN" if rid == "R3B-C1" else at_entry["sa_nocldwait"] is True
        contract(rid, f"B-LIF-11 kills the {desc} countermodel", confirmed, "R3B-C3-child-exits-before-pidfd",
                 f"{rid}-normalized", f"{rid}-ablation-not-normalized", "pidfd_open ok vs ESRCH",
                 "Linux clears sa_flags at execve, so SA_NOCLDWAIT reaches the reader only through in-process state "
                 "(injected at entry here); SIG_IGN does survive execve" if rid == "R3B-C2" else
                 "SIG_IGN set by the launcher before execve")
    mut = run_reader(py, linger=0.2, abl=["sigchld_handler_only"],
                     extra={"enter_sa_nocldwait": True} | cexit)
    row("R3B-C2-mutant-handler-only-normalization", "mutant: resetting only the handler keeps SA_NOCLDWAIT; the "
        "read-back refuses before any fork", "ablation", pick(mut, "outcome", "primary", "owner"),
        mut["primary"] == "reader_sigchld_state_not_normalized" and mut["owner"] == {})

    # ---- R3B-U1: BaseException inside the child bootstrap
    pos = run_reader(py, linger=0.2, extra={"inject": "child_baseexception"})
    row("R3B-U1-child-baseexception-exits", "child bootstrap BaseException → bounded setup report → os._exit; the "
        "parent refuses and tears down; the reader cleanup runs once, in the reader", "negative",
        pick(pos, "outcome", "primary", "primary_detail", "cleanup_marker_pids", "reader_pid", "survivors"),
        pos["primary"] == "transport_spawn_failed" and "KeyboardInterrupt" in pos.get("primary_detail", "")
        and pos["cleanup_marker_pids"] == [pos["reader_pid"]] and pos["survivors"] == 0)
    abl = run_reader(py, linger=0.2, abl=["child_unwind"], extra={"inject": "child_baseexception"})
    row("R3B-U1-ablation-child-unwinds", "ablation: the child unwinds into the reader's control flow and runs the "
        "reader cleanup a second time, in the child", "ablation",
        pick(abl, "cleanup_marker_pids", "reader_pid", "primary"),
        len(abl["cleanup_marker_pids"]) >= 2 and any(x != abl.get("reader_pid") for x in abl["cleanup_marker_pids"]))
    contract("R3B-U1", "ForkChildFailure != ReaderControlFlow (execve or os._exit, no third exit)",
             "KeyboardInterrupt" in pos.get("primary_detail", ""), None, "R3B-U1-child-baseexception-exits",
             "R3B-U1-ablation-child-unwinds", "cleanup markers [reader] vs [reader, child]",
             "BaseException injected after the signal reset; the witness is the cleanup marker file")

    json.dump({"rows": RESULTS, "contracts": CONTRACTS}, open(out_path, "w"), indent=1)
    print(f"rows={len(RESULTS)} contracts={len(CONTRACTS)} failures={len(FAILS)} {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    if sys.argv[1] == "reader":
        reader(json.loads(sys.argv[2]))
    else:
        sys.exit(harness(sys.argv[2], sys.argv[3]))
