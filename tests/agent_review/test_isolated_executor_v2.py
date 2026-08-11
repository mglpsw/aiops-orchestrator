"""Adversarial + integration tests for the #201-B2/B3 isolated executor.

Every test in this file spawns a REAL subprocess (`unshare`, resource
limits, privilege drop) -- marked `requires_network` following this
repo's own established convention for "spawns a real subprocess", not
literal network access (see `test_diff_acquisition_v2.py`). These tests
run in WHATEVER Linux host executes this suite -- this repository's own
CI or a development sandbox -- which is explicitly NOT the project's
pinned CT104 runner. They prove the isolation MECHANISM is real and works
in this environment; they do not and cannot prove CT104-specific
guarantees that stay `blocked_external: ct104_unavailable`.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Event, Timer

import pytest

from app.agent_review.isolated_executor_v2 import (
    EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2,
    EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2,
    EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2,
    EXECUTOR_REASON_ISOLATION_TOO_WEAK_FOR_TRUSTED_V2,
    EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2,
    EXECUTOR_REASON_PIDFD_UNAVAILABLE_V2,
    EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2,
    EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2,
    EXECUTOR_REASON_SETUP_FAILED_V2,
    AllowlistedCommandSpecV2,
    _classify_completed_process_v2,
    compute_check_command_inventory_digest_v2,
    execute_trusted_check_plan_v2,
)
from app.agent_review.trusted_check_authority_v2 import (
    ELIGIBILITY_ELIGIBLE_V2,
    ELIGIBILITY_INELIGIBLE_V2,
    EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2,
    EXECUTOR_REASON_EXECUTABLE_NOT_ABSOLUTE_V2,
    EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2,
    EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2,
    STATE_FAILED_V2,
    STATE_UNAVAILABLE_V2,
    STATE_VERIFIED_V2,
    WORKLOAD_SUBJECT_OWNED_V2,
    ExecutionClassV2,
)
from app.agent_review.trusted_check_namespace_kernel_v2 import assert_killable_host_pid_v2
from app.agent_review.trusted_check_stream_capture_v2 import capture_stream_v2
from app.agent_review.trusted_check_supervisor_v2 import SUPERVISOR_PROTOCOL_V2
from app.agent_review.trusted_checks_v2 import (
    AllowlistedCheckCommandV2,
    TrustedCheckAuthorityV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckPromotionError,
    bind_trusted_check_result_to_plan_v2,
    promote_trusted_check_to_required_v2,
)

pytestmark = pytest.mark.requires_network

RUN = hashlib.sha256(b"run").hexdigest()
HEAD = "a" * 40
HARNESS = hashlib.sha256(b"harness").hexdigest()


def _command(**overrides) -> AllowlistedCheckCommandV2:
    raw = dict(
        check_name="check", command_token="token", timeout_seconds=6,
        max_memory_mb=128, max_processes=16, network_allowed=False,
    )
    raw.update(overrides)
    return AllowlistedCheckCommandV2(**raw)


def _plan(*, inventory, checks=None) -> TrustedCheckPlanV2:
    return TrustedCheckPlanV2(
        schema_id="agent-review.trusted-check-plan.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, harness_digest=HARNESS,
        authority_suite_digest=compute_check_command_inventory_digest_v2(inventory),
        checks=list(checks) if checks is not None else [_command()],
    )


def _py(
    code: str,
    *,
    token: str = "token",
    execution_class: ExecutionClassV2 = ExecutionClassV2.DATA_ONLY_HOST_TOOL,
    host_owned_config: bool = True,
    loads_checkout_plugins: bool = False,
) -> AllowlistedCommandSpecV2:
    """A host-owned inventory entry running a host-owned interpreter over
    HOST-AUTHORED code -- the argv is written in this test file, never read
    from a checkout, so `data_only_host_tool` is the honest default here."""

    return AllowlistedCommandSpecV2(
        command_token=token,
        argv=(sys.executable, "-c", code),
        execution_class=execution_class,
        host_owned_config=host_owned_config,
        loads_checkout_plugins=loads_checkout_plugins,
    )


def _drive_supervisor(wrapped, *, argv, drop_to_nobody, cwd="/tmp", timeout=20.0) -> dict:
    """Run the real supervisor behind an already-wrapped isolation prefix and
    return its `result` record."""

    host, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    try:
        process = subprocess.Popen(
            wrapped, cwd=cwd, stdin=child.fileno(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        child.close()
        host.send(json.dumps({
            "kind": "config", "protocol": SUPERVISOR_PROTOCOL_V2,
            "nonce": "c" * 64, "spec_digest": "d" * 64, "argv": list(argv), "cwd": cwd,
            "max_memory_mb": 512, "max_processes": 64, "drop_to_nobody": drop_to_nobody,
        }).encode())
        host.settimeout(timeout)
        record = None
        while True:
            try:
                message = json.loads(host.recv(1 << 20).decode())
            except (socket.timeout, OSError, ValueError):
                break
            if message.get("kind") == "hello":
                host.send(json.dumps({
                    "kind": "ready", "protocol": SUPERVISOR_PROTOCOL_V2, "nonce": "c" * 64,
                }).encode())
            if message.get("kind") == "result":
                record = message
                break
        process.communicate(timeout=timeout)
        assert record is not None, "the supervisor produced no result record"
        return record
    finally:
        host.close()


_ROGUE_HANDSHAKE = """\
import json, socket
channel = socket.socket(fileno=0)
config = json.loads(channel.recv(1 << 20).decode())
channel.send(json.dumps({'kind': 'hello', 'protocol': config['protocol'],
                         'nonce': config['nonce'], 'pid': 1}).encode())
channel.recv(1 << 20)
"""


def _rogue_supervisor(tmp_path, name: str, tail: str):
    """A stand-in supervisor that completes the handshake correctly and then
    misbehaves only at the RESULT step -- so these tests exercise result
    validation, not an earlier failure that would mask it."""

    path = tmp_path / name
    path.write_text(_ROGUE_HANDSHAKE + tail)
    return path


@pytest.fixture
def require_direct_isolation_process():
    """Some tests monkeypatch a name in `isolated_executor_v2`'s OWN process
    (`SUPERVISOR_PATH_V2`, `verify_containment_v2`) to inject rogue
    behaviour. That works for the root-direct and weak-userns strategies,
    where this process performs discovery/containment/supervision itself --
    it CANNOT work for the sudo-elevated strategy, where that behaviour runs
    inside `trusted_check_broker_v2`, a genuinely separate privileged
    subprocess no monkeypatch in this process can reach. Skip (not hide):
    the equivalent invariant for the broker path is proven directly against
    the broker's OWN protocol in `test_trusted_check_broker_v2.py`
    (malformed/duplicate-key config, crash containment) and, for the
    misbehaving-record shape specifically, by
    `test_execute_refuses_a_broker_result_carrying_a_foreign_nonce` below,
    which monkeypatches `BROKER_PATH_V2` -- a name the broker path DOES
    consult in this process, unlike `SUPERVISOR_PATH_V2`."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    wrapped_and_strategy = isolated_executor_module._isolation_wrapped_argv_v2(
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert wrapped_and_strategy is not None
    _wrapped, uses_broker, _drop = wrapped_and_strategy
    if uses_broker:
        pytest.skip(
            "this environment selects the sudo-elevated (broker) strategy; the "
            "mechanic under test here runs inside a separate broker subprocess "
            "this test's monkeypatch cannot reach -- see this fixture's own docstring"
        )


@pytest.fixture
def require_broker_isolation_process():
    """Inverse of `require_direct_isolation_process`: skip when this
    environment does NOT select the sudo-elevated (broker) strategy, so the
    broker-targeted rogue test below only runs where it is meaningful."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    wrapped_and_strategy = isolated_executor_module._isolation_wrapped_argv_v2(
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert wrapped_and_strategy is not None
    _wrapped, uses_broker, _drop = wrapped_and_strategy
    if not uses_broker:
        pytest.skip("this environment does not select the sudo-elevated (broker) strategy")


_ROGUE_BROKER_HANDSHAKE = """\
import json, socket
channel = socket.socket(fileno=0)
config = json.loads(channel.recv(1 << 20).decode())
channel.send(json.dumps({'kind': 'ready', 'protocol': config['protocol'],
                         'nonce': config['nonce']}).encode())
"""


def _rogue_broker(tmp_path, name: str, tail: str):
    path = tmp_path / name
    path.write_text(_ROGUE_BROKER_HANDSHAKE + tail)
    return path


@pytest.fixture
def repo_root():
    # pytest's own tmp_path fixture lives several directories deep under a
    # root-owned, mode-0700 pytest-of-root tree -- chmod'ing the leaf alone
    # would not help, since the isolated child is deliberately dropped to an
    # unprivileged uid (`nobody`) BEFORE it ever touches this directory.
    path = Path(tempfile.mkdtemp())
    os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def require_strong_isolation():
    """The unprivileged-userns fallback has no real uid separation from the
    host caller, so `execute_trusted_check_plan_v2` refuses to back a
    TRUSTED result with it. Tests that exercise TRUSTED execution MECHANICS
    request this fixture to skip (not fail) on an environment where that
    fallback is the only thing that works."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    strategy = isolated_executor_module._select_isolation_strategy_v2()
    assert strategy is not None, "the module's own probe found no working isolation strategy"
    drop_to_nobody, _prefix = strategy
    if not drop_to_nobody:
        pytest.skip(
            "this environment's probe selected the unprivileged-userns fallback "
            "(no real uid separation from the host caller)"
        )


def test_execute_reports_success_for_a_zero_exit_check(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert len(executed) == 1
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert executed[0].result.artifact_sha256 is not None
    bind_trusted_check_result_to_plan_v2(executed[0].result, plan)  # does not raise


def test_execute_reports_infra_failure_when_the_allowlisted_binary_is_missing(
    repo_root, require_strong_isolation,
):
    """A review caught that a pre-exec setup failure inside the namespace
    (here: `execvp` failing because the allowlisted binary does not exist)
    exited the supervisor's child with the SAME status a real subject could
    also legitimately exit with -- misreporting a broken harness/runner as
    an ordinary, deterministic product FAILURE. End-to-end (not a rogue
    stand-in), so this exercises whichever isolation strategy this
    environment actually selects -- direct or via the broker, which now
    also relays the signal through its own protocol."""

    missing = AllowlistedCommandSpecV2(
        command_token="token",
        argv=("/nonexistent/definitely-not-a-real-binary-v2", "arg"),
        execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL,
        host_owned_config=True,
        loads_checkout_plugins=False,
    )
    inventory = {"token": missing}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_SETUP_FAILED_V2


def test_execute_reports_failure_for_a_nonzero_exit_check(repo_root, require_strong_isolation):
    inventory = {"token": _py("import sys; sys.exit(1)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE


def test_execute_ignores_forged_stdout_and_report_file_and_trusts_only_the_real_exit_code(repo_root, require_strong_isolation):
    """Forging what the check PRINTS or WRITES changes nothing: the verdict
    depends only on the real, kernel-observed exit status."""

    forge_path = repo_root / "forged-report.json"
    code = (
        f"import sys\n"
        f"print('ALL TESTS PASSED')\n"
        f"open({str(forge_path)!r}, 'w').write('{{\"status\": \"success\"}}')\n"
        f"sys.exit(1)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE


def test_execute_denies_real_outbound_network_access(repo_root, require_strong_isolation):
    code = (
        "import socket, sys\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('93.184.216.34', 80))\n"
        "    sys.exit(0)\n"
        "except OSError as exc:\n"
        "    sys.exit(exc.errno if exc.errno else 1)\n"
    )
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    wrapped_and_strategy = isolated_executor_module._isolation_wrapped_argv_v2(
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert wrapped_and_strategy is not None, "the module's own probe found no working isolation strategy"
    wrapped, uses_broker, drop_to_nobody = wrapped_and_strategy
    if uses_broker:
        pytest.skip("network-denial mechanics under the broker strategy are covered end-to-end elsewhere")
    record = _drive_supervisor(
        wrapped, argv=[sys.executable, "-c", code], drop_to_nobody=drop_to_nobody
    )
    assert record["exit_status"] == errno.ENETUNREACH, (
        "expected ENETUNREACH (no route in the isolated namespace), "
        f"got {record['exit_status']}"
    )


def test_execute_denies_sudo_inside_the_isolated_check(repo_root, require_strong_isolation):
    code = (
        "import subprocess, sys\n"
        "completed = subprocess.run(['sudo', '-n', 'true'], capture_output=True)\n"
        "sys.exit(0 if completed.returncode != 0 else 1)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS


def test_execute_refuses_a_command_token_not_in_the_inventory(repo_root):
    inventory = {"other": _py("pass", token="other")}
    plan = _plan(inventory=inventory, checks=[_command(command_token="token")])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2


def test_execute_refuses_an_inventory_that_does_not_match_the_plans_own_digest(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    other_inventory = {"token": _py("import sys; sys.exit(1)")}
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=other_inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2


def test_execute_refuses_an_inventory_whose_dict_keys_do_not_match_their_own_command_token(repo_root):
    inventory = {"other_token": _py("pass", token="token")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2


def test_execute_refuses_trusted_authority_when_only_weak_isolation_is_available(repo_root, monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(
        isolated_executor_module, "_select_isolation_strategy_v2", lambda: (False, ())
    )
    inventory = {"token": _py("import sys; sys.exit(0)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_ISOLATION_TOO_WEAK_FOR_TRUSTED_V2


def test_execute_still_allows_untrusted_advisory_over_weak_isolation(repo_root, monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    weak_prefix = (
        "unshare", "--user", "--map-root-user",
        *isolated_executor_module.NAMESPACE_FLAGS_V2, "--",
    )
    if not isolated_executor_module._probe_prefix_works_v2(weak_prefix):
        pytest.skip("this environment cannot create an unprivileged user namespace")
    monkeypatch.setattr(
        isolated_executor_module, "_select_isolation_strategy_v2", lambda: (False, weak_prefix)
    )
    inventory = {"token": _py("import sys; sys.exit(0)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert executed[0].result.authority is TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY


def test_probe_prefix_for_sudo_spawns_the_exact_broker_runtime_command(monkeypatch):
    """A review caught that the previous probe tested TWO INDEPENDENT
    sudoers grants (`sudo -n unshare ...` directly, plus a separate
    `sudo -n -l -- <broker>` authorization check) instead of the ONE
    command that actually runs at runtime: `sudo -n <python> -I <broker>`.
    Once that single command elevates the broker to root, its own internal
    `unshare` call needs no further sudo authorization at all -- it is a
    direct child `Popen`, never routed through `sudo` again. A
    least-privilege policy authorizing ONLY the exact broker command is
    correct and must not be rejected for lacking a second, unnecessary
    grant. This asserts the probe never calls `subprocess.run` (the old
    dual-check shape) and spawns exactly the real runtime command via
    `Popen`."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    captured: dict[str, object] = {}

    def fail_on_run(*a, **k):
        raise AssertionError(
            "the sudo probe must never call subprocess.run -- it must exercise "
            "the real broker command end-to-end via Popen"
        )

    def fake_popen(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["cwd"] = kwargs.get("cwd")
        raise OSError("sudo: a password is required")

    monkeypatch.setattr(isolated_executor_module.subprocess, "run", fail_on_run)
    monkeypatch.setattr(isolated_executor_module.subprocess, "Popen", fake_popen)

    sudo_path = isolated_executor_module.SUDO_PATH_V2
    result = isolated_executor_module._probe_prefix_works_v2((sudo_path, "-n"))

    assert result is False
    argv = captured["argv"]
    assert argv[:2] == [sudo_path, "-n"]
    assert argv[2] == sys.executable
    assert "-I" in argv
    assert str(isolated_executor_module.BROKER_PATH_V2) in argv
    assert "unshare" not in argv, (
        "the probe itself must never invoke unshare directly -- only the "
        "broker's own internal Popen call does that, unauthenticated, once "
        "the broker is already root"
    )


def test_probe_prefix_for_sudo_fails_closed_when_the_broker_never_reaches_ready(monkeypatch):
    """Authorization and spawn succeed but the broker never reaches
    `ready` (e.g. kernel/container policy blocks the namespace creation
    inside it -- the exact CT104 risk this subsystem's own docs name) --
    the probe must reject the candidate, not accept on a bare successful
    spawn."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            self.argv = list(argv)

        def wait(self, timeout=None):
            return 0

        def kill(self):  # pragma: no cover - not exercised here
            pass

    monkeypatch.setattr(isolated_executor_module.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(
        isolated_executor_module, "_recv_record_v2", lambda channel, timeout: None
    )

    sudo_path = isolated_executor_module.SUDO_PATH_V2
    assert isolated_executor_module._probe_prefix_works_v2((sudo_path, "-n")) is False


def test_probe_prefix_for_sudo_actually_reaches_ready_end_to_end(
    require_broker_isolation_process,
):
    """Real, unmocked exercise of the exact runtime command against this
    sandbox's own passwordless-sudo account -- proves the probe's success
    path genuinely spawns a real broker and observes a real `ready` record,
    not merely that the mocks above are internally consistent.
    `require_broker_isolation_process` already establishes (via
    `_select_isolation_strategy_v2`, which itself calls this same probe)
    that this account selects the broker strategy, so a direct call must
    also observe success."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    sudo_path = isolated_executor_module.SUDO_PATH_V2
    assert isolated_executor_module._probe_prefix_works_v2((sudo_path, "-n")) is True


def test_probe_prefix_for_non_sudo_candidates_is_unchanged(monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    captured: dict[str, list[str]] = {}

    class _FakeCompleted:
        returncode = 0

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return _FakeCompleted()

    monkeypatch.setattr(isolated_executor_module.subprocess, "run", fake_run)
    prefix = (
        "unshare", "--user", "--map-root-user",
        *isolated_executor_module.NAMESPACE_FLAGS_V2, "--",
    )
    isolated_executor_module._probe_prefix_works_v2(prefix)

    assert captured["argv"] == [*prefix, "true"]


def test_unshare_path_resolves_to_an_absolute_path_via_a_fixed_search_list():
    """A review caught that a bare `"unshare"` string handed to
    `subprocess.Popen(..., cwd=repo_root)` is resolved via PATH search
    performed AFTER the child has already `chdir`'d into the checkout --
    letting a PR-controlled `$PATH` entry (e.g. `.`) or a same-named file
    shipped in the checkout supply the "system" binary that runs as root,
    before any namespace or privilege drop exists. `_resolve_trusted_
    binary_v2` must resolve strictly via `os.defpath` (all-absolute
    entries), so the result never depends on cwd or on an
    attacker-influenced `$PATH`."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    assert isolated_executor_module.UNSHARE_PATH_V2 is not None
    assert os.path.isabs(isolated_executor_module.UNSHARE_PATH_V2)
    assert isolated_executor_module._resolve_trusted_binary_v2("unshare") == (
        isolated_executor_module.UNSHARE_PATH_V2
    )


def test_resolve_trusted_binary_ignores_a_malicious_path_and_only_uses_defpath(monkeypatch, tmp_path):
    """Even if `$PATH` is poisoned with a directory placing a rogue
    `unshare` first, resolution must still land on the real, fixed-list
    binary -- never the attacker-controlled one."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    rogue = tmp_path / "unshare"
    rogue.write_text("#!/bin/sh\nexit 0\n")
    rogue.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")

    resolved = isolated_executor_module._resolve_trusted_binary_v2("unshare")

    assert resolved != str(rogue)
    assert resolved == isolated_executor_module.UNSHARE_PATH_V2


def test_isolation_prefix_candidates_is_empty_when_unshare_cannot_be_resolved(monkeypatch):
    """Fail-closed, never a bare-name fallback: if `unshare` cannot be
    resolved to an absolute path at all, no candidate -- not even the
    root-direct one -- may be offered, since every candidate's `Popen`
    invocation depends on `UNSHARE_PATH_V2`."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "UNSHARE_PATH_V2", None)

    assert isolated_executor_module._isolation_prefix_candidates_v2() == ()


def test_isolation_prefix_candidates_use_the_resolved_absolute_path_never_a_bare_name(monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    fake_absolute = "/usr/bin/unshare"
    monkeypatch.setattr(isolated_executor_module, "UNSHARE_PATH_V2", fake_absolute)
    monkeypatch.setattr(isolated_executor_module.os, "geteuid", lambda: 0)

    candidates = isolated_executor_module._isolation_prefix_candidates_v2()

    assert len(candidates) == 1
    _drop_to_nobody, prefix = candidates[0]
    assert prefix[0] == fake_absolute
    assert "unshare" not in prefix, "must never fall back to the bare, PATH-searched name"


def test_broker_unshare_path_resolves_to_an_absolute_path():
    """The broker's own internal `unshare` invocation -- which runs with
    `cwd` set to the checkout, exactly the same hazard as the executor's
    direct path -- must be resolved the same way."""

    import app.agent_review.trusted_check_broker_v2 as broker_module

    assert broker_module.UNSHARE_PATH_V2 is not None
    assert os.path.isabs(broker_module.UNSHARE_PATH_V2)


def test_sudo_path_resolves_to_an_absolute_path_via_a_fixed_search_list():
    """A review caught the identical hazard one level up from `unshare`:
    `_run_isolated_via_broker_v2` spawns the sudo-elevated candidate with
    `cwd=str(repo_root)`, so an unresolved bare `"sudo"` would be searched
    for on `$PATH` only after the child has already `chdir`'d into the
    checkout -- letting a PR-controlled `$PATH` entry or a same-named
    checkout file supply the "sudo" that the broker probe just proved was
    authorized, and forge a bound `ready`/`result` record instead of ever
    invoking real sudo or the real broker."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    assert isolated_executor_module.SUDO_PATH_V2 is not None
    assert os.path.isabs(isolated_executor_module.SUDO_PATH_V2)
    assert isolated_executor_module._resolve_trusted_binary_v2("sudo") == (
        isolated_executor_module.SUDO_PATH_V2
    )


def test_isolation_prefix_candidates_offer_no_sudo_candidate_when_sudo_cannot_be_resolved(monkeypatch):
    """Fail-closed, never a bare-name fallback: if `sudo` cannot be
    resolved to an absolute path, the sudo-elevated candidate must simply
    be absent -- never offered with a bare `"sudo"` prefix."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "SUDO_PATH_V2", None)
    monkeypatch.setattr(isolated_executor_module.os, "geteuid", lambda: 1000)

    candidates = isolated_executor_module._isolation_prefix_candidates_v2()

    assert all(prefix[0] != "sudo" for _drop, prefix in candidates)
    assert len(candidates) == 1, "only the weak-userns fallback should remain"


def test_isolation_wrapped_argv_uses_the_resolved_sudo_path_never_a_bare_name(monkeypatch):
    """The actual command executed by `_run_isolated_via_broker_v2` (with
    `cwd` set to the checkout) must never contain the literal string
    `"sudo"` -- only the resolved absolute path."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    fake_sudo = "/usr/bin/sudo"
    monkeypatch.setattr(isolated_executor_module, "SUDO_PATH_V2", fake_sudo)
    monkeypatch.setattr(
        isolated_executor_module, "_select_isolation_strategy_v2", lambda: (True, (fake_sudo, "-n"))
    )

    wrapped_and_strategy = isolated_executor_module._isolation_wrapped_argv_v2(
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )

    assert wrapped_and_strategy is not None
    wrapped, uses_broker, _drop = wrapped_and_strategy
    assert uses_broker is True
    assert wrapped[0] == fake_sudo
    assert "sudo" not in wrapped, "must never fall back to the bare, PATH-searched name"


def test_execute_never_runs_unisolated_when_isolation_is_unavailable(repo_root, monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "_select_isolation_strategy_v2", lambda: None)
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2


def test_execute_produces_a_typed_timeout_outcome_for_a_hanging_check(repo_root, require_strong_isolation):
    inventory = {"token": _py("import time; time.sleep(300)")}
    plan = _plan(inventory=inventory, checks=[_command(timeout_seconds=2)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.TIMEOUT


def test_execute_produces_a_typed_cancelled_outcome_when_cancel_event_is_set(repo_root, require_strong_isolation):
    inventory = {"token": _py("import time; time.sleep(300)")}
    plan = _plan(inventory=inventory)
    cancel_event = Event()
    Timer(1.0, cancel_event.set).start()
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.TRUSTED, cancel_event=cancel_event,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.CANCELLED


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available in this environment")
def test_execute_enforces_the_memory_limit_and_classifies_the_kill_as_failure_not_oom(repo_root, require_strong_isolation):
    source = repo_root / "hog.c"
    binary = repo_root / "hog"
    source.write_text(
        "#include <stdlib.h>\n#include <string.h>\nint main(void) {\n"
        "  for (;;) { char *p = malloc(1 << 20); if (!p) return 1; memset(p, 1, 1 << 20); }\n"
        "  return 0;\n}\n"
    )
    subprocess.run(["gcc", "-O0", "-o", str(binary), str(source)], check=True)
    inventory = {
        "token": AllowlistedCommandSpecV2(
            command_token="token",
            argv=(str(binary),),
            execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL,
            host_owned_config=True,
        )
    }
    plan = _plan(inventory=inventory, checks=[_command(max_memory_mb=32, timeout_seconds=20)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE


def test_execute_result_is_deterministic_across_two_real_runs(repo_root):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    first = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    second = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert first[0].result.outcome == second[0].result.outcome


def test_execute_untrusted_advisory_result_still_refuses_promotion(repo_root):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    with pytest.raises(TrustedCheckPromotionError):
        promote_trusted_check_to_required_v2(executed[0].result)


class TestClassifyCompletedProcessUnit:
    @pytest.mark.parametrize("signal_number", [9, 11, 7, 6, 15])  # KILL, SEGV, BUS, ABRT, TERM
    def test_signal_death_classifies_as_failure_never_oom(self, signal_number):
        outcome, reason = _classify_completed_process_v2(returncode=-signal_number)
        assert outcome is TrustedCheckOutcomeV2.FAILURE
        assert reason is None

    def test_zero_returncode_classifies_as_success(self):
        outcome, reason = _classify_completed_process_v2(returncode=0)
        assert outcome is TrustedCheckOutcomeV2.SUCCESS


# -- #201-B3 (rev. 4) C1: authority boundary, refused BEFORE spawn ------------


@pytest.fixture
def forbid_spawn(monkeypatch):
    """Fails the test if ANY process is spawned. A refused check must never
    reach a spawn at all -- not spawn and then be discarded."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    def _boom(*args, **kwargs):  # pragma: no cover - the assertion is the point
        raise AssertionError("a refused check must never spawn a process")

    monkeypatch.setattr(isolated_executor_module.subprocess, "Popen", _boom)
    monkeypatch.setattr(isolated_executor_module.subprocess, "run", _boom)


def test_execute_refuses_trusted_for_subject_code_before_spawn(repo_root, forbid_spawn):
    inventory = {"token": _py("pass", execution_class=ExecutionClassV2.SUBJECT_CODE)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2
    assert executed[0].assessment.promotion_eligibility == ELIGIBILITY_INELIGIBLE_V2


def test_execute_refuses_trusted_for_unclassified_command_before_spawn(repo_root, forbid_spawn):
    inventory = {"token": _py("pass", execution_class=ExecutionClassV2.UNKNOWN)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_EXECUTION_CLASS_UNKNOWN_V2


def test_execute_refuses_trusted_when_a_data_only_tool_lacks_host_owned_config(repo_root, forbid_spawn):
    inventory = {"token": _py("pass", host_owned_config=False)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_HOST_OWNED_CONFIG_REQUIRED_V2


def test_execute_refuses_trusted_when_the_tool_loads_checkout_plugins(repo_root, forbid_spawn):
    inventory = {"token": _py("pass", loads_checkout_plugins=True)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_SUBJECT_CODE_CANNOT_BE_TRUSTED_V2


def test_execute_refuses_trusted_for_a_bare_executable_name_before_spawn(repo_root, forbid_spawn):
    """A review caught that a bare argv[0] is resolved by `execvp` searching
    $PATH from the checkout's own cwd -- a runner with `.` (or another
    PR-writable directory) on PATH would let the PR supply the "host-owned"
    binary itself. `forbid_spawn` proves this is refused BEFORE any process
    exists, not detected after the fact."""

    bare = AllowlistedCommandSpecV2(
        command_token="token",
        argv=("ruff", "check", "."),
        execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL,
        host_owned_config=True,
        loads_checkout_plugins=False,
    )
    inventory = {"token": bare}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_EXECUTABLE_NOT_ABSOLUTE_V2


def test_execute_still_runs_subject_code_under_untrusted_advisory(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass", execution_class=ExecutionClassV2.SUBJECT_CODE)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert executed[0].result.authority is TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY
    assert executed[0].assessment.promotion_eligibility == ELIGIBILITY_INELIGIBLE_V2


def test_forged_exit_zero_from_subject_code_can_never_be_promoted(repo_root, require_strong_isolation):
    forge = "import os\nos._exit(0)\n"
    inventory = {"token": _py(forge, execution_class=ExecutionClassV2.SUBJECT_CODE)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    result = executed[0].result
    assert result.outcome is TrustedCheckOutcomeV2.SUCCESS  # the forgery "worked" at the OS level
    assert executed[0].assessment.coverage_authority == STATE_UNAVAILABLE_V2
    with pytest.raises(TrustedCheckPromotionError):
        promote_trusted_check_to_required_v2(result)


def test_subject_code_coverage_authority_is_never_complete(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass", execution_class=ExecutionClassV2.SUBJECT_CODE)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert executed[0].assessment.coverage_authority == STATE_UNAVAILABLE_V2
    assert executed[0].assessment.workload_authority == WORKLOAD_SUBJECT_OWNED_V2


# -- #201-B3 (rev. 4) C2: private channel replaces the pgid handshake --------


def test_executor_never_creates_a_group_or_world_writable_file(repo_root, require_strong_isolation, monkeypatch):
    """#201-B2's pgid-report file cost three review rounds (attacker-writable
    pgid, then a fail-OPEN lockdown of that fix). #201-B3 deletes the file
    entirely rather than hardening it again -- this fails if the executor
    ever creates or chmods anything group/other-writable."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    offending: list[tuple[str, int]] = []
    real_chmod, real_open = os.chmod, os.open

    def _watch_chmod(path, mode, *args, **kwargs):
        if mode & 0o022:
            offending.append((str(path), mode))
        return real_chmod(path, mode, *args, **kwargs)

    def _watch_open(path, flags, mode=0o777, *args, **kwargs):
        if (flags & os.O_CREAT) and (mode & 0o022):
            offending.append((str(path), mode))
        return real_open(path, flags, mode, *args, **kwargs)

    monkeypatch.setattr(isolated_executor_module.os, "chmod", _watch_chmod)
    monkeypatch.setattr(isolated_executor_module.os, "open", _watch_open)

    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert offending == []


def test_execute_refuses_a_supervisor_record_carrying_a_foreign_nonce(repo_root, require_strong_isolation, monkeypatch, tmp_path, require_direct_isolation_process):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    rogue = _rogue_supervisor(tmp_path, "rogue_supervisor.py", """
channel.send(json.dumps({'kind': 'result', 'protocol': config['protocol'],
                         'nonce': 'f' * 64, 'spec_digest': config['spec_digest'],
                         'exit_status': 0, 'signaled': False}).encode())
""")
    monkeypatch.setattr(isolated_executor_module, "SUPERVISOR_PATH_V2", rogue)

    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2


def test_execute_refuses_a_supervisor_record_answering_a_different_spec(repo_root, require_strong_isolation, monkeypatch, tmp_path, require_direct_isolation_process):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    rogue = _rogue_supervisor(tmp_path, "wrong_spec_supervisor.py", """
channel.send(json.dumps({'kind': 'result', 'protocol': config['protocol'],
                         'nonce': config['nonce'], 'spec_digest': 'a' * 64,
                         'exit_status': 0, 'signaled': False}).encode())
""")
    monkeypatch.setattr(isolated_executor_module, "SUPERVISOR_PATH_V2", rogue)

    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2


def test_execute_refuses_a_broker_result_carrying_a_foreign_nonce(repo_root, require_strong_isolation, monkeypatch, tmp_path, require_broker_isolation_process):
    """The broker-path counterpart of the rogue-supervisor tests above.
    Unlike `SUPERVISOR_PATH_V2`, `BROKER_PATH_V2` IS consulted by
    `isolated_executor_v2` in THIS process even under the sudo-elevated
    strategy (it names the script the executor asks `sudo -n` to run), so
    monkeypatching it here genuinely reaches the code path under test."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    rogue = _rogue_broker(tmp_path, "rogue_broker.py", """
channel.send(json.dumps({'kind': 'result', 'protocol': config['protocol'],
                         'nonce': 'f' * 64, 'spec_digest': config['spec_digest'],
                         'exit_status': 0, 'signaled': False, 'contained': True}).encode())
""")
    monkeypatch.setattr(isolated_executor_module, "BROKER_PATH_V2", rogue)

    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2


def test_execute_refuses_when_no_record_arrives_at_all(repo_root, require_strong_isolation, monkeypatch, tmp_path, require_direct_isolation_process):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    rogue = _rogue_supervisor(tmp_path, "silent_supervisor.py", "")
    monkeypatch.setattr(isolated_executor_module, "SUPERVISOR_PATH_V2", rogue)

    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2


# -- #201-B3 (rev. 4) C3: identity + process-lifetime containment ------------


def _survivor_code(marker: Path, *, escape: str, leader_exit: str = "raise SystemExit(0)") -> str:
    escapes = {
        "fork": "",
        "setsid": "os.setsid()\n",
        "double_fork": "os.setsid()\nif os.fork() != 0: os._exit(0)\n",
        "closed_pipes": "os.close(0); os.close(1); os.close(2)\n",
    }
    child_body = (
        escapes[escape]
        + "import time\n"
        + "time.sleep(6)\n"
        + f"open({str(marker)!r}, 'w').write('survived')\n"
        + "os._exit(0)\n"
    )
    return (
        "import os, sys, time\n"
        "if os.fork() == 0:\n"
        + "".join(f"    {line}\n" for line in child_body.splitlines())
        + f"{leader_exit}\n"
    )


def _assert_no_survivor(marker: Path, *, grace: float = 9.0) -> None:
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        assert not marker.exists(), "a descendant survived the execution"
        time.sleep(0.25)


@pytest.mark.parametrize("escape", ["fork", "setsid", "double_fork", "closed_pipes"])
def test_no_descendant_survives_a_successful_check(repo_root, require_strong_isolation, escape):
    marker = repo_root / f"survivor-{escape}"
    inventory = {"token": _py(_survivor_code(marker, escape=escape))}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    _assert_no_survivor(marker)


def test_no_descendant_survives_a_failing_check(repo_root, require_strong_isolation):
    marker = repo_root / "survivor-failure"
    inventory = {
        "token": _py(_survivor_code(marker, escape="double_fork", leader_exit="raise SystemExit(1)"))
    }
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE
    _assert_no_survivor(marker)


def test_no_descendant_survives_a_timeout(repo_root, require_strong_isolation):
    marker = repo_root / "survivor-timeout"
    code = _survivor_code(marker, escape="double_fork", leader_exit="time.sleep(300)")
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory, checks=[_command(timeout_seconds=2)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.TIMEOUT
    _assert_no_survivor(marker)


def test_no_descendant_survives_a_cancellation(repo_root, require_strong_isolation):
    marker = repo_root / "survivor-cancel"
    code = _survivor_code(marker, escape="double_fork", leader_exit="time.sleep(300)")
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    cancel_event = Event()
    Timer(1.5, cancel_event.set).start()
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.TRUSTED, cancel_event=cancel_event,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.CANCELLED
    _assert_no_survivor(marker)


def test_many_descendants_and_grandchildren_all_die(repo_root, require_strong_isolation):
    marker = repo_root / "survivor-many"
    code = (
        "import os, time\n"
        "for _ in range(4):\n"
        "    if os.fork() == 0:\n"
        "        os.setsid()\n"
        "        for _ in range(2):\n"
        "            os.fork()\n"
        "        time.sleep(6)\n"
        f"        open({str(marker)!r}, 'a').write('x')\n"
        "        os._exit(0)\n"
        "raise SystemExit(0)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    _assert_no_survivor(marker)


def test_the_subject_cannot_see_the_host_process_table(repo_root, require_strong_isolation):
    code = (
        "import os, sys\n"
        "pids = sorted(int(n) for n in os.listdir('/proc') if n.isdigit())\n"
        "sys.exit(0 if max(pids) < 50 else 1)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS


def test_the_module_contains_no_killpg_and_no_negative_signal_target():
    """Row 27, asserted structurally: `killpg` and a `-<pgid>` operand are
    absent from every module in the trusted-check namespace-kernel/broker/
    executor family -- not merely unused."""

    import app.agent_review.isolated_executor_v2 as isolated_executor_module
    import app.agent_review.trusted_check_broker_v2 as broker_module
    import app.agent_review.trusted_check_namespace_kernel_v2 as kernel_module

    for module in (isolated_executor_module, kernel_module, broker_module):
        source = Path(module.__file__).read_text()
        assert "killpg(" not in source, module.__name__
        assert 'f"-{' not in source and "'-{" not in source, module.__name__
        # No `sudo ... kill` invocation anywhere -- checked as a real argv
        # shape, not a loose word-proximity heuristic that prose could trip.
        assert '"sudo", "-n", "kill"' not in source, module.__name__
        assert "'sudo', '-n', 'kill'" not in source, module.__name__
        assert "sudo -n kill" not in source, module.__name__


def test_a_namespace_relative_pid_never_reaches_a_kill(repo_root, require_strong_isolation):
    for forged in (1, 0, -1, -12345):
        with pytest.raises(ValueError):
            assert_killable_host_pid_v2(forged)
    assert assert_killable_host_pid_v2(4242) == 4242


def test_execute_fails_closed_when_pid_namespaces_are_unavailable(repo_root, monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "_select_isolation_strategy_v2", lambda: None)
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2


def test_execute_fails_closed_when_pidfd_is_unavailable(repo_root, monkeypatch):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "PIDFD_AVAILABLE_V2", False)
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PIDFD_UNAVAILABLE_V2


# -- #201-B3 (rev. 4) C4: bounded output, resources, coexisting failures -----


def test_large_output_on_both_streams_neither_deadlocks_nor_is_unbounded(repo_root, require_strong_isolation):
    code = (
        "import sys\n"
        "chunk = 'x' * 4096\n"
        "for _ in range(400):\n"
        "    sys.stdout.write(chunk)\n"
        "    sys.stderr.write(chunk)\n"
        "sys.exit(0)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory, checks=[_command(timeout_seconds=30)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert executed[0].assessment.resource_boundary == STATE_VERIFIED_V2


def test_output_budget_truncation_is_explicit_and_hashed():
    captured = capture_stream_v2(io.BytesIO(b"y" * 5000), budget=1000)
    assert captured.total_bytes == 5000
    assert captured.truncated is True
    assert len(captured.excerpt) == 1000
    assert captured.sha256 == hashlib.sha256(b"y" * 5000).hexdigest()


def test_a_containment_failure_and_a_product_failure_coexist(repo_root, monkeypatch, require_strong_isolation, require_direct_isolation_process):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "verify_containment_v2", lambda identity: False)
    inventory = {"token": _py("import sys; sys.exit(1)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assessment = executed[0].assessment
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE  # NOT rewritten
    assert assessment.product_outcome == "failure"
    assert assessment.containment == STATE_FAILED_V2
    assert assessment.promotion_eligibility == ELIGIBILITY_INELIGIBLE_V2


def test_an_unproven_containment_does_not_let_a_success_stand(repo_root, monkeypatch, require_strong_isolation, require_direct_isolation_process):
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "verify_containment_v2", lambda identity: False)
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2


def test_direct_isolation_dies_with_the_executor(tmp_path, repo_root, require_direct_isolation_process):
    """A review caught that nothing tied the direct (root/weak-userns)
    strategy's `unshare` process to the EXECUTOR's own lifetime --
    `--kill-child` only cascades DOWNWARD from `unshare` to the namespace it
    creates, never upward to the process that spawned `unshare` itself. If
    the executor died -- crash, OOM, a higher-level timeout -- while a check
    was still running, `unshare` (detached into its own session via
    `start_new_session=True`) would be reparented and keep running, along
    with the whole namespace under it, indefinitely and unsupervised.

    Proves the fix end-to-end: runs a REAL executor in a separate process
    and hard-kills THAT process mid-check, before it ever reaches its own
    teardown code -- exactly the scenario `--kill-child` alone cannot cover,
    since it only protects against `unshare` itself dying, not its parent."""

    # The subject runs as `nobody` (dropped privilege) and only ever touches
    # `repo_root` -- marker files must live INSIDE the `repo_root` FIXTURE
    # (a shallow, world-traversable `tempfile.mkdtemp()` directory), not
    # under `tmp_path`, which sits several directories deep under a
    # root-owned, mode-0700 `pytest-of-root` tree -- chmod'ing only the leaf
    # would not make the PARENT directories traversable by `nobody` (see
    # `repo_root`'s own fixture docstring).
    started = repo_root / "executor-death-started"
    survived = repo_root / "executor-death-survivor"

    subject_code = (
        f"import time\n"
        f"open({str(started)!r}, 'w').write('go')\n"
        f"time.sleep(5)\n"
        f"open({str(survived)!r}, 'w').write('survived')\n"
    )
    repo_toplevel = Path(__file__).resolve().parents[2]
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import hashlib, sys\n"
        f"sys.path.insert(0, {str(repo_toplevel)!r})\n"
        "from pathlib import Path\n"
        "from app.agent_review.isolated_executor_v2 import (\n"
        "    AllowlistedCommandSpecV2, compute_check_command_inventory_digest_v2,\n"
        "    execute_trusted_check_plan_v2,\n"
        ")\n"
        "from app.agent_review.trusted_check_authority_v2 import ExecutionClassV2\n"
        "from app.agent_review.trusted_checks_v2 import (\n"
        "    AllowlistedCheckCommandV2, TrustedCheckAuthorityV2, TrustedCheckPlanV2,\n"
        ")\n"
        "spec = AllowlistedCommandSpecV2(\n"
        f"    command_token='token', argv=({sys.executable!r}, '-c', {subject_code!r}),\n"
        "    execution_class=ExecutionClassV2.DATA_ONLY_HOST_TOOL, host_owned_config=True,\n"
        ")\n"
        "inventory = {'token': spec}\n"
        "plan = TrustedCheckPlanV2(\n"
        "    schema_id='agent-review.trusted-check-plan.v2', schema_version=2,\n"
        "    run_id=hashlib.sha256(b'run').hexdigest(), head_sha='a' * 40,\n"
        "    harness_digest=hashlib.sha256(b'harness').hexdigest(),\n"
        "    authority_suite_digest=compute_check_command_inventory_digest_v2(inventory),\n"
        "    checks=[AllowlistedCheckCommandV2(\n"
        "        check_name='check', command_token='token', timeout_seconds=30,\n"
        "        max_memory_mb=128, max_processes=16, network_allowed=False,\n"
        "    )],\n"
        ")\n"
        f"execute_trusted_check_plan_v2(\n"
        f"    plan, repo_root=Path({str(repo_root)!r}), inventory=inventory,\n"
        "    authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,\n"
        ")\n"
    )

    process = subprocess.Popen(
        [sys.executable, str(runner)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not started.exists():
            time.sleep(0.1)
        assert started.exists(), "the subject never started"

        # Simulate the executor dying: hard-kill the runner process itself,
        # mid-check, before it ever reaches its own teardown code.
        process.kill()
        process.wait(timeout=10)

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            assert not survived.exists(), "a descendant survived after the executor died"
            time.sleep(0.25)
    finally:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass


def test_a_clean_host_owned_check_reaches_full_promotion_eligibility(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assessment = executed[0].assessment
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert assessment.containment == STATE_VERIFIED_V2
    assert assessment.protocol_integrity == STATE_VERIFIED_V2
    assert assessment.subject_identity == STATE_VERIFIED_V2
    assert assessment.promotion_eligibility == ELIGIBILITY_ELIGIBLE_V2
    promote_trusted_check_to_required_v2(executed[0].result)


def test_subject_code_never_reaches_eligibility_however_clean_the_run(repo_root, require_strong_isolation):
    inventory = {"token": _py("pass", execution_class=ExecutionClassV2.SUBJECT_CODE)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory,
        authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assessment = executed[0].assessment
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert assessment.containment == STATE_VERIFIED_V2
    assert assessment.promotion_eligibility == ELIGIBILITY_INELIGIBLE_V2
