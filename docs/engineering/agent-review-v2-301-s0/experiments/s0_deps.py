"""EXPERIMENTAL ONLY -- #301 S0 dependency set D. Not production code.

Chain demonstrated (exp_functional.py):

  commit (expected identity) -> tree -> blob `requirements-agent-review.lock`   [in S, hash-on-read]
    -> per-distribution sha256 allowlist                                          [parsed from S's bytes]
    -> wheel file bytes, read ONCE and hashed                                     [acquired artifact]
    -> members extracted from THAT buffer, each checked against the wheel RECORD  [transformation]
    -> D container (same format as S), header binds sha256(lock bytes)            [sealed separately]

A venv's installed files are never read: `pip install --require-hashes` authenticates the
download it performed, not the bytes a later process loads from site-packages.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import re
import stat
import zipfile
from pathlib import Path

from s0_bootstrap import CaptureRefused

_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(lock_bytes: bytes) -> dict:
    """{normalized name: (version, {sha256,...})}; refuses entries without a hash."""
    text = lock_bytes.decode("utf-8")
    logical, buf = [], ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        buf += line.rstrip("\\").strip() + " "
        if not line.rstrip().endswith("\\"):
            if buf.strip():
                logical.append(buf.strip())
            buf = ""
    out = {}
    for entry in logical:
        m = _REQ.match(entry)
        hashes = set(_HASH.findall(entry))
        if m is None or not hashes:
            raise CaptureRefused("lock_entry_unparseable", entry[:60])
        out[_norm(m.group(1))] = (m.group(2), hashes)
    return out


def _record(zf: zipfile.ZipFile, dist_info: str) -> dict:
    rows = csv.reader(io.StringIO(zf.read(dist_info + "/RECORD").decode("utf-8")))
    rec = {}
    for row in rows:
        if not row:
            continue
        path, digest = row[0], row[1] if len(row) > 1 else ""
        if path in rec:
            raise CaptureRefused("wheel_record_duplicate", path)
        rec[path] = digest
    return rec


def build_dependencies(lock_bytes: bytes, wheel_dir: Path, *, max_bytes: int = 64 * 1024 * 1024):
    """Return (nodes, manifest). nodes use the S container's kinds; natives are regular nodes
    whose bytes the consumer seals into its own memfd before dlopen."""
    lock = parse_lock(lock_bytes)
    matched: dict = {}
    nodes: dict = {}
    total = 0
    manifest = []
    for wheel in sorted(Path(wheel_dir).glob("*.whl")):
        blob = wheel.read_bytes()  # read ONCE; everything below uses this buffer
        digest = hashlib.sha256(blob).hexdigest()
        dist, version = wheel.name.split("-")[0], wheel.name.split("-")[1]
        entry = lock.get(_norm(dist))
        if entry is None:
            raise CaptureRefused("wheel_not_in_lock", wheel.name)
        if entry[0] != version or digest not in entry[1]:
            raise CaptureRefused("wheel_hash_not_authorized", wheel.name)
        if _norm(dist) in matched:
            raise CaptureRefused("wheel_duplicate_distribution", wheel.name)
        matched[_norm(dist)] = digest
        zf = zipfile.ZipFile(io.BytesIO(blob))
        names = [i.filename for i in zf.infolist()]
        if len(names) != len(set(names)):
            raise CaptureRefused("wheel_duplicate_member", wheel.name)
        dist_infos = {n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")}
        if len(dist_infos) != 1:
            raise CaptureRefused("wheel_dist_info_ambiguous", wheel.name)
        dist_info = dist_infos.pop()
        record = _record(zf, dist_info)
        for info in zf.infolist():
            name = info.filename
            if name.endswith("/"):
                continue
            parts = name.split("/")
            if name.startswith("/") or any(p in ("", ".", "..") for p in parts):
                raise CaptureRefused("wheel_member_path", name)
            if parts[0].endswith(".data"):
                raise CaptureRefused("wheel_data_scheme_unsupported", name)
            if name.endswith(".pth"):
                raise CaptureRefused("wheel_pth_unsupported", name)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise CaptureRefused("wheel_symlink_unsupported", name)
            data = zf.read(info)
            if name != dist_info + "/RECORD":
                want = record.get(name)
                got = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                if want != got:
                    raise CaptureRefused("wheel_record_mismatch", name)
            path = name.encode()
            if path in nodes:
                raise CaptureRefused("dependency_path_collision", name)
            total += len(data)
            if total > max_bytes:
                raise CaptureRefused("budget_dependency_bytes", str(total))
            nodes[path] = ("regular", hashlib.sha256(data).hexdigest(), data)
        missing = set(record) - {i.filename for i in zf.infolist()}
        if missing:
            raise CaptureRefused("wheel_record_lists_absent_member", sorted(missing)[0])
        manifest.append({"wheel": wheel.name, "sha256": digest, "bytes": len(blob)})
    absent = set(lock) - set(matched)
    if absent:
        raise CaptureRefused("lock_entry_without_wheel", sorted(absent)[0])
    # parent directories as explicit tree nodes, so PEP 420 / package lookup matches the wheel layout
    for path in list(nodes):
        parts = path.split(b"/")
        for k in range(1, len(parts)):
            nodes.setdefault(b"/".join(parts[:k]), ("tree", "", b""))
    return nodes, manifest, total
