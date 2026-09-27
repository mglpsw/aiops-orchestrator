"""EXPERIMENTAL ONLY -- #301 S0 architecture C: privilege-separated producer of an attacker-immutable snapshot.

Ported from 301-S0 Spike C (PR #355 comment 5858608157). Not production code; the privilege used in the
experiments (container uid 0) is a MECHANISM, not the normative primitive: the contract depends on
RunnerCanRead(snapshot) AND NOT RunnerCanMutate(snapshot), established by the kernel.

The producer only handles PHYSICAL bytes (PhysicalSnapshot != AuthenticatedSubject):
  - storage opened by descriptor inside an admitted capability (G1C primitives, reused; who authorized the
    capability is #331/C2_B and is not proven here);
  - loose objects copied in their ORIGINAL compressed form (never inflated: B-3), pack-*.pack/.idx copied;
  - authorized alternates flattened physically, their pointer never copied; out-of-capability -> refusal;
  - no git, no zlib, no verify-pack, no semantic object validation;
  - no source config, remote, promisor, hooks, HEAD, refs, packed-refs;
  - physical budget charged from fstat BEFORE each file is read; every LISTED entry counts against the entry
    budget (copied or skipped), INCLUDING the structural probe of an alternate candidate (K3: no unbounded
    enumeration precedes the bounded one); short writes are retried until the whole chunk is written (RC-5);
  - staging/<id> (0700) -> finalize (files 0444, dirs 0555, fsync) -> atomic rename into committed/<id>:
    the COMMIT POINT. A crash before it leaves staging garbage, never a committed snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path

FORMATS = {"sha1": 40, "sha256": 64}
PACK_NAME = re.compile(r"^pack-[0-9a-f]{40,64}\.(pack|idx)$")
RECEIPT_NAME = "S0_SNAPSHOT_RECEIPT.json"
RECEIPT_SCHEMA = "ar301-s0-c.snapshot-receipt.v1"
ENUMERATED = {"entries": 0}   # experiment instrumentation: directory entries yielded by os.scandir in this process


def _counting_scandir(real):
    class Counting:
        """Iterator proxy over a real scandir iterator (os.walk and the G1C primitives use it too)."""

        def __init__(self, it):
            self._it = it

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self._it)
            ENUMERATED["entries"] += 1
            return entry

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

        def close(self):
            self._it.close()

    def scandir(*a, **kw):
        return Counting(real(*a, **kw))
    return scandir


class Refused(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason


def produce(repo_root: str, object_format: str, storage_roots: list, out_base: str, *, max_bytes: int = 256 << 20,
            max_entries: int = 200_000, max_alt_depth: int = 8, slow_s: float = 0.0,
            _test_max_write: int | None = None, _ablation_single_write: bool = False,
            _ablation_unbounded_alternate_probe: bool = False) -> dict:
    """object_format comes from the ADMITTED expected subject (not from the live repository)."""
    from app.agent_review import trusted_object_authority_v2 as g1c

    if object_format not in FORMATS:
        raise Refused("object_format_unsupported", object_format)
    hexlen = FORMATS[object_format]
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=Path(out_base) / "staging"))  # 0700 producer-only
    stats = {"bytes": 0, "entries": 0, "max_depth": 0, "object_dirs": 0, "skipped_non_object": 0, "scanned": 0}

    def scanned(entry_iter):
        """Every listed entry (copied, skipped or ignored) counts against the physical entry budget."""
        for entry in entry_iter:
            stats["scanned"] += 1
            if stats["scanned"] > max_entries:
                raise Refused("physical_budget_exceeded", f"scanned {stats['scanned']} entries")
            yield entry
    records = []

    def write(out: int, view) -> int:
        # _test_max_write forces SHORT writes (RC-5 discriminator); the real syscall is still used
        return os.write(out, view[:_test_max_write] if _test_max_write else view)

    def bounded_objects_dir_probe(objects_fd: int) -> bool:
        """K3: the G1C probe's semantics (sibling HEAD, pack/, info/, or a two-hex fanout) with O(1) probes first
        and the fanout search charged to the SAME entry budget, stopping when it is exhausted."""
        try:
            parent_fd = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=objects_fd)
        except OSError:
            parent_fd = None
        if parent_fd is not None:
            try:
                head = g1c._try_open_file_no_follow_v2(parent_fd, "HEAD")
                if head is not None:
                    os.close(head)
                    return True
            except g1c.TrustedObjectAuthorityError:
                pass
            finally:
                os.close(parent_fd)
        for name in ("pack", "info"):
            probe = g1c._try_open_dir_no_follow_v2(objects_fd, name)
            if probe is not None:
                os.close(probe)
                return True
        with os.scandir(objects_fd) as it:
            for entry in scanned(it):
                if len(entry.name) == 2 and all(c in "0123456789abcdef" for c in entry.name) \
                        and entry.is_dir(follow_symlinks=False):
                    return True
        return False

    def copy_raw(fd: int, dest: Path) -> None:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise Refused("physical_not_regular", str(dest))
            stats["bytes"] += st.st_size  # charged BEFORE any byte is read
            stats["entries"] += 1
            if stats["bytes"] > max_bytes or stats["entries"] > max_entries:
                raise Refused("physical_budget_exceeded", f"{stats['bytes']}B/{stats['entries']}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():  # same content-addressed name already copied from another object dir
                return
            h = hashlib.sha256()
            out = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
            try:
                left = st.st_size
                while left:
                    chunk = os.read(fd, min(left, 1 << 20))  # raw compressed bytes; never inflated
                    if not chunk:
                        raise Refused("physical_truncated", str(dest))
                    view = memoryview(chunk)
                    while view:  # a short write is retried, never recorded as written (RC-5)
                        view = view[write(out, view):]
                        if _ablation_single_write:
                            break          # mutant: one write per chunk, the remainder silently dropped
                    h.update(chunk)
                    left -= len(chunk)
                if os.read(fd, 1):
                    raise Refused("physical_grew_during_copy", str(dest))
                os.fsync(out)
            finally:
                os.close(out)
            records.append((str(dest.relative_to(stage)), st.st_size, h.hexdigest()))
            if slow_s:
                time.sleep(slow_s)
        finally:
            os.close(fd)

    def copy_objects(src_fd: int, src_path, depth: int, visited: set) -> None:
        try:
            st = os.fstat(src_fd)
            key = (st.st_dev, st.st_ino)
            if key in visited:
                return
            if depth > max_alt_depth:
                raise Refused("alternate_depth_exceeded", str(depth))
            probe = g1c._looks_like_git_objects_directory_fd_v2 if _ablation_unbounded_alternate_probe else bounded_objects_dir_probe
            if depth > 0 and not probe(src_fd):
                raise Refused("alternate_not_an_objects_directory")
            visited.add(key)
            stats["object_dirs"] += 1
            stats["max_depth"] = max(stats["max_depth"], depth)
            for entry in scanned(os.scandir(src_fd)):
                if len(entry.name) == 2 and all(c in "0123456789abcdef" for c in entry.name):
                    if entry.is_symlink():
                        raise Refused("physical_symlink", entry.name)
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    fan = g1c._open_dir_no_follow_v2(src_fd, entry.name)
                    try:
                        for obj in scanned(os.scandir(fan)):
                            if obj.is_symlink():
                                raise Refused("physical_symlink", obj.name)
                            if not obj.is_file(follow_symlinks=False):
                                continue
                            if len(entry.name + obj.name) != hexlen or not all(c in "0123456789abcdef" for c in obj.name):
                                stats["skipped_non_object"] += 1  # e.g. git's tmp_obj_*: not an object name of this format
                                continue
                            copy_raw(g1c._open_listed_file_no_follow_v2(fan, obj.name), stage / "objects" / entry.name / obj.name)
                    finally:
                        os.close(fan)
            pack_fd = g1c._try_open_dir_no_follow_v2(src_fd, "pack")
            if pack_fd is not None:
                try:
                    for entry in scanned(os.scandir(pack_fd)):
                        if entry.is_symlink():
                            raise Refused("physical_symlink", entry.name)
                        if entry.is_file(follow_symlinks=False) and PACK_NAME.match(entry.name):
                            copy_raw(g1c._open_listed_file_no_follow_v2(pack_fd, entry.name), stage / "objects" / "pack" / entry.name)
                finally:
                    os.close(pack_fd)
            info_fd = g1c._try_open_dir_no_follow_v2(src_fd, "info")
            if info_fd is not None:
                try:
                    alt = g1c._try_open_file_no_follow_v2(info_fd, "alternates")
                    if alt is not None:
                        raw = os.read(alt, 65536)
                        os.close(alt)
                        for base_fd, path_str in g1c._parse_alternates_v2(raw, owning_objects_fd=src_fd):
                            try:
                                alt_fd = g1c._open_dir_by_segments_no_follow_v2(
                                    base_fd=base_fd, path_str=path_str, base_path=src_path,
                                    authorized_storage=storage, authorized_roots=None)
                            except g1c.TrustedObjectAuthorityError as exc:
                                raise Refused("alternate_outside_authorized_storage", str(exc)) from exc
                            copy_objects(alt_fd, Path(path_str) if path_str.startswith("/") else None, depth + 1, visited)
                finally:
                    os.close(info_fd)
        finally:
            os.close(src_fd)

    storage = g1c.AuthorizedGitStorageSetV2.from_roots(storage_roots)
    try:
        repo_fd = g1c._open_repo_root_fd_v2(Path(repo_root))
        try:
            if not storage.contains_fd(repo_fd, logical_path=Path(repo_root)):
                raise Refused("storage_unauthorized")
            dirs = g1c._resolve_git_directories_fd_v2(repo_root_fd=repo_fd,
                                                     repo_root_path=Path(os.readlink(f"/proc/self/fd/{repo_fd}")),
                                                     authorized_storage=storage)
        finally:
            os.close(repo_fd)
        try:
            objects_fd = g1c._try_open_dir_no_follow_v2(dirs.common_dir_fd, "objects")
            if objects_fd is None:
                raise Refused("objects_absent")
            copy_objects(objects_fd, Path(os.readlink(f"/proc/self/fd/{objects_fd}")), 0, set())
        finally:
            for fd in (dirs.common_dir_fd, dirs.git_dir_fd):
                if fd is not None:
                    os.close(fd)
    except g1c.TrustedObjectAuthorityError as exc:
        raise Refused("storage_refused", str(exc)) from exc
    finally:
        storage.close()
    # producer-authored minimal bare structure: nothing from the source repository
    for d in ("objects/pack", "objects/info", "refs/heads"):
        (stage / d).mkdir(parents=True, exist_ok=True)
    (stage / "HEAD").write_text("ref: refs/heads/none\n")
    (stage / "config").write_text("[core]\n\trepositoryformatversion = 0\n\tbare = true\n" if object_format == "sha1" else
                                  "[core]\n\trepositoryformatversion = 1\n\tbare = true\n[extensions]\n\tobjectformat = sha256\n")
    digest = hashlib.sha256()
    for rel, size, h in sorted(records):
        digest.update(f"{rel}\0{size}\0{h}\n".encode())
    snapshot_id = digest.hexdigest()[:24] + "-" + os.urandom(4).hex()
    receipt = {  # traceability only: SnapshotReceipt != ObjectAuthenticityProof; no root_tree, no commit
        "schema_version": RECEIPT_SCHEMA, "object_format": object_format, "physical_bytes": stats["bytes"],
        "physical_entries": stats["entries"], "alternate_sources_count": stats["object_dirs"] - 1,
        "storage_capability_binding": sorted(str(r) for r in storage_roots),
        "published_snapshot_identity": {"snapshot_id": snapshot_id, "physical_content_sha256": digest.hexdigest()},
        "skipped_non_object_files": stats["skipped_non_object"]}
    (stage / RECEIPT_NAME).write_text(json.dumps(receipt, sort_keys=True))
    for dirpath, _dirs, files in os.walk(stage, topdown=False):
        for f in files:
            os.chmod(os.path.join(dirpath, f), 0o444)
        os.chmod(dirpath, 0o555)
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(fd)
    os.close(fd)
    final = Path(out_base) / "committed" / snapshot_id
    os.rename(stage, final)  # COMMIT POINT (same filesystem, atomic)
    cfd = os.open(final.parent, os.O_RDONLY | os.O_DIRECTORY)
    os.fsync(cfd)
    os.close(cfd)
    return {"snapshot": str(final), "receipt": receipt, "alternate_depth": stats["max_depth"]}


if __name__ == "__main__":  # separately measured producer process
    import resource
    import tracemalloc
    args = json.loads(sys.argv[1])
    sys.path[:0] = args.pop("paths")
    os.scandir = _counting_scandir(os.scandir)   # instrumentation only: counts every enumerated entry
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        result, status = produce(**args), "ok"
    except Exception as exc:  # noqa: BLE001
        result, status = {"reason": getattr(exc, "reason", type(exc).__name__), "detail": str(exc)[:200]}, "refused"
    _, peak = tracemalloc.get_traced_memory()
    io = dict(line.split(": ") for line in open("/proc/self/io").read().splitlines())
    print(json.dumps({"status": status, "result": result, "time_s": round(time.perf_counter() - t0, 3),
                      "heap_peak_MiB": round(peak / 2**20, 2),
                      "rss_MiB": round(int(next(l.split()[1] for l in open("/proc/self/status") if l.startswith("VmHWM"))) / 1024, 1),
                      "io": {"rchar": int(io["rchar"]), "wchar": int(io["wchar"])},
                      "dir_entries_enumerated": ENUMERATED["entries"],
                      "uid": os.getuid()}))
