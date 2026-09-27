"""EXPERIMENTAL ONLY -- #301 S0 architecture C: the RUNNER-side closure reader (no privilege).

Runs as the unprivileged runner over a COMMITTED snapshot published by the privilege-separated producer
(s0_snapshot_c.py). Producer owns immutability; runner owns subject derivation.

  expected_subject = {object_format, commit_oid, component_policy}   # the only external identity
  precondition     : the reader refuses a snapshot it could mutate (ownership/writability of every node
                     and of every ancestor directory) -- the boundary is checked at consumption, not assumed
  object format    : from expected_subject; checked against the commit id length and the producer-authored
                     snapshot config (the live repository is never asked)
  local git        : `git -c safe.directory=* cat-file --batch` on the snapshot only; own session, RLIMIT_AS,
                     per-object deadline, strict header (s0_capture.VerifiedObjectReader); the reader is a
                     CHILD SUBREAPER, so every descendant (setsid included) re-parents to it and the unit
                     teardown kills and reaps the whole subtree
  identity         : C + a content-addressed object map; root tree T DERIVED from the authenticated commit
                     bytes; one walk fetches and derives; seal; re-derive and compare with the SEALED bytes
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import signal
import stat
import sys
import time
from pathlib import Path

FORMATS = {"sha1": 40, "sha256": 64}
PR_SET_CHILD_SUBREAPER = 36


class Refused(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason


def become_subreaper() -> None:
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise Refused("subreaper_unavailable")


def descendants(root_pid: int) -> list:
    parent = {}
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                parent[int(pid)] = int(open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                pass
    out, frontier = [], [root_pid]
    while frontier:
        nxt = [p for p, pp in parent.items() if pp in frontier and p != root_pid]
        out += nxt
        frontier = nxt
    return out


def teardown_unit(deadline_s: float = 5.0) -> dict:
    """Kill + reap every descendant of this (subreaper) process until none remain."""
    killed, t_end = set(), time.monotonic() + deadline_s
    while time.monotonic() < t_end:
        ds = descendants(os.getpid())
        if not ds:
            break
        for p in ds:
            try:
                os.kill(p, signal.SIGKILL)
                killed.add(p)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
        time.sleep(0.01)
    return {"killed": len(killed), "remaining": descendants(os.getpid())}


def require_immutable_to_me(snapshot: Path) -> None:
    me = os.getuid()
    chain = [snapshot, *snapshot.parents]
    for d in chain:
        st = os.lstat(d)
        if st.st_uid == me or os.access(d, os.W_OK):
            raise Refused("snapshot_mutable_by_reader", str(d))
    for dirpath, dirnames, filenames in os.walk(snapshot):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            if st.st_uid == me or stat.S_ISLNK(st.st_mode) or os.access(p, os.W_OK):
                raise Refused("snapshot_mutable_by_reader", p)


def snapshot_format(snapshot: Path) -> str:
    """Object format declared by the PRODUCER-authored snapshot config (git's own default is sha1)."""
    import re
    section, fmt = None, "sha1"
    for line in (snapshot / "config").read_text().splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line.strip("[]").strip().lower()
        elif section == "extensions" and re.fullmatch(r"(?i)objectformat\s*=\s*(\S+)", line):
            fmt = re.fullmatch(r"(?i)objectformat\s*=\s*(\S+)", line).group(1).lower()
    return fmt


def git_object_hash(alg: str, kind: str, body: bytes) -> str:
    h = hashlib.new(alg)
    h.update(kind.encode() + b" %d\0" % len(body) + body)
    return h.hexdigest()


def walk(commit: str, load, *, component_limit: int, root_override: str | None = None):
    """ONE walk used to fetch and to derive. root_override exists ONLY as the ablation mutant."""
    import s0_capture as cap
    body = load(commit, "commit")
    first = body.split(b"\n", 1)[0].split(b" ")
    if len(first) != 2 or first[0] != b"tree" or len(first[1]) != len(commit):
        raise Refused("commit_unparseable")
    root = first[1].decode() if root_override is None else root_override
    nodes, stack, count = {}, [(b"", root, 0)], 0
    while stack:
        prefix, tree_oid, depth = stack.pop()
        seen = set()
        for mode, obj_type, oid, name in cap._parse_tree(load(tree_oid, "tree"), len(commit) // 2, 100_000):
            if name in seen:
                raise Refused("tree_duplicate_name")
            seen.add(name)
            if len(name) > component_limit:
                raise Refused("budget_component_len")
            if depth + 1 > 100:
                raise Refused("budget_depth")
            count += 1
            if count > 100_000:
                raise Refused("budget_nodes")
            path = prefix + name
            if obj_type == "tree":
                nodes[path] = ("tree", oid, b"")
                stack.append((path + b"/", oid, depth + 1))
            elif mode in ("100644", "100755"):
                nodes[path] = ("executable" if mode == "100755" else "regular", oid, load(oid, "blob"))
            elif mode == "120000":
                nodes[path] = ("symlink", oid, load(oid, "blob"))
            else:
                raise Refused("unsupported_mode", mode)
    return root, nodes


def check_expected_subject(snapshot: Path, expected: dict) -> tuple:
    fmt, commit = expected["object_format"], expected["commit_oid"]
    if fmt not in FORMATS or len(commit) != FORMATS[fmt] or any(c not in "0123456789abcdef" for c in commit):
        raise Refused("expected_subject_invalid")
    if snapshot_format(snapshot) != fmt:
        raise Refused("snapshot_format_mismatch", snapshot_format(snapshot))
    return fmt, commit, int(expected["component_policy"]["max_component_len"])


def fetch_objects(snapshot: Path, expected: dict, *, envelope=128 << 20, deadline_s=30.0, payload_budget=64 << 20,
                  argv=None, abort_delay_s=0.0, contain_unit=True):
    import s0_capture as cap
    require_immutable_to_me(snapshot)
    fmt, commit, limit = check_expected_subject(snapshot, expected)
    reader = cap.VerifiedObjectReader(snapshot, FORMATS[fmt], argv=argv or ["git", "-c", "safe.directory=*", "cat-file", "--batch"],
                                      address_space=envelope, deadline_s=deadline_s, abort_delay_s=abort_delay_s)
    budget = cap.Budget(max_component_len=limit, max_payload_bytes=payload_budget)
    objmap, ok = {}, False
    try:
        def load(oid, kind):
            if oid not in objmap:
                objmap[oid] = (kind, reader.get(oid, kind, budget))
            return objmap[oid][1]
        walk(commit, load, component_limit=limit)
        ok = True
        return objmap, {"objects_read": reader.objects_read, "unique_objects": len(objmap),
                        "payload_bytes": budget.payload_bytes, "metadata_bytes": budget.metadata_bytes}
    finally:
        reader.close() if ok else reader.abort()
        if contain_unit:
            teardown_unit()


def commit_sg(expected: dict, objmap: dict, *, root_override: str | None = None):
    from s0_bootstrap import parse, seal_committed, serialize
    fmt, commit, limit = expected["object_format"], expected["commit_oid"], int(expected["component_policy"]["max_component_len"])

    def load(oid, kind):
        if oid not in objmap:
            raise Refused("object_missing_from_map", oid)
        k, body = objmap[oid]
        if k != kind or git_object_hash(fmt, kind, body) != oid:
            raise Refused("object_map_binding_mismatch", oid)
        return body

    root, nodes = walk(commit, load, component_limit=limit, root_override=root_override)
    data = serialize(fmt, commit, root, nodes)
    fd = seal_committed(data, name="ar301-s0-c-sg")
    try:
        s_alg, s_commit, s_root, s_nodes = parse(os.pread(fd, os.fstat(fd).st_size, 0))
        d_root, d_nodes = walk(commit, load, component_limit=limit, root_override=root_override)
        if (s_alg, s_commit, s_root) != (fmt, commit, d_root) or s_nodes != d_nodes:
            raise Refused("sealed_derivation_mismatch")
        return fd, hashlib.sha256(data).hexdigest(), s_root, s_nodes
    except BaseException:
        os.close(fd)
        raise


def _vmhwm() -> float:
    return round(int(next(l.split()[1] for l in open("/proc/self/status") if l.startswith("VmHWM"))) / 1024, 1)


if __name__ == "__main__":
    import resource
    import tracemalloc
    mode, args = sys.argv[1], json.loads(sys.argv[2])
    sys.path[:0] = args.pop("paths")
    snapshot = Path(args["snapshot"]) if "snapshot" in args else None
    if args.get("subreaper", True):
        become_subreaper()
    tracemalloc.start()
    t0 = time.perf_counter()
    try:
        if mode == "acquire":
            objmap, acq = fetch_objects(snapshot, args["expected"], abort_delay_s=args.get("abort_delay_s", 0.0),
                                        payload_budget=args.get("payload_budget", 64 << 20))
            fd, digest, root, nodes = commit_sg(args["expected"], objmap)
            os.close(fd)
            result = {"s_g_sha256": digest, "root_tree_derived": root, "nodes": len(nodes),
                      "node_kinds": ({p.decode("utf-8", "backslashreplace"): v[0] for p, v in sorted(nodes.items())}
                                     if len(nodes) <= 64 else None),
                      "symlink_targets": ({p.decode(): v[2].decode("utf-8", "backslashreplace") for p, v in nodes.items()
                                           if v[0] == "symlink"} if len(nodes) <= 64 else None),
                      "path_bytes": sum(len(p) for p in nodes), **acq,
                      "object_format_origin": "expected_subject; checked vs commit id length and producer-authored snapshot config"}
        elif mode == "c6":
            objmap, _ = fetch_objects(snapshot, args["expected"])
            fd, dg, root, _n = commit_sg(args["expected"], objmap)
            os.close(fd)
            aux = {"root_tree": args["other_tree"]}   # mutable auxiliary record / identity pointing at another REAL tree
            identity = dict(aux)
            fd2, dg2, root2, _n2 = commit_sg(args["expected"], objmap)   # takes no root input at all
            os.close(fd2)
            fd3, dg3, root3, _n3 = commit_sg(args["expected"], objmap, root_override=identity["root_tree"])
            os.close(fd3)
            forged = dict(objmap)
            k, body = forged[args["other_tree"]]
            forged[args["other_tree"]] = (k, body[:-1] + bytes([body[-1] ^ 1]))
            try:
                commit_sg(args["expected"], forged)
                forged_result = "COMMITTED"
            except Refused as exc:
                forged_result = "REFUSED:" + exc.reason
            result = {"root": root, "root_with_aux_records_present": root2, "digest_stable": dg == dg2,
                      "ablation_root": root3, "ablation_changes_S_G": dg3 != dg and root3 != root, "forged_map": forged_result}
        elif mode == "c7":
            import s0_capture as cap
            reader = cap.VerifiedObjectReader(Path(args["cwd"]), 40, argv=args["argv"], address_space=None, deadline_s=2.0)
            try:
                reader.get("a" * 40, "blob", cap.Budget(max_component_len=255))
                res = "ACCEPTED"
            except cap.CaptureRefused as exc:
                res = "REFUSED:" + exc.reason
            unit = teardown_unit() if args.get("subreaper", True) else {"teardown": "none (ablation)"}
            result = {"reader": res, "unit": unit}
        status = "ok"
    except Exception as exc:  # noqa: BLE001
        result, status = {"reason": getattr(exc, "reason", type(exc).__name__), "detail": str(exc)[:200]}, "refused"
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps({"status": status, "result": result, "uid": os.getuid(), "time_s": round(time.perf_counter() - t0, 3),
                      "heap_peak_MiB": round(peak / 2**20, 2), "rss_MiB": _vmhwm(),
                      "git_unit_rss_MiB_upper_bound": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)}))
