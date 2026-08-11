"""Pure tests for the #201-B3 host-owned PID-namespace kernel primitives.

No subprocess anywhere here -- `signal_host_pid_v2` is monkeypatched at the
`os.kill`/`_signal.pidfd_send_signal` boundary, which is exactly the
boundary a review caught a real regression at: a fallback from a failed
`pidfd_send_signal` to a raw ``os.kill(<numeric pid>, ...)`` reintroduced
the pid-reuse hazard this whole module exists to close (see the module's
own docstring: "No value observed FROM INSIDE a PID namespace may reach a
kill path" -- the same principle extends to any value reached only because
a pidfd-based signal failed).
"""

from __future__ import annotations

import app.agent_review.trusted_check_namespace_kernel_v2 as kernel


def test_signal_host_pid_uses_pidfd_and_never_falls_back_to_raw_kill(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(kernel._signal, "pidfd_send_signal", lambda pidfd, sig: calls.append((pidfd, sig)))
    monkeypatch.setattr(kernel.os, "kill", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("os.kill must never be called when a pidfd is provided")
    ))

    kernel.signal_host_pid_v2(4242, pidfd=99)

    assert calls == [(99, kernel._signal.SIGKILL)]


def test_signal_host_pid_does_nothing_when_pidfd_is_absent(monkeypatch):
    """No pidfd means no signal at all -- fail closed, never a raw-PID
    fallback that pid reuse could redirect at an unrelated process."""

    monkeypatch.setattr(kernel.os, "kill", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("os.kill must never be called: this is exactly the pid-reuse hazard being closed")
    ))

    kernel.signal_host_pid_v2(4242, pidfd=None)  # must not raise, must not kill


def test_signal_host_pid_does_not_fall_back_when_pidfd_send_signal_fails(monkeypatch):
    """The regression this test exists to prevent: a failed
    ``pidfd_send_signal`` (e.g. ESRCH because the target already exited)
    must not fall through to a numeric ``os.kill`` -- by the time that
    fallback would run, the numeric pid may already have been recycled by
    an unrelated process, and signalling it would hit that unrelated
    process instead."""

    def _raise(pidfd, sig):
        raise OSError("target already exited")

    monkeypatch.setattr(kernel._signal, "pidfd_send_signal", _raise)
    monkeypatch.setattr(kernel.os, "kill", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("a failed pidfd_send_signal must never fall back to a raw os.kill")
    ))

    kernel.signal_host_pid_v2(4242, pidfd=99)  # must not raise, must not kill


def test_signal_host_pid_refuses_a_non_positive_or_namespace_relative_target(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(kernel._signal, "pidfd_send_signal", lambda pidfd, sig: calls.append((pidfd, sig)))

    kernel.signal_host_pid_v2(1, pidfd=99)
    kernel.signal_host_pid_v2(0, pidfd=99)
    kernel.signal_host_pid_v2(-42, pidfd=99)

    assert calls == [], "assert_killable_host_pid_v2 must refuse these before any signal is sent"


def test_tear_down_execution_signals_both_identity_and_direct_child_via_pidfd(monkeypatch):
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(kernel._signal, "pidfd_send_signal", lambda pidfd, sig: calls.append((pidfd, sig)))

    identity = kernel.ExecutionIdentityV2(
        host_pid=555, start_time="123", pidfd=11, namespace_fd=22, namespace_key=(1, 2),
    )
    kernel.tear_down_execution_v2(identity=identity, direct_child_pid=777, direct_child_pidfd=33)

    assert (11, kernel._signal.SIGKILL) in calls
    assert (33, kernel._signal.SIGKILL) in calls


def test_tear_down_execution_without_a_direct_child_pidfd_does_not_raw_kill(monkeypatch):
    """A caller that could not obtain a pidfd for the direct child (an
    exceptionally narrow race right after spawn) must not cause a fallback
    to a raw-PID kill for that target."""

    monkeypatch.setattr(kernel.os, "kill", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not raw-kill the direct child when no pidfd was obtained for it")
    ))

    kernel.tear_down_execution_v2(identity=None, direct_child_pid=777, direct_child_pidfd=None)
