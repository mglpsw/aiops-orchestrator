"""A data-only process boundary for a future SGAQ authority worker.

`#331`, SGAQ-T1. One proposition, and nothing else:

    parent Python runtime state cannot change the behaviour of a fresh,
    host-owned, bytes-in/bytes-out worker.

There is no SGAQ contract here, no semantic identity, no canonicalisation
authority, no observation, no admission, no Git, no materialisation. Those are
later slices. This one establishes where the trust boundary is, because four
previous attempts established where it is not.

## Why the boundary moved out of the interpreter

`#334`, `#335`, `#336` and `#337` each tried to establish authority with an
in-process seal, and each was defeated through state the judged party still
owned: a mutable mapping, a `str` subclass owning `__eq__`, a public
`FIELD_SEMANTICS` read by both the sealer and the re-sealer, a metaclass owning
`dict` equality. The generalisation those four support is that a trust
discipline cannot read its own rule from mutable state controlled by the
environment it is judging. Inside one interpreter, the judge and the judged
share a mutable substrate: module globals, `sys.modules`, class attributes, and
the `__eq__`/`__hash__` of every value compared. No object seal survives that.

## This is not a new trust model

`trusted_check_authority_v2` already draws the line, on merged master:
`SUBJECT_CODE` -- code the subject controls -- can never back `TRUSTED`, however
strong the containment around it, because isolating subject-controlled code does
not make its output authoritative. Only `DATA_ONLY_HOST_TOOL`, a host-owned tool
consuming the subject as DATA, is eligible. Read through that vocabulary, the
four stopped slices were attempts to make a `SUBJECT_CODE`-equivalent surface --
a caller-supplied Python object graph, arriving with its own comparison
operators and mutable class state -- behave like a host tool.

The worker here is a `DATA_ONLY_HOST_TOOL`. Caller material crosses inward as
bytes and never as objects, so the classification is structural rather than
declared.

## The two things the host must not confuse

`HostObservedExecutionV2` is what the host determines by itself: that a process
was spawned, from which interpreter, executing which exact bytes, how it exited,
whether it timed out, whether the result framing is intact. None of it comes
from what the child says about itself.

`WorkerSemanticOutputV2` is the result content. It may be read only after the
host-observed facts hold. The distinction matters in both directions: a worker
announcing "I am trusted" is worth nothing, but an authenticated host-owned
tool's output IS usable evidence -- which is exactly why `isolated_executor_v2`
refuses child output for `SUBJECT_CODE` and why that refusal does not transfer
here unexamined.

## Worker source continuity, and the mistake it avoids

The program is written to the child's stdin. The host digests the exact bytes it
sends, and those same bytes are what the interpreter executes -- there is no
window between authentication and execution.

The alternative, hashing a path and then handing that path to the interpreter,
is the `verify -> reopen` shape this issue has spent four rounds learning to
refuse for Git object storage. It would have been the same defect wearing
different clothes.

## Interpreter flags, corrected by measurement

`-I -S -B`, and the reasoning is empirical rather than assumed. `-I` ignores
every `PYTHON*` variable, so `PYTHONHASHSEED` and `PYTHONDONTWRITEBYTECODE`
cannot be relied on as controls under it -- measured: `PYTHONHASHSEED=0` under
`-I` still produced four distinct hashes across four execs. `-B` is what
actually refuses bytecode; `-S` is what stops `sitecustomize`.

The consequence is a stronger property than a pinned seed. Determinism here does
not depend on hash randomisation at all, and the corpus proves it by running the
same request through many fresh interpreters whose seeds genuinely differ.

## What is deliberately not claimed

An executable digest is not a full runtime identity. The interpreter build, the
standard library, and the shared libraries beneath it all participate in
behaviour and are not bound here. `WorkerToolchainBindingV2.full_runtime_closure_claimed`
is `False` and says so in the type rather than in a comment.

Containment is also not claimed. This module does not create PID namespaces or
supervise process lifetime; `trusted_check_namespace_kernel_v2` owns that, and a
later slice needing it must compose that module rather than fork it. What is
here is a launch, identity and channel boundary, with a host-observed timeout.
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import platform
import subprocess
import sys
import tempfile

__all__ = [
    "DataOnlyBoundaryError",
    "HostObservedExecutionV2",
    "PROTOCOL_VERSION",
    "WORKER_SOURCE_BYTES",
    "WorkerSemanticOutputV2",
    "WorkerToolchainBindingV2",
    "accept_semantic_output_v2",
    "describe_toolchain_binding_v2",
    "run_data_only_worker_v2",
]


class DataOnlyBoundaryError(AssertionError):
    """The host could not stand behind what came back across the boundary."""


PROTOCOL_VERSION = "sgaq.data-only-boundary.v1"

#: The frame the worker must emit, exactly once. Length-prefixed so truncation
#: and duplication are both detectable rather than merely unlikely.
_FRAME_PREFIX = b"SGAQ1 "

#: The worker. Stdlib only, no `app.*`, and deliberately not the repository's
#: canonical-JSON primitive: T1 proves the boundary and T2 owns what crosses it.
#:
#: It is a `bytes` constant rather than a file so the host can digest exactly
#: what it sends. Reading it from disk would reintroduce the verify-then-reopen
#: window this slice exists to remove.
#:
#: The body sorts its members on purpose. A worker that emitted them in set
#: order would be correct on any single run and non-deterministic across fresh
#: interpreters, which is the defect control G is built to find.
WORKER_SOURCE_BYTES = b'''import hashlib as _hashlib
import json as _json
import os as _os
import sys as _sys


def _read_request():
    descriptor = int(_os.environ["SGAQ_REQUEST_FD"])
    chunks = []
    while True:
        chunk = _os.read(descriptor, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _result():
    request = _read_request()
    lines = [line for line in request.decode("utf-8", "replace").split("\\n") if line]
    return {
        "protocol": "sgaq.data-only-boundary.v1",
        "request_digest": _hashlib.sha256(request).hexdigest(),
        "request_length": len(request),
        "members": sorted(set(lines)),
    }


def _emit(payload):
    body = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _sys.stdout.buffer.write(b"SGAQ1 " + str(len(body)).encode("ascii") + b"\\n" + body)
    _sys.stdout.buffer.flush()


_emit(_result())
'''


# --------------------------------------------------------------------------
# what the host determines by itself
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorkerToolchainBindingV2:
    """Identity the host establishes BEFORE the worker runs.

    Per CAEM ADR 0012: no observation made by an already-hooked process can
    certify the trustworthiness of its own startup. Every field here is
    computed by the host from its own state, never read back from the child.
    """

    interpreter_path: str
    interpreter_executable_digest: str
    python_version: str
    implementation: str
    platform_capability_profile: str
    worker_source_digest: str
    protocol_version: str
    #: Stated in the type, not in prose. The interpreter build, the standard
    #: library and the shared libraries beneath them are not bound here.
    full_runtime_closure_claimed: bool = False


@dataclasses.dataclass(frozen=True)
class HostObservedExecutionV2:
    """Facts about the run that the host owns. None are child self-assertions."""

    spawned: bool
    exit_status: int | None
    timed_out: bool
    protocol_framing_intact: bool
    binding: WorkerToolchainBindingV2
    raw_stdout: bytes = b""
    raw_stderr: bytes = b""


@dataclasses.dataclass(frozen=True)
class WorkerSemanticOutputV2:
    """Result content from an authenticated host-owned tool.

    Usable because the host already established which bytes ran, not because
    the worker vouches for itself.
    """

    request_digest: str
    request_length: int
    members: tuple[str, ...]
    raw_frame: bytes


def describe_toolchain_binding_v2(worker_source: bytes | None = None) -> WorkerToolchainBindingV2:
    """Bind the interpreter and the exact worker bytes, before any launch."""
    source = WORKER_SOURCE_BYTES if worker_source is None else worker_source
    interpreter = os.path.realpath(sys.executable)
    with open(interpreter, "rb") as handle:
        interpreter_digest = hashlib.sha256(handle.read()).hexdigest()
    return WorkerToolchainBindingV2(
        interpreter_path=interpreter,
        interpreter_executable_digest=interpreter_digest,
        python_version=platform.python_version(),
        implementation=platform.python_implementation(),
        platform_capability_profile=f"{platform.system()}/{platform.machine()}",
        worker_source_digest=hashlib.sha256(source).hexdigest(),
        protocol_version=PROTOCOL_VERSION,
    )


# --------------------------------------------------------------------------
# the boundary
# --------------------------------------------------------------------------

#: A positive allowlist. Nothing is inherited: the child's environment is
#: constructed, not filtered, so a variable nobody thought about cannot arrive
#: by default. `PYTHON*` entries are deliberately absent -- `-I` ignores them,
#: and listing them would assert a control that does not exist.
def _child_environment(request_fd: int, home: str) -> dict[str, str]:
    return {
        "SGAQ_REQUEST_FD": str(request_fd),
        "PATH": "/usr/bin:/bin",
        "HOME": home,
        "TMPDIR": home,
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "XDG_CONFIG_HOME": os.path.join(home, "config"),
        "XDG_CACHE_HOME": os.path.join(home, "cache"),
        "XDG_DATA_HOME": os.path.join(home, "data"),
    }


def _parse_frame(raw: bytes) -> WorkerSemanticOutputV2 | None:
    """Exactly one well-formed frame, or nothing.

    Truncation, trailing bytes and a second frame are all refused. Taking the
    first of two would be resolving ambiguity by preference.
    """
    if not raw.startswith(_FRAME_PREFIX):
        return None
    newline = raw.find(b"\n")
    if newline == -1:
        return None
    try:
        declared = int(raw[len(_FRAME_PREFIX):newline].decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return None
    body = raw[newline + 1:]
    if declared != len(body):
        return None
    try:
        import json

        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_VERSION:
        return None
    members = payload.get("members")
    digest = payload.get("request_digest")
    length = payload.get("request_length")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        return None
    if type(digest) is not str or type(length) is not int:
        return None
    return WorkerSemanticOutputV2(
        request_digest=digest,
        request_length=length,
        members=tuple(members),
        raw_frame=raw,
    )


def run_data_only_worker_v2(
    request: bytes,
    worker_source: bytes | None = None,
    timeout: float = 30.0,
) -> tuple[HostObservedExecutionV2, WorkerSemanticOutputV2 | None]:
    """Run the worker in a new interpreter image and report what the host saw.

    Only bytes and primitives cross inward. There is no parameter through which
    a caller object, callable, class or mapping could enter, which is what makes
    the `DATA_ONLY_HOST_TOOL` classification structural rather than declared.
    """
    if type(request) is not bytes:
        raise DataOnlyBoundaryError(
            f"request must be exactly bytes, got {type(request).__name__}"
        )
    source = WORKER_SOURCE_BYTES if worker_source is None else worker_source
    if type(source) is not bytes:
        raise DataOnlyBoundaryError("worker source must be exactly bytes")

    binding = describe_toolchain_binding_v2(source)

    with tempfile.TemporaryDirectory(prefix="sgaq-t1-") as workspace:
        # A fresh, empty, host-owned directory. Never the caller's cwd: a
        # shadowing module there would otherwise be one `sys.path` entry away.
        home = os.path.join(workspace, "home")
        cwd = os.path.join(workspace, "cwd")
        os.mkdir(home)
        os.mkdir(cwd)
        # The request is staged in the host-owned workspace and handed over as a
        # read-only descriptor, not a pipe. A pipe deadlocks the HOST: it has to
        # write before the child exists, so any request larger than the pipe
        # buffer never completes. Measured, on a 100 kB request -- the hostile
        # -input control found it.
        request_path = os.path.join(workspace, "request.bin")
        with open(request_path, "wb") as handle:
            handle.write(request)
        read_fd = os.open(request_path, os.O_RDONLY)
        os.set_inheritable(read_fd, True)
        try:
            completed = subprocess.run(  # noqa: S603 -- host-resolved absolute interpreter
                [binding.interpreter_path, "-I", "-S", "-B", "-"],
                input=source,
                capture_output=True,
                cwd=cwd,
                env=_child_environment(read_fd, home),
                pass_fds=(read_fd,),
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            return (
                HostObservedExecutionV2(
                    spawned=True, exit_status=None, timed_out=True,
                    protocol_framing_intact=False, binding=binding,
                    raw_stdout=expired.stdout or b"", raw_stderr=expired.stderr or b"",
                ),
                None,
            )
        finally:
            os.close(read_fd)

    output = _parse_frame(completed.stdout) if completed.returncode == 0 else None
    observed = HostObservedExecutionV2(
        spawned=True,
        exit_status=completed.returncode,
        timed_out=False,
        protocol_framing_intact=output is not None,
        binding=binding,
        raw_stdout=completed.stdout,
        raw_stderr=completed.stderr,
    )
    return observed, output


def accept_semantic_output_v2(
    observed: HostObservedExecutionV2, output: WorkerSemanticOutputV2 | None
) -> WorkerSemanticOutputV2:
    """Read the result only after the host-observed facts hold.

    The ordering is the contract. A worker's bytes become usable evidence
    because the host already proved which worker ran, never because the worker
    said so.
    """
    if not observed.spawned or observed.timed_out or observed.exit_status != 0:
        raise DataOnlyBoundaryError(
            f"host-observed execution did not succeed: spawned={observed.spawned} "
            f"timed_out={observed.timed_out} exit_status={observed.exit_status}"
        )
    if not observed.protocol_framing_intact or output is None:
        raise DataOnlyBoundaryError("host-observed protocol framing is not intact")
    return output
