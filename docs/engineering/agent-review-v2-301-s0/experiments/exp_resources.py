"""EXP-RES: resource vector, expansion, and failure/lifecycle ownership.

usage: python -I -S exp_resources.py <toolrepo_source_root> <scratch_dir> <interpreter>
A timeout here only protects the harness; each budget claim is shown by a refusal that happens
BEFORE the expansion (heap peak recorded), not by the run finishing in time.
"""
import errno
import fcntl
import hashlib
import json
import os
import resource
import subprocess
import sys
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SRC = Path(sys.argv[1]).resolve()
sys.path.insert(1, str(SRC))
W = Path(sys.argv[2]).resolve()
PY = sys.argv[3]

import s0_capture as cap  # noqa: E402
import s0_fixture as fx  # noqa: E402
import s0_launch as ln  # noqa: E402

out = {"cases": {}}


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


def children():
    me = str(os.getpid())
    kids = []
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if open(f"/proc/{pid}/stat").read().split(") ", 1)[1].split()[1] == me:
                    kids.append(pid)
            except OSError:
                pass
    return kids


def measured(fn):
    fds, kids = cap.open_fds(), children()
    tracemalloc.start()
    try:
        fn()
        res = "ACCEPTED"
    except cap.CaptureRefused as exc:
        res = "REFUSED:" + exc.reason
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return res, round(peak / 2**20, 2), cap.open_fds() == fds, children() == kids


repo = fx.init(W / "res")

# (1) repeated expansion: one 64 KiB blob, one subtree referenced 2,000 times -> 125 MiB per occurrence
big = fx.blob(repo, b"x" * 65536)
sub = fx.mktree(repo, [("100644", "blob", big, b"data.bin")])
dag = fx.commit(repo, fx.mktree(repo, [("040000", "tree", sub, b"d%04d" % i) for i in range(2000)]))
res, peak, fds_ok, kids_ok = measured(lambda: cap.build_subject(repo, dag, budget=cap.Budget(max_payload_bytes=16 * 2**20)))
case("shared_subtree_expansion_refused_per_occurrence", "REFUSED:budget_payload_bytes", res,
     unique_bytes=65536, per_occurrence_bytes=2000 * 65536, budget_bytes=16 * 2**20, heap_peak_MiB=peak,
     fds_restored=fds_ok, git_reaped=kids_ok)
res, _, _, _ = measured(lambda: cap.build_subject(repo, dag, budget=cap.Budget(max_nodes=1000)))
case("single_tree_entry_cap_is_C3s", "REFUSED:tree_unrepresentable", res,
     note="2,000 entries in ONE tree: refused by C3's own per-tree cap (_parse_tree_data), not by S's node budget")
small = fx.blob(repo, b"s\n")
sub40 = fx.mktree(repo, [("100644", "blob", small, b"f%02d" % i) for i in range(40)])
dag40 = fx.commit(repo, fx.mktree(repo, [("040000", "tree", sub40, b"d%02d" % i) for i in range(40)]))
res, peak, _, _ = measured(lambda: cap.build_subject(repo, dag40, budget=cap.Budget(max_nodes=1000)))
case("shared_subtree_node_budget_per_occurrence", "REFUSED:budget_nodes", res, heap_peak_MiB=peak,
     unique_nodes=41, per_occurrence_nodes=40 + 40 * 40, note="every tree < 1000 entries; only the occurrence count exceeds")

# (2) one oversized blob: refused from the object HEADER, before the body is read
huge = fx.blob(repo, os.urandom(32 * 2**20))
c_huge = fx.commit(repo, fx.mktree(repo, [("100644", "blob", huge, b"huge.bin")]))
res, peak, fds_ok, kids_ok = measured(lambda: cap.build_subject(repo, c_huge, budget=cap.Budget(max_payload_bytes=8 * 2**20)))
case("oversized_blob_refused_before_read", "REFUSED:budget_payload_bytes", res, blob_bytes=32 * 2**20,
     budget_bytes=8 * 2**20, heap_peak_MiB=peak, fds_restored=fds_ok, git_reaped=kids_ok)
res, peak, _, _ = measured(lambda: cap.build_subject(repo, c_huge))
case("CONTROL_same_blob_within_default_budget", "ACCEPTED", res, heap_peak_MiB=peak,
     note="heap ~ blob size: the carrier is in-memory by design; the budget bounds it")

# (3) depth budget applies to EVERY node kind (C3/R2 property), not only to trees
deep = fx.mktree(repo, [("100644", "blob", fx.blob(repo, b"x"), b"leaf.py")])
for i in range(70):
    deep = fx.mktree(repo, [("040000", "tree", deep, b"n")])
res, _, _, _ = measured(lambda: cap.build_subject(repo, fx.commit(repo, deep)))
case("depth_budget", "REFUSED:budget_depth", res)

# (4) failure injection: no partial result can look like a committed S; ownership stays linear
good = fx.commit(repo, fx.mktree(repo, [("100644", "blob", fx.blob(repo, b"ok\n"), b"ok.py")]))
fds0 = cap.open_fds()


def seal_with_fsize_limit():
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024, hard))
    try:
        return cap.seal_committed(b"y" * 4096)
    except OSError as exc:
        raise cap.CaptureRefused("write_failed", errno.errorcode.get(exc.errno, str(exc.errno)))
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))


case("write_failure_EFBIG_no_fd_leaked", ("REFUSED:write_failed", True),
     (measured(seal_with_fsize_limit)[0], cap.open_fds() == fds0))

real_fcntl = fcntl.fcntl


def failing_add_seals(fd, cmd, *a):
    if cmd == fcntl.F_ADD_SEALS:
        raise OSError(errno.EBUSY, "injected")
    return real_fcntl(fd, cmd, *a)


import s0_bootstrap  # noqa: E402

s0_bootstrap.fcntl.fcntl = failing_add_seals
try:
    r = measured(lambda: cap.seal_committed(b"z" * 100))
finally:
    s0_bootstrap.fcntl.fcntl = real_fcntl
case("seal_failure_no_fd_leaked", ("REFUSED:seal_failed", True), (r[0], r[2]))

fx.swap_loose(repo, fx.git(repo, "rev-parse", f"{good}^{{tree}}"), "tree", b"")
r = measured(lambda: cap.build_subject(repo, good))
case("mid_capture_hash_failure_git_reaped_fds_restored", ("REFUSED:object_hash_mismatch", True, True), (r[0], r[2], r[3]))

# (5) consumer lifecycle: a child that never answers yields NO result, is killed and reaped
data = b"s0"
s_fd = cap.seal_committed(cap.serialize("sha1", "0" * 40, "0" * 40, {b"ok.py": ("regular", "0" * 40, b"x = 1\n")}))
hang = b"def run(inputs):\n    import time\n    time.sleep(3600)\n"
x_fd = cap.seal_committed(hang)
spec = {"s": {"fd": s_fd, "sha256": hashlib.sha256(os.pread(s_fd, os.fstat(s_fd).st_size, 0)).hexdigest(), "commit": "0" * 40},
        "d": None, "x": {"fd": x_fd, "sha256": hashlib.sha256(hang).hexdigest()}, "roots": ["ok"], "inputs": {}}
kids = children()
r = ln.launch(PY, spec, (s_fd, x_fd), timeout=3)
case("hung_consumer_no_result_killed_reaped", {"reply": None, "timeout": True, "reaped": True},
     {"reply": r["reply"], "timeout": r.get("timeout", False), "reaped": children() == kids})
crash = b"def run(inputs):\n    import os\n    os._exit(0)\n"
x2 = cap.seal_committed(crash)
r = ln.launch(PY, dict(spec, x={"fd": x2, "sha256": hashlib.sha256(crash).hexdigest()}), (s_fd, x2), timeout=30)
case("consumer_exits_rc0_without_reply_is_not_success", {"rc": 0, "reply": None}, {"rc": r["rc"], "reply": r["reply"]})
for fd in (s_fd, x_fd, x2):
    os.close(fd)
case("harness_fds_restored", fds0, cap.open_fds())
out["all_pass"] = all(c.get("pass") for c in out["cases"].values())
print(json.dumps(out, indent=1, default=str))
