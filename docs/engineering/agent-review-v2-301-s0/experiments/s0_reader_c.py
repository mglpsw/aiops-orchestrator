"""EXPERIMENTAL ONLY -- #301 S0 architecture C: the RUNNER-side closure reader (no privilege).

Runs as the unprivileged runner over a COMMITTED snapshot published by the privilege-separated producer
(s0_snapshot_c.py). Producer owns immutability; runner owns subject derivation.

  expected_subject = {object_format, commit_oid, component_policy}   # the only external identity
  reader principal : the credentials the FILESYSTEM uses and git INHERITS, read from /proc/self/status and
                     compared with an EXPECTED runner principal given by the caller (never self-reported):
                     real == effective == saved == filesystem uid == expected uid, same for gids, and
                     CapEff == CapPrm == CapInh == CapAmb == 0 (ReaderPrincipalIdentity != ReaderSelfAssertion)
  precondition     : the reader refuses a snapshot its principal could mutate: the path must be absolute and
                     canonical with NO symlink in any component (so the path git re-resolves is the path checked),
                     and no component from / down, nor any node below, may be owned by the effective uid or be
                     writable under EFFECTIVE credentials (faccessat AT_EACCESS: euid, egid, supplementary groups)
                     -- the boundary is checked at consumption, not assumed
  object format    : from expected_subject; checked against the commit id length and the producer-authored
                     snapshot config (the live repository is never asked)
  local git        : `git -c safe.directory=* cat-file --batch` on the snapshot only; own session, RLIMIT_AS,
                     per-object deadline, strict header (s0_capture.VerifiedObjectReader); the reader is a
                     CHILD SUBREAPER (verified before git starts), so every descendant (setsid included)
                     re-parents to it and the unit teardown kills and reaps the whole subtree; a descendant
                     that survives the teardown deadline turns the capture into a refusal on EVERY path
                     (success or failure): unit_teardown_incomplete takes precedence, the first failure is kept
                     as primary_failure
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
PR_GET_CHILD_SUBREAPER = 37


class Refused(Exception):
    def __init__(self, reason, detail="", **debug):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.debug = debug   # experimental diagnostics only (no production schema)


PARSE_STATS = {"largest_tree_list": 0, "last_parser_cap": None, "materialized_beyond_remaining": 0}   # instrumentation (K4)


def reader_principal() -> dict:
    """The process credentials as the KERNEL reports them (not os.getuid())."""
    f = {}
    for line in open("/proc/self/status"):
        k, _, v = line.partition(":")
        f[k] = v.split()
    return {"uid": [int(x) for x in f["Uid"]], "gid": [int(x) for x in f["Gid"]],
            "groups": sorted(int(x) for x in f.get("Groups", [])),
            "caps": {c: f[c][0] for c in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")}}


def establish_reader_principal(expected: dict) -> dict:
    """ReaderPrincipalEstablished: real/effective/saved/filesystem ids all equal the EXPECTED runner principal,
    and no capability is held or re-activatable. CapBnd is recorded, not required to be empty."""
    if not expected or "uid" not in expected or "gid" not in expected:
        raise Refused("reader_principal_unspecified")
    p = reader_principal()
    if any(u != int(expected["uid"]) for u in p["uid"]) or any(g != int(expected["gid"]) for g in p["gid"]):
        raise Refused("reader_principal_mismatch", f"uid={p['uid']} gid={p['gid']} expected={expected}", principal=p)
    held = {c: v for c, v in p["caps"].items() if c != "CapBnd" and int(v, 16) != 0}
    if held:
        raise Refused("reader_has_capabilities", json.dumps(held), principal=p)
    return p


def become_subreaper() -> None:
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise Refused("subreaper_unavailable")


def is_subreaper() -> bool:
    flag = ctypes.c_int(0)
    if ctypes.CDLL(None, use_errno=True).prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(flag), 0, 0, 0) != 0:
        return False
    return flag.value == 1


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


def require_immutable_to_me(snapshot: Path, expected_principal: dict) -> dict:
    """ReaderPrincipalEstablished AND CanonicalSnapshotPath AND NoSymlinkComponent AND
    NOT EffectiveCredentialsCanMutateSnapshot -- checked by the reader itself (C11, K1).

    The check is only meaningful if git later resolves the SAME objects with the SAME credentials: the principal
    has no capability and no other uid to switch to, and an absolute, canonical path with no symlink component
    cannot be re-pointed by a principal that owns and can write none of its components."""
    principal = establish_reader_principal(expected_principal)
    raw = os.fspath(snapshot)
    if not os.path.isabs(raw) or os.path.normpath(raw) != raw or raw.startswith("//"):
        raise Refused("snapshot_path_not_canonical", raw)
    me = principal["uid"][1]   # effective == filesystem uid (established above)

    def mutable(p: str, st) -> bool:
        return st.st_uid == me or os.access(p, os.W_OK, effective_ids=True)

    prefix = "/"
    for part in [""] + raw.strip("/").split("/"):
        prefix = os.path.join(prefix, part) if part else "/"
        st = os.lstat(prefix)
        if stat.S_ISLNK(st.st_mode):
            raise Refused("snapshot_path_not_canonical", "symlink component: " + prefix)
        if mutable(prefix, st):
            raise Refused("snapshot_mutable_by_reader", prefix)

    def unreadable(exc: OSError) -> None:
        raise Refused("snapshot_not_fully_inspectable", str(exc))

    for dirpath, dirnames, filenames in os.walk(raw, onerror=unreadable):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode) or mutable(p, st):
                raise Refused("snapshot_mutable_by_reader", p)
    return principal


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


def walk(commit: str, load, *, component_limit: int, root_override: str | None = None, budget=None,
         _ablation_global_tree_cap: bool = False):
    """ONE walk used to fetch and to derive. root_override exists ONLY as the ablation mutant.

    With a budget (fetch phase) every logical node is charged: node count, depth and cumulative path bytes
    (Budget.charge_node), and every blob OCCURRENCE is charged as payload -- the first from the transport header
    before the body is read, each repeat from the cached authenticated body before it is used again."""
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
        # K4: TreeParserExpansion <= RemainingClosureNodeBudget (not a global constant)
        remaining = 100_000 - count
        if budget is not None:
            remaining = min(remaining, budget.max_nodes - budget.nodes)
        if remaining <= 0:
            raise Refused("budget_nodes", "no remaining node budget before parsing tree " + tree_oid)
        cap_entries = 100_000 if _ablation_global_tree_cap else remaining
        PARSE_STATS["last_parser_cap"] = cap_entries
        try:
            entries = cap._parse_tree(load(tree_oid, "tree"), len(commit) // 2, cap_entries)
        except cap.CaptureRefused as exc:
            if exc.reason == "tree_unrepresentable" and cap_entries < 100_000:
                # C3 reports entry overflow with the same code as some unrepresentable names; the cap it was
                # fed is the remaining closure budget, so the refusal is attributed to that budget (S1: ask the
                # C3 owner for a distinct overflow reason)
                raise Refused("budget_nodes", f"tree {tree_oid} exceeds remaining node budget {cap_entries}") from exc
            raise
        PARSE_STATS["largest_tree_list"] = max(PARSE_STATS["largest_tree_list"], len(entries))
        if len(entries) > remaining:   # only reachable under the ablation: a list beyond the remaining budget
            PARSE_STATS["materialized_beyond_remaining"] = max(PARSE_STATS["materialized_beyond_remaining"], len(entries))
        for mode, obj_type, oid, name in entries:
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
            if budget is not None:
                budget.charge_node(path, depth + 1)
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
                  argv=None, abort_delay_s=0.0, contain_unit=True, budget_overrides=None, expected_principal=None,
                  teardown=None, _ablation_teardown_success_only=False, _ablation_global_tree_cap=False):
    import s0_capture as cap
    principal = require_immutable_to_me(snapshot, expected_principal)
    if contain_unit and not is_subreaper():
        raise Refused("subreaper_required")
    fmt, commit, limit = check_expected_subject(snapshot, expected)
    reader = cap.VerifiedObjectReader(snapshot, FORMATS[fmt], argv=argv or ["git", "-c", "safe.directory=*", "cat-file", "--batch"],
                                      address_space=envelope, deadline_s=deadline_s, abort_delay_s=abort_delay_s)
    budget = cap.Budget(max_component_len=limit, max_payload_bytes=payload_budget, **(budget_overrides or {}))
    objmap, ok = {}, False
    try:
        def load(oid, kind):
            if oid not in objmap:
                objmap[oid] = (kind, reader.get(oid, kind, budget))   # charged from the header, before the read
            elif kind == "blob":
                budget.charge_payload(oid, len(objmap[oid][1]))       # repeated occurrence: charged again
            return objmap[oid][1]
        walk(commit, load, component_limit=limit, budget=budget, _ablation_global_tree_cap=_ablation_global_tree_cap)
        ok = True
        return objmap, {"objects_read": reader.objects_read, "unique_objects": len(objmap),
                        "payload_bytes": budget.payload_bytes, "metadata_bytes": budget.metadata_bytes,
                        "path_bytes_charged": budget.path_bytes, "reader_principal": principal}
    finally:
        primary = sys.exc_info()[1]
        reader.close() if ok else reader.abort()
        if contain_unit:
            unit = (teardown or teardown_unit)()
            # K2: any surviving transport descendant is an independent, still-active failure on EVERY path
            if unit["remaining"] and (ok or not _ablation_teardown_success_only):
                raise Refused("unit_teardown_incomplete", str(unit["remaining"]),
                              primary_failure=(getattr(primary, "reason", type(primary).__name__) if primary else None),
                              teardown_failure=unit)


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
                                        payload_budget=args.get("payload_budget", 64 << 20),
                                        budget_overrides=args.get("budget_overrides"),
                                        expected_principal=args.get("expected_principal"),
                                        _ablation_global_tree_cap=args.get("ablation_global_tree_cap", False))
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
            objmap, _ = fetch_objects(snapshot, args["expected"], expected_principal=args.get("expected_principal"))
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
        elif mode == "k2":
            # a transport that FAILS, and a teardown that REPORTS a surviving descendant (real D state is not
            # constructible here): the branch logic is what is discriminated
            try:
                fetch_objects(snapshot, args["expected"], expected_principal=args.get("expected_principal"),
                              argv=args["argv"], deadline_s=2.0,
                              teardown=lambda: {"killed": 0, "remaining": [[424242, "stub-survivor"]]},
                              _ablation_teardown_success_only=args.get("ablation_success_only", False))
                res = {"outcome": "ACCEPTED"}
            except Refused as exc:
                res = {"outcome": "REFUSED:" + exc.reason, **exc.debug}
            except Exception as exc:  # noqa: BLE001
                res = {"outcome": "REFUSED:" + getattr(exc, "reason", type(exc).__name__)}
            result = {**res, "real_unit_after": teardown_unit()}
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
        result, status = {"reason": getattr(exc, "reason", type(exc).__name__), "detail": str(exc)[:200],
                          **getattr(exc, "debug", {})}, "refused"
    _, peak = tracemalloc.get_traced_memory()
    print(json.dumps({"status": status, "result": result, "uid": os.getuid(), "parse_stats": PARSE_STATS,
                      "principal_observed": reader_principal(), "time_s": round(time.perf_counter() - t0, 3),
                      "heap_peak_MiB": round(peak / 2**20, 2), "rss_MiB": _vmhwm(),
                      "git_unit_rss_MiB_upper_bound": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1)}))
