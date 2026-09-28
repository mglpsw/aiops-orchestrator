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
    """Admission policy of one S_G capture.

    Values MIRROR C3 where C3 owns the rule (entries 100_000, depth 100); the byte budgets are S's own
    (RAM-backed; C3's 2 GiB is a disk budget and does not transfer). `max_component_len` is an
    EXPLICIT admission parameter (decision (d)): S_G is not a filesystem, so no value is intrinsic to
    it; C3 parity holds only when the caller passes the limit C3 admitted under.
    `child_address_space_bytes` is the kernel envelope of the untrusted git transport (decision (a)).
    """
    max_component_len: int | None = None
    max_nodes: int = 100_000
    max_depth: int = 100
    max_payload_bytes: int = 64 * 1024 * 1024  # blob bytes, per occurrence
    max_metadata_bytes: int = 64 * 1024 * 1024  # commit + tree bodies, charged before reading
    max_commit_bytes: int = 1024 * 1024
    max_path_bytes: int = 16 * 1024 * 1024
    child_address_space_bytes: int | None = 128 * 1024 * 1024
    transport_deadline_s: float | None = 30.0  # per object; None = ablation (no deadline)
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
        # Charged from the object HEADER, before the body is read.
        self.payload_bytes += size
        self.unique_payload[oid] = size
        if self.payload_bytes > self.max_payload_bytes:
            raise CaptureRefused("budget_payload_bytes", str(self.payload_bytes))


MAX_HEADER_BYTES = 160  # "<64-hex oid> <type> <decimal size>\n" with room to spare
GIT_OBJECT_TYPES = (b"commit", b"tree", b"blob", b"tag")


def _contain(address_space: int | None):
    """preexec hook: the kernel owns the git child's memory envelope, set BEFORE exec (decision (a))."""
    if address_space is None:
        return None

    def apply() -> None:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    return apply


def _git(repo: Path, *args: str, address_space: int | None = None, timeout: float | None = 60) -> str:
    cp = subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, capture_output=True, timeout=timeout or 60,
                        preexec_fn=_contain(address_space))
    if cp.returncode != 0:
        raise CaptureRefused("git_failed", " ".join(args))
    return cp.stdout.decode("ascii").strip()


class VerifiedObjectReader:
    """`git cat-file --batch` as an UNTRUSTED, CONTAINED transport over the PRIVATE SNAPSHOT (arch. B).

    - own process group (start_new_session) under RLIMIT_AS set before exec; refusal kills the GROUP;
    - unbuffered pipe read through select() with a per-object DEADLINE (a malformed object must not
      hang the capture: availability is a separate property from integrity, and both are required);
    - header read with a bound and parsed strictly before anything is charged or read;
    - bytes accepted only if they hash to the oid they were requested by.
    `verify=False` is the ablation mutant; `argv`/`abort_delay_s` are test hooks."""

    def __init__(self, repo: Path, expected_hexlen: int, *, verify: bool = True,
                 address_space: int | None = 128 * 1024 * 1024, argv: list | None = None,
                 abort_delay_s: float = 0.0, deadline_s: float | None = 30.0) -> None:
        if expected_hexlen not in ALGORITHM_BY_HEXLEN:
            raise CaptureRefused("object_format_unsupported", str(expected_hexlen))
        self.algorithm = ALGORITHM_BY_HEXLEN[expected_hexlen]
        if argv is None:
            try:
                declared = _git(repo, "rev-parse", "--show-object-format", address_space=address_space,
                                timeout=deadline_s)
            except subprocess.TimeoutExpired as exc:
                raise CaptureRefused("transport_deadline", "rev-parse") from exc
            if declared != self.algorithm:
                raise CaptureRefused("object_format_mismatch", f"expected={self.algorithm} repo={declared}")
        self.verify = verify
        self.abort_delay_s = abort_delay_s
        self.deadline_s = deadline_s
        self.bytes_read = 0
        self.objects_read = 0
        self._buf = bytearray()
        self.proc = subprocess.Popen(
            argv or ["git", "cat-file", "--batch"], cwd=repo, env=GIT_ENV, bufsize=0,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            preexec_fn=_contain(address_space), start_new_session=True,
        )

    def _fill(self, deadline: float | None) -> bool:
        import select
        import time
        fd = self.proc.stdout.fileno()
        timeout = None if deadline is None else max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            raise CaptureRefused("transport_deadline")
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return False
        self._buf += chunk
        return True

    def _line(self, limit: int, deadline: float | None) -> bytes:
        while b"\n" not in self._buf:
            if len(self._buf) >= limit or not self._fill(deadline):
                raise CaptureRefused("transport_header_invalid", "unterminated_or_too_long")
        i = self._buf.index(b"\n")
        if i >= limit:
            raise CaptureRefused("transport_header_invalid", "unterminated_or_too_long")
        line = bytes(self._buf[:i + 1])
        del self._buf[:i + 1]
        return line

    def _exact(self, n: int, deadline: float | None) -> bytes:
        while len(self._buf) < n:
            if not self._fill(deadline):
                raise CaptureRefused("object_truncated")
        data = bytes(self._buf[:n])
        del self._buf[:n]
        return data

    def get(self, oid: str, want: str, budget: Budget | None = None) -> bytes:
        try:
            return self._get(oid, want, budget)
        except BaseException:
            self.abort()
            raise

    def _get(self, oid: str, want: str, budget: Budget | None) -> bytes:
        import time
        deadline = None if self.deadline_s is None else time.monotonic() + self.deadline_s
        try:
            self.proc.stdin.write(oid.encode("ascii") + b"\n")
        except OSError as exc:
            raise CaptureRefused("transport_failed", f"errno={exc.errno}") from exc
        line = self._line(MAX_HEADER_BYTES, deadline)
        fields = line[:-1].split(b" ")
        if len(fields) == 2 and fields[0] == oid.encode("ascii") and fields[1] == b"missing":
            raise CaptureRefused("object_missing", oid)
        if len(fields) != 3 or fields[0] != oid.encode("ascii"):
            raise CaptureRefused("transport_header_invalid", "fields_or_oid")
        kind_b, size_b = fields[1], fields[2]
        if kind_b not in GIT_OBJECT_TYPES:
            raise CaptureRefused("transport_header_invalid", "unknown_type")
        if not (1 <= len(size_b) <= 19) or not all(48 <= c <= 57 for c in size_b):  # ASCII digits only
            raise CaptureRefused("transport_header_invalid", "size_not_plain_decimal")
        kind, size = kind_b.decode("ascii"), int(size_b)
        if kind != want:
            raise CaptureRefused("object_type_mismatch", f"{oid} want={want} got={kind}")
        if budget is not None:  # every body is charged from its (strictly parsed) header, before it is read
            if want == "blob":
                budget.charge_payload(oid, size)
            else:
                budget.charge_metadata(want, size)
        body = self._exact(size + 1, deadline)
        if body[-1:] != b"\n":
            raise CaptureRefused("object_truncated", oid)
        body = body[:-1]
        self.bytes_read += size
        self.objects_read += 1
        if self.verify:
            h = hashlib.new(self.algorithm)
            h.update(kind_b + b" " + size_b + b"\0")
            h.update(body)
            if h.hexdigest() != oid:
                raise CaptureRefused("object_hash_mismatch", f"{kind} {oid}")
        return body

    def _kill_group(self) -> None:
        import signal
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)  # the whole transport group, not only the direct child
        except ProcessLookupError:
            pass

    def abort(self) -> None:
        """Refusal path: kill the process GROUP + reap FIRST, then release the pipes. Idempotent."""
        if self.abort_delay_s:
            import time
            time.sleep(self.abort_delay_s)  # test hook: an adversarially slow parent
        self._kill_group()
        self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=self.deadline_s or 5)
        except subprocess.TimeoutExpired:
            self._kill_group()
            self.proc.wait()
        self._kill_group()  # nothing of the transport group may outlive the capture
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except OSError:
            pass


def _parse_tree(raw: bytes, oid_len: int, max_entries: int):
    # Derive, do not reimplement: C3's raw tree rule (name validity, mode classes) is the
    # authority. Imported lazily so the consumer-side bootstrap never needs it.
    from app.agent_review.git_commit_subject_v2 import SubjectMaterialisationError, _parse_tree_data

    try:
        return _parse_tree_data(raw, oid_len, max_entries)
    except SubjectMaterialisationError as exc:
        raise CaptureRefused("tree_unrepresentable", getattr(exc, "reason_code", str(exc))) from exc


CONTAINER_CONTRACT = "AR-301-S0-EXP-1"


@dataclass(frozen=True)
class AcquisitionIdentity:
    """Created INSIDE the walk from the authenticated commit body; compared with the sealed header.
    Its integrity rests on the producer's process integrity (premise P), like the record's."""
    algorithm: str
    commit: str
    root_tree: str
    component_limit: int
    container_contract: str = CONTAINER_CONTRACT


@dataclass(frozen=True)
class Subject:
    algorithm: str
    commit: str
    root_tree: str
    nodes: dict  # raw path bytes -> (kind, oid-or-"", payload bytes)
    record: object  # read-only mapping raw path -> (kind, oid): what the authenticated walk admitted
    identity: AcquisitionIdentity
    budget: Budget
    objects_read: int
    git_bytes_read: int


def build_subject(repo: Path, commit: str, *, budget: Budget | None = None, verify: bool = True,
                  _abort_delay_s: float = 0.0) -> Subject:
    budget = budget or Budget()
    if budget.max_component_len is None:  # decision (d): no implicit limit
        raise CaptureRefused("admission_limit_missing", "max_component_len")
    reader = VerifiedObjectReader(repo, len(commit), verify=verify, address_space=budget.child_address_space_bytes,
                                  abort_delay_s=_abort_delay_s, deadline_s=budget.transport_deadline_s)
    GIT_INVOCATIONS[0] += 2  # rev-parse --show-object-format + cat-file --batch
    ok = False
    try:
        commit_body = reader.get(commit, "commit", budget)
        first = commit_body.split(b"\n", 1)[0].split(b" ")
        if len(first) != 2 or first[0] != b"tree" or len(first[1]) != len(commit):
            raise CaptureRefused("commit_unparseable", commit)
        root_tree = first[1].decode("ascii")
        identity = AcquisitionIdentity(reader.algorithm, commit, root_tree, budget.max_component_len)
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
        import types
        record = types.MappingProxyType({p: (k, o) for p, (k, o, _v) in nodes.items()})
        ok = True
        return Subject(reader.algorithm, commit, root_tree, nodes, record, identity, budget,
                       reader.objects_read, reader.bytes_read)
    finally:
        reader.close() if ok else reader.abort()


def _git_blob_oid(algorithm: str, payload: bytes) -> str:
    h = hashlib.new(algorithm)
    h.update(b"blob %d\0" % len(payload))
    h.update(payload)
    return h.hexdigest()


def validate_sealed_subject(fd: int, subject: Subject) -> None:
    """Post-seal revalidation of the FINAL object (decision (b)): re-read the sealed bytes and check
    identity (algorithm implied by C, C, root tree), that the node set/kinds/oids equal the acquisition
    record, that every leaf payload re-hashes to its oid, and structural form (parents are trees).
    Tree bodies are not embedded, so tree oids are bound through the record, not re-hashed here."""
    data = os.pread(fd, os.fstat(fd).st_size, 0)
    algorithm, commit, root_tree, nodes = parse(data)
    ident = subject.identity  # the IMMUTABLE acquisition identity, never the mutable Subject header fields (R4-2)
    if (algorithm, commit, root_tree) != (ident.algorithm, ident.commit, ident.root_tree) or \
            algorithm != ALGORITHM_BY_HEXLEN.get(len(ident.commit)):
        raise CaptureRefused("sealed_identity_mismatch")
    if {p: (k, o) for p, (k, o, _v) in nodes.items()} != dict(subject.record):
        raise CaptureRefused("sealed_record_mismatch")
    for path, (kind, oid, payload) in nodes.items():
        if "/" in path.decode("utf-8", "surrogateescape"):
            parent = path.rsplit(b"/", 1)[0]
            if nodes.get(parent, ("?",))[0] != "tree":
                raise CaptureRefused("sealed_structure_invalid", repr(parent))
        if kind == "tree":
            if payload:
                raise CaptureRefused("sealed_structure_invalid", "tree_payload")
        elif _git_blob_oid(algorithm, payload) != oid:
            raise CaptureRefused("sealed_binding_mismatch", repr(path))


def commit_subject(subject: Subject, *, revalidate: bool = True, _before_serialize=None, _after_write=None):
    """Serialize -> seal -> post-seal revalidation -> capability. Returns (fd, container_sha256).
    `revalidate=False` is the ablation mutant; `_before_serialize`/`_after_write` inject the
    authenticate-A-consume-B substitution and the pre-seal writer."""
    nodes = subject.nodes
    if _before_serialize is not None:
        nodes = _before_serialize(dict(nodes))
    data = serialize(subject.algorithm, subject.commit, subject.root_tree, nodes)
    fd = seal_committed(data, name="ar-301-s0-sg", after_write=_after_write)
    try:
        if revalidate:
            validate_sealed_subject(fd, subject)
        return fd, hashlib.sha256(data).hexdigest()
    except BaseException:
        os.close(fd)
        raise


def lock_bytes(nodes: dict, path: bytes = b"requirements-agent-review.lock") -> bytes:
    """The lock that authorizes S_D must be a REGULAR file node of S_G (not a symlink's target text)."""
    node = nodes.get(path)
    if node is None or node[0] != "regular":
        raise CaptureRefused("dependency_lock_not_regular_file", repr(node[0] if node else None))
    return node[2]


def open_fds() -> list[int]:
    return sorted(int(n) for n in os.listdir("/proc/self/fd"))
