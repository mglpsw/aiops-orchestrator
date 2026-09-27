"""EXP-BOOT: configuration that acts BEFORE the bootstrap's first line, and what -I / -S change.

usage: python -I -S exp_bootstrap_env.py <base_interpreter_abs_path> <scratch_dir>
Run as an UNPRIVILEGED user while the base interpreter and its stdlib are root-owned.
Every "payload ran" observation is a marker file checked by THIS harness after the child exits,
never the child's own report (a hooked process can report anything).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = sys.argv[1]
W = Path(sys.argv[2]).resolve()
MARK = W / "markers"
MARK.mkdir(parents=True)
out = {"cases": {}, "floor": {}}


def payload(name):
    # posix-level: runs even inside encodings/__init__ during startup, before builtins.open exists
    return f"import posix as _s0p; _s0p.close(_s0p.open({str(MARK / name)!r}, 0o101, 0o644))\n"


def ran(name):
    p = MARK / name
    hit = p.exists()
    if hit:
        p.unlink()
    return hit


def run(argv, env=None, cwd="/"):
    for stale in MARK.iterdir():  # a marker left by an earlier case must never be credited to this one
        stale.unlink()
    cp = subprocess.run(argv, env=env if env is not None else {}, cwd=cwd, capture_output=True, text=True, timeout=60)
    return cp.returncode, cp.stdout.strip()[-300:], cp.stderr.strip()[-300:]


def case(name, expected_marker, marker, **extra):
    out["cases"][name] = {"expected_payload_ran": expected_marker, "payload_ran": marker,
                          "pass": expected_marker == marker, **extra}


# --- the floor the design relies on: root-owned interpreter and stdlib ---------------------------
stdlib = Path(subprocess.run([BASE, "-I", "-S", "-c", "import os;print(os.path.dirname(os.__file__))"],
                             env={}, capture_output=True, text=True).stdout.strip())
for label, path in (("interpreter", Path(os.path.realpath(BASE))), ("interpreter_dir", Path(os.path.realpath(BASE)).parent),
                    ("stdlib_dir", stdlib), ("stdlib_encodings", stdlib / "encodings" / "__init__.py")):
    st = path.stat()
    try:
        probe = (path if path.is_dir() else path.parent) / ".s0-write-probe"
        probe.touch()
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    out["floor"][label] = {"path": str(path), "uid": st.st_uid, "mode": oct(st.st_mode & 0o7777),
                           "writable_by_this_uid": writable}
out["floor"]["this_uid"] = os.getuid()

# --- venv interpreter: pyvenv.cfg is read before any Python code, -I -S do not disable it ---------
evil_prefix = W / "evilprefix"
shutil.copytree(stdlib, evil_prefix / "lib" / stdlib.name, symlinks=True,
                ignore=shutil.ignore_patterns("site-packages", "test", "idlelib", "tkinter", "ensurepip", "__pycache__"))
(evil_prefix / "bin").mkdir()
enc = evil_prefix / "lib" / stdlib.name / "encodings" / "__init__.py"
enc.write_text(enc.read_text() + "\n" + payload("venv_stdlib_redirect"))
venv = W / "venv"
subprocess.run([BASE, "-m", "venv", "--without-pip", str(venv)], env={}, check=True)
cfg = venv / "pyvenv.cfg"
original_cfg = cfg.read_text()
rc = run([str(venv / "bin" / "python"), "-I", "-S", "-c", "import sys;print(sys.prefix)"])
case("venv_python_I_S_intact_cfg", False, ran("venv_stdlib_redirect"), rc=rc)
cfg.write_text("".join(f"home = {evil_prefix / 'bin'}\n" if l.startswith("home") else l
                       for l in original_cfg.splitlines(keepends=True)))
rc = run([str(venv / "bin" / "python"), "-I", "-S", "-c", "import sys;print(sys.prefix, sys.base_prefix)"])
case("venv_python_I_S_same_uid_edited_pyvenv_cfg", True, ran("venv_stdlib_redirect"), rc=rc,
     note="payload runs before the first line of -c code: revalidates spike EXP-F1 on this runtime")
rc = run([BASE, "-I", "-S", "-c", "import sys;print(sys.prefix)"])
case("base_interpreter_abs_path_I_S_same_attacker_prefix_present", False, ran("venv_stdlib_redirect"), rc=rc)
cfg.write_text(original_cfg)

# --- dynamic loader environment acts before the interpreter; -I does not touch it ---------------
so = W / "preload.so"
csrc = W / "preload.c"
csrc.write_text('#include <stdio.h>\n__attribute__((constructor)) static void s0(void){FILE*f=fopen("%s","w");if(f)fclose(f);}\n'
                % (MARK / "ld_preload"))
cc = subprocess.run(["cc", "-shared", "-fPIC", "-o", str(so), str(csrc)], capture_output=True, text=True)
if cc.returncode == 0:
    rc = run([BASE, "-I", "-S", "-c", "pass"], env={"LD_PRELOAD": str(so)})
    case("LD_PRELOAD_with_I_S", True, ran("ld_preload"), rc=rc)
    rc = run([BASE, "-I", "-S", "-c", "pass"], env={})
    case("empty_environment_I_S", False, ran("ld_preload"), rc=rc)
else:
    out["cases"]["LD_PRELOAD_with_I_S"] = {"NOT_TESTED": "no C compiler: " + cc.stderr[-200:]}

# --- -I versus -S: they close different doors ------------------------------------------------------
shadow, cwd = W / "shadow", W / "cwd"
shadow.mkdir()
cwd.mkdir()
(shadow / "json.py").write_text(payload("pythonpath_shadow"))
(cwd / "json.py").write_text(payload("cwd_shadow"))
home = W / "home"
usersite = subprocess.run([BASE, "-c", "import site;print(site.getusersitepackages())"],
                          env={"HOME": str(home)}, capture_output=True, text=True).stdout.strip()
Path(usersite).mkdir(parents=True)
(Path(usersite) / "s0.pth").write_text(payload("user_site_pth"))
env_attack = {"PYTHONPATH": str(shadow), "HOME": str(home)}
MATRIX = [
    ("S_only_env_PYTHONPATH", ["-S"], "/", "pythonpath_shadow", True),
    ("S_only_cwd_module", ["-S"], str(cwd), "cwd_shadow", True),
    ("E_S_cwd_module_no_safe_path", ["-E", "-S"], str(cwd), "cwd_shadow", True),
    ("no_flags_user_site_pth", [], "/", "user_site_pth", True),
    ("I_only_env_PYTHONPATH", ["-I"], "/", "pythonpath_shadow", False),
    ("I_only_cwd_module", ["-I"], str(cwd), "cwd_shadow", False),
    ("I_only_user_site_pth", ["-I"], "/", "user_site_pth", False),
    ("I_S_env_PYTHONPATH", ["-I", "-S"], "/", "pythonpath_shadow", False),
    ("I_S_cwd_module", ["-I", "-S"], str(cwd), "cwd_shadow", False),
]
for name, flags, where, marker, expected in MATRIX:
    rc = run([BASE, *flags, "-c", "import json"], env=env_attack, cwd=where)
    case(name, expected, ran(marker), rc=rc)
self_report = run([BASE, "-I", "-S", "-c", "import sys;print(sys.flags.isolated, sys.flags.no_site, sys.flags.safe_path)"])
out["child_self_report_flags_I_S_informative_only"] = self_report[1]
out["all_pass"] = all(c.get("pass") for c in out["cases"].values() if "pass" in c)
print(json.dumps(out, indent=1))
