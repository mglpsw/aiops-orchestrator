"""EXPERIMENTAL ONLY -- #301 S0 dependency set D. Not production code.

Chain demonstrated (exp_functional.py, exp_deps.py):

  commit (expected identity) -> tree -> REGULAR blob `requirements-agent-review.lock`  [in S, hash-on-read]
    -> per-distribution (version, sha256 allowlist); duplicates/markers refused       [parsed from S's bytes]
    -> wheel file bytes, read ONCE and hashed                                         [acquired artifact]
    -> wheel identity from INSIDE the authenticated bytes: .dist-info stem, METADATA
       Name/Version, WHEEL Tag set -- must equal the lock entry and the target interpreter
    -> members extracted from THAT buffer: size checked BEFORE decompression, each member
       checked against RECORD; file/directory prefix collisions refused
    -> native members: ELF dynamic section inspected; RPATH/RUNPATH and path-bearing
       DT_NEEDED refused (their closure must be the root-owned system loader path = TCB)
    -> D container (same format as S); header binds sha256(lock) and the python tag

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
import struct
import zipfile
from email.parser import BytesHeaderParser
from pathlib import Path

from s0_bootstrap import CaptureRefused

_REQ = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9._+!-]*)$")
_HASH_TOKEN = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")
ALLOWED_PLATFORMS = ("any", "manylinux_2_17_x86_64", "manylinux2014_x86_64", "linux_x86_64")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(lock_bytes: bytes) -> dict:
    """{normalized name: (version, {sha256,...})}. Stricter than pip on purpose: duplicate names,
    environment markers, extras and hashes outside `--hash=` tokens are refused, never merged."""
    logical, buf = [], ""
    for line in lock_bytes.decode("utf-8").splitlines():
        line = re.sub(r"(^|\s)#.*$", "", line)  # comments (full-line or inline) carry no authority
        cont = line.rstrip().endswith("\\")
        buf += line.rstrip().rstrip("\\") + " "
        if not cont:
            if buf.strip():
                logical.append(buf.split())
            buf = ""
    out = {}
    for tokens in logical:
        m = _REQ.match(tokens[0])
        hashes = set()
        for tok in tokens[1:]:
            h = _HASH_TOKEN.match(tok)
            if h is None:
                raise CaptureRefused("lock_entry_unsupported_token", tok[:40])
            hashes.add(h.group(1))
        if m is None or not hashes:
            raise CaptureRefused("lock_entry_unparseable", tokens[0][:60])
        name = _norm(m.group(1))
        if name in out:
            raise CaptureRefused("lock_duplicate_entry", name)
        out[name] = (m.group(2), hashes)
    return out


def _wheel_filename(name: str):
    """PEP 427: {dist}-{ver}(-{build})?-{py}-{abi}-{plat}.whl -> (dist, ver, {tags})."""
    if not name.endswith(".whl"):
        raise CaptureRefused("wheel_filename_invalid", name)
    parts = name[:-4].split("-")
    if len(parts) not in (5, 6):
        raise CaptureRefused("wheel_filename_invalid", name)
    dist, ver, py, abi, plat = parts[0], parts[1], parts[-3], parts[-2], parts[-1]
    tags = {(p, a, pl) for p in py.split(".") for a in abi.split(".") for pl in plat.split(".")}
    return dist, ver, tags


def _compatible(tags: set, python_tag: str) -> bool:
    for py, abi, plat in tags:
        if plat not in ALLOWED_PLATFORMS:
            continue
        if (py, abi) == (python_tag, python_tag) or (py in ("py3", python_tag) and abi == "none"):
            return True
    return False


def elf_dynamic(data: bytes) -> dict:
    """Minimal ELF64 little-endian x86_64 reader: {'needed': [...], 'rpath': [...], 'runpath': [...]}."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1 or struct.unpack_from("<H", data, 18)[0] != 62:
        raise CaptureRefused("native_not_elf64_x86_64")
    phoff, phentsize, phnum = struct.unpack_from("<Q", data, 32)[0], *struct.unpack_from("<HH", data, 54)
    loads, dyn = [], None
    for i in range(phnum):
        p_type, _flags, p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from("<IIQQQQ", data, phoff + i * phentsize)
        if p_type == 1:
            loads.append((p_vaddr, p_offset, p_filesz))
        elif p_type == 2:
            dyn = (p_offset, p_filesz)
    if dyn is None:
        raise CaptureRefused("native_no_dynamic_section")

    def off(vaddr):
        for va, fo, sz in loads:
            if va <= vaddr < va + sz:
                return fo + (vaddr - va)
        raise CaptureRefused("native_dynamic_unmapped")

    entries, strtab = [], None
    for k in range(dyn[1] // 16):
        tag, val = struct.unpack_from("<qQ", data, dyn[0] + k * 16)
        if tag == 0:
            break
        entries.append((tag, val))
        if tag == 5:  # DT_STRTAB
            strtab = off(val)
    if strtab is None:
        raise CaptureRefused("native_no_strtab")

    def string(o):
        return data[strtab + o:data.index(b"\0", strtab + o)].decode("utf-8", "replace")

    names = {1: "needed", 15: "rpath", 29: "runpath"}
    out = {"needed": [], "rpath": [], "runpath": []}
    for tag, val in entries:
        if tag in names:
            out[names[tag]].append(string(val))
    return out


def _record(zf: zipfile.ZipFile, dist_info: str) -> dict:
    rec = {}
    for row in csv.reader(io.StringIO(zf.read(dist_info + "/RECORD").decode("utf-8"))):
        if not row:
            continue
        if row[0] in rec:
            raise CaptureRefused("wheel_record_duplicate", row[0])
        rec[row[0]] = row[1] if len(row) > 1 else ""
    return rec


def build_dependencies(lock_bytes: bytes, wheel_dir: Path, *, python_tag: str,
                       max_bytes: int = 64 * 1024 * 1024, ext_suffixes: tuple = (".so",)):
    """Return (nodes, manifest, total). python_tag is the TARGET interpreter's tag (e.g. cp311)."""
    lock = parse_lock(lock_bytes)
    matched: dict = {}
    nodes: dict = {}
    total = 0
    manifest = []
    for wheel in sorted(Path(wheel_dir).glob("*.whl")):
        blob = wheel.read_bytes()  # read ONCE; everything below uses this buffer
        digest = hashlib.sha256(blob).hexdigest()
        dist, version, tags = _wheel_filename(wheel.name)
        entry = lock.get(_norm(dist))
        if entry is None:
            raise CaptureRefused("wheel_not_in_lock", wheel.name)
        if entry[0] != version or digest not in entry[1]:
            raise CaptureRefused("wheel_hash_not_authorized", wheel.name)
        if _norm(dist) in matched:
            raise CaptureRefused("wheel_duplicate_distribution", wheel.name)
        matched[_norm(dist)] = digest
        zf = zipfile.ZipFile(io.BytesIO(blob))
        infos = zf.infolist()
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise CaptureRefused("wheel_duplicate_member", wheel.name)
        dist_infos = {n.split("/")[0] for n in names if n.split("/")[0].endswith(".dist-info")}
        if len(dist_infos) != 1:
            raise CaptureRefused("wheel_dist_info_ambiguous", wheel.name)
        dist_info = dist_infos.pop()
        # identity from INSIDE the authenticated bytes, not from the file name
        stem = dist_info[: -len(".dist-info")]
        if "-" not in stem or _norm(stem.rsplit("-", 1)[0]) != _norm(dist) or stem.rsplit("-", 1)[1] != version:
            raise CaptureRefused("wheel_identity_mismatch", f"dist-info={dist_info}")
        meta = BytesHeaderParser().parsebytes(zf.read(dist_info + "/METADATA"))
        if _norm(meta.get("Name", "")) != _norm(dist) or meta.get("Version") != version:
            raise CaptureRefused("wheel_identity_mismatch", "METADATA")
        wheel_meta = BytesHeaderParser().parsebytes(zf.read(dist_info + "/WHEEL"))
        inner_tags = set()
        for t in wheel_meta.get_all("Tag", []):  # compressed tag sets (e.g. maturin) expand like the filename
            py, abi, plat = t.strip().split("-")
            inner_tags |= {(p, a, pl) for p in py.split(".") for a in abi.split(".") for pl in plat.split(".")}
        if not tags <= inner_tags:
            raise CaptureRefused("wheel_tag_mismatch", wheel.name)
        if not _compatible(tags, python_tag):
            raise CaptureRefused("wheel_incompatible_with_interpreter", f"{wheel.name} vs {python_tag}")
        record = _record(zf, dist_info)
        for info in infos:
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
            if total + info.file_size > max_bytes:  # BEFORE decompression
                raise CaptureRefused("budget_dependency_bytes", str(total + info.file_size))
            data = zf.read(info)
            if len(data) != info.file_size:
                raise CaptureRefused("wheel_member_size_mismatch", name)
            if name != dist_info + "/RECORD":
                got = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
                if record.get(name) != got:
                    raise CaptureRefused("wheel_record_mismatch", name)
            if name.endswith(ext_suffixes):
                dyn = elf_dynamic(data)
                if dyn["rpath"] or dyn["runpath"] or any("/" in n for n in dyn["needed"]):
                    raise CaptureRefused("native_search_path_outside_tcb", f"{name}: {dyn}")
            path = name.encode()
            if path in nodes:
                raise CaptureRefused("dependency_path_collision", name)
            total += len(data)
            nodes[path] = ("regular", hashlib.sha256(data).hexdigest(), data)
        missing = set(record) - set(names)
        if missing:
            raise CaptureRefused("wheel_record_lists_absent_member", sorted(missing)[0])
        manifest.append({"wheel": wheel.name, "sha256": digest, "bytes": len(blob)})
    absent = set(lock) - set(matched)
    if absent:
        raise CaptureRefused("lock_entry_without_wheel", sorted(absent)[0])
    # parent directories as explicit tree nodes; a FILE that is also a parent is a collision
    for path in list(nodes):
        parts = path.split(b"/")
        for k in range(1, len(parts)):
            parent = b"/".join(parts[:k])
            if parent in nodes and nodes[parent][0] != "tree":
                raise CaptureRefused("dependency_file_directory_collision", parent.decode())
            nodes.setdefault(parent, ("tree", "", b""))
    return nodes, manifest, total
