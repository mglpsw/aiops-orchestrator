"""EXP-CAPTURE: stability of the committed S, the pre-seal window, and binding substitution.

usage: python -I -S exp_capture_stability.py <toolrepo_source_root> <scratch_dir> <interpreter>
Every attacker below is a SEPARATE same-UID process that reaches the object through
/proc/<pid>/fd/<n> (no shared Python state with the producer).
"""
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
SRC = Path(sys.argv[1]).resolve()
sys.path.insert(1, str(SRC))
W = Path(sys.argv[2]).resolve()
PY = sys.argv[3]

import s0_capture as cap  # noqa: E402

def admitted(**kw):
    """Explicit admission policy (decision (d)): 255 = C3's enumeration default, stated, never implied."""
    return cap.Budget(max_component_len=255, **kw)

import s0_fixture as fx  # noqa: E402
import s0_launch as ln  # noqa: E402

out = {"cases": {}}


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


ATTACK_SEALED = r"""
import ctypes, fcntl, json, mmap, os, sys
p = sys.argv[1]; r = {}
libc = ctypes.CDLL(None, use_errno=True)
libc.mmap.restype = ctypes.c_void_p
libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
def tr(k, f):
    try: f(); r[k] = "SUCCEEDED"
    except OSError as e: r[k] = "denied:%d" % e.errno
def check(rc, what):
    if rc != 0: raise OSError(ctypes.get_errno(), what)
fd = os.open(p, os.O_RDWR); r["open_rdwr"] = "ok"
tr("write", lambda: os.write(fd, b"EVIL"))
tr("pwrite", lambda: os.pwrite(fd, b"EVIL", 3))
tr("ftruncate_shrink", lambda: os.ftruncate(fd, 1))
tr("ftruncate_grow", lambda: os.ftruncate(fd, 1 << 20))
tr("fallocate_grow", lambda: os.posix_fallocate(fd, 1 << 20, 4096))
tr("fallocate_punch_hole", lambda: check(libc.fallocate(fd, 3, ctypes.c_long(0), ctypes.c_long(4096)), "punch"))
tr("mmap_shared_write", lambda: mmap.mmap(fd, 16, mmap.MAP_SHARED, mmap.PROT_WRITE))
def ro_map_then_mprotect_write():
    addr = libc.mmap(None, 4096, mmap.PROT_READ, mmap.MAP_SHARED, fd, 0)
    if addr in (None, ctypes.c_void_p(-1).value): raise OSError(ctypes.get_errno(), "mmap")
    check(libc.mprotect(addr, 4096, mmap.PROT_READ | mmap.PROT_WRITE), "mprotect")
    ctypes.memmove(addr, b"EVIL", 4)
tr("shared_ro_map_then_mprotect_write", ro_map_then_mprotect_write)
tr("add_seal_future_write", lambda: fcntl.fcntl(fd, fcntl.F_ADD_SEALS, 16))
priv = mmap.mmap(fd, 16, mmap.MAP_PRIVATE, mmap.PROT_READ | mmap.PROT_WRITE); priv[0:4] = b"EVIL"
r["map_private_cow_write"] = "SUCCEEDED(copy-on-write; file must be unchanged)"
print(json.dumps(r))
"""

ATTACK_WRITE_ONCE = r"""
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR); os.pwrite(fd, b"EVIL", 0); print("wrote")
"""

ATTACK_ADD_SEAL_SEAL = r"""
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR); fcntl.fcntl(fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SEAL); print("sealed-seal")
"""

ATTACK_HOLD_WRITABLE_MAP = r"""
import mmap, os, sys
fd = os.open(sys.argv[1], os.O_RDWR); m = mmap.mmap(fd, 16, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
print("mapped", flush=True); sys.stdin.readline()
"""


def attacker(src, target, **kw):
    return subprocess.run([PY, "-I", "-S", "-c", src, target], capture_output=True, text=True, timeout=30, **kw)


data = b"S0-SEALED-CONTENT\n" * 256
digest = hashlib.sha256(data).hexdigest()

# --- committed S against a separate same-UID process -----------------------------------------
fd = cap.seal_committed(data)
seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
r = attacker(ATTACK_SEALED, f"/proc/{os.getpid()}/fd/{fd}")
res = json.loads(r.stdout) if r.returncode == 0 else {"rc": r.returncode, "err": r.stderr[-400:]}
out["cases"]["sealed_attack_battery"] = {"observed": res, "F_GET_SEALS": hex(seals),
                                         "pass": all(not str(v).startswith("SUCCEEDED") or k == "map_private_cow_write"
                                                     for k, v in res.items() if k != "open_rdwr")}
case("sealed_digest_unchanged_after_attacks", digest, hashlib.sha256(cap.open_sealed(fd, digest)).hexdigest())
os.close(fd)

# --- pre-seal window: a writer that reaches the memfd BEFORE the seal ---------------------------
def pre_seal(src, **kw):
    def hook(f):
        kw["result"] = attacker(src, f"/proc/{os.getpid()}/fd/{f}").stdout.strip()
    return hook


def outcome(fn):
    try:
        f = fn()
        os.close(f)
        return "COMMITTED"
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


case("pre_seal_write_detected_at_commit", "REFUSED:sealed_content_mismatch",
     outcome(lambda: cap.seal_committed(data, after_write=pre_seal(ATTACK_WRITE_ONCE))))
case("ABLATION_no_post_seal_rehash_commits_tampered", "COMMITTED",
     outcome(lambda: cap.seal_committed(data, after_write=pre_seal(ATTACK_WRITE_ONCE), verify_after_seal=False)))
case("pre_seal_foreign_F_SEAL_SEAL_fails_closed", "REFUSED:seal_failed",
     outcome(lambda: cap.seal_committed(data, after_write=pre_seal(ATTACK_ADD_SEAL_SEAL))))

holder = {}


def hold_map(f):
    holder["p"] = subprocess.Popen([PY, "-I", "-S", "-c", ATTACK_HOLD_WRITABLE_MAP, f"/proc/{os.getpid()}/fd/{f}"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    holder["p"].stdout.readline()


case("pre_seal_foreign_writable_mapping_fails_closed", "REFUSED:seal_failed",
     outcome(lambda: cap.seal_committed(data, after_write=hold_map)))
holder["p"].communicate("go\n", timeout=10)
case("control_no_attacker_commits", "COMMITTED", outcome(lambda: cap.seal_committed(data)))

# --- capture vs later mutation of M; consumer never falls back to M ----------------------------
repo = fx.init(W / "cap")
b_init = fx.blob(repo, b'VALUE = "trusted"\n')
pkg = fx.mktree(repo, [("100644", "blob", b_init, b"__init__.py")])
c = fx.commit(repo, fx.mktree(repo, [("040000", "tree", pkg, b"pkg"), ("040000", "tree", fx.empty_tree(repo), b"ns")]))
s = cap.build_subject(repo, c, budget=admitted())
s_bytes = cap.serialize(s.algorithm, s.commit, s.root_tree, s.nodes)
s_fd, s_digest = cap.commit_subject(s)  # seal + post-seal revalidation of the final object
assert s_digest == hashlib.sha256(s_bytes).hexdigest()

# --- decision (b): the committed representation is the truth-maker ------------------------------
EVIL_PKG = b'VALUE = "EVIL"\n'


def commit_outcome(**kw):
    try:
        fd, digest = cap.commit_subject(s, **kw)
        body = cap.parse(os.pread(fd, os.fstat(fd).st_size, 0))[3]
        os.close(fd)
        return "COMMITTED:" + body[b"pkg/__init__.py"][2].decode().strip()
    except cap.CaptureRefused as exc:
        return "REFUSED:" + exc.reason


def substitute_payload(nodes):  # authenticate A, then hand B to the representation (same oid label)
    kind, oid, _ = nodes[b"pkg/__init__.py"]
    nodes[b"pkg/__init__.py"] = (kind, oid, EVIL_PKG)
    return nodes


def add_node(nodes):
    nodes[b"planted.py"] = ("regular", b_init, b'VALUE = "trusted"\n')
    return nodes


case("authenticate_A_substitute_B_before_commit_refused", "REFUSED:sealed_binding_mismatch",
     commit_outcome(_before_serialize=substitute_payload),
     note="the seal digest is computed over B, so only the post-seal binding revalidation can catch it")
case("ABLATION_no_post_seal_revalidation_commits_B", 'COMMITTED:VALUE = "EVIL"',
     commit_outcome(_before_serialize=substitute_payload, revalidate=False))
case("node_not_in_acquisition_record_refused", "REFUSED:sealed_record_mismatch",
     commit_outcome(_before_serialize=add_node))
case("mutate_unsealed_S_then_seal_refused", "REFUSED:sealed_content_mismatch",
     commit_outcome(_after_write=pre_seal(ATTACK_WRITE_ONCE)))

from app.agent_review.git_commit_subject_v2 import materialise_commit_subject_v2  # noqa: E402

M = W / "cap-M"
materialise_commit_subject_v2(repo_root=repo, ref=c, destination=M)
(M / "pkg" / "__init__.py").write_bytes(b'VALUE = "EVIL"\n')
(M / "ns" / "__init__.py").write_bytes(b'VALUE = "EVIL-NS"\n')
(M / "extra.py").write_bytes(b'VALUE = "EXTRA"\n')
case("M_mutated_after_capture_S_unchanged", s_digest, hashlib.sha256(cap.open_sealed(s_fd, s_digest)).hexdigest())

DRIVER = b"""
def run(inputs):
    import importlib
    out = {}
    for name in inputs["mods"]:
        try:
            m = importlib.import_module(name)
            out[name] = {"value": getattr(m, "VALUE", None), "loader": type(m.__spec__.loader).__name__,
                         "has_file": hasattr(m, "__file__")}
        except Exception as exc:
            out[name] = {"error": type(exc).__name__}
    return out
"""
x_fd = cap.seal_committed(DRIVER)
x = {"fd": x_fd, "sha256": hashlib.sha256(DRIVER).hexdigest()}
base_spec = {"s": {"fd": s_fd, "sha256": s_digest, "commit": c}, "d": None, "x": x,
             "roots": ["pkg", "ns", "extra"], "inputs": {"mods": ["pkg", "ns", "extra"]}, "watch": [str(M)]}
run = ln.launch(PY, base_spec, (s_fd, x_fd), cwd=str(M), env={"PYTHONPATH": str(M), "PYTHONSAFEPATH": ""})
reply = run["reply"] or {}
case("consumer_imports_trusted_bytes_from_S", {"pkg": "trusted", "ns": "NamespaceLoader", "extra": "ModuleNotFoundError"},
     {"pkg": (reply.get("result") or {}).get("pkg", {}).get("value"),
      "ns": (reply.get("result") or {}).get("ns", {}).get("loader"),
      "extra": (reply.get("result") or {}).get("extra", {}).get("error")},
     note="cwd=M and PYTHONPATH=M were offered; -I -S and the closed finder must ignore both")
case("consumer_opens_under_M", [], (reply.get("observed") or {}).get("opens_watched"))

# --- binding substitution: every wrong handoff is refused before the driver runs -------------
plain = tempfile.TemporaryFile()
plain.write(s_bytes)
plain.flush()
evil_s = cap.serialize(s.algorithm, s.commit, s.root_tree, {**s.nodes, b"pkg/__init__.py": ("regular", b_init, b'VALUE = "EVIL"\n')})
evil_fd = cap.seal_committed(evil_s)
weak_fd = os.memfd_create("weak", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
os.write(weak_fd, s_bytes)
fcntl.fcntl(weak_fd, fcntl.F_ADD_SEALS, fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SEAL)  # no F_SEAL_WRITE
closed_fd = os.dup(s_fd)
os.close(closed_fd)
mislabeled = cap.serialize("sha256", s.commit, s.root_tree, s.nodes)  # right commit, wrong algorithm label (R2-4)
mislabeled_fd = cap.seal_committed(mislabeled)
SUBST = {
    "regular_file_with_identical_bytes": ({"fd": plain.fileno(), "sha256": s_digest, "commit": c}, (plain.fileno(),),
                                          "descriptor_not_sealable"),
    "attacker_sealed_memfd_other_bytes": ({"fd": evil_fd, "sha256": s_digest, "commit": c}, (evil_fd,),
                                          "descriptor_digest_mismatch"),
    "expected_identity_absent": ({"fd": s_fd, "commit": c}, (s_fd,), "expected_identity_absent"),
    "memfd_without_F_SEAL_WRITE": ({"fd": weak_fd, "sha256": s_digest, "commit": c}, (weak_fd,), "descriptor_not_sealed"),
    "descriptor_not_inherited": ({"fd": closed_fd, "sha256": s_digest, "commit": c}, (), "descriptor_invalid"),
    "right_bytes_wrong_subject_identity": ({"fd": s_fd, "sha256": s_digest, "commit": "0" * 40}, (s_fd,),
                                           "subject_identity_mismatch"),
    "right_commit_wrong_algorithm_label": ({"fd": mislabeled_fd, "sha256": hashlib.sha256(mislabeled).hexdigest(),
                                            "commit": c}, (mislabeled_fd,), "subject_algorithm_mismatch"),
}
for name, (s_part, fds, want) in SUBST.items():
    spec = dict(base_spec, s=s_part)
    r = ln.launch(PY, spec, (*fds, x_fd), cwd="/")
    rep = r["reply"] or {}
    case(f"binding_{name}", f"refused:{want}", f"{rep.get('status')}:{rep.get('reason')}",
         driver_ran="result" in rep)

out["all_pass"] = all(c.get("pass") for c in out["cases"].values())
print(json.dumps(out, indent=1))
