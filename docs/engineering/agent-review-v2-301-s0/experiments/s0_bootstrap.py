"""EXPERIMENTAL ONLY -- #301 S0 consumer bootstrap. Not production code.

Single owner, inside this experiment, of: the container format (serialize/parse), the
sealing commitment, and consumer-side validation of a received descriptor. The producer
(s0_capture.py) imports these; the launcher (s0_launch.py) passes this file's SOURCE to
`python -I -S -c <source> <spec-json>`, so in the child it runs before any engine import and
uses only builtin/frozen modules and the interpreter's own stdlib (declared TCB).

Child protocol (spec JSON in argv[1]):
  s:        {"fd", "sha256", "commit"}   engine subject S (Git closure)
  d:        {"fd", "sha256"} | null      dependency set D (lock-authenticated wheels)
  x:        {"fd", "sha256"}             driver the experiment runs after the bootstrap
  roots:    top-level packages importable from S (explicit engine import roots)
  inputs:   opaque JSON handed to the driver's run()
  result_fd: descriptor of the result channel (socketpair end)
  watch:    path prefixes whose opens are reported (checkout / venv / M)
  dumpable: false -> PR_SET_DUMPABLE(0) before validation
"""
import fcntl
import hashlib
import os
import stat
import struct
import sys

MAGIC = b"AR-301-S0-EXP-1\n"
KIND_CODE = {"tree": b"T", "regular": b"F", "executable": b"X", "symlink": b"L"}
CODE_KIND = {v: k for k, v in KIND_CODE.items()}
REQUIRED_SEALS = fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE | fcntl.F_SEAL_SEAL


class CaptureRefused(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(reason + (": " + detail if detail else ""))
        self.reason = reason
        self.detail = detail


def serialize(algorithm, commit, root_tree, nodes):
    out = [MAGIC, struct.pack(">B", len(algorithm)), algorithm.encode(), struct.pack(">B", len(commit)),
           commit.encode(), struct.pack(">B", len(root_tree)), root_tree.encode(), struct.pack(">Q", len(nodes))]
    for path in sorted(nodes):  # canonical order: raw path bytes (memcmp)
        kind, oid, payload = nodes[path]
        out += [KIND_CODE[kind], struct.pack(">I", len(path)), path, struct.pack(">B", len(oid)), oid.encode(),
                struct.pack(">Q", len(payload)), payload]
    return b"".join(out)


def parse(data):
    """Inverse of serialize over ONE buffer; refuses truncation, trailing bytes, unsorted/duplicate index."""
    if not data.startswith(MAGIC):
        raise CaptureRefused("container_magic")
    pos = [len(MAGIC)]

    def take(n):
        i = pos[0]
        if i + n > len(data):
            raise CaptureRefused("container_truncated")
        pos[0] = i + n
        return data[i:i + n]

    algorithm = take(take(1)[0]).decode()
    commit = take(take(1)[0]).decode()
    root_tree = take(take(1)[0]).decode()
    (count,) = struct.unpack(">Q", take(8))
    nodes = {}
    last = None
    for _ in range(count):
        kind = CODE_KIND.get(take(1))
        if kind is None:
            raise CaptureRefused("container_kind")
        (plen,) = struct.unpack(">I", take(4))
        path = take(plen)
        if last is not None and path <= last:
            raise CaptureRefused("container_order")
        last = path
        oid = take(take(1)[0]).decode()
        (n,) = struct.unpack(">Q", take(8))
        nodes[path] = (kind, oid, take(n))
    if pos[0] != len(data):
        raise CaptureRefused("container_trailing")
    return algorithm, commit, root_tree, nodes


def seal_committed(data, name="ar-301-s0", verify_after_seal=True, after_write=None):
    """Sealed memfd holding exactly `data`, or raise. The fd has one owner until returned.

    Commitment point: seals read back AND the sealed content re-hashes to sha256(data), so a
    writer that reached the fd before sealing is detected. `after_write` is a pre-seal test
    hook; `verify_after_seal=False` is the ablation mutant.
    """
    expected = hashlib.sha256(data).digest()
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        if after_write is not None:
            after_write(fd)
        try:
            fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        except OSError as exc:
            raise CaptureRefused("seal_failed", "errno=%d" % exc.errno)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
            raise CaptureRefused("seal_incomplete")
        if verify_after_seal:
            size = os.fstat(fd).st_size
            if size != len(data) or hashlib.sha256(os.pread(fd, size, 0)).digest() != expected:
                raise CaptureRefused("sealed_content_mismatch")
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_sealed(fd, expected_sha256):
    """Validate a RECEIVED descriptor; return the one buffer that was hashed (never re-read)."""
    if expected_sha256 is None:
        raise CaptureRefused("expected_identity_absent")
    if fd is None:
        raise CaptureRefused("descriptor_absent")
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise CaptureRefused("descriptor_invalid", "errno=%d" % exc.errno)
    if not stat.S_ISREG(st.st_mode):
        raise CaptureRefused("descriptor_not_regular")
    try:
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
    except OSError as exc:  # EINVAL: not a sealable (shmem/memfd) file
        raise CaptureRefused("descriptor_not_sealable", "errno=%d" % exc.errno)
    if seals & REQUIRED_SEALS != REQUIRED_SEALS:
        raise CaptureRefused("descriptor_not_sealed", hex(seals))
    data = os.pread(fd, st.st_size, 0)
    if len(data) != st.st_size or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise CaptureRefused("descriptor_digest_mismatch")
    return data


def _main(spec):
    import importlib.abc
    import importlib.machinery
    import importlib.util
    import json

    watch = tuple(spec.get("watch", ()))
    observed = {"opens_watched": [], "opens_other": [], "dlopen": [], "subprocess": []}

    def audit(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            p = os.fsdecode(args[0])
            if watch and p.startswith(watch):
                observed["opens_watched"].append(p)
            elif not p.startswith(("/usr/", "/proc/self/fd/", "/dev/")):
                observed["opens_other"].append(p)
        elif event == "ctypes.dlopen":
            observed["dlopen"].append(str(args[0]))
        elif event in ("subprocess.Popen", "os.exec", "os.posix_spawn", "os.fork"):
            observed["subprocess"].append(event)

    sys.addaudithook(audit)
    if spec.get("dumpable") is False:
        import ctypes
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4

    result_fd = spec["result_fd"]

    def reply(obj):
        blob = json.dumps(obj, sort_keys=True, default=str).encode()
        view = memoryview(struct.pack(">Q", len(blob)) + blob)
        while view:
            view = view[os.write(result_fd, view):]

    try:
        s_raw = open_sealed(spec["s"].get("fd"), spec["s"].get("sha256"))
        s_alg, s_commit, _s_tree, S = parse(s_raw)
        if s_commit != spec["s"].get("commit"):
            raise CaptureRefused("subject_identity_mismatch", s_commit)
        D = {}
        if spec.get("d"):
            d_raw = open_sealed(spec["d"].get("fd"), spec["d"].get("sha256"))
            _alg, lock_digest, _wheels, D = parse(d_raw)
            lock = S.get(spec.get("lock_path", "requirements-agent-review.lock").encode())
            if lock is None or hashlib.sha256(lock[2]).hexdigest() != lock_digest:
                raise CaptureRefused("dependency_lock_binding_mismatch")
        x_raw = open_sealed(spec["x"].get("fd"), spec["x"].get("sha256"))
    except CaptureRefused as exc:
        reply({"status": "refused", "reason": exc.reason, "detail": exc.detail})
        return 3

    ext_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    native_fds = []

    class ClosedFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        """Filesystem-import precedence over ONE closed node map: package dir with __init__.py,
        then module source, then extension (D only), then a bare tree node -> PEP 420 namespace.
        Symlink nodes are data in S, never an import path (explicit policy)."""

        def __init__(self, label, nodes, roots=None):
            self.label, self.nodes, self.roots = label, nodes, roots

        def find_spec(self, name, path=None, target=None):
            if self.roots is not None and name.split(".")[0] not in self.roots:
                return None
            rel = name.replace(".", "/").encode()
            for cand, pkg in ((rel + b"/__init__.py", True), (rel + b".py", False)):
                node = self.nodes.get(cand)
                if node is not None:
                    if node[0] == "symlink":
                        raise ImportError("%s: symlink node is not importable (policy)" % name)
                    if node[0] in ("regular", "executable"):
                        return importlib.util.spec_from_loader(
                            name, self, origin="s0:%s:%s" % (self.label, cand.decode()), is_package=pkg)
            for suffix in ext_suffixes if self.label == "D" else ():  # native code only from D
                node = self.nodes.get(rel + suffix.encode())
                if node is not None and node[0] in ("regular", "executable"):
                    fd = seal_committed(node[2], name="ar-301-s0-native")
                    native_fds.append(fd)
                    where = "/proc/self/fd/%d" % fd
                    return importlib.machinery.ModuleSpec(
                        name, importlib.machinery.ExtensionFileLoader(name, where), origin=where)
            node = self.nodes.get(rel)
            if node is not None and node[0] == "tree":
                spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
                spec.submodule_search_locations = []
                return spec
            if node is not None and node[0] == "symlink":
                raise ImportError("%s: symlink node is not importable (policy)" % name)
            return None

        def create_module(self, spec):
            return None

        def _rel(self, spec):
            return spec.origin.split(":", 2)[2].encode()

        def exec_module(self, module):
            source = self.nodes[self._rel(module.__spec__)][2]
            exec(compile(source, module.__spec__.origin, "exec", dont_inherit=True), module.__dict__)

        def get_source(self, fullname):
            spec = self.find_spec(fullname)
            return None if spec is None else self.nodes[self._rel(spec)][2].decode("utf-8")

    path_finder_index = next(i for i, f in enumerate(sys.meta_path) if f is importlib.machinery.PathFinder)
    sys.meta_path.insert(path_finder_index, ClosedFinder("S", S, set(spec.get("roots", ["app"]))))
    if D:
        sys.meta_path.append(ClosedFinder("D", D))  # after PathFinder: stdlib precedes site-packages
    sys.path_importer_cache.clear()

    driver = type(sys)("__s0_driver__")
    try:
        exec(compile(x_raw, "s0:driver", "exec", dont_inherit=True), driver.__dict__)
        result = driver.run(spec.get("inputs", {}))
        status = "ok"
    except BaseException as exc:  # reported, never converted into a valid result
        result, status = {"error": "%s: %s" % (type(exc).__name__, exc)}, "driver_failed"

    import importlib.metadata as md
    by_origin = {}
    for mname, mod in sorted(sys.modules.items()):
        mspec = getattr(mod, "__spec__", None)
        origin = getattr(mspec, "origin", None) or ""
        loader = type(getattr(mspec, "loader", None)).__name__
        if origin.startswith("s0:S:"):
            cls = "S_source"
        elif origin.startswith("s0:D:"):
            cls = "D_source"
        elif origin.startswith("/proc/self/fd/"):
            cls = "D_native_memfd"
        elif origin in ("built-in", "frozen") or loader in ("BuiltinImporter", "FrozenImporter"):
            cls = "builtin_or_frozen"
        elif origin.startswith(sys.base_prefix + "/lib/"):
            cls = "stdlib_extension" if origin.endswith(ext_suffixes) else "stdlib_source"
        elif mspec is not None and mspec.origin is None and mspec.submodule_search_locations is not None:
            cls = "namespace"
        else:
            cls = "OTHER:" + origin
        by_origin.setdefault(cls, []).append(mname)
    reply({
        "status": status,
        "result": result,
        "modules_by_origin": by_origin,
        "distributions_visible": sorted(d.metadata["Name"] or "?" for d in md.distributions()),
        "observed": observed,
        "native_fds": len(native_fds),
        "flags": {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site,
                  "ignore_environment": sys.flags.ignore_environment, "safe_path": sys.flags.safe_path},
        "executable": sys.executable,
        "sys_path": sys.path,
    })
    return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1].startswith("{"):
    import json as _json
    raise SystemExit(_main(_json.loads(sys.argv[1])))
