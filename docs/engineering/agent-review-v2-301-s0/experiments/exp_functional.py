"""EXP-FUNC: the real engine percurso executed ONLY from S (engine) + D (dependencies).

usage: python -I -S -B exp_functional.py <toolrepo_clone> <expected_commit> <wheels_dir> <venv_dir>
                                      <target_fixture_dir> <scratch_dir> <base_interpreter> <producer_src>
<toolrepo_clone> is the MUTABLE checkout (git objects read through hash-on-read; normal-path runs);
<producer_src> is the root-owned copy the producer imports C3's tree rule from (declared TCB).
Positive: E's result equals the normal (checkout + venv) run on untampered inputs.
Countermodels: after capture, tamper the checkout source, plant a .pyc behind an intact source,
tamper the venv, plant a pydantic plugin distribution -- the normal path executes each, E none.
Census: every module E loaded, by origin class; set difference against the normal path.
"""
import hashlib
import importlib.util
import json
import marshal
import os
import shutil
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLONE, COMMIT, WHEELS, VENV, TARGET_FIXTURE, W, BASE, PRODUCER_SRC = (
    Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3]).resolve(), Path(sys.argv[4]).resolve(),
    Path(sys.argv[5]).resolve(), Path(sys.argv[6]).resolve(), sys.argv[7], Path(sys.argv[8]).resolve())
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(PRODUCER_SRC))  # producer-side only: C3's _parse_tree_data, from the root-owned copy

import s0_capture as cap  # noqa: E402
import s0_deps as deps  # noqa: E402
import s0_launch as ln  # noqa: E402

out = {"cases": {}, "resources": {}, "census": {}}
W.mkdir(parents=True, exist_ok=True)
MARK = W / "markers"
MARK.mkdir()


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


DIFF = """diff --git a/backend/scheduling/shift_rules.py b/backend/scheduling/shift_rules.py
index 1111111..2222222 100644
--- a/backend/scheduling/shift_rules.py
+++ b/backend/scheduling/shift_rules.py
@@ -1,2 +1,3 @@
 def rule():
-    return 1
+    value = 2
+    return value
diff --git a/tests/scheduling/test_shift_rules.py b/tests/scheduling/test_shift_rules.py
index 3333333..4444444 100644
--- a/tests/scheduling/test_shift_rules.py
+++ b/tests/scheduling/test_shift_rules.py
@@ -1,2 +1,2 @@
 def test_rule():
-    assert True
+    assert 1 + 1 == 2
"""
target = W / "target"
shutil.copytree(TARGET_FIXTURE, target)
INPUTS = {"target_root": str(target), "diff_text": DIFF, "pr_number": 101, "base_sha": "1" * 40,
          "head_sha": "2" * 40, "toolrepo_sha": COMMIT,
          "rules": [{"rule_id": "backend", "group": "primary_backend_logic", "patterns": ["backend/scheduling/*.py"]},
                    {"rule_id": "tests", "group": "tests", "patterns": ["tests/scheduling/*.py"]}]}

# ---------------- capture: S (Git closure) and D (lock-authenticated wheels) --------------------
fds_before = cap.open_fds()
tracemalloc.start()
t0 = time.perf_counter()
subject = cap.build_subject(CLONE, COMMIT)
t_build = time.perf_counter() - t0
t0 = time.perf_counter()
s_bytes = cap.serialize(subject.algorithm, subject.commit, subject.root_tree, subject.nodes)
s_digest = hashlib.sha256(s_bytes).hexdigest()
s_fd = cap.seal_committed(s_bytes)
t_seal = time.perf_counter() - t0
_, heap_peak_s = tracemalloc.get_traced_memory()
tracemalloc.reset_peak()
lock_bytes = cap.lock_bytes(subject.nodes)
TARGET = json.loads(subprocess.run(
    [BASE, "-I", "-S", "-c", "import json,sys,importlib.machinery as m;"
     "print(json.dumps(['cp%d%d' % sys.version_info[:2], m.EXTENSION_SUFFIXES]))"],
    env={}, capture_output=True, text=True, check=True).stdout)
PY_TAG, EXT_SUFFIXES = TARGET[0], tuple(TARGET[1])
D_ALG = "wheelset-sha256/" + PY_TAG
t0 = time.perf_counter()
d_nodes, d_manifest, d_payload = deps.build_dependencies(lock_bytes, WHEELS, python_tag=PY_TAG, ext_suffixes=EXT_SUFFIXES)
d_bytes = cap.serialize(D_ALG, hashlib.sha256(lock_bytes).hexdigest(),
                        hashlib.sha256(json.dumps(d_manifest, sort_keys=True).encode()).hexdigest(), d_nodes)
d_digest = hashlib.sha256(d_bytes).hexdigest()
d_fd = cap.seal_committed(d_bytes)
t_deps = time.perf_counter() - t0
_, heap_peak_d = tracemalloc.get_traced_memory()
tracemalloc.stop()
driver_src = (HERE / "s0_driver_engine.py").read_bytes()
x_fd = cap.seal_committed(driver_src)
x_digest = hashlib.sha256(driver_src).hexdigest()

kinds = {}
for _p, (k, _o, _v) in subject.nodes.items():
    kinds[k] = kinds.get(k, 0) + 1
empty_trees = sum(1 for p, (k, _o, _v) in subject.nodes.items()
                  if k == "tree" and not any(q.startswith(p + b"/") for q in subject.nodes))
out["resources"]["S"] = {
    "commit": COMMIT, "algorithm": subject.algorithm, "nodes": len(subject.nodes), "by_kind": kinds,
    "empty_trees": empty_trees, "container_bytes": len(s_bytes), "payload_bytes_per_occurrence": subject.budget.payload_bytes,
    "payload_bytes_unique_blobs": sum(subject.budget.unique_payload.values()), "path_bytes": subject.budget.path_bytes,
    "git_objects_read": subject.objects_read, "git_bytes_read": subject.git_bytes_read,
    "git_subprocesses_counted": cap.GIT_INVOCATIONS[0], "metadata_bytes_read": subject.budget.metadata_bytes,
    "build_s": round(t_build, 3), "serialize_seal_verify_s": round(t_seal, 3),
    "producer_heap_peak_MiB_build_to_seal": round(heap_peak_s / 2**20, 1), "sha256": s_digest}
out["resources"]["D"] = {"wheels": d_manifest, "nodes_including_directories": len(d_nodes),
                         "members": sum(1 for k, _o, _v in d_nodes.values() if k != "tree"), "python_tag": PY_TAG, "payload_bytes": d_payload,
                         "container_bytes": len(d_bytes), "build_seal_s": round(t_deps, 3),
                         "producer_heap_peak_MiB": round(heap_peak_d / 2**20, 1), "sha256": d_digest,
                         "lock_sha256": hashlib.sha256(lock_bytes).hexdigest()}


# ---------------- runners -----------------------------------------------------------------------
def run_E(label):
    spec = {"s": {"fd": s_fd, "sha256": s_digest, "commit": COMMIT}, "d": {"fd": d_fd, "sha256": d_digest},
            "x": {"fd": x_fd, "sha256": x_digest}, "roots": ["app"], "inputs": INPUTS,
            "watch": [str(CLONE), str(VENV)]}
    r = ln.launch(BASE, spec, (s_fd, d_fd, x_fd), cwd=str(CLONE), timeout=180)
    out.setdefault("runs", {})[label] = {k: r[k] for k in ("rc", "elapsed_s", "stderr_tail") if k in r}
    return r["reply"] or {"status": "absent"}


NORMAL_RUNNER = r"""
import json, sys, time
t0 = time.perf_counter()
ns = {}
exec(compile(open(sys.argv[1]).read(), sys.argv[1], "exec"), ns)
try:
    result, status = ns["run"](json.loads(sys.argv[2])), "ok"
except BaseException as exc:
    result, status = {"error": "%s: %s" % (type(exc).__name__, exc)}, "driver_failed"
classes = {}
for n, m in sorted(sys.modules.items()):
    o = getattr(getattr(m, "__spec__", None), "origin", None) or ""
    cls = ("checkout" if o.startswith(sys.argv[3]) else "venv_site_packages" if "site-packages" in o else
           "stdlib" if o.startswith(sys.base_prefix) else "builtin_or_frozen" if o in ("built-in", "frozen") else
           "namespace_or_other:" + o[:40])
    classes.setdefault(cls, []).append(n)
print(json.dumps({"status": status, "result": result, "modules_by_origin": classes, "elapsed_s": time.perf_counter() - t0}))
"""


def run_normal(label):
    """The production shape: venv interpreter, PYTHONPATH = toolrepo checkout (AgentEscala v2 workflow)."""
    env = {"PYTHONPATH": str(CLONE), "PATH": os.defpath, "HOME": str(W)}
    cp = subprocess.run([str(VENV / "bin" / "python3"), "-c", NORMAL_RUNNER, str(HERE / "s0_driver_engine.py"),
                         json.dumps(INPUTS), str(CLONE)], env=env, cwd=str(W), capture_output=True, text=True, timeout=180)
    out.setdefault("runs", {})[label] = {"rc": cp.returncode, "stderr_tail": cp.stderr[-400:]}
    try:
        return json.loads(cp.stdout)
    except ValueError:
        return {"status": "absent", "stdout": cp.stdout[-300:]}


def verdict(reply):
    res = reply.get("result") or {}
    return res.get("bundle_sha256") or res.get("error") or reply.get("reason") or reply.get("status")


def markers():
    found = sorted(p.name for p in MARK.iterdir())
    for p in MARK.iterdir():
        p.unlink()
    return found


# ---------------- positive: parity with the normal path -------------------------------------------
normal0 = run_normal("normal_untampered")
e0 = run_E("E_untampered")
case("positive_E_result_equals_normal_path", verdict(normal0), verdict(e0),
     readiness=((e0.get("result") or {}).get("bundle") or {}).get("readiness_state"),
     chunks=((e0.get("result") or {}).get("bundle") or {}).get("chunks"))
case("E_no_open_under_checkout_or_venv", [], (e0.get("observed") or {}).get("opens_watched"))
case("E_no_subprocess_on_this_percurso", [], (e0.get("observed") or {}).get("subprocess"))
case("E_distribution_metadata_visible", [], e0.get("distributions_visible"))
out["census"]["E_modules_by_origin"] = {k: (v if not k.startswith(("stdlib", "builtin")) else len(v))
                                        for k, v in (e0.get("modules_by_origin") or {}).items()}
out["census"]["E_opens_outside_usr_proc_dev"] = (e0.get("observed") or {}).get("opens_other")
out["census"]["E_native_memfds"] = e0.get("native_fds")
out["census"]["E_import_pwd_stack"] = (e0.get("observed") or {}).get("import_pwd_stack")
out["census"]["E_flags_self_report"] = e0.get("flags")
out["census"]["normal_modules_by_origin_counts"] = {k: len(v) for k, v in (normal0.get("modules_by_origin") or {}).items()}
e_mods = {m for v in (e0.get("modules_by_origin") or {}).values() for m in v}
n_mods = {m for v in (normal0.get("modules_by_origin") or {}).values() for m in v}
out["census"]["modules_only_in_normal"] = sorted(n_mods - e_mods)
out["census"]["modules_only_in_E"] = sorted(e_mods - n_mods)
out["resources"]["E_child"] = {"maxrss_kib": (e0.get("result") or {}).get("maxrss_kib"),
                               "inherited_sealed_fds_by_construction": 3, "native_memfds_created_in_child": e0.get("native_fds"),
                               "result_channel": "socketpair", "elapsed_s": out["runs"]["E_untampered"].get("elapsed_s")}
out["resources"]["normal"] = {"maxrss_kib": (normal0.get("result") or {}).get("maxrss_kib"),
                              "elapsed_s": normal0.get("elapsed_s")}

# ---------------- countermodels: mutable storage modified AFTER capture ----------------------------
TARGET_MOD = CLONE / "app" / "agent_review" / "readiness_decision_v2.py"
original_src = TARGET_MOD.read_bytes()
EVIL_TAIL = b"\n\ndef compute_readiness_decision_v2(**kw):\n    raise RuntimeError('TAMPERED_CODE_EXECUTED')\n"

# (a) checkout source tampered
TARGET_MOD.write_bytes(original_src + EVIL_TAIL)
case("checkout_source_tampered", {"normal": "RuntimeError: TAMPERED_CODE_EXECUTED", "E": verdict(e0)},
     {"normal": verdict(run_normal("normal_source_tampered")), "E": verdict(run_E("E_source_tampered"))})
TARGET_MOD.write_bytes(original_src)
os.utime(TARGET_MOD, (1_700_000_000, 1_700_000_000))

# (b) source byte-identical to the commit, stale-looking-valid .pyc planted beside it
run_normal("normal_warm_pycache")  # the normal path writes __pycache__ into the checkout itself
pyc = Path(importlib.util.cache_from_source(str(TARGET_MOD)).replace(sys.implementation.cache_tag,
                                                                      "cpython-%d%d" % sys.version_info[:2]))
header = pyc.read_bytes()[:16]
pyc.write_bytes(header + marshal.dumps(compile(original_src + EVIL_TAIL, str(TARGET_MOD), "exec")))
blob_oid = subprocess.run(["git", "hash-object", str(TARGET_MOD)], capture_output=True, text=True,
                          env={"PATH": os.defpath, "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"}).stdout.strip()
committed_oid = subject.nodes[b"app/agent_review/readiness_decision_v2.py"][1]
case("pyc_planted_source_matches_commit",
     {"source_blob_equals_commit": True, "normal": "RuntimeError: TAMPERED_CODE_EXECUTED", "E": verdict(e0)},
     {"source_blob_equals_commit": blob_oid == committed_oid, "normal": verdict(run_normal("normal_pyc_planted")),
      "E": verdict(run_E("E_pyc_planted"))},
     note="verifying the SOURCE tree (Q/G1) does not cover the bytecode the normal path loads")
shutil.rmtree(pyc.parent)

# (c) venv file tampered after `pip install --require-hashes`
site = next(VENV.glob("lib/python3*/site-packages"))
pyd_init = site / "pydantic" / "__init__.py"
pyd_orig = pyd_init.read_bytes()
pyd_init.write_bytes(pyd_orig + b"\nimport posix as _p; _p.close(_p.open(%r, 0o101, 0o644))\n" % str(MARK / "venv_tamper").encode())
run_normal("normal_venv_tampered")
normal_marks = markers()
run_E("E_venv_tampered")
case("venv_installed_file_tampered", {"normal": ["venv_tamper"], "E": []}, {"normal": normal_marks, "E": markers()})
pyd_init.write_bytes(pyd_orig)

# (d) a pydantic plugin DISTRIBUTION planted in the venv: no locked file is modified at all
(site / "s0_plugin.py").write_text("import posix as _p; _p.close(_p.open(%r, 0o101, 0o644))\n" % str(MARK / "plugin"))
di = site / "s0_plugin-0.dist-info"
di.mkdir()
(di / "METADATA").write_text("Metadata-Version: 2.1\nName: s0-plugin\nVersion: 0\n")
(di / "entry_points.txt").write_text("[pydantic]\ns0 = s0_plugin\n")
run_normal("normal_plugin_planted")
normal_marks = markers()
run_E("E_plugin_planted")
case("pydantic_plugin_distribution_planted", {"normal": ["plugin"], "E": []}, {"normal": normal_marks, "E": markers()},
     note="lock hashes constrain what pip installed, not what importlib.metadata discovers later")
shutil.rmtree(di)
(site / "s0_plugin.py").unlink()

# (e) D bound to S: a D built from a different lock is refused before any import
other_lock = lock_bytes + b"\n# drift\n"
bad_d = cap.serialize(D_ALG, hashlib.sha256(other_lock).hexdigest(), "0" * 64, d_nodes)
bad_fd = cap.seal_committed(bad_d)
r = ln.launch(BASE, {"s": {"fd": s_fd, "sha256": s_digest, "commit": COMMIT},
                     "d": {"fd": bad_fd, "sha256": hashlib.sha256(bad_d).hexdigest()},
                     "x": {"fd": x_fd, "sha256": x_digest}, "roots": ["app"], "inputs": INPUTS},
              (s_fd, bad_fd, x_fd), timeout=60)
case("D_not_bound_to_S_lock_refused", "refused:dependency_lock_binding_mismatch",
     f"{(r['reply'] or {}).get('status')}:{(r['reply'] or {}).get('reason')}")
os.close(bad_fd)

# (f) D omitted: the engine cannot silently fall back to the venv
r = ln.launch(BASE, {"s": {"fd": s_fd, "sha256": s_digest, "commit": COMMIT}, "d": None,
                     "x": {"fd": x_fd, "sha256": x_digest}, "roots": ["app"], "inputs": INPUTS,
                     "watch": [str(VENV)]}, (s_fd, x_fd), cwd=str(CLONE), timeout=60)
rep = r["reply"] or {}
case("D_absent_no_fallback", {"status": "driver_failed", "error_is_missing_dependency": True, "venv_opens": []},
     {"status": rep.get("status"), "error_is_missing_dependency": "No module named" in str((rep.get("result") or {}).get("error")),
      "venv_opens": (rep.get("observed") or {}).get("opens_watched")})

# (g) loader confinement: a D that tries to add submodules to S's package and to stdlib packages,
#     and a top-level module that would shadow stdlib -- none may be importable from D
INJECT = {b"app/extra.py": b"VALUE = 'D-in-app'\n", b"json/injected.py": b"VALUE = 'D-in-json'\n",
          b"encodings/s0evil.py": b"import codecs\ndef getregentry():\n    return codecs.lookup('utf-8')\n",
          b"json.py": b"VALUE = 'D-shadows-stdlib-json'\n"}
inj_nodes = dict(d_nodes)
for path, data in INJECT.items():
    inj_nodes[path] = ("regular", hashlib.sha256(data).hexdigest(), data)
    parts = path.split(b"/")
    for k in range(1, len(parts)):
        inj_nodes.setdefault(b"/".join(parts[:k]), ("tree", "", b""))
inj = cap.serialize(D_ALG, hashlib.sha256(lock_bytes).hexdigest(), "0" * 64, inj_nodes)
inj_fd = cap.seal_committed(inj)
PROBE = b"""
def run(inputs):
    import codecs, importlib, json
    out = {"json_is_stdlib": not hasattr(json, "VALUE")}
    for name in ("app.extra", "json.injected", "encodings.s0evil"):
        try:
            importlib.import_module(name); out[name] = "IMPORTED"
        except ImportError as exc:
            out[name] = type(exc).__name__
    try:
        codecs.lookup("s0evil"); out["codec_s0evil"] = "FOUND"
    except LookupError:
        out["codec_s0evil"] = "LookupError"
    return out
"""
probe_fd = cap.seal_committed(PROBE)
r = ln.launch(BASE, {"s": {"fd": s_fd, "sha256": s_digest, "commit": COMMIT},
                     "d": {"fd": inj_fd, "sha256": hashlib.sha256(inj).hexdigest()},
                     "x": {"fd": probe_fd, "sha256": hashlib.sha256(PROBE).hexdigest()}, "roots": ["app"], "inputs": {}},
              (s_fd, inj_fd, probe_fd), timeout=60)
case("D_cannot_extend_S_or_stdlib_packages",
     {"json_is_stdlib": True, "app.extra": "ModuleNotFoundError", "json.injected": "ModuleNotFoundError",
      "encodings.s0evil": "ModuleNotFoundError", "codec_s0evil": "LookupError"},
     ((r["reply"] or {}).get("result")), reviewer_repro_of="F3 (finder ignored `path`)")
os.close(inj_fd)
os.close(probe_fd)

# (h) the auditor itself is discriminated (R2-5): a RELATIVE read of the checkout, with cwd = checkout,
#     must be reported as a checkout read; the raw event path alone would not match the watched root
READER = b"""
def run(inputs):
    with open("requirements-agent-review.lock", "rb") as fh:
        return {"read_bytes": len(fh.read(16))}
"""
reader_fd = cap.seal_committed(READER)
r = ln.launch(BASE, {"s": {"fd": s_fd, "sha256": s_digest, "commit": COMMIT}, "d": None,
                     "x": {"fd": reader_fd, "sha256": hashlib.sha256(READER).hexdigest()}, "roots": ["app"],
                     "inputs": {}, "watch": [str(CLONE), str(VENV)]}, (s_fd, reader_fd), cwd=str(CLONE), timeout=60)
watched = ((r["reply"] or {}).get("observed") or {}).get("opens_watched") or []
case("auditor_detects_relative_checkout_read",
     {"detected": True, "raw_is_relative": True, "resolved_under_checkout": True},
     {"detected": bool(watched),
      "raw_is_relative": bool(watched) and not watched[0]["raw"].startswith("/"),
      "resolved_under_checkout": bool(watched) and watched[0]["resolved"].startswith(str(CLONE) + "/")},
     note="positive control of the measurement behind E_no_open_under_checkout_or_venv; a prefix match on "
          "the raw event path (round-2 auditor) would not have matched")
os.close(reader_fd)

for fd in (s_fd, d_fd, x_fd):
    os.close(fd)
case("producer_fds_restored", fds_before, cap.open_fds())
out["all_pass"] = all(c.get("pass") for c in out["cases"].values())
print(json.dumps(out, indent=1, default=str))
