"""EXPERIMENTAL ONLY -- #301 S0 capture/seal/validate prototype. Not production code.

Opt-in evidence for docs/engineering/agent-review-v2-301-s0/CONTRACT.md. Nothing under
app/ imports this module, and pytest does not discover it (testpaths = tests).

What it demonstrates (each claim is exercised by an exp_*.py script, not by this file):

  VerifiedObjectReader  every Git object consumed is re-hashed at read time against the
                        oid it was requested by (type + length + content, per Git's object
                        format). The hash algorithm is chosen by the EXPECTED oid's length
                        and cross-checked with the repository's declared object format.
  build_subject         commit -> tree -> blob closure; tree bytes are parsed by C3's own
                        `_parse_tree_data` (the existing owner of the raw tree rule); every
                        budget is charged per OCCURRENCE before the object body is read.
  serialize / parse     (s0_bootstrap) one canonical container: header (algorithm, commit, root tree),
                        index and payload in the same bytes, so the index is covered by the
                        same seal and digest as the payload.
  seal_committed        (s0_bootstrap) memfd + F_SEAL_{WRITE,GROW,SHRINK,SEAL}; commitment only after the
                        seals are read back AND the sealed bytes re-hash to the digest of the
                        authenticated bytes (a pre-seal writer is caught here).
  open_sealed           (s0_bootstrap) consumer-side validation of a received descriptor.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Container format, sealing commitment and consumer validation have ONE owner: the bootstrap.
from s0_bootstrap import (  # noqa: F401  (re-exported for the exp_* scripts)
    REQUIRED_SEALS, CaptureRefused, open_sealed, parse, seal_committed, serialize,
)

ALGORITHM_BY_HEXLEN = {40: "sha1", 64: "sha256"}
GIT_INVOCATIONS = [0]  # counted per build, reported by EXP-FUNC
GIT_ENV = {
    "PATH": os.defpath,
    "HOME": "/nonexistent",
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_NO_REPLACE_OBJECTS": "1",
}


@dataclass
class Budget:
    # Values MIRROR C3 where C3 owns the rule (entries 100_000, depth 100, component 255); the
    # byte budgets are S's own (RAM-backed; C3's 2 GiB is a disk budget and does not transfer).
    # S1 must obtain the C3-owned rules from C3's hierarchical builder, not from these copies.
    max_nodes: int = 100_000
    max_depth: int = 100
    max_payload_bytes: int = 64 * 1024 * 1024  # blob bytes, per occurrence
    max_metadata_bytes: int = 64 * 1024 * 1024  # commit + tree bodies, charged before reading
    max_commit_bytes: int = 1024 * 1024
    max_path_bytes: int = 16 * 1024 * 1024
    max_component_len: int = 255
    nodes: int = 0
    payload_bytes: int = 0
    metadata_bytes: int = 0
    path_bytes: int = 0
    unique_payload: dict = field(default_factory=dict)

    def charge_node(self, path: bytes, depth: int) -> None:
        if depth > self.max_depth:
            raise CaptureRefused("budget_depth", str(depth))
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise CaptureRefused("budget_nodes", str(self.nodes))
        self.path_bytes += len(path)
        if self.path_bytes > self.max_path_bytes:
            raise CaptureRefused("budget_path_bytes", str(self.path_bytes))

    def charge_metadata(self, kind: str, size: int) -> None:
        """Charged from the object HEADER, before the commit/tree body is read."""
        if kind == "commit" and size > self.max_commit_bytes:
            raise CaptureRefused("budget_commit_bytes", str(size))
        if kind == "tree" and size // 295 > self.max_nodes - self.nodes:
            # C3's pre-read bound (`_list_single_tree_entries_v2`): an entry is at most 295 bytes, so a
            # body this large cannot fit the remaining entry budget. Mirrored here; owned by C3.
            raise CaptureRefused("budget_tree_entries_pre_read", str(size))
        self.metadata_bytes += size
        if self.metadata_bytes > self.max_metadata_bytes:
            raise CaptureRefused("budget_metadata_bytes", str(self.metadata_bytes))

    def charge_payload(self, oid: str, size: int) -> None:
        # Charged from the object HEADER, before the body is read: an oversized or
        # repeatedly referenced blob is refused without being expanded into memory.
        self.payload_bytes += size
        self.unique_payload[oid] = size
        if self.payload_bytes > self.max_payload_bytes:
            raise CaptureRefused("budget_payload_bytes", str(self.payload_bytes))


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, timeout=60)
    if cp.returncode != 0:
        raise CaptureRefused("git_failed", " ".join(args))
    return cp.stdout.decode("ascii").strip()


class VerifiedObjectReader:
    """`git cat-file --batch` as an UNTRUSTED transport: bytes are accepted only if they hash
    to the oid they were requested by. `verify=False` exists solely as the ablation mutant."""

    def __init__(self, repo: Path, expected_hexlen: int, *, verify: bool = True) -> None:
        if expected_hexlen not in ALGORITHM_BY_HEXLEN:
            raise CaptureRefused("object_format_unsupported", str(expected_hexlen))
        self.algorithm = ALGORITHM_BY_HEXLEN[expected_hexlen]
        declared = _git(repo, "rev-parse", "--show-object-format")
        if declared != self.algorithm:
            raise CaptureRefused("object_format_mismatch", f"expected={self.algorithm} repo={declared}")
        self.verify = verify
        self.bytes_read = 0
        self.objects_read = 0
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=repo, env=GIT_ENV,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )

    def get(self, oid: str, want: str, budget: Budget | None = None) -> bytes:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(oid.encode("ascii") + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().rstrip(b"\n").split(b" ")
        if len(header) == 2 and header[1] == b"missing":
            raise CaptureRefused("object_missing", oid)
        if len(header) != 3 or header[0].decode("ascii", "replace") != oid:
            raise CaptureRefused("object_header_invalid", oid)
        kind, size = header[1].decode("ascii", "replace"), int(header[2])
        if kind != want:
            raise CaptureRefused("object_type_mismatch", f"{oid} want={want} got={kind}")
        if budget is not None:  # every body is charged from its header, before it is read
            if want == "blob":
                budget.charge_payload(oid, size)
            else:
                budget.charge_metadata(want, size)
        body = self.proc.stdout.read(size)
        if len(body) != size or self.proc.stdout.read(1) != b"\n":
            raise CaptureRefused("object_truncated", oid)
        self.bytes_read += size
        self.objects_read += 1
        if self.verify:
            h = hashlib.new(self.algorithm)
            h.update(kind.encode("ascii") + b" " + str(size).encode("ascii") + b"\0")
            h.update(body)
            if h.hexdigest() != oid:
                raise CaptureRefused("object_hash_mismatch", f"{kind} {oid}")
        return body

    def close(self) -> None:
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


def _parse_tree(raw: bytes, oid_len: int, max_entries: int):
    # Derive, do not reimplement: C3's raw tree rule (name validity, mode classes) is the
    # authority. Imported lazily so the consumer-side bootstrap never needs it.
    from app.agent_review.git_commit_subject_v2 import SubjectMaterialisationError, _parse_tree_data

    try:
        return _parse_tree_data(raw, oid_len, max_entries)
    except SubjectMaterialisationError as exc:
        raise CaptureRefused("tree_unrepresentable", getattr(exc, "reason_code", str(exc))) from exc


@dataclass
class Subject:
    algorithm: str
    commit: str
    root_tree: str
    nodes: dict  # raw path bytes -> (kind, oid-or-"", payload bytes)
    budget: Budget
    objects_read: int
    git_bytes_read: int


def build_subject(repo: Path, commit: str, *, budget: Budget | None = None, verify: bool = True) -> Subject:
    budget = budget or Budget()
    reader = VerifiedObjectReader(repo, len(commit), verify=verify)
    GIT_INVOCATIONS[0] += 2  # rev-parse --show-object-format + cat-file --batch
    try:
        commit_body = reader.get(commit, "commit", budget)
        first = commit_body.split(b"\n", 1)[0].split(b" ")
        if len(first) != 2 or first[0] != b"tree" or len(first[1]) != len(commit):
            raise CaptureRefused("commit_unparseable", commit)
        root_tree = first[1].decode("ascii")
        nodes: dict = {}
        stack = [(b"", root_tree, 0, (root_tree,))]
        while stack:
            prefix, tree_oid, depth, ancestors = stack.pop()
            raw = reader.get(tree_oid, "tree", budget)
            seen: set = set()
            for mode, obj_type, oid, name in _parse_tree(raw, len(commit) // 2, budget.max_nodes - budget.nodes):
                # mirrors of C3 trie rules (_build_canonical_trie_hierarchical); owned by C3
                if os.fsencode(os.fsdecode(name)) != name:
                    raise CaptureRefused("tree_unrepresentable", "fs_round_trip")
                if name in seen:
                    raise CaptureRefused("tree_duplicate_name", repr(prefix + name))
                seen.add(name)
                if len(name) > budget.max_component_len:
                    raise CaptureRefused("budget_component_len", repr(name[:32]))
                path = prefix + name
                budget.charge_node(path, depth + 1)
                if obj_type == "tree":
                    if oid in ancestors:  # unreachable under hash-on-read; kept as C3 mirror
                        raise CaptureRefused("tree_cycle", oid)
                    nodes[path] = ("tree", oid, b"")
                    stack.append((path + b"/", oid, depth + 1, ancestors + (oid,)))
                elif mode in ("100644", "100755"):
                    nodes[path] = ("executable" if mode == "100755" else "regular", oid, reader.get(oid, "blob", budget))
                elif mode == "120000":
                    nodes[path] = ("symlink", oid, reader.get(oid, "blob", budget))
                else:  # gitlink (160000) and every non-canonical mode: explicit refusal, never a skip
                    raise CaptureRefused("unsupported_mode", f"{mode} {path!r}")
        return Subject(reader.algorithm, commit, root_tree, nodes, budget, reader.objects_read, reader.bytes_read)
    finally:
        reader.close()


def lock_bytes(nodes: dict, path: bytes = b"requirements-agent-review.lock") -> bytes:
    """The lock that authorizes S_D must be a REGULAR file node of S_G (not a symlink's target text)."""
    node = nodes.get(path)
    if node is None or node[0] != "regular":
        raise CaptureRefused("dependency_lock_not_regular_file", repr(node[0] if node else None))
    return node[2]


def open_fds() -> list[int]:
    return sorted(int(n) for n in os.listdir("/proc/self/fd"))
