"""P2e (disposable, final freeze amendment, #301 5921013078): the two focal witnesses for
AB-1 and AB-2. ExperimentalEvidence != ImplementationQualification: this establishes the
freeze-level propositions only; qualification of the production bootstrap/outcome is B2.

AB-1  NPTLReservedSignal != ApplicationSignalCapability.
      Signals 32/33 are reserved by glibc/NPTL: libc sigaction refuses them (EINVAL) and
      Python's valid_signals() omits them, so they cannot be "canonicalized" by the
      application. CannotSafelyCanonicalizeReservedSignal -> VerifyCompatibleStateOrRefuse:
      before exec, the kernel-visible SigIgn/SigBlk bits of 32/33 must be clear, else a
      typed refusal. Countermodels AB1-CM1..CM4 put 32/33 into an ignored/blocked state
      through raw syscalls. Mutant: a libc-only check (application-addressable set only)
      reports success.
AB-2  FinalOutcomeIsDerivedAfterTeardown, linearized at the FINALIZATION_BARRIER (boundary
      adjudication #301 5921263805): after teardown, block CONTROLLED (the barrier), drain the
      wakeup channel, consume flags, then derive. A signal delivered before the barrier belongs
      to the unit (NOT COMPLETED, termination_request retained; with an incomplete teardown the
      dominant reason is unit_teardown_incomplete); one that becomes pending only after the
      barrier is outside it. Mutant: derive before blocking/draining. This is a focal model of
      the finalization order (real pipe + set_wakeup_fd + sigmask), not the P2d reader.

Usage: p2e_amendment_witness.py <out.json>     (x86_64 Linux syscall numbers)
"""
import ctypes
import json
import os
import signal
import sys

LIBC = ctypes.CDLL(None, use_errno=True)
SYS_RT_SIGACTION, SYS_RT_SIGPROCMASK = 13, 14
RESERVED = (32, 33)
RESULTS, FAILS = [], []


class KSigaction(ctypes.Structure):  # kernel struct sigaction (x86_64)
    _fields_ = [("handler", ctypes.c_ulong), ("flags", ctypes.c_ulong), ("restorer", ctypes.c_ulong),
                ("mask", ctypes.c_ulong)]


def row(rid, prop, kind, observed, ok, limit=""):
    RESULTS.append({"id": rid, "proposition": prop, "kind": kind, "observed": observed,
                    "verdict": "PASS" if ok else "FAIL", "claim_limit": limit})
    if not ok:
        FAILS.append(rid)
    print(f"{'PASS' if ok else 'FAIL':5} {rid:40} {json.dumps(observed)[:150]}", flush=True)


# -- AB-1 -------------------------------------------------------------------------------------

def status_bits(field):
    for ln in open("/proc/self/status"):
        if ln.startswith(field + ":"):
            return int(ln.split()[1], 16)
    raise RuntimeError(field)


def reserved_state_check():
    """The frozen rule: kernel-visible SigIgn/SigBlk bits of the reserved signals must be clear."""
    ign, blk = status_bits("SigIgn"), status_bits("SigBlk")
    bad = {s: {"ignored": bool(ign >> (s - 1) & 1), "blocked": bool(blk >> (s - 1) & 1)} for s in RESERVED}
    bad = {s: v for s, v in bad.items() if v["ignored"] or v["blocked"]}
    return ("refused", "reader_child_signal_state_unusable", bad) if bad else ("accepted", None, {})


def libc_only_check():
    """Mutant: verifies only what libc can address (the round-3c read-back); 32/33 are invisible to it."""
    old = ctypes.create_string_buffer(152)
    for s in signal.valid_signals():
        if s in (signal.SIGKILL, signal.SIGSTOP):
            continue
        if LIBC.sigaction(int(s), None, old) != 0:
            return ("error", str(int(s)), {})
    return ("accepted", None, {})


def in_child(entry):
    """Apply the entry state with raw syscalls in a fresh child, run both checks, report."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(r)
            applied = {}
            for sig, what in entry:
                if what == "ignore":
                    act = KSigaction(1, 0, 0, 0)
                    applied[f"ign{sig}"] = LIBC.syscall(SYS_RT_SIGACTION, sig, ctypes.byref(act), None, 8)
                else:
                    m = ctypes.c_ulong(1 << (sig - 1))
                    applied[f"blk{sig}"] = LIBC.syscall(SYS_RT_SIGPROCMASK, 0, ctypes.byref(m), None, 8)  # SIG_BLOCK
            out = {"applied": applied, "frozen": reserved_state_check(), "mutant_libc_only": libc_only_check(),
                   "glibc_sigaction_32_errno": (LIBC.sigaction(32, None, ctypes.create_string_buffer(152)),
                                                ctypes.get_errno())}
            os.write(w, json.dumps(out).encode())
        finally:
            os._exit(0)
    os.close(w)
    data = b""
    while True:
        b = os.read(r, 65536)
        if not b:
            break
        data += b
    os.close(r)
    os.waitpid(pid, 0)
    return json.loads(data)


def ab1():
    base = in_child([])
    row("AB1-POS-clean-state", "clean reserved state is accepted by the frozen check", "positive", base,
        base["frozen"][0] == "accepted" and base["glibc_sigaction_32_errno"] == [-1, 22])
    for rid, entry in (("AB1-CM1-32-ignored", [(32, "ignore")]), ("AB1-CM2-33-ignored", [(33, "ignore")]),
                       ("AB1-CM3-32-blocked", [(32, "block")]), ("AB1-CM4-33-blocked", [(33, "block")])):
        o = in_child(entry)
        applied_ok = all(v == 0 for v in o["applied"].values())
        row(rid, "unexpected reserved state is detected and refused before exec (typed)", "negative",
            {"applied": o["applied"], "frozen": o["frozen"]},
            applied_ok and o["frozen"][0] == "refused" and o["frozen"][1] == "reader_child_signal_state_unusable",
            "raw syscalls create the state; the check reads kernel-visible /proc/self/status bits")
        row(f"{rid}-mutant-libc-only", "mutant: the libc-addressable check pretends canonicalization succeeded",
            "mutant", {"mutant": o["mutant_libc_only"]}, applied_ok and o["mutant_libc_only"][0] == "accepted")


# -- AB-2 -------------------------------------------------------------------------------------

def derive(primary, close_failure, teardown_failure, termination):
    """§15: COMPLETED iff no primary, close, teardown failure and no termination request."""
    if termination and primary is None:
        primary = "unit_terminated_by_signal"
    if primary is None and close_failure is None and teardown_failure is None and not termination:
        return {"outcome": "COMPLETED"}
    dominant = ("unit_teardown_incomplete" if teardown_failure else "reader_descriptor_close_failed"
                if close_failure else primary)
    return {"outcome": "FailureOutcome", "dominant_reason": dominant, "primary_failure": primary,
            "descriptor_close_failure": close_failure, "teardown_failure": teardown_failure,
            "termination_request": {"signals": termination} if termination else None}


CONTROLLED = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}


def finalize_unit(mode, teardown_incomplete=False):
    """Boundary adjudication (#301 5921263805): FINALIZATION_BARRIER.
    Work has completed normally and teardown has finished its observations. Modes:
      barrier  - a controlled signal is DELIVERED before the barrier (handler + wakeup byte);
                 then block CONTROLLED (= FINALIZATION_BARRIER), drain wakeup, consume flags, derive
      mutant   - derive the outcome BEFORE blocking/draining; the same signal is then delivered
                 (still before the barrier) and is lost: the frozen outcome says COMPLETED
      late     - block CONTROLLED first; the signal becomes pending only AFTER the barrier and is
                 outside the unit (COMPLETED; the signal stays pending, never drained)"""
    seen = []
    r, w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
    prev = {sg: signal.signal(sg, lambda s, f: seen.append(int(s))) for sg in CONTROLLED}   # non-raising
    signal.set_wakeup_fd(w, warn_on_full_buffer=False)
    teardown_failure = {"remaining": ["stuck"]} if teardown_incomplete else None
    premature = None
    try:
        if mode == "mutant":
            premature = derive(None, None, teardown_failure, list(seen))       # frozen too early
        if mode in ("barrier", "mutant"):
            os.kill(os.getpid(), signal.SIGTERM)                               # delivered before the barrier
        signal.pthread_sigmask(signal.SIG_BLOCK, CONTROLLED)                   # FINALIZATION_BARRIER
        if mode == "late":
            os.kill(os.getpid(), signal.SIGTERM)                               # pending only after the barrier
        drained = b""
        while True:                                                            # drain signal_wakeup_read
            try:
                b = os.read(r, 64)
            except BlockingIOError:
                break
            if not b:
                break
            drained += b
        term = sorted(set(seen) | set(drained))                               # flags + wakeup bytes
        pending = sorted(int(x) for x in signal.sigpending())
        result = premature if mode == "mutant" else derive(None, None, teardown_failure, term)
        return {"mode": mode, "signals_seen": list(seen), "wakeup_bytes": list(drained),
                "pending_after_barrier": pending, "result": result}
    finally:
        signal.set_wakeup_fd(-1)
        if 15 in (int(x) for x in signal.sigpending()):
            signal.sigtimedwait({signal.SIGTERM}, 0)                           # discard the late signal
        signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROLLED)                 # witness cleanup only
        for sg, h in prev.items():
            signal.signal(sg, h)
        os.close(r)
        os.close(w)


def ab2():
    o = finalize_unit("barrier")
    row("AB2-BARRIER-delivered-before", "normal completion + teardown complete + signal delivered before the "
        "FINALIZATION_BARRIER → barrier, drain → NOT COMPLETED; termination_request retained", "positive", o,
        o["wakeup_bytes"] == [15] and o["result"]["outcome"] == "FailureOutcome"
        and o["result"]["primary_failure"] == "unit_terminated_by_signal"
        and o["result"]["termination_request"] == {"signals": [15]})
    o = finalize_unit("barrier", teardown_incomplete=True)
    row("AB2-BARRIER-delivered-before-incomplete-teardown", "same + incomplete teardown → dominant "
        "unit_teardown_incomplete; primary and termination_request retained", "positive", o,
        o["result"]["dominant_reason"] == "unit_teardown_incomplete"
        and o["result"]["primary_failure"] == "unit_terminated_by_signal"
        and o["result"]["termination_request"] == {"signals": [15]} and bool(o["result"]["teardown_failure"]))
    m = finalize_unit("mutant")
    row("AB2-MUT-derive-before-barrier", "mutant: the outcome derived before blocking/draining returns COMPLETED "
        "although a controlled signal was delivered before the barrier", "mutant", m,
        m["result"] == {"outcome": "COMPLETED"} and (15 in m["signals_seen"] or 15 in m["wakeup_bytes"]))
    o = finalize_unit("late")
    row("AB2-LATE-after-barrier", "a signal that becomes pending only after CONTROLLED is blocked is outside the "
        "unit: COMPLETED, nothing drained, the signal stays pending", "negative", o,
        o["result"] == {"outcome": "COMPLETED"} and o["wakeup_bytes"] == [] and 15 in o["pending_after_barrier"])


def main():
    ab1()
    ab2()
    json.dump({"rows": RESULTS, "claims": {
        "establishes": ["reserved 32/33 ignored/blocked state is kernel-visible and refusable before exec (AB-1 compatibility check; NOT tamper detection, NOT handler authorship)",
                        "a signal delivered before the FINALIZATION_BARRIER is captured by block+drain; one pending only after it is outside the unit (AB-2)"],
        "does_not_establish": ["the production child bootstrap or outcome derivation (B2)"],
        "future_qualification_owner": "B2"}}, open(sys.argv[1], "w"), indent=1)
    print(f"rows={len(RESULTS)} failures={len(FAILS)} {FAILS}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
