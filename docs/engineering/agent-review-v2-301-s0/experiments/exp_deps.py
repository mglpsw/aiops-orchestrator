"""EXP-DEPS: every S_D refusal the contract states, on synthetic wheels, plus a positive control.

usage: python -I -S exp_deps.py <scratch_dir>
Wheels are built here from declared members (zip + RECORD + METADATA + WHEEL); the lock that
authorizes them is written with their real sha256. Natives are tiny ELF objects compiled with
`cc` (NOT_TESTED if no compiler).
"""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tracemalloc
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
W = Path(sys.argv[1]).resolve()
W.mkdir(parents=True)

import s0_deps as deps  # noqa: E402
from s0_bootstrap import CaptureRefused  # noqa: E402

TAG = "cp%d%d" % sys.version_info[:2]
out = {"cases": {}, "python_tag": TAG}


def case(name, expected, observed, **extra):
    out["cases"][name] = {"expected": expected, "observed": observed, "pass": expected == observed, **extra}


def rec(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def make_wheel(d, dist="demo", ver="1.0", tag="py3-none-any", members=None, *, filename=None, di_name=None,
               meta_name=None, meta_ver=None, wheel_tag=None, record_drop=(), record_corrupt=(), raw_extra=(),
               symlink=None, compress_bomb=0):
    members = dict(members if members is not None else {"demo/__init__.py": b"X = 1\n"})
    di = di_name or f"{dist}-{ver}.dist-info"
    members[f"{di}/METADATA"] = f"Metadata-Version: 2.1\nName: {meta_name or dist}\nVersion: {meta_ver or ver}\n".encode()
    members[f"{di}/WHEEL"] = f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {wheel_tag or tag}\n".encode()
    if compress_bomb:
        members["demo/bomb.bin"] = b"\0" * compress_bomb
    lines = [f"{n},{'sha256=bad' if n in record_corrupt else rec(b)},{len(b)}" for n, b in members.items()
             if n not in record_drop]
    lines.append(f"{di}/RECORD,,")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in members.items():
            z.writestr(n, b)
        for n, b in raw_extra:
            z.writestr(n, b)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (0o120777 << 16)
            z.writestr(info, b"target")
            lines.insert(0, f"{symlink},{rec(b'target')},6")
        z.writestr(f"{di}/RECORD", "\n".join(lines) + "\n")
    p = d / (filename or f"{dist}-{ver}-{tag}.whl")
    p.write_bytes(buf.getvalue())
    return p


def lock_for(*wheels, extra=b""):
    body = b""
    for w in wheels:
        dist, ver = w.name.split("-")[0], w.name.split("-")[1]
        body += f"{dist}=={ver} \\\n    --hash=sha256:{hashlib.sha256(w.read_bytes()).hexdigest()}\n".encode()
    return body + extra


def attempt(name, expected, build, **extra):
    d = W / name
    d.mkdir()
    try:
        wheels, lock = build(d)
        deps.build_dependencies(lock, d, python_tag=TAG, **extra)
        observed = "ACCEPTED"
    except CaptureRefused as exc:
        observed = "REFUSED:" + exc.reason
    except Exception as exc:  # noqa: BLE001 -- an incidental crash is recorded, never counted as a refusal
        observed = "CRASH:" + type(exc).__name__
    case(name, expected, observed)


def one(**kw):
    def build(d):
        w = make_wheel(d, **kw)
        return [w], lock_for(w)
    return build


attempt("control_pure_wheel_accepted", "ACCEPTED", one())
attempt("wheel_renamed_dist_info_mismatch", "REFUSED:wheel_identity_mismatch", one(di_name="other-1.0.dist-info"))
attempt("wheel_renamed_metadata_mismatch", "REFUSED:wheel_identity_mismatch", one(meta_name="other"))
attempt("wheel_tag_differs_from_WHEEL", "REFUSED:wheel_tag_mismatch", one(wheel_tag="py2-none-any"))
attempt("wheel_other_interpreter_abi", "REFUSED:wheel_incompatible_with_interpreter",
        one(tag="cp39-cp39-manylinux2014_x86_64", wheel_tag="cp39-cp39-manylinux2014_x86_64"))
attempt("wheel_member_duplicate", "REFUSED:wheel_duplicate_member", one(raw_extra=[("demo/__init__.py", b"EVIL = 1\n")]))
attempt("wheel_record_mismatch", "REFUSED:wheel_record_mismatch", one(record_corrupt=("demo/__init__.py",)))
attempt("wheel_member_absent_from_record", "REFUSED:wheel_record_mismatch", one(record_drop=("demo/__init__.py",)))
attempt("wheel_pth_member", "REFUSED:wheel_pth_unsupported", one(members={"demo/__init__.py": b"", "evil.pth": b"import os\n"}))
attempt("wheel_data_scheme", "REFUSED:wheel_data_scheme_unsupported",
        one(members={"demo/__init__.py": b"", "demo-1.0.data/scripts/x": b"#!/bin/sh\n"}))
attempt("wheel_dotdot_member", "REFUSED:wheel_member_path", one(members={"demo/../../evil.py": b""}))
attempt("wheel_symlink_member", "REFUSED:wheel_symlink_unsupported", one(symlink="demo/link"))
attempt("file_directory_prefix_collision_one_wheel", "REFUSED:dependency_file_directory_collision",
        one(members={"demo": b"file", "demo/mod.py": b""}))


def two_wheels_collide(d):
    a = make_wheel(d, dist="alpha", members={"shared/__init__.py": b""})
    b = make_wheel(d, dist="beta", members={"shared/__init__.py": b"EVIL = 1\n"})
    return [a, b], lock_for(a, b)


attempt("path_collision_across_wheels", "REFUSED:dependency_path_collision", two_wheels_collide)


def hash_not_authorized(d):
    w = make_wheel(d)
    return [w], f"demo==1.0 \\\n    --hash=sha256:{'0' * 64}\n".encode()


attempt("wheel_hash_not_in_lock", "REFUSED:wheel_hash_not_authorized", hash_not_authorized)


def wheel_not_in_lock(d):
    a, b = make_wheel(d, dist="alpha"), make_wheel(d, dist="beta", members={"beta/__init__.py": b""})
    return [a, b], lock_for(a)


attempt("wheel_not_in_lock", "REFUSED:wheel_not_in_lock", wheel_not_in_lock)


def lock_without_wheel(d):
    a = make_wheel(d)
    return [a], lock_for(a) + f"ghost==1.0 --hash=sha256:{'1' * 64}\n".encode()


attempt("lock_entry_without_wheel", "REFUSED:lock_entry_without_wheel", lock_without_wheel)


def lock_duplicate(d):
    a = make_wheel(d)
    return [a], lock_for(a) + lock_for(a).replace(b"==1.0", b"==2.0")


attempt("lock_duplicate_entry_refused_not_last_wins", "REFUSED:lock_duplicate_entry", lock_duplicate)


def lock_marker(d):
    a = make_wheel(d)
    return [a], lock_for(a).replace(b"demo==1.0 ", b'demo==1.0; python_version>="3" ')


attempt("lock_environment_marker_refused", "REFUSED:lock_entry_unsupported_token", lock_marker)


def lock_hash_only_in_comment(d):
    a = make_wheel(d)
    return [a], f"demo==1.0 # --hash=sha256:{hashlib.sha256(a.read_bytes()).hexdigest()}\n".encode()


attempt("lock_hash_in_comment_carries_no_authority", "REFUSED:lock_entry_unparseable", lock_hash_only_in_comment)

# decompression bomb: 200 MiB of zeros compress to ~200 KiB; the budget must refuse BEFORE inflating
d = W / "bomb"
d.mkdir()
bomb = make_wheel(d, compress_bomb=200 * 2**20)
lock = lock_for(bomb)
tracemalloc.start()
try:
    deps.build_dependencies(lock, d, python_tag=TAG, max_bytes=8 * 2**20)
    observed = "ACCEPTED"
except CaptureRefused as exc:
    observed = "REFUSED:" + exc.reason
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
case("compressed_member_refused_before_inflate", "REFUSED:budget_dependency_bytes", observed,
     wheel_bytes=bomb.stat().st_size, inflated_bytes=200 * 2**20, budget=8 * 2**20, heap_peak_MiB=round(peak / 2**20, 2))

# native members: ELF dynamic section decides whether the closure stays inside the system loader path
src = W / "n.c"
src.write_text("int s0(void){return 1;}\n")
NATIVE = {"plain": [], "rpath": ["-Wl,-rpath,/tmp/attacker-lib", "-Wl,--disable-new-dtags"],
          "runpath": ["-Wl,-rpath,/tmp/attacker-lib", "-Wl,--enable-new-dtags"]}
built = {}
for label, flags in NATIVE.items():
    so = W / f"{label}.so"
    cp = subprocess.run(["cc", "-shared", "-fPIC", "-o", str(so), str(src), *flags], capture_output=True, text=True)
    if cp.returncode == 0:
        built[label] = so.read_bytes()
if "plain" in built:
    abs_needed = W / "absneeded.so"
    # links by ABSOLUTE path (no SONAME) -> DT_NEEDED contains '/'; --no-as-needed keeps the unused dependency
    subprocess.run(["cc", "-shared", "-fPIC", "-o", str(abs_needed), str(src), "-Wl,--no-as-needed", str(W / "plain.so")],
                   capture_output=True, text=True)
    if abs_needed.exists() and any("/" in n for n in deps.elf_dynamic(abs_needed.read_bytes())["needed"]):
        built["absolute_needed"] = abs_needed.read_bytes()
    suffix = "." + TAG.replace("cp", "cpython-") + "-x86_64-linux-gnu.so"
    ntag = f"{TAG}-{TAG}-manylinux_2_17_x86_64"
    for label, expected in (("plain", "ACCEPTED"), ("rpath", "REFUSED:native_search_path_outside_tcb"),
                            ("runpath", "REFUSED:native_search_path_outside_tcb"),
                            ("absolute_needed", "REFUSED:native_search_path_outside_tcb")):
        if label in built:
            attempt(f"native_{label}", expected,
                    one(tag=ntag, members={"demo/__init__.py": b"", f"demo/_ext{suffix}": built[label]}),
                    ext_suffixes=(suffix,))
        else:
            out["cases"][f"native_{label}"] = {"NOT_TESTED": "compiler could not build this variant"}
    out["elf_of_plain"] = deps.elf_dynamic(built["plain"])
else:
    out["cases"]["native_plain"] = {"NOT_TESTED": "no C compiler"}

out["all_pass"] = all(c.get("pass") for c in out["cases"].values() if "pass" in c)
print(json.dumps(out, indent=1))
