"""EXP-SNAP: architecture B -- S_G acquired from a private, remote-less physical snapshot.

usage: python -I -S -B exp_snapshot.py <producer_src_root> <scratch_dir>
Ports Spike B (PR #355 comment 5852938161) into the recorded suite and adds the discriminators the
maintainer's grant requires: per-object deadline, removal of verify-pack from the S_G path (forged
pack/idx never yields a false positive), sha256 through the snapshot, immutable acquisition identity,
snapshot identity for traceability, separate physical vs subject budgets, and an orphan census.
"""
import dataclasses
import json
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SRC = Path(sys.argv[1]).resolve()
sys.path.insert(1, str(SRC))
W = Path(sys.argv[2]).resolve()
W.mkdir(parents=True)

import s0_capture as cap  # noqa: E402
import s0_fixture as fx  # noqa: E402
import s0_snapshot as snap  # noqa: E402

out = {"cases": {}, "observations": {}}
SPAWNS: list = []


def audit(ev, args):
    if ev == "subprocess.Popen":
        argv = [os.fsdecode(a) for a in (args[1] or [])]
        SPAWNS.append({"argv": argv[:4], "cwd": os.fsdecode(args[2]) if len(args) > 2 and args[2] else None})


sys.addaudithook(audit)


def census():
    """Whole-PID-namespace census: meaningful in the isolated container run (run_py311.sh); on a shared
    host it also sees other sessions' processes, so a host trial of this case is not evidence."""
    procs = set()
    for pid in os.listdir("/proc"):
        if pid.isdigit() and int(pid) != os.getpid():
            try:
                procs.add((int(pid), open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")[:120]))
            except OSError:
                pass
    listen = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in open(f).read().splitlines()[1:]:
                parts = line.split()
                if parts[3] == "0A":  # LISTEN
                    listen.add(parts[1])
        except OSError:
            pass
    return procs, listen


BEFORE = census()


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


def admitted(**kw):
    return cap.Budget(max_component_len=255, **kw)


def outcome(fn):
    try:
        r = fn()
        return r if isinstance(r, str) else "ACCEPTED"
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


def snapshot(repo, commit, **kw):
    return snap.private_snapshot(repo, expected_commit=commit, storage_roots=[repo], **kw)


# ---- Q1/Q2: partial clone, promisor remote, upload-pack helper that leaves a marker -------------------
src = fx.init(W / "src")
lazy_blob = fx.blob(src, b"lazy = 1\n")
here_blob = fx.blob(src, b"here = 1\n")
c_lazy = fx.commit(src, fx.mktree(src, [("100644", "blob", lazy_blob, b"lazy.py")]))
c_here = fx.commit(src, fx.mktree(src, [("100644", "blob", here_blob, b"here.py")]))
fx.git(src, "update-ref", "refs/heads/main", c_lazy)
fx.git(src, "update-ref", "refs/heads/other", c_here)
for k, v in (("uploadpack.allowFilter", "true"), ("uploadpack.allowAnySHA1InWant", "true")):
    fx.git(src, "config", k, v)
part = W / "part"
subprocess.run(["git", "clone", "-q", "--no-checkout", "--filter=blob:none", "file://" + str(src), str(part)],
               env=fx.FIXTURE_ENV, check=True, capture_output=True)
subprocess.run(["git", "-C", str(part), "fetch", "-q", "origin", "other"], env=fx.FIXTURE_ENV, capture_output=True)
subprocess.run(["git", "-C", str(part), "cat-file", "blob", here_blob], env=fx.FIXTURE_ENV, capture_output=True)
marker = W / "fetch-attempted"
helper = W / "marker-upload-pack"
helper.write_text("#!/bin/sh\necho called >> %s\nexec git-upload-pack \"$@\"\n" % marker)
helper.chmod(0o755)
fx.git(part, "config", "remote.origin.uploadpack", str(helper))
out["observations"]["fixture"] = {"promisor_config_in_live_repo": "promisor" in (part / ".git" / "config").read_text()}

n0 = len(SPAWNS)
with snapshot(part, c_lazy) as (cas, sid):
    snapshot_spawns = SPAWNS[n0:]
    case("Q1_snapshot_runs_no_git_and_no_fetch", {"processes": [], "fetch_attempted": False, "remote_in_snapshot": False},
         {"processes": snapshot_spawns, "fetch_attempted": marker.exists(),
          "remote_in_snapshot": any(w in (cas / "config").read_text() for w in ("remote", "promisor"))})
    n1 = len(SPAWNS)
    case("Q2_missing_required_object_typed_refusal", "REFUSED:object_missing", outcome(lambda: cap.build_subject(cas, c_lazy, budget=admitted())))
    case("Q2_offline_closure_complete_partial_clone_admitted", "ACCEPTED", outcome(lambda: cap.build_subject(cas, c_here, budget=admitted())))
    case("Q2_no_fetch_during_closure_reads", False, marker.exists())
    reader_spawns = SPAWNS[n1:]
    case("Q6_reader_runs_only_rev_parse_and_cat_file_inside_the_snapshot", True,
         all(s["cwd"] == str(cas) and s["argv"][1] in ("rev-parse", "cat-file") for s in reader_spawns) and bool(reader_spawns),
         reader_processes=reader_spawns)

# ---- Q3: same-UID mutation of the snapshot after it was taken --------------------------------------------
rq = fx.init(W / "q3")
b3 = fx.blob(rq, b'VALUE = "trusted"\n')
c3 = fx.commit(rq, fx.mktree(rq, [("100644", "blob", b3, b"m.py")]))
with snapshot(rq, c3) as (cas, sid):
    loose = cas / "objects" / b3[:2] / b3[2:]
    os.chmod(loose, 0o644)
    evil = b'VALUE = "EVIL!!!"\n'
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(evil) + evil))
    case("Q3_loose_object_swapped_in_snapshot", "REFUSED:object_hash_mismatch", outcome(lambda: cap.build_subject(cas, c3, budget=admitted())))
    loose.write_bytes(zlib.compress(b"blob 99\0" + evil))  # malformed: header length != content length
    t0 = time.monotonic()
    r = outcome(lambda: cap.build_subject(cas, c3, budget=admitted(transport_deadline_s=3.0)))
    case("Q3_malformed_loose_object_refused_by_deadline", {"result": "REFUSED:transport_deadline", "within_10s": True},
         {"result": r, "within_10s": time.monotonic() - t0 < 10}, elapsed_s=round(time.monotonic() - t0, 1))
    probe = ("import sys; sys.path[:0]=[sys.argv[1], sys.argv[2]]; import s0_capture as cap\n"
             "try:\n    cap.build_subject(__import__('pathlib').Path(sys.argv[3]), sys.argv[4], "
             "budget=cap.Budget(max_component_len=255, transport_deadline_s=None)); print('ACCEPTED')\n"
             "except cap.CaptureRefused as e:\n    print('REFUSED:' + e.reason)\n")
    try:
        r = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", probe, str(HERE), str(SRC), str(cas), c3],
                           capture_output=True, text=True, timeout=15).stdout.strip()
    except subprocess.TimeoutExpired:
        r = "HANG_KILLED_BY_HARNESS_AT_15s"
    case("Q3_ABLATION_no_deadline_hangs", "HANG_KILLED_BY_HARNESS_AT_15s", r)

rp = fx.init(W / "q3p")
bp1, bp2 = fx.blob(rp, b"A" * 3000 + b"\n"), fx.blob(rp, b"B" * 3000 + b"\n")
cp_ = fx.commit(rp, fx.mktree(rp, [("100644", "blob", bp1, b"a.bin"), ("100644", "blob", bp2, b"b.bin")]))
fx.git(rp, "update-ref", "refs/heads/main", cp_)
fx.git(rp, "repack", "-a", "-d", "-q")
with snapshot(rp, cp_) as (cas, sid):
    case("Q5_packed_control_accepted_without_verify_pack", "ACCEPTED", outcome(lambda: cap.build_subject(cas, cp_, budget=admitted())))
    idx = next((cas / "objects" / "pack").glob("*.idx"))
    raw = bytearray(idx.read_bytes())
    (n,) = struct.unpack_from(">I", raw, 8 + 255 * 4)
    oids = [raw[8 + 1024 + i * 20: 8 + 1024 + (i + 1) * 20].hex() for i in range(n)]
    off_base = 8 + 1024 + n * 20 + n * 4
    i1, i2 = oids.index(bp1), oids.index(bp2)
    o1, o2 = raw[off_base + i1 * 4: off_base + i1 * 4 + 4], raw[off_base + i2 * 4: off_base + i2 * 4 + 4]
    raw[off_base + i1 * 4: off_base + i1 * 4 + 4], raw[off_base + i2 * 4: off_base + i2 * 4 + 4] = o2, o1
    os.chmod(idx, 0o644)
    idx.write_bytes(bytes(raw))  # forged idx: blob A's name now points at blob B's bytes
    r = outcome(lambda: cap.build_subject(cas, cp_, budget=admitted()))
    case("Q3_forged_idx_never_false_positive", True, r.startswith("REFUSED"), observed_reason=r,
         note="verify-pack is not run on the S_G path; hash-on-read is the truth-maker")
with snapshot(rp, cp_) as (cas, sid):
    pack = next((cas / "objects" / "pack").glob("*.pack"))
    raw = bytearray(pack.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    os.chmod(pack, 0o644)
    pack.write_bytes(bytes(raw))
    r = outcome(lambda: cap.build_subject(cas, cp_, budget=admitted()))
    case("Q3_corrupted_pack_never_false_positive", True, r.startswith("REFUSED"), observed_reason=r)

# ---- Q4: identity changed between acquisition and commit (R4-2 countermodel) ---------------------------
rq4 = fx.init(W / "q4")
c4 = fx.commit(rq4, fx.mktree(rq4, [("100644", "blob", fx.blob(rq4, b"x = 1\n"), b"a.py")]))
with snapshot(rq4, c4) as (cas, sid):
    s4 = cap.build_subject(cas, c4, budget=admitted())

    def commit_outcome(subject):
        try:
            fd, _d = cap.commit_subject(subject)
            os.close(fd)
            return "COMMITTED"
        except cap.CaptureRefused as exc:
            return "REFUSED:" + exc.reason

    case("Q4_root_tree_changed_after_acquisition_refused", "REFUSED:sealed_identity_mismatch",
         commit_outcome(dataclasses.replace(s4, root_tree="0" * 40)))
    case("Q4_commit_changed_after_acquisition_refused", "REFUSED:sealed_identity_mismatch",
         commit_outcome(dataclasses.replace(s4, commit="1" * 40)))
    case("Q4_control_commits", "COMMITTED", commit_outcome(s4))
    try:
        s4.root_tree = "0" * 40  # type: ignore[misc]
        frozen = False
    except dataclasses.FrozenInstanceError:
        frozen = True
    case("Q4_subject_is_frozen", True, frozen)

# ---- sha256 repository through the snapshot (skeleton declares the expected object format) ------------
r256 = fx.init(W / "sha256", "sha256")
b256 = fx.blob(r256, b"s = 256\n")
c256 = fx.commit(r256, fx.mktree(r256, [("100644", "blob", b256, b"s.py")]))
with snapshot(r256, c256) as (cas, sid):
    case("SHA256_capture_through_snapshot", {"result": "ACCEPTED", "snapshot_format": "sha256"},
         {"result": outcome(lambda: cap.build_subject(cas, c256, budget=admitted())), "snapshot_format": sid.object_format})
    loose = cas / "objects" / b256[:2] / b256[2:]
    os.chmod(loose, 0o644)
    loose.write_bytes(zlib.compress(b"blob 8\0s = EVL\n"))
    case("SHA256_snapshot_tamper_refused", "REFUSED:object_hash_mismatch", outcome(lambda: cap.build_subject(cas, c256, budget=admitted())))

# ---- Q7 + budgets: physical snapshot budget vs subject closure budget are different classes ------------
big = fx.init(W / "big")
junk = fx.blob(big, os.urandom(48 << 20))
old = fx.commit(big, fx.mktree(big, [("100644", "blob", junk, b"junk.bin")]))
tiny = fx.commit(big, fx.mktree(big, [("100644", "blob", fx.blob(big, b"t\n"), b"t.py")]))
fx.git(big, "update-ref", "refs/heads/old", old)
fx.git(big, "update-ref", "refs/heads/main", tiny)
fx.git(big, "repack", "-a", "-d", "-q")


def snap_outcome(budget):
    try:
        with snapshot(big, tiny, budget=budget):
            return "SNAPSHOT_TAKEN"
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


case("Q7_physical_budget_refuses_by_store_footprint_not_subject", "REFUSED:snapshot_refused",
     snap_outcome(snap.SnapshotBudget(max_bytes=8 << 20)), store_bytes=sum(
         f.stat().st_size for f in (big / ".git" / "objects").rglob("*") if f.is_file()), subject="1 tiny blob")
with snapshot(big, old) as (cas, sid):
    case("Q7_subject_budget_is_a_separate_class", "REFUSED:budget_payload_bytes",
         outcome(lambda: cap.build_subject(cas, old, budget=admitted(max_payload_bytes=8 << 20))),
         note="snapshot within its physical budget; the SUBJECT budget refuses the 48 MiB closure")

# ---- snapshot identity: traceability that separates snapshot / closure / S_G differences ---------------
tr = fx.init(W / "trace")
ct = fx.commit(tr, fx.mktree(tr, [("100644", "blob", fx.blob(tr, b"t = 1\n"), b"t.py")]))


def trace():
    with snapshot(tr, ct) as (cas, sid):
        s = cap.build_subject(cas, ct, budget=admitted())
        fd, digest = cap.commit_subject(s)
        os.close(fd)
        return sid, digest


sid_a, sg_a = trace()
sid_b, sg_b = trace()
fx.blob(tr, b"unrelated object added to the store\n")
sid_c, sg_c = trace()
case("snapshot_identity_distinguishes_snapshot_from_S_G",
     {"same_store_same_receipt": True, "store_changed_receipt_changed": True, "S_G_unchanged": True},
     {"same_store_same_receipt": sid_a.receipt_sha256 == sid_b.receipt_sha256,
      "store_changed_receipt_changed": sid_a.receipt_sha256 != sid_c.receipt_sha256,
      "S_G_unchanged": sg_a == sg_b == sg_c},
     snapshot_identity=dataclasses.asdict(sid_c))

# ---- orphan census: nothing started by this experiment may outlive it ------------------------------------
time.sleep(0.5)
after = census()
new_procs = sorted((p, c.decode(errors="replace")) for p, c in after[0] - BEFORE[0] if b"exp_snapshot" not in c)
case("no_orphan_processes_or_listeners_remain", {"processes": [], "listeners": []},
     {"processes": new_procs, "listeners": sorted(after[1] - BEFORE[1])})
out["all_pass"] = all(c.get("pass") for c in out["cases"].values())
print(json.dumps(out, indent=1, default=str))
