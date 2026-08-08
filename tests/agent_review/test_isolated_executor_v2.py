"""Adversarial + integration tests for the #201-B2 isolated executor.

Every test in this file spawns a REAL subprocess (`unshare`, resource
limits, privilege drop) -- marked `requires_network` following this
repo's own established convention for "spawns a real subprocess", not
literal network access (see `test_diff_acquisition_v2.py`). These tests
run in WHATEVER Linux host executes this suite -- this repository's own
CI or a development sandbox -- which is explicitly NOT the project's
pinned CT104 runner (see `isolated_executor_v2.py`'s own module
docstring). They prove the isolation MECHANISM is real and works in this
environment; they do not and cannot prove CT104-specific guarantees
(pinned image digest verification, a properly unprivileged production
host) that stay `blocked_external: ct104_unavailable`.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from threading import Event, Timer

import pytest

from app.agent_review.isolated_executor_v2 import (
    EXECUTOR_REASON_CANCELLED_V2,
    EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2,
    EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2,
    EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2,
    EXECUTOR_REASON_TIMEOUT_V2,
    AllowlistedCommandSpecV2,
    _classify_completed_process_v2,
    compute_check_command_inventory_digest_v2,
    execute_trusted_check_plan_v2,
)
from app.agent_review.trusted_checks_v2 import (
    AllowlistedCheckCommandV2,
    TrustedCheckAuthorityV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    bind_trusted_check_result_to_plan_v2,
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
    # authority_suite_digest is a REAL digest of `inventory`, not an
    # arbitrary constant -- execute_trusted_check_plan_v2 now checks this
    # before resolving a single command_token (the fix for the inventory-
    # provenance gap a review of this slice found real), so every test
    # must build a plan whose digest genuinely matches the inventory it
    # will call execute_trusted_check_plan_v2 with, unless the test is
    # SPECIFICALLY exercising a deliberate mismatch.
    return TrustedCheckPlanV2(
        schema_id="agent-review.trusted-check-plan.v2", schema_version=2,
        run_id=RUN, head_sha=HEAD, harness_digest=HARNESS,
        authority_suite_digest=compute_check_command_inventory_digest_v2(inventory),
        checks=list(checks) if checks is not None else [_command()],
    )


def _py(code: str, *, token: str = "token") -> AllowlistedCommandSpecV2:
    return AllowlistedCommandSpecV2(command_token=token, argv=(sys.executable, "-c", code))


@pytest.fixture
def repo_root():
    # pytest's own tmp_path fixture lives several directories deep under a
    # root-owned, mode-0700 pytest-of-root tree -- chmod'ing the leaf
    # alone would not help, since the isolated child is deliberately
    # dropped to an unprivileged uid (`nobody`) BEFORE it ever touches
    # this directory, and real DAC permission checks apply to every
    # ancestor directory for that real uid, regardless of the throwaway
    # user-namespace's internal root mapping. Using a directory created
    # directly under system `/tmp` (mode 1777, world-traversable) and
    # chmod'd 0777 itself sidesteps that without weakening what's being
    # tested -- every test's actual assertion is about the EXECUTOR's
    # verdict logic and isolation primitives, not filesystem permissions.
    path = Path(tempfile.mkdtemp())
    os.chmod(path, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_execute_reports_success_for_a_zero_exit_check(repo_root):
    inventory = {"token": _py("pass")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert len(executed) == 1
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    assert executed[0].result.artifact_sha256 is not None
    bind_trusted_check_result_to_plan_v2(executed[0].result, plan)  # does not raise


def test_execute_reports_failure_for_a_nonzero_exit_check(repo_root):
    inventory = {"token": _py("import sys; sys.exit(1)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE


def test_execute_ignores_forged_stdout_and_report_file_and_trusts_only_the_real_exit_code(repo_root):
    """Proves ONE HALF of the "conftest.py malicioso" scenario issue #201
    names: forging what the check PRINTS or WRITES (stdout, a fake report
    file) does not change the verdict, because neither is ever read for
    it. Does NOT prove (and does not claim to -- see isolated_executor_v2
    .py's own "one thing this module cannot defend against" docstring
    section) that PR-controlled code cannot forge the check's own EXIT
    CODE (e.g. a conftest.py hook calling os._exit(0) unconditionally) --
    that remains open, unresolved, tracked for #201-B3, and MUST NOT be
    treated as closed by this test passing."""
    report_path = repo_root / "forged_report.json"
    code = (
        "import sys, pathlib\n"
        "print('ALL TESTS PASSED')\n"
        f"pathlib.Path({str(report_path)!r}).write_text('{{\"passed\": true}}')\n"
        "sys.exit(1)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE, (
        "a forged stdout banner and a forged report file must not override the real exit code"
    )
    assert report_path.exists(), "the forged report really was written -- the executor just never read it"
    assert "ALL TESTS PASSED" not in (executed[0].result.model_dump_json())


def test_execute_denies_real_outbound_network_access(repo_root):
    """Proven by the SPECIFIC failure mode, not just a nonzero exit: a
    fresh, isolated network namespace has no route at all, so a raw-IP
    connection attempt fails with ENETUNREACH -- a stronger, structural
    signature than "some ambient firewall dropped it" (which ordinary
    egress filtering, unrelated to this module, could also produce as a
    timeout in some environments)."""
    code = (
        "import socket, sys\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('8.8.8.8', 53))\n"
        "    sys.exit(0)\n"
        "except OSError as e:\n"
        "    sys.exit(e.errno if e.errno else 99)\n"
    )
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    artifact = executed[0].result
    assert artifact.outcome is TrustedCheckOutcomeV2.FAILURE
    # returncode -> errno was smuggled through the process's OWN exit code
    # for this test's assertion only (not part of the contract). Re-wraps
    # via the module's OWN _isolation_wrapped_argv_v2 -- whichever real
    # candidate _select_isolation_strategy_v2 actually picked in THIS
    # environment (root-direct / sudo-elevated / unprivileged-userns) --
    # rather than hardcoding one specific unshare invocation that may not
    # be the one this environment's own probe actually selected.
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    wrapped = isolated_executor_module._isolation_wrapped_argv_v2(
        (sys.executable, "-c", code), max_memory_mb=128, max_processes=16,
    )
    assert wrapped is not None, "the module's own probe found no working isolation strategy"
    completed = subprocess.run(wrapped, capture_output=True, timeout=8)
    assert completed.returncode == errno.ENETUNREACH, (
        "expected ENETUNREACH (no route in the isolated namespace), "
        f"got {completed.returncode}"
    )


def test_execute_denies_sudo_inside_the_isolated_check(repo_root):
    code = "import subprocess, sys; r = subprocess.run(['sudo', '-n', 'whoami'], capture_output=True); sys.exit(0 if r.returncode != 0 else 1)"
    inventory = {"token": _py(code)}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS, "sudo must be refused inside the isolated check"


def test_execute_refuses_a_command_token_not_in_the_inventory(repo_root):
    # The inventory itself is real and matches the plan's own digest --
    # isolating this test to the SPECIFIC "token not in inventory" case,
    # not the separate "inventory doesn't match the plan at all" case
    # (see test_execute_refuses_an_inventory_that_does_not_match_the_
    # plans_own_digest below).
    inventory = {"other_token": _py("pass")}
    plan = _plan(inventory=inventory, checks=[_command(command_token="not_in_inventory")])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2
    assert executed[0].result.artifact_sha256 is None


def test_execute_refuses_an_inventory_that_does_not_match_the_plans_own_digest(repo_root):
    """Red-tests the exact provenance gap a review of this slice found
    real: TrustedCheckPlanV2.authority_suite_digest exists specifically
    to pin the host-owned inventory a plan committed to, but nothing
    previously checked that the inventory a CALLER hands to
    execute_trusted_check_plan_v2 is actually that same one. A caller
    could resolve the plan's command_tokens against an entirely
    different (even otherwise-valid) inventory -- e.g. one with a looser
    or malicious command for the same token -- and nothing would catch
    it. Mirrors #211's own target_profile/manifest.identity.profile_hash
    fix for review_content_extraction_v2 exactly."""
    planning_inventory = {"token": _py("import sys; sys.exit(0)")}
    plan = _plan(inventory=planning_inventory)

    divergent_inventory = {"token": _py("import sys; sys.exit(1)")}
    assert (
        compute_check_command_inventory_digest_v2(divergent_inventory)
        != plan.authority_suite_digest
    )

    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=divergent_inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert len(executed) == len(plan.checks)
    for item in executed:
        assert item.result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
        assert item.diagnostic_reason == EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2
        assert item.result.artifact_sha256 is None

    # The SAME (planning) inventory, independently reconstructed, is
    # accepted -- proving the check is a real digest comparison over
    # content, not object identity.
    reconstructed_inventory = {"token": _py("import sys; sys.exit(0)")}
    accepted = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=reconstructed_inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert accepted[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS


def test_execute_never_runs_unisolated_when_isolation_is_unavailable(repo_root, monkeypatch):
    """Fail-closed proof: if NONE of the isolation strategies this module
    probes actually work, it must NEVER silently fall back to running
    the check without isolation -- it must refuse with a typed
    INFRA_FAILURE. Patches the probe itself (not e.g. shutil.which)
    because which candidate strategy is even attempted is environment-
    dependent (this module tries real root, sudo-elevated, and
    unprivileged-userns in order -- see _select_isolation_strategy_v2);
    what must hold regardless of environment is "no working candidate ->
    fail closed", which this proves directly."""
    import app.agent_review.isolated_executor_v2 as isolated_executor_module

    monkeypatch.setattr(isolated_executor_module, "_select_isolation_strategy_v2", lambda: None)
    inventory = {"token": _py("import sys; sys.exit(0)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.INFRA_FAILURE
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2


def test_execute_produces_a_typed_timeout_outcome_for_a_hanging_check(repo_root):
    inventory = {"token": _py("import time; time.sleep(60)")}
    plan = _plan(inventory=inventory, checks=[_command(timeout_seconds=1)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.TIMEOUT
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_TIMEOUT_V2
    assert executed[0].result.artifact_sha256 is None


def test_execute_produces_a_typed_cancelled_outcome_when_cancel_event_is_set(repo_root):
    inventory = {"token": _py("import time; time.sleep(30)")}
    plan = _plan(inventory=inventory, checks=[_command(timeout_seconds=30)])
    cancel_event = Event()
    Timer(0.5, cancel_event.set).start()
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
        cancel_event=cancel_event,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.CANCELLED
    assert executed[0].diagnostic_reason == EXECUTOR_REASON_CANCELLED_V2


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available in this environment")
@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available in this environment")
def test_execute_enforces_the_memory_limit_and_classifies_the_kill_as_failure_not_oom(repo_root):
    """RLIMIT_AS really caps address space -- proven here directly: a C
    program that mallocs past the configured limit and writes to the
    (failed, NULL) allocation is genuinely killed by the kernel
    (SIGSEGV), proving the SAFETY property holds. The typed OUTCOME is
    FAILURE, not OOM: an earlier version of this module guessed OOM from
    exactly this signal, but a review of this slice pointed out that
    signal alone cannot distinguish "killed because RLIMIT_AS was hit"
    from "an unrelated real crash bug" without independent evidence
    (cgroup memory.events) this slice does not implement -- see
    _classify_completed_process_v2's own docstring. Classifying every
    ambiguous signal death as FAILURE (attributable, promotable
    candidate) is the conservative default; it is not proof this
    specific process died FROM the memory limit rather than some other
    signal-raising bug, which is exactly the point -- this module no
    longer claims to know the difference."""
    source = repo_root / "oom.c"
    binary = repo_root / "oom_bin"
    source.write_text(
        "#include <stdlib.h>\n#include <string.h>\n"
        "int main() { char *p = malloc(2000UL*1024*1024); memset(p, 1, 2000UL*1024*1024); return 0; }\n"
    )
    subprocess.run(["gcc", "-O0", "-o", str(binary), str(source)], check=True, capture_output=True)

    inventory = {"token": AllowlistedCommandSpecV2(command_token="token", argv=(str(binary),))}
    plan = _plan(inventory=inventory, checks=[_command(max_memory_mb=64)])
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.FAILURE
    assert executed[0].result.artifact_sha256 is not None, (
        "FAILURE is a resolved outcome -- the captured (empty) stdout/stderr is still a real artifact"
    )


def test_execute_result_is_deterministic_across_two_real_runs(repo_root):
    inventory = {"token": _py("print('deterministic output'); import sys; sys.exit(0)")}
    plan = _plan(inventory=inventory)
    first = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    second = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.TRUSTED,
    )
    assert first[0].result.result_sha256 == second[0].result.result_sha256
    assert first[0].result.artifact_sha256 == second[0].result.artifact_sha256


def test_execute_untrusted_advisory_result_still_refuses_promotion(repo_root):
    """Reuses #201-A's own promotion authority unmodified -- an isolated
    executor result stamped UNTRUSTED_ADVISORY by its caller must still
    never promote, exactly like the #201-B1 simulator's own result."""
    from app.agent_review.trusted_checks_v2 import (
        TrustedCheckPromotionError,
        promote_trusted_check_to_required_v2,
    )

    inventory = {"token": _py("import sys; sys.exit(0)")}
    plan = _plan(inventory=inventory)
    executed = execute_trusted_check_plan_v2(
        plan, repo_root=repo_root, inventory=inventory, authority=TrustedCheckAuthorityV2.UNTRUSTED_ADVISORY,
    )
    assert executed[0].result.outcome is TrustedCheckOutcomeV2.SUCCESS
    with pytest.raises(TrustedCheckPromotionError):
        promote_trusted_check_to_required_v2(executed[0].result)


class TestClassifyCompletedProcessUnit:
    """Pure-function unit tests for the classification rule itself."""

    def test_zero_returncode_is_success(self):
        outcome, reason = _classify_completed_process_v2(returncode=0)
        assert outcome is TrustedCheckOutcomeV2.SUCCESS
        assert reason is None

    def test_positive_returncode_is_failure(self):
        outcome, reason = _classify_completed_process_v2(returncode=1)
        assert outcome is TrustedCheckOutcomeV2.FAILURE

    @pytest.mark.parametrize("signal_number", [9, 11, 7, 6, 15])  # KILL, SEGV, BUS, ABRT, TERM
    def test_every_signal_death_classifies_as_failure_never_oom(self, signal_number):
        # No signal-based OOM guessing (removed -- see this module's own
        # _classify_completed_process_v2 docstring for why: a review of
        # this slice pointed out that a genuine RLIMIT_AS-triggered kill
        # and an unrelated real crash bug are indistinguishable by signal
        # alone without independent evidence this slice does not have).
        # Every signal death -- including the SIGSEGV/SIGBUS/SIGABRT/
        # SIGKILL signature an earlier version treated as OOM -- is now
        # the conservative, attributable FAILURE.
        outcome, reason = _classify_completed_process_v2(returncode=-signal_number)
        assert outcome is TrustedCheckOutcomeV2.FAILURE
        assert reason is None
