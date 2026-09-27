"""Round-2 finding reproduction on the exact code of head 1e2453e (host 3.12; logic is version-independent)."""
import base64, hashlib, io, json, sys, tracemalloc, zipfile
from pathlib import Path
sys.path.insert(0, sys.argv[1]); W = Path(sys.argv[2])
import s0_deps as deps
from s0_bootstrap import CaptureRefused
TAG = "cp%d%d" % sys.version_info[:2]
def rec(b): return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b"=").decode()
def wheel(d, *, meta_pad=0, record_pad=0, wheel_tags=("py3-none-any",)):
    di = "demo-1.0.dist-info"
    m = {"demo/__init__.py": b"", f"{di}/METADATA": b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n" + b" " * meta_pad,
         f"{di}/WHEEL": ("Wheel-Version: 1.0\n" + "".join(f"Tag: {t}\n" for t in wheel_tags)).encode()}
    lines = [f"{n},{rec(b)},{len(b)}" for n, b in m.items()] + [f"{di}/RECORD,,"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in m.items(): z.writestr(n, b)
        z.writestr(f"{di}/RECORD", "\n".join(lines) + "\n" + "#" * record_pad)
    d.mkdir(); p = d / "demo-1.0-py3-none-any.whl"; p.write_bytes(buf.getvalue())
    return f"demo==1.0 --hash=sha256:{hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode()
out = {}
for name, kw in (("metadata_bomb_200MiB", {"meta_pad": 200 * 2**20}), ("record_bomb_200MiB", {"record_pad": 200 * 2**20}),
                 ("wheel_tags_superset", {"wheel_tags": ("py3-none-any", "py2-none-any")})):
    d = W / name; lock = wheel(d, **kw)
    tracemalloc.start()
    try: deps.build_dependencies(lock, d, python_tag=TAG, max_bytes=8 * 2**20); r = "ACCEPTED"
    except CaptureRefused as e: r = "REFUSED:" + e.reason
    except Exception as e: r = "CRASH:" + type(e).__name__
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    out[name] = {"result": r, "heap_peak_MiB": round(peak / 2**20, 1), "budget_MiB": 8}
# DT_FILTER / DT_AUXILIARY are not among the tags the ELF reader returns
import inspect
src = inspect.getsource(deps.elf_dynamic)
out["elf_reader_tags"] = {"handles_DT_FILTER_0x7fffffff": "0x7fffffff" in src or "2147483647" in src,
                          "handles_DT_AUXILIARY_0x7ffffffd": "0x7ffffffd" in src or "2147483645" in src}
print(json.dumps(out, indent=1))
