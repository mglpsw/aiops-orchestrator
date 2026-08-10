"""Isolated executor for AgentReview v2 trusted checks (#201-B2/B3, third
slice of #201, child of the distribution epic #199).

Executes ONE check from an already-built ``TrustedCheckPlanV2`` (#201-A)
as a REAL, isolated subprocess and produces a real, plan-bindable
``TrustedCheckResultV2``.

## What this module is NOT allowed to trust

> O código do HEAD da PR é executado isoladamente como sujeito sob teste. O
> harness, a seleção de checks, os testes de autoridade e o serializador
> vêm da base confiável / host e são pinados por digest. A PR nunca pode
> controlar a evidência que a aprova.

Concretely, this module never lets the subprocess it isolates decide its
own verdict:

- **which command runs** is resolved from ``command_token`` against a
  HOST-supplied ``inventory: Mapping[str, AllowlistedCommandSpecV2]`` --
  never a raw string the plan or any PR-influenced input assembles;
- **the verdict** is derived EXCLUSIVELY from signals the HOST observes
  about the child process via the kernel -- never from anything the
  subject prints or writes;
- **authority itself** (#201-B3) is refused BEFORE the process is even
  created, for any command whose class the subject could control. See
  ``trusted_check_authority_v2``'s own module docstring for the full
  argument: isolating PR-controlled code does not make its output
  authoritative, so ``ExecutionClassV2.SUBJECT_CODE`` (pytest, repository
  scripts, any checkout-authored process) can never back
  ``authority=TRUSTED``, no matter how strong the containment around it
  is. Only ``ExecutionClassV2.DATA_ONLY_HOST_TOOL`` -- a host-owned binary
  consuming the checkout as data, never as code -- is TRUSTED-eligible.

## Process-lifetime containment (#201-B3)

``#201-B2`` supervised a *process group*; a descendant that called
``setsid()`` or double-forked escaped it and could outlive the executor's
return. This module now creates a real PID namespace
(``unshare --pid --fork --kill-child --mount-proc --net``) for every
check: the kernel destroys EVERY process in that namespace unconditionally
the moment its init dies. See ``trusted_check_namespace_kernel_v2`` for the
identity/discovery/teardown primitives, all derived from procfs the CALLER
holds by construction -- never from anything the namespace's own occupant
reports about itself (inside the namespace ``os.getpid()`` returns ``1``,
and ``1`` is exactly the value a privileged kill must never receive).

## Amendment A1 -- the sudo-elevated strategy and the privileged broker

The isolation strategy that actually runs in this project's own GitHub
Actions CI is the sudo-elevated one: the caller is unprivileged, but has
passwordless ``sudo``, and the namespace's own init ends up owned by root.
Under that strategy, this module's HOST-SIDE process cannot itself read
``/proc/<root-owned-pid>/ns/pid`` (``PTRACE_MODE_READ`` denies it) -- so it
cannot perform the discovery/containment-verification this file otherwise
does directly for the root-direct and weak-userns strategies.

The fix is not to weaken discovery (never trust the namespace's own
occupant) and not to demote sudo-elevated to weak/advisory-only (it has
full uid separation; demoting it would silently degrade every CI run to
non-authoritative checks). Instead, ``trusted_check_broker_v2`` -- a small,
host-owned, stdlib-only process launched via ``sudo -n python -I
<broker>`` -- performs EXACTLY the same discovery/containment/teardown
this file does for root-direct, because it genuinely IS root. It answers
only a closed, enumerated protocol back to this (unprivileged) host: never
a PID, never a signal target this host could redirect. See that module's
own docstring for the full design, including how a broker CRASH is
contained by the kernel (``PR_SET_PDEATHSIG``), not by broker code that
would not get to run.

## OOM is not classified -- named as a limitation, not guessed at

``RLIMIT_AS`` is real and DOES cap a check's address space -- the safety
property holds regardless of what typed outcome a killed check gets.
Precisely typing a memory-exhaustion kill as ``OOM`` would require
independent evidence (e.g. cgroup ``memory.events``' own ``oom_kill``
counter) this module does not implement; every signal death classifies as
``FAILURE`` (attributable, a promotable candidate for
``DATA_ONLY_HOST_TOOL``), the conservative default, rather than guessing
``OOM`` and risking a real regression being silently reclassified as
environmental.

## What is proven here, and what is `blocked_external: ct104_unavailable`

This module's isolation is built from portable Linux primitives and is
REAL, working, and adversarially tested in whatever Linux host actually
runs this test suite. It is explicitly **not** CT104:

**How the namespace is created is PROBED for real, never guessed from
euid alone.** ``_select_isolation_strategy_v2`` tries, in order,
real-root-direct, passwordless-``sudo``-elevated, then
unprivileged-user-namespace -- and only uses whichever candidate a live
``<prefix> -- true`` invocation actually exits ``0`` for. None of the
three succeeding means fail-closed
(``EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2``), never a fourth guess and
never running unisolated.

- this module has no way to assert it is running ON the project's real,
  pinned CT104 runner -- that identity is a deployment fact asserted by
  whoever invokes it, never self-detected here;
- ``harness_digest`` (``TrustedCheckPlanV2``, #201-A) is threaded through
  and echoed into the result unmodified, but this module does not
  independently verify it against a real, running, digest-pinned
  container image. That verification is `blocked_external:
  ct104_unavailable`, not silently skipped or assumed true;
- a development sandbox that happens to run this test suite as root with
  broad kernel capabilities is a WEAKER starting point than a properly
  hardened, unprivileged CT104 runner -- isolation demonstrated here
  proves the MECHANISM is real, not that it would resist a determined
  container-escape attempt under CT104's own, materially different threat
  model.

See ``docs/checkpoints/AGENT_REVIEW_V2_201B2_ISOLATED_EXECUTOR.md`` for the
#201-B2 acceptance-criteria mapping this module preserves, and
``docs/checkpoints/AGENT_REVIEW_V2_201B3_ADVERSARIAL_HARDENING.md`` for
#201-B3's.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Mapping

from pydantic import model_validator

from app.agent_review.contracts_v2 import ContractV2Model, SafeIdentifier
from app.agent_review.redaction import sanitize_artifact_value
from app.agent_review.trusted_check_authority_v2 import (
    STATE_FAILED_V2,
    STATE_UNKNOWN_V2,
    STATE_VERIFIED_V2,
    ExecutionAssessmentV2,
    ExecutionClassV2,
    ExecutionClassValueV2,
    classify_command_spec_v2,
    compute_promotion_eligibility_v2,
    coverage_authority_for_class_v2,
    trusted_refusal_reason_v2,
    workload_authority_for_class_v2,
)
from app.agent_review.trusted_check_broker_v2 import BROKER_PATH_V2, BROKER_PROTOCOL_V2
from app.agent_review.trusted_check_namespace_kernel_v2 import (
    NAMESPACE_FLAGS_V2,
    PIDFD_AVAILABLE_V2,
    ExecutionIdentityV2,
    discover_namespace_leader_v2,
    is_descendant_of_v2,
    tear_down_execution_v2,
    verify_containment_v2,
)
from app.agent_review.trusted_check_stream_capture_v2 import (
    CapturedStreamV2,
    finish_drain_v2,
    start_drain_v2,
)
from app.agent_review.trusted_check_supervisor_v2 import (
    MAX_MESSAGE_BYTES_V2,
    SUPERVISOR_PATH_V2,
    SUPERVISOR_PROTOCOL_V2,
    canonical_json_bytes_v2 as supervisor_canonical_json_bytes_v2,
    strict_json_loads_v2,
)
from app.agent_review.trusted_checks_v2 import (
    TrustedCheckAuthorityV2,
    TrustedCheckAuthorityValueV2,
    TrustedCheckOutcomeV2,
    TrustedCheckPlanV2,
    TrustedCheckResultMaterialV2,
    TrustedCheckResultV2,
    compute_trusted_check_result_sha256_v2,
)

EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2 = "isolated_executor_command_not_in_inventory"
EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2 = "isolated_executor_isolation_unavailable"
EXECUTOR_REASON_SETUP_FAILED_V2 = "isolated_executor_setup_failed"
EXECUTOR_REASON_CANCELLED_V2 = "isolated_executor_cancelled"
EXECUTOR_REASON_TIMEOUT_V2 = "isolated_executor_timeout"
EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2 = "isolated_executor_inventory_digest_mismatch"
EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2 = "isolated_executor_inventory_key_token_mismatch"
EXECUTOR_REASON_ISOLATION_TOO_WEAK_FOR_TRUSTED_V2 = "isolated_executor_isolation_too_weak_for_trusted"
EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2 = "isolated_executor_protocol_channel_invalid"
EXECUTOR_REASON_PID_NAMESPACE_UNAVAILABLE_V2 = "isolated_executor_pid_namespace_unavailable"
EXECUTOR_REASON_PIDFD_UNAVAILABLE_V2 = "isolated_executor_pidfd_unavailable"
EXECUTOR_REASON_HOST_PID_IDENTITY_INVALID_V2 = "isolated_executor_subject_identity_invalid"
EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2 = "isolated_executor_process_containment_failed"

_TIMEOUT_POLL_INTERVAL_SECONDS_V2 = 0.05
_COMMUNICATE_GRACE_SECONDS_V2 = 5.0
_HELLO_WAIT_SECONDS_V2 = 10.0
_RECORD_WAIT_SECONDS_V2 = 10.0
_BROKER_READY_WAIT_SECONDS_V2 = 15.0
_BROKER_TEARDOWN_WAIT_SECONDS_V2 = 10.0
_BROKER_RESULT_WAIT_SECONDS_V2 = 10.0
_DISCOVERY_WAIT_SECONDS_V2 = 10.0

# Mirrors trusted_checks_v2._RESOLVED_OUTCOMES_V2 (module-private there,
# so re-declared here rather than reaching into that module's own
# private constant) -- the only two outcomes TrustedCheckResultMaterialV2
# allows an artifact_sha256 for.
_RESOLVED_OUTCOMES_LOCAL_V2 = frozenset({TrustedCheckOutcomeV2.SUCCESS, TrustedCheckOutcomeV2.FAILURE})


class AllowlistedCommandSpecV2(ContractV2Model):
    """One entry in a HOST-owned command inventory. ``argv`` is a fixed,
    non-empty tuple of real argv tokens -- never text assembled from the
    plan, from PR-controlled input, or from any model output. A
    ``TrustedCheckPlanV2`` (#201-A) never carries argv at all, only a
    ``command_token``; resolving that token against THIS inventory is the
    only way an actual command comes to exist.

    Being HOST/target-repo-owned is a provenance claim this class alone
    cannot prove -- ``execute_trusted_check_plan_v2`` closes HALF of that
    gap by checking ``compute_check_command_inventory_digest_v2(inventory)
    == plan.authority_suite_digest`` before ever resolving a single token.

    Three additive fields (#201-B3) carry the AUTHORITY classification this
    entry claims. They are host-owned declarations covered by the inventory
    digest, and a declaration is deliberately not treated as a proof --
    ``classify_command_spec_v2`` downgrades a ``data_only_host_tool`` claim
    whenever the entry also admits checkout influence. ``execution_class``
    defaults to ``UNKNOWN`` so an inventory that predates this field is
    refused for ``TRUSTED`` rather than silently inheriting it."""

    command_token: SafeIdentifier
    argv: tuple[str, ...]
    execution_class: ExecutionClassValueV2 = ExecutionClassV2.UNKNOWN
    # Necessary (never sufficient) for `data_only_host_tool`: the tool's own
    # configuration must come from the host, not from the checkout, or the
    # subject gets to choose what "pass" means.
    host_owned_config: bool = False
    # A tool that loads plugins from the checkout executes checkout code by
    # definition, whatever the inventory calls it.
    loads_checkout_plugins: bool = False

    @model_validator(mode="after")
    def validate_argv(self) -> AllowlistedCommandSpecV2:
        if not self.argv:
            raise ValueError("argv must not be empty")
        return self


def _canonical_json_bytes_v2(value: object) -> bytes:
    # Same canonical-JSON discipline used throughout contracts_v2.py,
    # manifest_v2.py, review_content_v2.py, and trusted_checks_v2.py.
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def compute_check_command_inventory_digest_v2(
    inventory: Mapping[str, AllowlistedCommandSpecV2],
) -> str:
    """Canonical sha256 of an entire ``inventory``: ``{command_token: argv +
    authority fields}`` for every entry, sorted by token, hashed the same way
    every other digest in this codebase is computed."""

    entries = {token: spec.model_dump(mode="json") for token, spec in inventory.items()}
    return hashlib.sha256(_canonical_json_bytes_v2(entries)).hexdigest()


def compute_command_spec_digest_v2(spec: AllowlistedCommandSpecV2) -> str:
    """Canonical digest of ONE inventory entry, binding a supervisor/broker
    record to the exact command this execution authorised -- so a record
    carrying the right nonce but answering a different spec is still
    refused."""

    return hashlib.sha256(_canonical_json_bytes_v2(spec.model_dump(mode="json"))).hexdigest()


class IsolatedExecutorError(RuntimeError):
    """Raised only for a setup-time failure this module refuses to paper
    over by silently degrading (e.g. isolation unavailable). Callers that
    want a typed, non-raising ``INFRA_FAILURE`` result instead of an
    exception should use ``execute_trusted_check_plan_v2``, which catches
    this and converts it -- this exception exists for lower-level direct
    callers of ``_run_isolated_v2``."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ExecutedCheckV2:
    """A ``TrustedCheckResultV2`` plus non-authoritative diagnostics.
    ``diagnostic_reason`` is NEVER part of the hashed contract material
    (``TrustedCheckResultMaterialV2`` has no such field) -- it exists
    purely for logs/tests to assert WHY an environmental outcome
    happened, without smuggling free text into the frozen result.

    ``assessment`` (#201-B3) is the vectorial internal assessment, on the
    same footing: diagnostic, never hashed. It exists so the executor never
    has to collapse independent facts into one scalar -- a check can have
    FAILED on product grounds while ALSO having failed containment, and
    neither may erase the other."""

    result: TrustedCheckResultV2
    diagnostic_reason: str | None
    assessment: ExecutionAssessmentV2 = ExecutionAssessmentV2()


def _isolation_prefix_candidates_v2() -> tuple[tuple[bool, tuple[str, ...]], ...]:
    """Ordered ``(drop_to_nobody_inside, prefix_argv)`` candidates, most to
    least preferred.

    - **real root, direct**: real root has ``CAP_SYS_ADMIN`` and needs no
      unprivileged user namespace at all;
    - **elevate via passwordless sudo**: for a caller that starts
      unprivileged but has passwordless sudo (this project's own GitHub
      Actions runner). Identity/containment for this candidate is handled
      by ``trusted_check_broker_v2`` (amendment A1) -- see the module
      docstring;
    - **unprivileged user namespace**: the last resort. No uid separation
      from the caller at all -- refused for ``TRUSTED`` in
      ``_isolation_wrapped_argv_v2``.

    This is a PROBED chain, not guessed from euid alone -- this project's
    own GitHub Actions runner is neither real root nor able to create an
    unprivileged user namespace by default, observed directly, not assumed."""

    if os.geteuid() == 0:
        return ((True, ("unshare", *NAMESPACE_FLAGS_V2, "--")),)
    candidates: list[tuple[bool, tuple[str, ...]]] = []
    if shutil.which("sudo") is not None:
        candidates.append((True, ("sudo", "-n")))
    candidates.append((False, ("unshare", "--user", "--map-root-user", *NAMESPACE_FLAGS_V2, "--")))
    return tuple(candidates)


def _probe_prefix_works_v2(prefix: tuple[str, ...]) -> bool:
    if prefix and prefix[0] == "sudo":
        probe = [*prefix, "true"]
    else:
        probe = [*prefix, "true"]
    try:
        completed = subprocess.run(probe, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _select_isolation_strategy_v2() -> tuple[bool, tuple[str, ...]] | None:
    """Actually TESTS each candidate rather than guessing from euid alone.
    Returns ``None`` if nothing works -- the caller must fail closed, never
    fall back to running unisolated."""

    for drop_to_nobody, prefix in _isolation_prefix_candidates_v2():
        if _probe_prefix_works_v2(prefix):
            return drop_to_nobody, prefix
    return None


def _isolation_wrapped_argv_v2(
    *, authority: TrustedCheckAuthorityV2,
) -> tuple[list[str], bool, bool] | None:
    """Returns ``(wrapped_argv, uses_broker, drop_to_nobody)``, or ``None``
    if ``_select_isolation_strategy_v2`` finds nothing that actually works,
    or if the only strategy that DID work is the unprivileged-userns
    fallback and ``authority`` is ``TRUSTED`` (that fallback has no real uid
    separation from the host caller, and #201-A treats ``TRUSTED`` as the
    sole authority a promotable ``RequiredCheckResultV2`` can come from).

    ``uses_broker`` tells the caller whether this execution must go through
    ``trusted_check_broker_v2`` (amendment A1): the sudo-elevated candidate
    is real strong isolation, but this host process cannot itself perform
    discovery/containment against a root-owned namespace, so the ENTIRE
    execution -- not just privilege drop -- is delegated to a small,
    host-owned, root-privileged broker rather than wrapping ``argv``
    directly here.

    For the root-direct and weak-userns candidates, ``wrapped_argv`` points
    directly at ``trusted_check_supervisor_v2.py``, launched by absolute
    path under ``-I`` -- never ``-m`` and never an inline ``-c`` string:
    with ``-m`` the cwd (the PR checkout) would be searched for importable
    packages, and a PR shipping its own
    ``app/agent_review/trusted_check_supervisor_v2.py`` could then hijack
    the very process meant to supervise it. ``argv`` of the check itself is
    never part of this command line either way -- it travels over the
    private channel, established after this function returns."""

    strategy = _select_isolation_strategy_v2()
    if strategy is None:
        return None
    drop_to_nobody, prefix = strategy
    if not drop_to_nobody and authority is TrustedCheckAuthorityV2.TRUSTED:
        return None
    uses_broker = bool(prefix) and prefix[0] == "sudo"
    if uses_broker:
        return ["sudo", "-n", sys.executable, "-I", str(BROKER_PATH_V2)], True, drop_to_nobody
    return [*prefix, sys.executable, "-I", str(SUPERVISOR_PATH_V2)], False, drop_to_nobody


def _classify_completed_process_v2(*, returncode: int) -> tuple[TrustedCheckOutcomeV2, str | None]:
    """``returncode == 0`` -> ``SUCCESS``; anything else (a positive exit
    code, OR a signal death) -> ``FAILURE``, never ``OOM`` -- see the module
    docstring's "OOM is not classified" section."""

    if returncode == 0:
        return TrustedCheckOutcomeV2.SUCCESS, None
    return TrustedCheckOutcomeV2.FAILURE, None


def _build_result_v2(
    *,
    plan: TrustedCheckPlanV2,
    check_name: str,
    authority: TrustedCheckAuthorityV2,
    outcome: TrustedCheckOutcomeV2,
    artifact_payload: dict[str, object] | None,
) -> TrustedCheckResultV2:
    artifact_sha256 = None
    if artifact_payload is not None:
        sanitized = sanitize_artifact_value(artifact_payload)
        canonical = json.dumps(
            sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        artifact_sha256 = hashlib.sha256(canonical).hexdigest()

    material = TrustedCheckResultMaterialV2(
        schema_id="agent-review.trusted-check-result.v2",
        schema_version=2,
        run_id=plan.run_id,
        head_sha=plan.head_sha,
        check_name=check_name,
        authority=authority,
        outcome=outcome,
        harness_digest=plan.harness_digest,
        artifact_sha256=artifact_sha256,
    )
    return TrustedCheckResultV2(
        **material.model_dump(mode="json"),
        result_sha256=compute_trusted_check_result_sha256_v2(material),
    )


def _recv_record_v2(channel: socket.socket, *, timeout: float) -> dict | None:
    """One bounded, strictly-parsed message off a private channel.
    ``SOCK_SEQPACKET`` preserves message boundaries, so a record is exactly
    one ``recv``. Returns ``None`` on timeout, malformed JSON, a duplicate
    key, or an oversized datagram: every one of those is fail-closed for the
    caller, never a partially trusted document."""

    channel.settimeout(timeout)
    try:
        payload = channel.recv(MAX_MESSAGE_BYTES_V2)
    except (socket.timeout, OSError):
        return None
    if not payload:
        return None
    try:
        record = strict_json_loads_v2(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _record_is_bound_v2(record: dict | None, *, kind: str, nonce: str) -> bool:
    """Every record must name a protocol this executor speaks AND carry this
    execution's own nonce. The nonce is what makes a replayed record from
    another run (or another check in the same run) inert rather than merely
    unlikely."""

    return (
        record is not None
        and record.get("kind") == kind
        and record.get("protocol") in (SUPERVISOR_PROTOCOL_V2, BROKER_PROTOCOL_V2)
        and record.get("nonce") == nonce
    )


def _assess_v2(
    *,
    classification=None,
    inventory_binding: str = STATE_UNKNOWN_V2,
    product_outcome: str = STATE_UNKNOWN_V2,
    contained: bool | None = None,
    executed: bool = False,
    reasons: tuple[str, ...] = (),
) -> ExecutionAssessmentV2:
    """Assemble the vectorial assessment and derive ``promotion_eligibility``
    from the whole conjunction rather than from any single axis.

    Axes this slice cannot yet establish stay ``unknown`` on purpose --
    ``compute_promotion_eligibility_v2`` treats an unknown premise as a false
    conjunct, so an axis that is not yet proven can only ever make a result
    LESS promotable, never more."""

    effective_class = (
        classification.effective_class if classification is not None else ExecutionClassV2.UNKNOWN
    )
    containment = STATE_UNKNOWN_V2
    if contained is not None:
        containment = STATE_VERIFIED_V2 if contained else STATE_FAILED_V2
    # These are established by the act of executing at all: a validated
    # record arrived over the private channel (protocol), behind a harness
    # the subject could not substitute (harness), against an execution
    # identity the host itself discovered and pinned (subject identity),
    # under enforced rlimits and a bounded output drain (resource boundary).
    proven = STATE_VERIFIED_V2 if executed else STATE_UNKNOWN_V2
    assessment = ExecutionAssessmentV2(
        subject_identity=proven,
        inventory_binding=inventory_binding,
        execution_class=effective_class.value,
        workload_authority=workload_authority_for_class_v2(effective_class),
        harness_integrity=proven,
        protocol_integrity=proven,
        containment=containment,
        resource_boundary=proven,
        coverage_authority=coverage_authority_for_class_v2(effective_class),
        product_outcome=product_outcome,
        diagnostic_reasons=reasons,
    )
    return replace(
        assessment, promotion_eligibility=compute_promotion_eligibility_v2(assessment)
    )


def _run_isolated_v2(
    *,
    argv: tuple[str, ...],
    repo_root: Path,
    timeout_seconds: int,
    max_memory_mb: int,
    max_processes: int,
    cancel_event: Event | None,
    authority: TrustedCheckAuthorityV2,
    spec_digest: str,
) -> tuple[TrustedCheckOutcomeV2, str | None, dict[str, object] | None, bool]:
    """Runs ``argv`` isolated and returns ``(outcome, diagnostic_reason,
    artifact_payload, contained)``. Raises ``IsolatedExecutorError`` only for
    a setup-time failure (never for the CHECK'S OWN failure/timeout, which
    are typed outcomes, not exceptions).

    Branches on ``uses_broker`` (amendment A1): the sudo-elevated strategy
    delegates the entire execution -- discovery, wait, containment
    verification, teardown -- to ``trusted_check_broker_v2``, because this
    host process cannot itself read a root-owned namespace's identity. The
    root-direct and weak-userns strategies are handled directly here,
    unchanged from the non-broker design."""

    if not PIDFD_AVAILABLE_V2:
        # No silent fallback to signalling a bare pid: pid reuse can redirect
        # that at an unrelated process, and containment we cannot prove is
        # not containment.
        raise IsolatedExecutorError(EXECUTOR_REASON_PIDFD_UNAVAILABLE_V2)

    wrapped_and_strategy = _isolation_wrapped_argv_v2(authority=authority)
    if wrapped_and_strategy is None:
        strategy = _select_isolation_strategy_v2()
        if strategy is not None and not strategy[0] and authority is TrustedCheckAuthorityV2.TRUSTED:
            raise IsolatedExecutorError(EXECUTOR_REASON_ISOLATION_TOO_WEAK_FOR_TRUSTED_V2)
        raise IsolatedExecutorError(EXECUTOR_REASON_ISOLATION_UNAVAILABLE_V2)
    wrapped, uses_broker, drop_to_nobody = wrapped_and_strategy

    if uses_broker:
        return _run_isolated_via_broker_v2(
            wrapped=wrapped, argv=argv, repo_root=repo_root, timeout_seconds=timeout_seconds,
            max_memory_mb=max_memory_mb, max_processes=max_processes, cancel_event=cancel_event,
            spec_digest=spec_digest,
        )
    return _run_isolated_direct_v2(
        wrapped=wrapped, argv=argv, repo_root=repo_root, timeout_seconds=timeout_seconds,
        max_memory_mb=max_memory_mb, max_processes=max_processes, cancel_event=cancel_event,
        spec_digest=spec_digest, drop_to_nobody=drop_to_nobody,
    )


def _run_isolated_direct_v2(
    *, wrapped: list[str], argv: tuple[str, ...], repo_root: Path, timeout_seconds: int,
    max_memory_mb: int, max_processes: int, cancel_event: Event | None, spec_digest: str,
    drop_to_nobody: bool,
) -> tuple[TrustedCheckOutcomeV2, str | None, dict[str, object] | None, bool]:
    """Root-direct or weak-userns: this host process performs discovery and
    containment verification directly, using ``trusted_check_namespace_
    kernel_v2`` exactly as ``trusted_check_broker_v2`` does internally for
    the sudo-elevated strategy -- same code, different caller identity."""

    nonce = secrets.token_hex(32)
    identity: ExecutionIdentityV2 | None = None
    contained = False
    host_channel, child_channel = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    record = None
    try:
        try:
            process = subprocess.Popen(
                wrapped,
                cwd=str(repo_root),
                stdin=child_channel.fileno(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc
        finally:
            child_channel.close()

        drain_state, drain_threads = start_drain_v2(process)

        try:
            host_channel.send(supervisor_canonical_json_bytes_v2({
                "kind": "config",
                "protocol": SUPERVISOR_PROTOCOL_V2,
                "nonce": nonce,
                "spec_digest": spec_digest,
                "argv": list(argv),
                "cwd": str(repo_root),
                "max_memory_mb": max_memory_mb,
                "max_processes": max_processes,
                "drop_to_nobody": drop_to_nobody,
            }))
        except OSError as exc:
            raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc

        hello = _recv_record_v2(host_channel, timeout=_HELLO_WAIT_SECONDS_V2)
        if _record_is_bound_v2(hello, kind="hello", nonce=nonce):
            # The supervisor is now BLOCKED waiting for our `ready`, so the
            # namespace leader is guaranteed alive for the whole of discovery
            # -- the identity cannot be lost to a fast-finishing subject.
            identity = discover_namespace_leader_v2(
                process.pid, deadline=time.monotonic() + _DISCOVERY_WAIT_SECONDS_V2
            )
            if identity is not None and not is_descendant_of_v2(identity.host_pid, process.pid):
                identity.close()  # pragma: no cover - defensive
                identity = None  # pragma: no cover - defensive

        if identity is None:
            # Fail closed BEFORE the subject runs: an execution whose
            # containment we could not establish must not execute at all.
            tear_down_execution_v2(identity=None, direct_child_pid=process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass
            return (
                TrustedCheckOutcomeV2.INFRA_FAILURE,
                EXECUTOR_REASON_HOST_PID_IDENTITY_INVALID_V2,
                None,
                False,
            )

        try:
            host_channel.send(supervisor_canonical_json_bytes_v2({
                "kind": "ready", "protocol": SUPERVISOR_PROTOCOL_V2, "nonce": nonce,
            }))
        except OSError as exc:
            raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc

        deadline = time.monotonic() + timeout_seconds
        cancelled = False
        timed_out = False
        while True:
            returncode = process.poll()
            if returncode is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_TIMEOUT_POLL_INTERVAL_SECONDS_V2)

        if cancelled or timed_out:
            tear_down_execution_v2(identity=identity, direct_child_pid=process.pid)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive only
                pass
            contained = verify_containment_v2(identity)
            if cancelled:
                outcome = TrustedCheckOutcomeV2.CANCELLED
                reason = EXECUTOR_REASON_CANCELLED_V2
            else:
                outcome = TrustedCheckOutcomeV2.TIMEOUT
                reason = EXECUTOR_REASON_TIMEOUT_V2
            # An environmental outcome is already non-promotable, so a
            # containment failure here is reported WITHOUT overwriting it --
            # neither fact is allowed to erase the other.
            if not contained:
                reason = EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2
            return outcome, reason, None, contained

        # The supervisor has exited, but a DESCENDANT could still hold the
        # inherited pipes open, so the drain is bounded in TIME as well as in
        # BYTES, and started at spawn time (not here) to avoid a
        # self-inflicted deadlock -- see trusted_check_stream_capture_v2's
        # own module docstring.
        record = _recv_record_v2(host_channel, timeout=_RECORD_WAIT_SECONDS_V2)
        stdout_capture, stderr_capture = finish_drain_v2(drain_state, drain_threads)
        try:
            process.wait(timeout=_COMMUNICATE_GRACE_SECONDS_V2)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            tear_down_execution_v2(identity=identity, direct_child_pid=process.pid)
        contained = verify_containment_v2(identity)
    finally:
        host_channel.close()
        if identity is not None:
            identity.close()

    return _classify_from_record_v2(
        record=record, nonce=nonce, spec_digest=spec_digest, contained=contained,
        stdout_capture=stdout_capture if record is not None else None,
        stderr_capture=stderr_capture if record is not None else None,
    )


def _classify_from_record_v2(
    *, record: dict | None, nonce: str, spec_digest: str, contained: bool,
    stdout_capture: CapturedStreamV2 | None, stderr_capture: CapturedStreamV2 | None,
) -> tuple[TrustedCheckOutcomeV2, str | None, dict[str, object] | None, bool]:
    """Shared tail: turn a validated `result` record plus a containment
    verdict into the executor's typed outcome. Used by both the direct and
    broker paths so their classification logic cannot drift apart."""

    if not _record_is_bound_v2(record, kind="result", nonce=nonce):
        return (
            TrustedCheckOutcomeV2.INFRA_FAILURE,
            EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2,
            None,
            contained,
        )
    if record.get("spec_digest") != spec_digest:
        # A record that answers a DIFFERENT command than the one this
        # execution authorised, even with the right nonce.
        return (
            TrustedCheckOutcomeV2.INFRA_FAILURE,
            EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2,
            None,
            contained,
        )
    exit_status = record.get("exit_status")
    if not isinstance(exit_status, int) or isinstance(exit_status, bool):
        return (
            TrustedCheckOutcomeV2.INFRA_FAILURE,
            EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2,
            None,
            contained,
        )

    outcome, reason = _classify_completed_process_v2(returncode=exit_status)
    if not contained:
        # Asymmetric on purpose. A SUCCESS whose cleanup cannot be proven
        # must not stand. A FAILURE is NOT rewritten: turning a real product
        # regression into an environmental outcome is the exact inversion
        # issue #201 forbids, and it buys nothing since a FAILURE already
        # blocks readiness.
        if outcome is TrustedCheckOutcomeV2.SUCCESS:
            return (
                TrustedCheckOutcomeV2.INFRA_FAILURE,
                EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2,
                None,
                False,
            )
        reason = EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2

    artifact_payload = None
    if outcome in _RESOLVED_OUTCOMES_LOCAL_V2:
        stdout_capture = stdout_capture or CapturedStreamV2(b"", 0, hashlib.sha256().hexdigest(), False)
        stderr_capture = stderr_capture or CapturedStreamV2(b"", 0, hashlib.sha256().hexdigest(), False)
        artifact_payload = {
            "returncode": exit_status,
            "stdout": stdout_capture.excerpt.decode("utf-8", "replace"),
            "stderr": stderr_capture.excerpt.decode("utf-8", "replace"),
            "stdout_bytes": stdout_capture.total_bytes,
            "stderr_bytes": stderr_capture.total_bytes,
            "stdout_sha256": stdout_capture.sha256,
            "stderr_sha256": stderr_capture.sha256,
            "stdout_truncated": stdout_capture.truncated,
            "stderr_truncated": stderr_capture.truncated,
        }
    return outcome, reason, artifact_payload, contained


def _run_isolated_via_broker_v2(
    *, wrapped: list[str], argv: tuple[str, ...], repo_root: Path, timeout_seconds: int,
    max_memory_mb: int, max_processes: int, cancel_event: Event | None, spec_digest: str,
) -> tuple[TrustedCheckOutcomeV2, str | None, dict[str, object] | None, bool]:
    """Amendment A1: delegate the whole execution to
    ``trusted_check_broker_v2``. This host process never learns a PID from
    the broker's protocol and never needs to -- every privileged action
    happens inside the broker, which holds the real kernel identity by
    having performed the discovery itself, as root."""

    nonce = secrets.token_hex(32)
    host_channel, child_channel = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    record = None
    contained = False
    try:
        try:
            process = subprocess.Popen(
                wrapped, cwd=str(repo_root), stdin=child_channel.fileno(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
        except OSError as exc:
            raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc
        finally:
            child_channel.close()

        try:
            host_channel.send(supervisor_canonical_json_bytes_v2({
                "kind": "config",
                "protocol": BROKER_PROTOCOL_V2,
                "nonce": nonce,
                "spec_digest": spec_digest,
                "argv": list(argv),
                "cwd": str(repo_root),
                "max_memory_mb": max_memory_mb,
                "max_processes": max_processes,
            }))
        except OSError as exc:
            raise IsolatedExecutorError(EXECUTOR_REASON_SETUP_FAILED_V2) from exc

        ready = _recv_record_v2(host_channel, timeout=_BROKER_READY_WAIT_SECONDS_V2)
        if not _record_is_bound_v2(ready, kind="ready", nonce=nonce):
            # The broker never established identity (or never even started
            # cleanly) -- fail closed before trusting anything it might send
            # later. Best-effort kill; the broker's own crash-containment
            # (PR_SET_PDEATHSIG on its child) does not depend on this.
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - defensive
                pass
            return (
                TrustedCheckOutcomeV2.INFRA_FAILURE,
                EXECUTOR_REASON_PROTOCOL_CHANNEL_INVALID_V2,
                None,
                False,
            )

        deadline = time.monotonic() + timeout_seconds
        cancelled = False
        timed_out = False
        while True:
            if process.poll() is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_TIMEOUT_POLL_INTERVAL_SECONDS_V2)

        if cancelled or timed_out:
            try:
                host_channel.send(supervisor_canonical_json_bytes_v2({
                    "kind": "cancel", "protocol": BROKER_PROTOCOL_V2, "nonce": nonce,
                }))
            except OSError:  # pragma: no cover - broker already gone
                pass
            teardown_record = _recv_record_v2(host_channel, timeout=_BROKER_TEARDOWN_WAIT_SECONDS_V2)
            if _record_is_bound_v2(teardown_record, kind="result", nonce=nonce):
                contained = bool(teardown_record.get("contained") is True)
            else:
                # The broker did not (or could not) confirm containment.
                # Containment itself is NOT in doubt -- PR_SET_PDEATHSIG on
                # the broker's own child guarantees it kernel-side even if
                # the broker cannot report back -- but this host has no way
                # to VERIFY that without the broker's cooperation, and an
                # unverifiable claim is reported as such, not assumed true.
                contained = False
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):  # pragma: no cover - defensive
                pass
            outcome = TrustedCheckOutcomeV2.CANCELLED if cancelled else TrustedCheckOutcomeV2.TIMEOUT
            reason = EXECUTOR_REASON_CANCELLED_V2 if cancelled else EXECUTOR_REASON_TIMEOUT_V2
            if not contained:
                reason = EXECUTOR_REASON_PROCESS_CONTAINMENT_FAILED_V2
            return outcome, reason, None, contained

        record = _recv_record_v2(host_channel, timeout=_BROKER_RESULT_WAIT_SECONDS_V2)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            pass
        if _record_is_bound_v2(record, kind="result", nonce=nonce):
            contained = bool(record.get("contained") is True)
    finally:
        host_channel.close()

    stdout_capture = stderr_capture = None
    if isinstance(record, dict):
        stdout_capture = CapturedStreamV2(
            excerpt=b"",  # See the broker's own module docstring: excerpt
            # content is not relayed through this path, only integrity
            # (bytes/sha256/truncated) -- diagnostic text only, never
            # authoritative, and explicitly out of this amendment's scope.
            total_bytes=int(record.get("stdout_bytes") or 0),
            sha256=str(record.get("stdout_sha256") or hashlib.sha256().hexdigest()),
            truncated=bool(record.get("stdout_truncated")),
        )
        stderr_capture = CapturedStreamV2(
            excerpt=b"",
            total_bytes=int(record.get("stderr_bytes") or 0),
            sha256=str(record.get("stderr_sha256") or hashlib.sha256().hexdigest()),
            truncated=bool(record.get("stderr_truncated")),
        )

    return _classify_from_record_v2(
        record=record, nonce=nonce, spec_digest=spec_digest, contained=contained,
        stdout_capture=stdout_capture, stderr_capture=stderr_capture,
    )


def _inventory_keys_match_command_tokens_v2(
    inventory: Mapping[str, AllowlistedCommandSpecV2],
) -> bool:
    return all(token == spec.command_token for token, spec in inventory.items())


def _refuse_whole_plan_v2(
    plan: TrustedCheckPlanV2, *, authority: TrustedCheckAuthorityV2, reason: str
) -> tuple[ExecutedCheckV2, ...]:
    """A structural problem with ``inventory`` itself (not any one
    check) refuses the WHOLE plan: every check gets a typed
    ``INFRA_FAILURE`` with the same ``reason``, none attempted."""

    return tuple(
        ExecutedCheckV2(
            result=_build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            ),
            diagnostic_reason=reason,
            assessment=_assess_v2(reasons=(reason,)),
        )
        for check in plan.checks
    )


def execute_trusted_check_plan_v2(
    plan: TrustedCheckPlanV2,
    *,
    repo_root: Path,
    inventory: Mapping[str, AllowlistedCommandSpecV2],
    authority: TrustedCheckAuthorityValueV2,
    cancel_event: Event | None = None,
) -> tuple[ExecutedCheckV2, ...]:
    """Executes every check in ``plan.checks`` in order, isolated, and
    returns one ``ExecutedCheckV2`` per check (never fewer). ``authority`` is
    required with no default: calling this function with ``TRUSTED`` is the
    caller asserting ITS OWN deployment context actually is host-controlled
    -- this module does not infer or upgrade authority on the caller's
    behalf.

    ``inventory`` must be HOST/target-repo-owned. This is checked, not
    merely asserted: ``compute_check_command_inventory_digest_v2(inventory)``
    must equal ``plan.authority_suite_digest`` before a single
    ``command_token`` is resolved -- a mismatch refuses the WHOLE plan.

    Before any process exists, each check's inventory entry is classified
    (#201-B3): ``TRUSTED`` is refused for any command whose effective class
    is not ``data_only_host_tool`` -- see ``trusted_check_authority_v2``."""

    if not _inventory_keys_match_command_tokens_v2(inventory):
        return _refuse_whole_plan_v2(
            plan, authority=authority, reason=EXECUTOR_REASON_INVENTORY_KEY_TOKEN_MISMATCH_V2,
        )

    if compute_check_command_inventory_digest_v2(inventory) != plan.authority_suite_digest:
        return _refuse_whole_plan_v2(
            plan, authority=authority, reason=EXECUTOR_REASON_INVENTORY_DIGEST_MISMATCH_V2,
        )

    results: list[ExecutedCheckV2] = []
    for check in plan.checks:
        spec = inventory.get(check.command_token)
        if spec is None:
            result = _build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            )
            results.append(
                ExecutedCheckV2(result=result, diagnostic_reason=EXECUTOR_REASON_COMMAND_NOT_IN_INVENTORY_V2)
            )
            continue

        # THE AUTHORITY BOUNDARY (#201-B3). Classify, and refuse BEFORE any
        # process exists -- not after running one and discarding the result.
        classification = classify_command_spec_v2(
            execution_class=spec.execution_class,
            host_owned_config=spec.host_owned_config,
            loads_checkout_plugins=spec.loads_checkout_plugins,
        )
        refusal = trusted_refusal_reason_v2(classification=classification, authority=authority)
        if refusal is not None:
            result = _build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            )
            results.append(
                ExecutedCheckV2(
                    result=result,
                    diagnostic_reason=refusal,
                    assessment=_assess_v2(classification=classification, reasons=(refusal,)),
                )
            )
            continue

        try:
            outcome, reason, artifact_payload, contained = _run_isolated_v2(
                argv=spec.argv, repo_root=repo_root, timeout_seconds=check.timeout_seconds,
                max_memory_mb=check.max_memory_mb, max_processes=check.max_processes,
                cancel_event=cancel_event, authority=authority,
                spec_digest=compute_command_spec_digest_v2(spec),
            )
        except IsolatedExecutorError as exc:
            result = _build_result_v2(
                plan=plan, check_name=check.check_name, authority=authority,
                outcome=TrustedCheckOutcomeV2.INFRA_FAILURE, artifact_payload=None,
            )
            results.append(
                ExecutedCheckV2(
                    result=result,
                    diagnostic_reason=exc.reason_code,
                    assessment=_assess_v2(
                        classification=classification, inventory_binding=STATE_VERIFIED_V2,
                        reasons=(exc.reason_code,),
                    ),
                )
            )
            continue

        result = _build_result_v2(
            plan=plan, check_name=check.check_name, authority=authority,
            outcome=outcome, artifact_payload=artifact_payload,
        )
        results.append(
            ExecutedCheckV2(
                result=result,
                diagnostic_reason=reason,
                assessment=_assess_v2(
                    classification=classification, inventory_binding=STATE_VERIFIED_V2,
                    product_outcome=outcome.value, contained=contained, executed=True,
                    reasons=(reason,) if reason is not None else (),
                ),
            )
        )

    return tuple(results)
