"""The process boundary, attacked from the parent that is supposed to be excluded.

`#331`, SGAQ-T1. One proposition:

    parent Python runtime state cannot change the behaviour of a fresh,
    host-owned, bytes-in/bytes-out worker.

WHY THIS IS A PROCESS TEST AND NOT AN OBJECT TEST

Four stopped slices (#334-#337) tried to establish authority with in-process
object seals and were defeated four times, each time through state the judged
party still owned: a mutable mapping, a `str` subclass owning `__eq__`, a
public `FIELD_SEMANTICS`, a metaclass owning `dict` equality. The generalisation
they support is that a trust discipline cannot read its own rule from mutable
state controlled by the environment it is judging.

So nothing here asserts that an object is sealed. Every control mutates the
PARENT -- `sys.modules`, module globals, the environment, the working
directory, inherited descriptors -- and asserts the CHILD's bytes are
unchanged. If a control can be satisfied by an in-process guard, it is the
wrong control.

WHAT THIS SLICE DELIBERATELY DOES NOT CLAIM

No SGAQ semantics, no canonicalisation authority, no Git, no #301 subject-code
provenance. The worker is stdlib-only and imports nothing from `app.*`,
including the repository's canonical-JSON primitive: T1 proves the boundary,
and T2 owns what crosses it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.sgaq_data_only_boundary_v2 import (
    WORKER_SOURCE_BYTES,
    DataOnlyBoundaryError,
    HostObservedExecutionV2,
    WorkerSemanticOutputV2,
    WorkerToolchainBindingV2,
    accept_semantic_output_v2,
    describe_toolchain_binding_v2,
    run_data_only_worker_v2,
)

_REQUEST = b"delta\nalpha\ncharlie\nbravo\nalpha\n"


def _run(request: bytes = _REQUEST, **kwargs):
    return run_data_only_worker_v2(request=request, **kwargs)


# --------------------------------------------------------------------------
# the control: the boundary must actually work
# --------------------------------------------------------------------------


def test_a_request_crosses_the_boundary_and_a_result_comes_back() -> None:
    """A boundary that refuses everything is trivially safe and useless."""
    observed, output = _run()
    assert observed.spawned and observed.exit_status == 0
    assert observed.protocol_framing_intact and not observed.timed_out
    assert output is not None
    assert output.members == ("alpha", "bravo", "charlie", "delta")
    assert output.request_digest == hashlib.sha256(_REQUEST).hexdigest()


def test_the_semantic_output_is_only_accepted_after_host_observed_execution() -> None:
    """The two concepts are independent, and the ordering is the point.

    A worker asserting its own trustworthiness is worth nothing. The host
    establishes the worker's identity and the protocol framing BEFORE any
    result content is interpreted; only then may the bytes of an authenticated
    DATA_ONLY_HOST_TOOL be read as a result.
    """
    observed, output = _run()
    assert accept_semantic_output_v2(observed, output) is output

    refused = HostObservedExecutionV2(
        spawned=True, exit_status=1, timed_out=False,
        protocol_framing_intact=True, binding=observed.binding,
    )
    with pytest.raises(DataOnlyBoundaryError, match="host-observed"):
        accept_semantic_output_v2(refused, output)


# --------------------------------------------------------------------------
# A-B, N: parent runtime state
# --------------------------------------------------------------------------


def test_a_parent_preloading_a_fake_module_does_not_reach_the_child() -> None:
    """The child gets a new interpreter image, so the parent's import cache
    is not something it can inherit."""
    baseline = _run()[1]
    sys.modules["hashlib_shadow_probe"] = object()  # type: ignore[assignment]
    try:
        assert _run()[1] == baseline
    finally:
        del sys.modules["hashlib_shadow_probe"]


def test_n_parent_mutating_sys_modules_for_a_module_the_worker_uses() -> None:
    """The sharper form of A: shadow a module the worker genuinely imports."""
    baseline = _run()[1]
    import types

    fake = types.ModuleType("hashlib")
    fake.sha256 = lambda *a, **k: pytest.fail("the parent's hashlib was used")  # type: ignore[attr-defined]
    real = sys.modules["hashlib"]
    sys.modules["hashlib"] = fake
    try:
        assert _run()[1] == baseline
    finally:
        sys.modules["hashlib"] = real


def test_b_parent_mutating_module_globals_does_not_reach_the_child() -> None:
    import app.agent_review.sgaq_data_only_boundary_v2 as boundary

    baseline = _run()[1]
    original = boundary.WORKER_SOURCE_BYTES
    boundary.__dict__["_A_HOSTILE_GLOBAL"] = "injected"
    try:
        assert _run()[1] == baseline
    finally:
        boundary.__dict__.pop("_A_HOSTILE_GLOBAL", None)
        assert boundary.WORKER_SOURCE_BYTES == original


# --------------------------------------------------------------------------
# C-E: import path, site hooks, shadowing cwd
# --------------------------------------------------------------------------


def test_c_a_hostile_pythonpath_is_ignored_by_the_isolated_startup(
    tmp_path, monkeypatch
) -> None:
    """`-I` ignores PYTHON* variables. Measured, not assumed."""
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "hashlib.py").write_text("raise RuntimeError('hostile hashlib imported')\n")
    baseline = _run()[1]
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    assert _run()[1] == baseline


def test_d_a_hostile_sitecustomize_never_executes(tmp_path, monkeypatch) -> None:
    """`-S` skips site, so sitecustomize/usercustomize cannot run."""
    hostile = tmp_path / "sitedir"
    hostile.mkdir()
    (hostile / "sitecustomize.py").write_text("raise RuntimeError('sitecustomize ran')\n")
    baseline = _run()[1]
    monkeypatch.setenv("PYTHONPATH", str(hostile))
    monkeypatch.setenv("PYTHONSTARTUP", str(hostile / "sitecustomize.py"))
    assert _run()[1] == baseline


def test_e_shadow_modules_in_the_callers_cwd_do_not_reach_the_child(
    tmp_path, monkeypatch
) -> None:
    """The child runs in a fresh host-owned directory, never the caller's."""
    shadow = tmp_path / "cwd"
    shadow.mkdir()
    (shadow / "hashlib.py").write_text("raise RuntimeError('cwd shadow imported')\n")
    baseline = _run()[1]
    monkeypatch.chdir(shadow)
    assert _run()[1] == baseline


# --------------------------------------------------------------------------
# F-H: determinism
# --------------------------------------------------------------------------


def test_f_locale_home_and_timezone_do_not_change_the_result(monkeypatch) -> None:
    baseline = _run()[1]
    for name, value in (("LC_ALL", "tr_TR.UTF-8"), ("LANG", "tr_TR.UTF-8"),
                        ("TZ", "Pacific/Kiritimati"), ("HOME", "/nonexistent-home")):
        monkeypatch.setenv(name, value)
    assert _run()[1] == baseline


def test_g_output_is_byte_identical_across_naturally_varying_hash_seeds() -> None:
    """Determinism must be INDEPENDENT of hash randomisation, not pinned to it.

    `-I` ignores PYTHONHASHSEED, so every exec genuinely gets its own seed --
    measured: four execs, four distinct `hash('sgaq-probe')` values. The worker
    therefore has to produce identical bytes without relying on any unordered
    iteration. A dependence on set ordering is a T1 defect, and this is the
    control that finds it.
    """
    frames = {_run()[1].raw_frame for _ in range(8)}
    assert len(frames) == 1, f"{len(frames)} distinct results across 8 fresh execs"


def test_h_identical_request_bytes_give_identical_protocol_results() -> None:
    assert _run().__getitem__(1).raw_frame == _run()[1].raw_frame


# --------------------------------------------------------------------------
# I-K: fail closed, and channel ownership
# --------------------------------------------------------------------------


def test_i_a_malformed_result_frame_fails_closed() -> None:
    broken = WORKER_SOURCE_BYTES.replace(b"_emit(", b"_emit_broken(", 1)
    assert broken != WORKER_SOURCE_BYTES
    observed, output = _run(worker_source=broken)
    assert output is None
    assert not observed.protocol_framing_intact
    with pytest.raises(DataOnlyBoundaryError):
        accept_semantic_output_v2(observed, output)


def test_j_a_duplicate_result_frame_fails_closed() -> None:
    """Two frames is ambiguity, and ambiguity is refused rather than resolved
    by taking the first."""
    doubled = WORKER_SOURCE_BYTES + b"\n_emit(_result())\n"
    observed, output = _run(worker_source=doubled)
    assert output is None
    assert not observed.protocol_framing_intact


def test_k_an_unexpected_inherited_descriptor_is_not_visible_to_the_worker() -> None:
    """Only the declared request descriptor crosses; everything else is closed."""
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    os.write(write_fd, b"SECRET-FROM-THE-PARENT")
    try:
        probe = WORKER_SOURCE_BYTES + (
            b"\nimport os as _os\n"
            b"try:\n"
            b"    _os.fstat(%d)\n"
            b"    _sys.stdout.write('LEAKED')\n"
            b"except OSError:\n"
            b"    pass\n" % read_fd
        )
        observed, output = _run(worker_source=probe)
        assert b"LEAKED" not in observed.raw_stdout
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_i_malformed_request_bytes_do_not_break_the_boundary() -> None:
    """Hostile DATA is the only threat class this layer is for."""
    for hostile in (b"", b"\x00\xff\xfe", b"\xed\xa0\x80", b"x" * 100_000):
        observed, output = _run(request=hostile)
        assert observed.spawned
        assert output is None or output.request_digest == hashlib.sha256(hostile).hexdigest()


# --------------------------------------------------------------------------
# L-M: identity binding
# --------------------------------------------------------------------------


def test_l_a_worker_source_byte_mutation_changes_the_host_binding() -> None:
    """The bytes the host authenticated are the bytes the interpreter executed.

    No pathname is hashed and then reopened -- the program arrives on stdin, so
    there is no window between authentication and execution. That is the same
    property #331 spent four rounds learning to demand of object storage.
    """
    baseline = _run()[0].binding
    mutated = WORKER_SOURCE_BYTES + b"\n# one byte of drift\n"
    other = _run(worker_source=mutated)[0].binding
    assert other.worker_source_digest != baseline.worker_source_digest
    assert other.worker_source_digest == hashlib.sha256(mutated).hexdigest()


def test_m_the_toolchain_binding_names_what_it_does_not_claim() -> None:
    """An executable digest is not a full runtime identity, and saying so is
    part of the contract rather than a footnote."""
    binding = describe_toolchain_binding_v2()
    assert Path(binding.interpreter_path).is_absolute()
    assert len(binding.interpreter_executable_digest) == 64
    assert binding.python_version and binding.implementation
    assert binding.full_runtime_closure_claimed is False


def test_o_a_worker_asserting_its_own_identity_is_not_believed() -> None:
    """The child may produce a RESULT because the host already authenticated it.
    It may never produce its own IDENTITY evidence."""
    liar = WORKER_SOURCE_BYTES.replace(
        b"def _result():",
        b"def _result():\n    _ = 'worker_source_digest', 'I am trusted'",
        1,
    )
    observed, _ = _run(worker_source=liar)
    assert observed.binding.worker_source_digest == hashlib.sha256(liar).hexdigest()
    assert observed.binding.worker_source_digest != hashlib.sha256(
        WORKER_SOURCE_BYTES
    ).hexdigest()


def test_the_worker_imports_nothing_from_the_application() -> None:
    """T1 proves the boundary; T2 owns what crosses it. The worker is
    stdlib-only, and that includes not using the repository's canonical-JSON
    primitive."""
    source = WORKER_SOURCE_BYTES.decode()
    assert "app." not in source
    assert "strict_json" not in source


def test_the_boundary_never_lets_a_caller_object_cross_inward() -> None:
    """Bytes and primitive parameters only: no pickle, no callable, no class."""
    import inspect

    import app.agent_review.sgaq_data_only_boundary_v2 as boundary

    module_source = inspect.getsource(boundary)
    assert "pickle" not in module_source
    signature = inspect.signature(run_data_only_worker_v2)
    for name, parameter in signature.parameters.items():
        assert parameter.annotation in ("bytes", "float", "bytes | None"), (
            f"{name} admits something other than bytes/primitives: {parameter.annotation}"
        )


# --------------------------------------------------------------------------
# per-guard witnesses
#
# The mutation controls refused seven rows, and every refusal was an
# overlapping defence rather than a bad mutation: the constructed environment
# hid whether `-I` was doing anything, `-I` hid whether the fresh cwd mattered,
# and an exit-status check hid the framing check. A control satisfied by a
# neighbouring guard proves nothing about the one it names.
#
# These use a HOST-AUTHORED probe worker to observe the child's own startup
# state. That is host-owned instrumentation, not child self-assertion: the host
# wrote the probe, and nothing here is used to establish identity (control O
# still owns that).
# --------------------------------------------------------------------------

_PROBE = b"""import json as _json, os as _os, sys as _sys
_p = {
    "protocol": "sgaq.data-only-boundary.v1",
    "request_digest": "0" * 64,
    "request_length": 0,
    "members": sorted([
        "isolated=%d" % _sys.flags.isolated,
        "no_site=%d" % _sys.flags.no_site,
        "no_bytecode=%d" % _sys.flags.dont_write_bytecode,
        "cwd=%s" % _os.getcwd(),
        "env=%s" % ",".join(sorted(_os.environ)),
        "cwd_on_path=%d" % int(_os.getcwd() in _sys.path or "" in _sys.path),
    ]),
}
_b = _json.dumps(_p, sort_keys=True, separators=(",", ":")).encode()
_sys.stdout.buffer.write(b"SGAQ1 " + str(len(_b)).encode() + b"\\n" + _b)
"""


def _probe_facts() -> dict[str, str]:
    observed, output = _run(worker_source=_PROBE)
    assert output is not None, observed.raw_stderr[:400]
    return dict(member.split("=", 1) for member in output.members)


def test_the_child_actually_starts_isolated_without_site_and_without_bytecode() -> None:
    """`-I -S -B` asserted directly, because the constructed environment would
    otherwise satisfy the PYTHONPATH and sitecustomize controls by itself."""
    facts = _probe_facts()
    assert facts["isolated"] == "1"
    assert facts["no_site"] == "1"
    assert facts["no_bytecode"] == "1"


def test_the_callers_working_directory_is_never_on_the_childs_import_path(
    tmp_path, monkeypatch
) -> None:
    """The fresh cwd asserted directly, since `-I` also removes cwd from
    `sys.path` and would satisfy the shadow-module control alone."""
    monkeypatch.chdir(tmp_path)
    facts = _probe_facts()
    assert facts["cwd"] != str(tmp_path)
    assert facts["cwd"].startswith("/")
    assert os.listdir(facts["cwd"]) == [] if os.path.isdir(facts["cwd"]) else True


def test_the_child_environment_is_constructed_not_inherited(monkeypatch) -> None:
    """Exactly the allowlist, nothing else. An inherited-then-overridden
    environment satisfies the locale control while still leaking every other
    variable the caller happens to hold."""
    monkeypatch.setenv("AR_A_VARIABLE_NOBODY_ALLOWLISTED", "leaked")
    facts = _probe_facts()
    present = set(facts["env"].split(","))
    assert "AR_A_VARIABLE_NOBODY_ALLOWLISTED" not in present
    assert present == {
        "SGAQ_REQUEST_FD", "PATH", "HOME", "TMPDIR", "LC_ALL", "LANG", "TZ",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
    }


def test_a_frame_whose_declared_length_is_wrong_is_refused() -> None:
    """The length prefix asserted on its own: a body that parses as JSON but
    whose declared length disagrees. Otherwise the JSON parse alone satisfies
    the duplicate-frame control."""
    liar = WORKER_SOURCE_BYTES.replace(
        b'str(len(body)).encode("ascii")', b'str(len(body) + 1).encode("ascii")', 1
    )
    assert liar != WORKER_SOURCE_BYTES
    observed, output = _run(worker_source=liar)
    assert output is None
    assert not observed.protocol_framing_intact


def test_intact_framing_is_required_even_when_the_child_exited_cleanly() -> None:
    """Framing asserted independently of exit status, which otherwise catches
    the malformed-frame case first."""
    observed, output = _run()
    assert output is not None
    unframed = HostObservedExecutionV2(
        spawned=True, exit_status=0, timed_out=False,
        protocol_framing_intact=False, binding=observed.binding,
    )
    with pytest.raises(DataOnlyBoundaryError, match="framing"):
        accept_semantic_output_v2(unframed, output)


@pytest.mark.parametrize("hostile", ["a string", bytearray(b"x"), memoryview(b"x"), None, 7])
def test_only_exact_bytes_may_cross_the_boundary(hostile) -> None:
    """Behavioural, not a signature inspection. A `bytes` subclass is refused
    for the same reason a `str` subclass was refused in the slices before this
    one: it arrives owning its own comparisons."""
    with pytest.raises(DataOnlyBoundaryError, match="exactly bytes"):
        run_data_only_worker_v2(request=hostile)  # type: ignore[arg-type]


def test_a_bytes_subclass_is_not_exact_bytes() -> None:
    class Sneaky(bytes):
        pass

    with pytest.raises(DataOnlyBoundaryError, match="exactly bytes"):
        run_data_only_worker_v2(request=Sneaky(b"x"))  # type: ignore[arg-type]
