"""DISPOSABLE SPIKE (outside the branch): is the right layer ONE charged archive reader?

Hypothesis: the S_D resource defect recurred because the bound was attached to individual read
sites (member loop) while other sites (METADATA/WHEEL/RECORD, the archive file itself) kept their
own unbounded reads. Mechanism under test: every byte of an archive enters memory only through a
BoundedArchive whose constructor charges the ARCHIVE size (fstat before read) and the MEMBER COUNT
(central directory) and whose single `read(name)` charges `file_size` before inflating and checks
the inflated length. No other code path may call zipfile.
"""
import hashlib, io, os, sys, tracemalloc, zipfile, json, base64
from pathlib import Path

class Refused(Exception):
    pass

class Budget:
    def __init__(self, max_archive, max_members, max_inflated):
        self.max_archive, self.max_members, self.max_inflated, self.inflated = max_archive, max_members, max_inflated, 0

class BoundedArchive:
    def __init__(self, path, budget):
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            size = os.fstat(fd).st_size
            if size > budget.max_archive:
                raise Refused("budget_archive_bytes")
            chunks, left = [], size
            while left:
                c = os.read(fd, min(left, 1 << 20))
                if not c:
                    raise Refused("archive_truncated")
                chunks.append(c); left -= len(c)
            self.blob = b"".join(chunks)
        finally:
            os.close(fd)
        self.sha256 = hashlib.sha256(self.blob).hexdigest()
        self._zf = zipfile.ZipFile(io.BytesIO(self.blob))  # central directory <= archive size (charged)
        self.infos = self._zf.infolist()
        if len(self.infos) > budget.max_members:
            raise Refused("budget_archive_members")
        self.budget = budget

    def read(self, name):
        info = self._zf.getinfo(name)
        if self.budget.inflated + info.file_size > self.budget.max_inflated:
            raise Refused("budget_inflated_bytes")
        data = self._zf.read(info)
        if len(data) != info.file_size:
            raise Refused("member_size_mismatch")
        self.budget.inflated += len(data)
        return data

def rec(b): return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(b).digest()).rstrip(b"=").decode()

def wheel(p, *, meta_pad=0, record_pad=0, member_pad=0, stored_pad=0):
    di = "demo-1.0.dist-info"
    m = {"demo/__init__.py": b"", f"{di}/METADATA": b"Name: demo\nVersion: 1.0\n" + b" " * meta_pad, f"{di}/WHEEL": b"Tag: py3-none-any\n"}
    if member_pad: m["demo/big.bin"] = b"\0" * member_pad
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in m.items(): z.writestr(n, b)
        if stored_pad: z.writestr(zipfile.ZipInfo("demo/pad.bin"), os.urandom(stored_pad), compress_type=zipfile.ZIP_STORED)
        z.writestr(f"{di}/RECORD", "\n".join(f"{n},{rec(b)},{len(b)}" for n, b in m.items()) + "\n" + "#" * record_pad)
    p.write_bytes(buf.getvalue())

def ingest(p, budget):
    a = BoundedArchive(p, budget)
    di = next(i.filename.split("/")[0] for i in a.infos if i.filename.split("/")[0].endswith(".dist-info"))
    for name in (f"{di}/RECORD", f"{di}/METADATA", f"{di}/WHEEL"):  # identity reads use the SAME charged path
        a.read(name)
    for i in a.infos:
        if not i.filename.endswith("/"):
            a.read(i.filename) if i.filename not in (f"{di}/RECORD", f"{di}/METADATA", f"{di}/WHEEL") else None

W = Path(sys.argv[1]); W.mkdir(parents=True, exist_ok=True)
out = {}
for name, kw in (("control", {}), ("metadata_bomb_200MiB", {"meta_pad": 200 << 20}), ("record_bomb_200MiB", {"record_pad": 200 << 20}),
                 ("member_bomb_200MiB", {"member_pad": 200 << 20}), ("archive_40MiB_stored_padding", {"stored_pad": 40 << 20})):
    p = W / f"{name}.whl"; wheel(p, **kw)
    tracemalloc.start()
    try: ingest(p, Budget(max_archive=16 << 20, max_members=10_000, max_inflated=8 << 20)); r = "ACCEPTED"
    except Refused as e: r = "REFUSED:" + str(e)
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    out[name] = {"result": r, "heap_peak_MiB": round(peak / 2**20, 2), "archive_bytes": p.stat().st_size}
print(json.dumps(out, indent=1))
