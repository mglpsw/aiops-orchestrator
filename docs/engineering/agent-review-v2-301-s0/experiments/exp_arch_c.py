"""EXP-C: architecture C candidate -- privilege-separated immutable snapshot -> runner-derived S_G.

usage (as the privileged side, inside the container): python -I -S -B exp_arch_c.py <tcb_src> <real_toolrepo> <real_commit> <nonce>
Roles: producer = this process's uid (0 in the container: a MECHANISM, not the normative primitive);
runner/attacker = uid 2000 (owns every source repository); the closure reader ALWAYS runs as the runner.
Ports 301-S0 Spike C (PR #355 comment 5858608157) and adds C11 (unprivileged runner-side reader).
"""
import json
import os
import signal
import subprocess
import sys
import time
import zlib
from pathlib import Path

TCB, REAL, REAL_COMMIT, NONCE = sys.argv[1:5]
HERE = Path(__file__).resolve().parent
PATHS = [str(HERE), TCB]
sys.path[:0] = PATHS
import s0_fixture as fx  # noqa: E402

fx.FIXTURE_ENV.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "*"})
PY = sys.executable
RUNNER = 2000
BASE = Path(f"/srv/{NONCE}")
WORK = Path(f"/work/{NONCE}")
out = {"cases": {}, "observed": {}, "not_tested": []}
SETPRIV = ["setpriv", f"--reuid={RUNNER}", f"--regid={RUNNER}", "--clear-groups", "--"]


def census():
    procs = {}
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                start = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()[19]
                procs[(int(pid), start)] = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ")[:160].decode(errors="replace")
            except OSError:
                pass
    listen = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            listen |= {l.split()[1] for l in open(f).read().splitlines()[1:] if l.split()[3] == "0A"}
        except OSError:
            pass
    return procs, listen


BEFORE = census()


def case(name, expected, observed, evidence="REPRODUCED", **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, "evidence": evidence, **extra}


def chown_runner(p: Path) -> None:
    subprocess.run(["chown", "-R", f"{RUNNER}:{RUNNER}", str(p)], check=True)


def produce(repo: Path, fmt: str, roots, **kw):
    args = {"paths": PATHS, "repo_root": str(repo), "object_format": fmt, "storage_roots": [str(r) for r in roots],
            "out_base": str(BASE), **kw}
    cp = subprocess.run([PY, "-I", "-S", "-B", str(HERE / "s0_snapshot_c.py"), json.dumps(args)], capture_output=True,
                        text=True, timeout=600)
    return json.loads(cp.stdout) if cp.stdout.strip() else {"status": "crashed", "stderr": cp.stderr[-400:]}


def reader(mode: str, **args):
    """ALWAYS as the unprivileged runner."""
    cp = subprocess.run([*SETPRIV, PY, "-I", "-S", "-B", str(HERE / "s0_reader_c.py"), mode, json.dumps({"paths": PATHS, **args})],
                        capture_output=True, text=True, timeout=600, env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
    return json.loads(cp.stdout) if cp.stdout.strip() else {"status": "crashed", "stderr": cp.stderr[-400:]}


def expected(commit: str, fmt: str = "sha1", limit: int = 255):
    return {"object_format": fmt, "commit_oid": commit, "component_policy": {"max_component_len": limit}}


def outcome(r):
    return "ACCEPTED" if r.get("status") == "ok" else "REFUSED:" + str(r.get("result", {}).get("reason", r.get("stderr", "?")))


def as_runner(src: str, *args) -> str:
    cp = subprocess.run([*SETPRIV, PY, "-I", "-S", "-c", src, *args], capture_output=True, text=True, timeout=120,
                        env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
    return cp.stdout.strip() or ("ERR:" + cp.stderr[-300:])


for d, mode in ((BASE, 0o755), (BASE / "staging", 0o700), (BASE / "committed", 0o755)):
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, mode)
WORK.mkdir(parents=True)
out["observed"]["privilege"] = {"producer_uid": os.getuid(), "runner_uid": RUNNER,
                                "note": "container uid 0 is the experiment's mechanism; the contract property is RunnerCanRead AND NOT RunnerCanMutate"}

# ---------------------------------------------------------------- hostile source + authorized alternate -----
alt_pool = fx.init(WORK / "altpool")
alt_blob = fx.blob(alt_pool, b"from the authorized alternate pool\n")
src = fx.init(WORK / "src")
(src / ".git" / "objects" / "info" / "alternates").write_text(str(alt_pool / ".git" / "objects") + "\n")
b_main = fx.blob(src, b'VALUE = "trusted"\n')
b_exec = fx.blob(src, b"#!/bin/sh\necho s0\n")
b_link = fx.blob(src, b"pkg/../main.py")
empty = fx.empty_tree(src)
pkg = fx.mktree(src, [("100644", "blob", b_main, b"__init__.py")])
nested = fx.mktree(src, [("040000", "tree", pkg, b"inner")])
c_src = fx.commit(src, fx.mktree(src, [("100644", "blob", b_main, b"main.py"), ("100755", "blob", b_exec, b"run.sh"),
                                       ("120000", "blob", b_link, b"link"), ("040000", "tree", empty, b"empty"),
                                       ("040000", "tree", nested, b"nested"), ("100644", "blob", alt_blob, b"alt.txt")]))
fx.git(src, "update-ref", "refs/heads/main", c_src)
fx.git(src, "repack", "-a", "-d", "-l", "-q")
b_extra = fx.blob(src, b"extra loose object\n")
for k, v in (("remote.origin.url", "file:///nonexistent"), ("remote.origin.promisor", "true"),
             ("extensions.partialClone", "origin"), ("core.hooksPath", str(src / ".git" / "hooks"))):
    fx.git(src, "config", k, v)
(src / ".git" / "hooks").mkdir(exist_ok=True)
(src / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\ntouch /tmp/HOOK_RAN\n")
(src / ".git" / "objects" / b_extra[:2] / "tmp_obj_ABCDEF").write_bytes(b"garbage")
chown_runner(WORK)

p = produce(src, "sha1", [src, alt_pool])
snap = Path(p["result"]["snapshot"])
cfg = (snap / "config").read_text()
files = sorted(str(f.relative_to(snap)) for f in snap.rglob("*"))
case("C2_source_metadata_does_not_cross",
     {"remote": False, "promisor": False, "hooks": False, "source_config": False, "refs": [], "alternates_pointer": False},
     {"remote": "remote" in cfg, "promisor": "promisor" in cfg or "partialclone" in cfg.lower(),
      "hooks": any(f.startswith("hooks") for f in files), "source_config": "hooksPath" in cfg,
      "refs": [f for f in files if f.startswith("refs/") and (snap / f).is_file()],
      "alternates_pointer": (snap / "objects" / "info" / "alternates").exists()},
     top_level=[f for f in files if "/" not in f], receipt=p["result"]["receipt"])

# ---------------------------------------------------------------- C1: runner mutations ---------------------
ATTACK = r"""
import errno, json, os, sys
s, loose, pack = sys.argv[1:4]; r = {}
def t(k, f):
    try: f(); r[k] = "SUCCEEDED"
    except OSError as e: r[k] = errno.errorcode.get(e.errno, str(e.errno))
t("write_config", lambda: os.open(s + "/config", os.O_WRONLY))
t("create_alternates", lambda: os.open(s + "/objects/info/alternates", os.O_WRONLY | os.O_CREAT, 0o644))
t("replace_loose_object_by_write", lambda: os.open(loose, os.O_WRONLY))
t("replace_loose_object_by_rename", lambda: os.rename(s + "/HEAD", loose))
t("replace_pack", lambda: os.open(pack, os.O_WRONLY))
t("unlink_file", lambda: os.unlink(loose))
t("rename_object", lambda: os.rename(loose, loose + ".x"))
t("mkdir_inside", lambda: os.mkdir(s + "/objects/zz"))
t("chmod_snapshot", lambda: os.chmod(s, 0o777))
t("chmod_object", lambda: os.chmod(loose, 0o666))
t("rename_snapshot_dir", lambda: os.rename(s, s + ".evil"))
t("create_sibling_in_committed", lambda: os.mkdir(os.path.dirname(s) + "/evil"))
try:
    open(s + "/config").read(); os.listdir(s + "/objects"); open(loose, "rb").read(); r["POSITIVE_read"] = "ok"
except OSError as e:
    r["POSITIVE_read"] = errno.errorcode.get(e.errno)
print(json.dumps(r))
"""
loose = str(next(snap.glob("objects/??/*")))
pack = str(next(snap.glob("objects/pack/*.pack")))
res = json.loads(as_runner(ATTACK, str(snap), loose, pack))
case("C1_runner_mutations_denied_by_kernel", True, all(v in ("EACCES", "EPERM") for k, v in res.items() if k != "POSITIVE_read"),
     syscall_results=res, snapshot_owner_mode=f"{os.stat(snap).st_uid}:{oct(os.stat(snap).st_mode & 0o777)}")
case("C1_POSITIVE_runner_reads_snapshot", "ok", res.get("POSITIVE_read"))

# ---------------------------------------------------------------- C11 + C9 + structure: RUNNER derives S_G ----
r11 = reader("acquire", snapshot=str(snap), expected=expected(c_src))
DECLARED = {"alt.txt": "regular", "empty": "tree", "link": "symlink", "main.py": "regular", "nested": "tree",
            "nested/inner": "tree", "nested/inner/__init__.py": "regular", "run.sh": "executable"}
case("C11_unprivileged_reader_derives_S_G_structure",
     {"status": "ok", "uid": RUNNER, "node_kinds": DECLARED, "symlink_targets": {"link": "pkg/../main.py"}},
     {"status": r11["status"], "uid": r11.get("uid"), "node_kinds": r11.get("result", {}).get("node_kinds"),
      "symlink_targets": r11.get("result", {}).get("symlink_targets")},
     note="explicit declared tree: empty explicit tree, executable bit, symlink target bytes, nested trees, authorized-alternate blob")
case("C9_authorized_alternate_flattened_no_pointer", {"accepted": True, "pointer": False, "alternate_sources": 1},
     {"accepted": r11["status"] == "ok", "pointer": (snap / "objects" / "info" / "alternates").exists(),
      "alternate_sources": p["result"]["receipt"]["alternate_sources_count"]})
# counter-control: the reader refuses a snapshot the runner itself could mutate (the boundary is checked at consumption)
mutable = WORK / "mutable-copy"
subprocess.run(["cp", "-a", str(snap), str(mutable)], check=True)
chown_runner(mutable)
subprocess.run(["chmod", "-R", "u+w", str(mutable)], check=True)
case("C11_COUNTER_reader_refuses_runner_mutable_snapshot", "REFUSED:snapshot_mutable_by_reader",
     outcome(reader("acquire", snapshot=str(mutable), expected=expected(c_src))))
# counter-controls for the path the reader is handed (review of a858dc9: Codex P1 + adversarial F1). The published
# snapshot is copied, still root-owned 0555/0444, under a RUNNER-owned directory; the runner then owns a real ancestor.
rdir = WORK / "runner-owned-parent"
rdir.mkdir()
subprocess.run(["cp", "-a", str(snap), str(rdir / "snap")], check=True)
os.chown(rdir, RUNNER, RUNNER)
os.chmod(rdir, 0o555)
link_parent = BASE / "link"
link_parent.mkdir(mode=0o755)
os.symlink(rdir, link_parent / "pub")            # root-owned symlink: the runner cannot create this one
RENAME = "import os,sys\ntry:\n    os.chmod(sys.argv[1], 0o755); os.rename(sys.argv[2], sys.argv[2] + '.x'); os.rename(sys.argv[2] + '.x', sys.argv[2]); os.chmod(sys.argv[1], 0o555); print('RENAMED_AND_RESTORED')\nexcept OSError as e:\n    print('DENIED', e.errno)"
case("C11_COUNTER_symlinked_path_into_runner_owned_parent_refused", "REFUSED:snapshot_path_not_canonical",
     outcome(reader("acquire", snapshot=str(link_parent / "pub" / "snap"), expected=expected(c_src))),
     countermodel_is_real=as_runner(RENAME, str(rdir), str(rdir / "snap")),
     mutant="a858dc9 lexical ancestor check ACCEPTED this path and the runner then replaced the snapshot (PR #355 review)")
case("C11_COUNTER_runner_owned_real_ancestor_refused", "REFUSED:snapshot_mutable_by_reader",
     outcome(reader("acquire", snapshot=str(rdir / "snap"), expected=expected(c_src))))
case("C11_COUNTER_relative_path_refused", "REFUSED:snapshot_path_not_canonical",
     outcome(reader("acquire", snapshot=os.path.relpath(snap, "/"), expected=expected(c_src))))
case("C11_expected_format_mismatch_refused", "REFUSED:snapshot_format_mismatch",
     outcome(reader("acquire", snapshot=str(snap), expected={**expected(c_src), "object_format": "sha256",
                                                            "commit_oid": c_src + "0" * 24})))

# ---------------------------------------------------------------- C3 / C4 / C8 ------------------------------
INJECT = r"""
import errno, json, os, sys
s = sys.argv[1]; r = {}
def t(k, f):
    try: f(); r[k] = "SUCCEEDED"
    except OSError as e: r[k] = errno.errorcode.get(e.errno, str(e.errno))
t("inject_alternates", lambda: open(s + "/objects/info/alternates", "w").write(sys.argv[2] + "\n"))
t("inject_promisor_config", lambda: open(s + "/config", "a").write("[extensions]\npartialClone = origin\n[remote \"origin\"]\npromisor = true\nurl = file://" + sys.argv[3] + "\nuploadpack = " + sys.argv[4] + "\n"))
print(json.dumps(r))
"""
lazy_src = fx.init(WORK / "lazysrc")
lazy_blob = fx.blob(lazy_src, b"lazy = 1\n")
c_lazy = fx.commit(lazy_src, fx.mktree(lazy_src, [("100644", "blob", lazy_blob, b"lazy.py")]))
here_blob = fx.blob(lazy_src, b"here = 1\n")
c_here = fx.commit(lazy_src, fx.mktree(lazy_src, [("100644", "blob", here_blob, b"here.py")]))
fx.git(lazy_src, "update-ref", "refs/heads/main", c_lazy)
fx.git(lazy_src, "update-ref", "refs/heads/other", c_here)
for k, v in (("uploadpack.allowFilter", "true"), ("uploadpack.allowAnySHA1InWant", "true")):
    fx.git(lazy_src, "config", k, v)
unauth = fx.init(WORK / "unauthorized")
fx.blob(unauth, b"only here\n")
marker = WORK / "FETCH_ATTEMPTED"
helper = WORK / "marker-upload-pack"
helper.write_text("#!/bin/sh\necho called >> %s\nexec git-upload-pack \"$@\"\n" % marker)
helper.chmod(0o755)
chown_runner(WORK)
part = WORK / "part"
RG = [*SETPRIV, "env", "-i", "PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_GLOBAL=/dev/null", "git"]
subprocess.run(RG + ["clone", "-q", "--no-checkout", "--filter=blob:none", "file://" + str(lazy_src), str(part)], check=True, capture_output=True)
subprocess.run(RG + ["-C", str(part), "config", "remote.origin.uploadpack", str(helper)], check=True)
subprocess.run(RG + ["-C", str(part), "fetch", "-q", "origin", "other"], capture_output=True)
subprocess.run(RG + ["-C", str(part), "cat-file", "blob", here_blob], capture_output=True)
missing_here = subprocess.run(RG + ["-C", str(part), "rev-list", "--objects", "--missing=print", c_here], capture_output=True, text=True).stdout
missing_lazy = subprocess.run(RG + ["-C", str(part), "rev-list", "--objects", "--missing=print", c_lazy], capture_output=True, text=True).stdout
marker.unlink(missing_ok=True)
pp = produce(part, "sha1", [part])
psnap = pp["result"]["snapshot"]
inj = json.loads(as_runner(INJECT, psnap, str(unauth / ".git" / "objects"), str(lazy_src), str(helper)))
case("C3_inject_alternates_after_publish_kernel_refused", "EACCES", inj.get("inject_alternates"))
case("C4_inject_promisor_config_after_publish_kernel_refused", "EACCES", inj.get("inject_promisor_config"))
if any(l.startswith("?") for l in missing_lazy.splitlines()):
    case("C4_C8B_missing_required_object_no_network", {"result": "REFUSED:object_missing", "fetch_marker": False},
         {"result": outcome(reader("acquire", snapshot=psnap, expected=expected(c_lazy))), "fetch_marker": marker.exists()},
         precondition_object_absent_in_source=True)
else:
    out["not_tested"].append({"C8B": "fixture did not leave the object absent"})
if missing_here.strip() and not any(l.startswith("?") for l in missing_here.splitlines()):
    case("C8A_partial_clone_complete_closure_accepted", {"result": "ACCEPTED", "fetch_marker": False},
         {"result": outcome(reader("acquire", snapshot=psnap, expected=expected(c_here))), "fetch_marker": marker.exists()},
         precondition_closure_complete_in_source=True)
else:
    out["not_tested"].append({"C8A": "fixture could not complete the closure", "rev_list": missing_here[-200:]})

# ---------------------------------------------------------------- C9 negative ------------------------------
bad = fx.init(WORK / "bad")
(bad / ".git" / "objects" / "info" / "alternates").write_text(str(unauth / ".git" / "objects") + "\n")
c_bad = fx.commit(bad, fx.mktree(bad, [("100644", "blob", fx.blob(bad, b"b\n"), b"b.py")]))
chown_runner(WORK)
case("C9_unauthorized_alternate_typed_refusal", "REFUSED:alternate_outside_authorized_storage",
     outcome(produce(bad, "sha1", [bad])))

# ---------------------------------------------------------------- C5 inflate bomb ---------------------------
bomb = fx.init(WORK / "bomb")
payload = b"\0" * (256 << 20)
bomb_oid = __import__("hashlib").sha1(b"blob %d\0" % len(payload) + payload).hexdigest()
(bomb / ".git" / "objects" / bomb_oid[:2]).mkdir(exist_ok=True)
(bomb / ".git" / "objects" / bomb_oid[:2] / bomb_oid[2:]).write_bytes(zlib.compress(b"blob %d\0" % len(payload) + payload, 9))
del payload
c_unrel = fx.commit(bomb, fx.mktree(bomb, [("100644", "blob", fx.blob(bomb, b"s\n"), b"s.py")]))
c_in = fx.commit(bomb, fx.mktree(bomb, [("100644", "blob", bomb_oid, b"zeros.bin")]))
chown_runner(WORK)
pb = produce(bomb, "sha1", [bomb])
case("C5_producer_never_inflates", {"status": "ok", "producer_vmhwm_under_64MiB": True},
     {"status": pb["status"], "producer_vmhwm_under_64MiB": pb.get("rss_MiB", 999) < 64},
     producer_vmhwm_MiB=pb.get("rss_MiB"), producer_heap_MiB=pb.get("heap_peak_MiB"),
     physical_bytes=pb.get("result", {}).get("receipt", {}).get("physical_bytes"), inflated_bytes=256 << 20)
case("C5_unrelated_bomb_does_not_affect_runner_S_G", "ACCEPTED",
     outcome(reader("acquire", snapshot=pb["result"]["snapshot"], expected=expected(c_unrel))))
r5 = reader("acquire", snapshot=pb["result"]["snapshot"], expected=expected(c_in), abort_delay_s=1.0)
case("C5_bomb_in_closure_runner_bounded_refusal", {"refused": True, "git_unit_under_128MiB_envelope": True},
     {"refused": r5["status"] == "refused", "git_unit_under_128MiB_envelope": r5.get("git_unit_rss_MiB_upper_bound", 999) <= 128},
     reason=r5.get("result", {}).get("reason"), git_unit_rss_MiB_upper_bound=r5.get("git_unit_rss_MiB_upper_bound"))

# per-occurrence charging and the path-byte budget in the C reader (review of a858dc9: Codex P1 x2 + adversarial F2)
occ = fx.init(WORK / "occ")
mib = fx.blob(occ, os.urandom(1 << 20))
c_occ = fx.commit(occ, fx.mktree(occ, [("100644", "blob", mib, b"f%03d" % i) for i in range(100)]))
c_occ_ok = fx.commit(occ, fx.mktree(occ, [("100644", "blob", mib, b"f%03d" % i) for i in range(4)]))
deep = fx.init(WORK / "deep")
e0 = fx.blob(deep, b"")
t = fx.mktree(deep, [("100644", "blob", e0, b"%04d" % i + b"L" * 246) for i in range(700)])
for _ in range(98):
    t = fx.mktree(deep, [("040000", "tree", t, b"D" * 250)])
c_deep = fx.commit(deep, fx.mktree(deep, [("040000", "tree", t, b"top")]))
chown_runner(WORK)
socc, sdeep = produce(occ, "sha1", [occ])["result"]["snapshot"], produce(deep, "sha1", [deep])["result"]["snapshot"]
case("C5_blob_charged_per_occurrence_refused", "REFUSED:budget_payload_bytes",
     outcome(reader("acquire", snapshot=socc, expected=expected(c_occ), payload_budget=8 << 20)),
     shape="one 1 MiB blob at 100 paths, payload budget 8 MiB (a858dc9 accepted it and sealed 104.9 MB)")
case("C5_POSITIVE_repeated_blob_within_budget_accepted", "ACCEPTED",
     outcome(reader("acquire", snapshot=socc, expected=expected(c_occ_ok), payload_budget=8 << 20)))
case("C5_path_bytes_budget_enforced_during_walk", "REFUSED:budget_path_bytes",
     outcome(reader("acquire", snapshot=sdeep, expected=expected(c_deep))),
     shape="700 leaves under 98 dirs of 250-byte names: ~18.6 MB of path bytes vs max_path_bytes 16 MiB (a858dc9 accepted)")
scan = fx.init(WORK / "scan")
c_scan = fx.commit(scan, fx.mktree(scan, [("100644", "blob", fx.blob(scan, b"s\n"), b"s.py")]))
fan = scan / ".git" / "objects" / "ab"
fan.mkdir(exist_ok=True)
for i in range(1200):
    (fan / f"tmp_obj_{i:05d}").write_bytes(b"")
chown_runner(WORK)
case("C5_producer_listing_charged_to_entry_budget", "REFUSED:physical_budget_exceeded",
     outcome(produce(scan, "sha1", [scan], max_entries=1000)),
     shape="1,200 non-object names in one fanout dir, max_entries 1000 (a858dc9 listed them unbounded)")

# ---------------------------------------------------------------- C6 identity (runner) ----------------------
r6 = reader("c6", snapshot=str(snap), expected=expected(c_src), other_tree=nested)
res6 = r6.get("result", {})
derived = subprocess.run(["git", "-C", str(src), "rev-parse", c_src + "^{tree}"], capture_output=True, text=True,
                         env=fx.FIXTURE_ENV).stdout.strip()
# aux_record_no_effect holds BY CONSTRUCTION (commit_sg takes no root input); the discriminators are the ablation
# and the forged map entry (review of a858dc9, adversarial F6)
case("C6_root_derived_from_authenticated_commit_only", {"root": derived, "aux_record_no_effect": True},
     {"root": res6.get("root"), "aux_record_no_effect": res6.get("root_with_aux_records_present") == derived and res6.get("digest_stable")})
case("C6_ABLATION_trusting_aux_root_changes_S_G", True, res6.get("ablation_changes_S_G"), ablation_root=res6.get("ablation_root"))
case("C6_forged_object_map_entry_refused", "REFUSED:object_map_binding_mismatch", res6.get("forged_map"))

# ---------------------------------------------------------------- C7 process tree (runner-side unit) --------
FAKE = r"""
import os, subprocess, sys, time
tag = sys.argv[1]
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)", tag + "-child"])
if os.fork() == 0:
    os.setsid()
    if os.fork() == 0:
        os.execv(sys.executable, [sys.executable, "-c", "import time; time.sleep(3600)", tag + "-grandchild-setsid"])
    os._exit(0)
time.sleep(3600)
"""


def c7(subreaper: bool):
    tag = f"{NONCE}-C7-{'unit' if subreaper else 'ablation'}"
    r = reader("c7", cwd=str(WORK), argv=[PY, "-c", FAKE, tag], subreaper=subreaper)
    time.sleep(1.0)
    surv = [(pid, start) for (pid, start), cmd in census()[0].items() if tag in cmd]
    return r, surv


r7, s7 = c7(True)
case("C7_runner_unit_teardown_0_survivors", {"reader": "REFUSED:transport_deadline", "survivors": []},
     {"reader": r7.get("result", {}).get("reader"), "survivors": s7}, unit=r7.get("result", {}).get("unit"), reader_uid=r7.get("uid"))
r7b, s7b = c7(False)
case("C7_ABLATION_process_group_only_leaves_setsid_survivor", True, len(s7b) > 0, survivors=s7b)
for pid, start in s7b:  # cleanup ONLY by pid + start time + nonce tag
    cmd = census()[0].get((pid, start), "")
    if NONCE in cmd:
        os.kill(pid, signal.SIGKILL)

# ---------------------------------------------------------------- C10 crash before publish -------------------
crash = fx.init(WORK / "crash")
blobs = [fx.blob(crash, b"x%d\n" % i) for i in range(40)]
c_crash = fx.commit(crash, fx.mktree(crash, [("100644", "blob", b, b"f%02d" % i) for i, b in enumerate(blobs)]))
chown_runner(WORK)
committed_before, staging_before = set(os.listdir(BASE / "committed")), set(os.listdir(BASE / "staging"))
args = {"paths": PATHS, "repo_root": str(crash), "object_format": "sha1", "storage_roots": [str(crash)], "out_base": str(BASE), "slow_s": 0.2}
proc = subprocess.Popen([PY, "-I", "-S", "-B", str(HERE / "s0_snapshot_c.py"), json.dumps(args)], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
time.sleep(2.0)
proc.send_signal(signal.SIGKILL)
proc.wait()
case("C10_sigkill_before_publish_no_committed_snapshot", {"new_committed": [], "staging_garbage": True},
     {"new_committed": sorted(set(os.listdir(BASE / "committed")) - committed_before),
      "staging_garbage": bool(set(os.listdir(BASE / "staging")) - staging_before)},
     runner_listing_staging=as_runner("import os,sys\ntry:\n    print(os.listdir(sys.argv[1]))\nexcept OSError as e:\n    print('DENIED', e.errno)",
                                      str(BASE / "staging")),
     commit_point="rename(staging/<id> -> committed/<id>) after modes + fsync")

# ---------------------------------------------------------------- positives: sha256 + real toolrepo ------------
r256 = fx.init(WORK / "sha256", "sha256")
c256 = fx.commit(r256, fx.mktree(r256, [("100644", "blob", fx.blob(r256, b"s = 256\n"), b"s.py")]))
chown_runner(WORK)
p256 = produce(r256, "sha256", [r256])
case("POSITIVE_sha256_runner_S_G", {"snapshot_format": "sha256", "result": "ACCEPTED"},
     {"snapshot_format": p256.get("result", {}).get("receipt", {}).get("object_format"),
      "result": outcome(reader("acquire", snapshot=p256["result"]["snapshot"], expected=expected(c256, "sha256")))})
preal = produce(Path(REAL), "sha1", [REAL])
areal = reader("acquire", snapshot=preal["result"]["snapshot"], expected=expected(REAL_COMMIT))
case("C11_POSITIVE_real_toolrepo_runner_derived_S_G_equals_prior_record",
     {"uid": RUNNER, "s_g": "95504743b1075c6dd6e9be27014e6b6dde13f0385dcf54621baaac373e9b0085"},
     {"uid": areal.get("uid"), "s_g": areal.get("result", {}).get("s_g_sha256")},
     note="positive equivalence control on this exact subject; the producer only published the snapshot")
out["resource_vector"] = {
    "physical_snapshot": {"compressed_bytes": preal["result"]["receipt"]["physical_bytes"],
                          "entry_count": preal["result"]["receipt"]["physical_entries"],
                          "alternate_depth": preal["result"]["alternate_depth"], "producer_heap_MiB": preal["heap_peak_MiB"],
                          "producer_rss_MiB_vmhwm": preal["rss_MiB"], "io": preal["io"], "time_s": preal["time_s"],
                          "producer_uid": preal["uid"]},
    "subject_closure": {"object_count_unique": areal["result"].get("unique_objects"), "object_reads": areal["result"].get("objects_read"),
                        "nodes": areal["result"].get("nodes"), "payload_bytes_charged_per_occurrence": areal["result"].get("payload_bytes"),
                        "metadata_bytes": areal["result"].get("metadata_bytes"), "path_bytes": areal["result"].get("path_bytes"),
                        "reader_heap_MiB": areal["heap_peak_MiB"], "reader_rss_MiB_vmhwm": areal["rss_MiB"],
                        "local_git_processes": 1, "per_object_deadline_s": 30, "time_s": areal["time_s"], "reader_uid": areal["uid"]},
    "local_git_unit": {"rss_MiB_upper_bound": areal.get("git_unit_rss_MiB_upper_bound"), "envelope": "RLIMIT_AS 128 MiB per process",
                       "aggregate_memory": "NOT_TESTED (no cgroup)"}}
out["not_tested"] += ["CT104", "production producer/service and its provenance (U3)", "C2_B host policy provenance (#331)",
                      "#319 anchor provenance", "cgroup aggregate memory of the git unit", "total-capture deadline",
                      "production staging GC", "R4-3 envelope vs mapped packs (reproduced by review F3 as unavailability, not re-run here)",
                      "teardown with a descendant in uninterruptible sleep (fail-closed path by code reading only)",
                      "producer short write (retry by code reading only)"]

time.sleep(0.5)
after = census()
new = {k: v for k, v in after[0].items() if k not in BEFORE[0] and "exp_arch_c" not in v}
case("LIFECYCLE_no_survivors_no_listeners", {"processes": {}, "listeners": []},
     {"processes": {f"{p}@{s}": c for (p, s), c in new.items()}, "listeners": sorted(after[1] - BEFORE[1])})
out["all_pass"] = all(c["pass"] for c in out["cases"].values())
print(json.dumps(out, indent=1, default=str))
