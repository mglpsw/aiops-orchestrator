"""P2 spawn spike harness (disposable experiment). Exit status is nonzero on any
expectation failure (failed_case_exit_nonzero). An unavailable witness is recorded as
gate_unavailable, which is never PASS.

Usage: harness.py <python> <out.json>
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
READER = os.path.join(HERE, "spike_reader.py")
CHILD = os.path.join(HERE, "spike_child.py")
AS_LIMIT = 4 * 1024 ** 3
PY = sys.argv[1]


def survivors(token: str) -> list:
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            cmd = open(f"/proc/{name}/cmdline", "rb").read()
        except OSError:
            continue
        if token.encode() in cmd:
            found.append(int(name))
    for pid in found:  # harness cleanup only; never counted as a pass
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return found


def run(mech: str, scenario: str, *, inject=None, mode="report", abl=(), fd_setup="normal", deadline=3.0):
    token = "s1bspike-" + uuid.uuid4().hex
    base = tempfile.mkdtemp(prefix="s1b-p2-")
    snap_path = os.path.join(base, "snapshot")
    os.mkdir(snap_path)
    orig = os.stat(snap_path)
    flags = os.O_RDONLY | os.O_DIRECTORY
    if fd_setup == "snapshot_opath":
        flags = os.O_PATH | os.O_DIRECTORY
    snap_fd = os.open(snap_path, flags)
    os.set_inheritable(snap_fd, True)
    # NOT subprocess.DEVNULL: that is /dev/null opened O_RDWR (found by the census in run 1)
    stdin_ro = os.open("/dev/null", os.O_RDONLY)
    pass_fds, stdin = [snap_fd], stdin_ro
    extra = [stdin_ro]
    decoy = None
    if scenario == "rebind":
        os.rename(snap_path, snap_path + ".moved")
        os.mkdir(snap_path)
        decoy = os.stat(snap_path)
    if fd_setup == "extra_rw":
        fd = os.open(os.path.join(base, "host-policy"), os.O_RDWR | os.O_CREAT, 0o600)
        os.set_inheritable(fd, True)
        extra.append(fd)
    if fd_setup == "extra_opath":
        fd = os.open(base, os.O_PATH | os.O_DIRECTORY)
        os.set_inheritable(fd, True)
        extra.append(fd)
    if fd_setup == "stdin_rdwr":
        stdin = os.open("/dev/null", os.O_RDWR)
        extra.append(stdin)
    pass_fds += [fd for fd in extra if fd not in (stdin, stdin_ro)]
    cfg = {"mechanism": mech, "scenario": scenario, "inject": inject, "child_mode": mode,
           "ablations": list(abl), "token": token, "python": PY, "child": CHILD,
           "snapshot_fd": snap_fd, "snapshot_path": snap_path, "as_limit": AS_LIMIT, "unit_deadline": deadline}
    argv = [PY, "-I", "-S", READER, json.dumps(cfg)]
    if scenario == "TF5-G":
        argv = ["unshare", "--user", "--map-root-user", "--pid", "--fork", "--mount-proc"] + argv
    p = subprocess.run(argv, stdin=stdin, capture_output=True, pass_fds=pass_fds, timeout=30)
    for fd in [snap_fd] + extra:
        os.close(fd)
    surv = survivors(token)
    try:
        res = json.loads(p.stdout.decode().strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        res = {"outcome": "NO_RESULT", "stderr": p.stderr.decode()[-800:], "rc": p.returncode}
    res["survivors_after_reader_exit"] = len(surv)
    res["orig_ino"], res["decoy_ino"] = orig.st_ino, decoy.st_ino if decoy else None
    return res


def fds_post(res):
    return sorted(d["fd"] for d in res.get("child_report", {}).get("fds_post_exec", []))


# (id, mechanism, kwargs, obligation, kind, predicate, description)
ROWS = []


def row(rid, mech, kw, obligation, kind, pred, desc):
    ROWS.append((rid, mech, kw, obligation, kind, pred, desc))


for M in ("A", "B"):
    clean = lambda r: r["survivors_after_reader_exit"] == 0 and not r["teardown"]["remaining"]
    row(f"{M}-POS-base", M, {}, "NNP+Rlimit+DescRelative+InheritanceClosed+Teardown", "positive",
        lambda r: r["outcome"] == "completed" and r["child_report"]["nnp"] == "1"
        and r["child_report"]["as_soft"] == str(AS_LIMIT) and r["child_report"]["cwd_ino"] == r["orig_ino"]
        and fds_post(r) == [0, 1, 2] and clean(r), "normal unit: report, then clean teardown")
    row(f"{M}-POS-rebind", M, {"scenario": "rebind"}, "DescriptorRelativeSnapshotBinding", "countermodel",
        lambda r: r["outcome"] == "completed" and r["child_report"]["cwd_ino"] == r["orig_ino"]
        and r["child_report"]["cwd_ino"] != r["decoy_ino"] and clean(r), "path renamed + decoy planted after fd open")
    row(f"{M}-TF5-A", M, {"inject": "setup_exception", "mode": "hold"}, "TeardownCoversEveryExit", "countermodel",
        lambda r: r["outcome"] == "transport_spawn_failed" and clean(r), "exception during child setup")
    row(f"{M}-TF5-B", M, {"inject": "constructor_fails_after_fork", "mode": "hold"},
        "OwnerEstablishedBeforeChildExists+NoSpawnToUnownedWindow", "countermodel",
        lambda r: r["outcome"] == "injected_parent_failure" and r["teardown"]["signalled"] >= 1 and clean(r),
        "child exists, parent constructor path fails (no handle)")
    row(f"{M}-TF5-C", M, {"inject": "baseexception_after_spawn", "mode": "hold"}, "TeardownCoversEveryExit",
        "countermodel", lambda r: (r["escaped"] or "").startswith("KeyboardInterrupt") and clean(r),
        "BaseException immediately after spawn, before pidfd")
    row(f"{M}-TF5-D", M, {"mode": "stall", "deadline": 0.5}, "TeardownCoversEveryExit", "countermodel",
        lambda r: r["outcome"] == "unit_deadline" and clean(r), "child stalls before the protocol")
    row(f"{M}-TF5-E", M, {"inject": "exec_missing"}, "TeardownCoversEveryExit", "countermodel",
        lambda r: r["outcome"] == "transport_spawn_failed" and clean(r), "pre-exec setup failure (exec ENOENT)")
    row(f"{M}-TF5-F", M, {"mode": "grandchild"}, "SubreaperEstablishedBeforeSpawn", "countermodel",
        lambda r: r["outcome"] == "completed" and r["teardown"]["reaped"] >= 2 and clean(r),
        "grandchild calls setsid")
    row(f"{M}-TF5-G", M, {"scenario": "TF5-G"}, "PidIdentityNotBarePid", "countermodel",
        lambda r: r.get("pid_reuse", {}).get("gate") == "available" and r["pid_reuse"]["via"] == "stale_pidfd_ESRCH"
        and r["pid_reuse"]["unrelated_process_killed"] is False, "real PID reuse (ns_last_pid) vs stale pidfd")
    row(f"{M}-TF5-H", M, {"fd_setup": "extra_rw"}, "DescriptorInheritanceClosed", "countermodel",
        lambda r: r["outcome"] == "reader_fd_capability_unexpected" and r["teardown"]["signalled"] == 0 and clean(r),
        "inherited unexpected O_RDWR fd")
    row(f"{M}-TF5-I", M, {"fd_setup": "stdin_rdwr"}, "DescriptorInheritanceClosed", "countermodel",
        lambda r: r["outcome"] == "reader_fd_capability_unexpected" and clean(r), "expected fd (stdin) with O_RDWR")
    row(f"{M}-TF5-J", M, {"fd_setup": "snapshot_opath"}, "DescriptorInheritanceClosed", "countermodel",
        lambda r: r["outcome"] == "reader_fd_capability_unexpected" and clean(r), "snapshot as O_PATH capability")
    row(f"{M}-TF5-J2", M, {"fd_setup": "extra_opath"}, "DescriptorInheritanceClosed", "countermodel",
        lambda r: r["outcome"] == "reader_fd_capability_unexpected" and clean(r), "unauthorized extra O_PATH fd")
    # -- ablations: the SAME witness must turn red when the mechanism element is removed
    dirty = lambda r: r["survivors_after_reader_exit"] > 0
    row(f"{M}-ABL-handle-B", M, {"inject": "constructor_fails_after_fork", "mode": "hold", "abl": ["handle_only"]},
        "OwnerEstablishedBeforeChildExists", "ablation", dirty, "teardown by spawn handle only")
    row(f"{M}-ABL-handle-C", M, {"inject": "baseexception_after_spawn", "mode": "hold", "abl": ["handle_only"]},
        "TeardownCoversEveryExit", "ablation", dirty, "teardown by spawn handle only")
    row(f"{M}-ABL-handle-F", M, {"mode": "grandchild", "abl": ["handle_only"]},
        "SubreaperEstablishedBeforeSpawn", "ablation", dirty, "teardown by spawn handle only")
    row(f"{M}-ABL-subreaper-F", M, {"mode": "grandchild", "abl": ["no_subreaper"]},
        "SubreaperEstablishedBeforeSpawn", "ablation", dirty, "no subreaper")
    row(f"{M}-ABL-nnp", M, {"abl": ["no_nnp"]}, "NNPBeforeExec", "ablation",
        lambda r: r["child_report"]["nnp"] == "0", "no NNP")
    row(f"{M}-ABL-rlimit", M, {"abl": ["rlimit_after_spawn"]}, "RlimitBeforeExec", "ablation",
        lambda r: r["child_report"]["as_soft"] == "unlimited", "prlimit after spawn")
    row(f"{M}-ABL-pathcwd", M, {"scenario": "rebind", "abl": ["path_cwd"]}, "DescriptorRelativeSnapshotBinding",
        "ablation", lambda r: r["child_report"]["cwd_ino"] == r["decoy_ino"], "cwd=<snapshot path>")
    row(f"{M}-ABL-barepid", M, {"scenario": "TF5-G", "abl": ["bare_pid"]}, "PidIdentityNotBarePid", "ablation",
        lambda r: r.get("pid_reuse", {}).get("unrelated_process_killed") is True, "kill by bare pid")
    row(f"{M}-ABL-census-H", M, {"fd_setup": "extra_rw", "abl": ["no_fd_census"] + (["B_no_close"] if M == "B" else [])},
        "DescriptorInheritanceClosed", "ablation",
        (lambda r: r["outcome"] == "completed") if M == "A" else (lambda r: len(fds_post(r)) > 3),
        "no typed census" + (" (A: reader holds O_RDWR; close_fds hides it from the child)" if M == "A"
                            else " + no child-side close: child inherits the fd"))


def main() -> int:
    results, failures = [], 0
    for rid, mech, kw, obligation, kind, pred, desc in ROWS:
        kw = dict(kw)
        scenario = kw.pop("scenario", rid.split("-", 1)[1])
        res = run(mech, scenario, inject=kw.get("inject"), mode=kw.get("mode", "report"),
                  abl=kw.get("abl", ()), fd_setup=kw.get("fd_setup", "normal"), deadline=kw.get("deadline", 3.0))
        gate = res.get("pid_reuse", {}).get("gate")
        try:
            ok = bool(pred(res))
        except (KeyError, TypeError):
            ok = False
        verdict = "gate_unavailable" if gate == "gate_unavailable" else ("PASS" if ok else "FAIL")
        if verdict != "PASS":
            failures += 1
        results.append({"id": rid, "mechanism": mech, "obligation": obligation, "kind": kind, "description": desc,
                        "verdict": verdict, "outcome": res.get("outcome"), "result": res})
        print(f"{verdict:16} {rid:22} {kind:12} {obligation:48} outcome={res.get('outcome')} "
              f"survivors={res.get('survivors_after_reader_exit')}", flush=True)
    with open(sys.argv[2], "w") as f:
        json.dump(results, f, indent=1)
    print(f"rows={len(results)} failures_or_unavailable={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
