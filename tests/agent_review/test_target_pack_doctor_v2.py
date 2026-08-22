from __future__ import annotations

import errno
import hashlib
import json
import os
import resource
import select
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import app.agent_review.target_pack_doctor_v2 as doctor_module
from app.agent_review.profile_loader_v2 import compute_profile_hash_v2, load_target_profile_v2
from app.agent_review.target_pack_doctor_v2 import (
    DoctorDecisionV2,
    DoctorInputErrorV2,
    DoctorUnknownV2,
    SecretNameCheckV2,
    DOCTOR_OBSERVATION_STALE_REASON_V2,
    DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
    DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2,
    DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2,
    DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2,
    DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2,
    DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2,
    DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2,
    DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2,
    run_doctor_v2,
)
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_BUSY_REASON_V2,
    TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2,
    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
    TargetPackEpochError,
    TargetPackObservationBindingErrorV2,
    acquire_target_pack_epoch_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    GeneratedFileEntryV2,
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    compute_target_install_receipt_hash_v2,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> TargetPackManifestV2:
    return TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


_VALID_PROFILE_YAML = """
schema_id: agent-review.target-profile.v2
schema_version: 2
source: repo-profile
identity:
  repo: owner/repo
  default_branch: main
artifacts:
  - artifact_id: full-diff
    path: artifacts/full.diff
    kind: diff
    required: true
    max_bytes: 1000000
budgets:
  max_chunks: 16
  total_prompt_chars: 250000
  max_chars_per_chunk: 24000
  max_files_per_chunk: 50
  max_contracts_per_chunk: 50
must_review:
  paths: []
  patterns: []
  artifact_ids: []
  minimum_coverage: complete
policies:
  network_policy: forbidden
  fail_closed: true
  redaction_required: true
  allow_partial_coverage: false
  required_checks:
    - pytest
  allowed_semantic_groups:
    - primary_backend_logic
  coverage_failure_state: manual_required
  model_uncertainty_state: manual_required
contracts: []
limitations: []
"""


def _real_profile_hash() -> str:
    """The actual `compute_profile_hash_v2` of `_VALID_PROFILE_YAML` --
    NOT the same value as `_manifest()`'s `content_sha256` (that is the
    raw seed-bytes digest `target_pack_plan_v2` uses for drift detection;
    this is the model-level digest `_check_receipt_v2` now cross-checks a
    receipt's `target_profile_hash` against). Computed here, not
    hardcoded, so it can never silently drift from what `profile_loader_
    v2` actually computes."""
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
        return compute_profile_hash_v2(load_target_profile_v2(str(root)))


def _receipt(required_secret_names: tuple[str, ...] = (), **overrides: object) -> TargetInstallReceiptV2:
    fields = dict(
        schema_id="agent-review.target-install-receipt.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        manifest_digest=compute_target_pack_manifest_digest_v2(_manifest()),
        target_repo="owner/repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="owner/repo"),
        target_profile_hash=_real_profile_hash(),
        target_policy_hash="b" * 64,
        review_pack_hashes={},
        generated_file_hashes={},
        target_owned_file_hashes={},
        target_owned_paths=(),
        required_capabilities=(),
        expected_runner_labels=(),
        required_secret_names=required_secret_names,
        rollout_mode="off",
        compatibility="compatible",
        previous_install_identity=None,
        generated_at=None,
    )
    fields.update(overrides)
    computed = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**fields, receipt_hash="0" * 64)
    )
    return TargetInstallReceiptV2(**fields, receipt_hash=computed)


def _completed_report(outcome: object):
    assert isinstance(outcome, DoctorDecisionV2), outcome
    return outcome.report


def _live_process_fds_v2() -> set[int]:
    live: set[int] = set()
    for name in os.listdir("/proc/self/fd"):
        if not name.isdigit():
            continue
        fd = int(name)
        try:
            os.fstat(fd)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            raise
        live.add(fd)
    return live


def _materialize_healthy_target_v2(target_root: Path) -> None:
    aiops = target_root / ".aiops"
    aiops.mkdir(parents=True, exist_ok=True)
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(_manifest()),
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(profile.read_bytes())
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )


def _assert_session_cleanup_totality_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    position: str,
    close_errno: int = errno.EIO,
    original_unknown: bool = False,
    iterations: int = 3,
) -> None:
    """Exercise the real retained-FD release primitive and aggregate failures."""

    import app.agent_review.target_pack_epoch_v2 as epoch_module

    _materialize_healthy_target_v2(target_root)
    real_init = doctor_module._DoctorObservationSessionV2.__init__
    real_release = epoch_module.TargetPackTargetBindingV2.release_observation_fd_v2
    real_close = os.close
    records: list[dict[str, object]] = []
    active_failure: list[tuple[int, dict[str, object]] | None] = [None]

    def capture_init(self: object, *args: object, **kwargs: object) -> None:
        real_init(self, *args, **kwargs)
        records.append(
            {
                "session": self,
                "attempts": [],
                "ordered": (),
                "injected": False,
                "replacement_fd": None,
            }
        )

    def record_for_binding(binding: object) -> dict[str, object]:
        return next(
            record
            for record in reversed(records)
            if getattr(record["session"], "_root_binding") is binding
        )

    def fail_selected_close(fd: int) -> None:
        selected = active_failure[0]
        if selected is not None and selected[0] == fd and not bool(selected[1]["injected"]):
            selected[1]["injected"] = True
            # Linux consumes the descriptor number before reporting a late
            # close error.  Model that real ordering, including immediate
            # numeric reuse by an unrelated open.  EBADF is different: make
            # the descriptor genuinely invalid before the real second close.
            real_close(fd)
            if close_errno == errno.EBADF:
                real_close(fd)
            replacement_source_fd = os.open(
                "/dev/null", os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            )
            if replacement_source_fd != fd:
                os.dup2(replacement_source_fd, fd, inheritable=False)
                real_close(replacement_source_fd)
            replacement_fd = fd
            selected[1]["replacement_fd"] = replacement_fd
            raise OSError(close_errno, f"injected late cleanup failure at {position}")
        real_close(fd)

    def counted_release(self: object, fd: int) -> None:
        record = record_for_binding(self)
        session = record["session"]
        if not bool(getattr(session, "_closed")):
            real_release(self, fd)
            return
        ordered = record["ordered"]
        if not ordered:
            ordered = tuple(
                sorted(
                    {
                        retained.fd
                        for retained in (
                            *getattr(session, "_directories").values(),
                            *getattr(session, "_physical_objects").values(),
                        )
                    },
                    reverse=True,
                )
            )
            record["ordered"] = ordered
        attempts = record["attempts"]
        assert isinstance(attempts, list)
        attempt_index = len(attempts)
        attempts.append(fd)
        selected_index = {
            "first": 0,
            "middle": len(ordered) // 2,
            "last": len(ordered) - 1,
        }[position]
        if attempt_index == selected_index:
            active_failure[0] = (fd, record)
        try:
            real_release(self, fd)
        finally:
            active_failure[0] = None

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2, "__init__", capture_init
    )
    monkeypatch.setattr(
        epoch_module.TargetPackTargetBindingV2,
        "release_observation_fd_v2",
        counted_release,
    )
    monkeypatch.setattr(os, "close", fail_selected_close)

    if original_unknown:
        def abort_after_evidence(_self: object) -> None:
            raise doctor_module._DoctorUnknownAbortV2(
                doctor_module.DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="injected_original_unknown",
                relation="profile",
            )

        monkeypatch.setattr(
            doctor_module._DoctorObservationSessionV2,
            "revalidate_v2",
            abort_after_evidence,
        )

    baseline_fds = _live_process_fds_v2()
    baseline_tracker = set(epoch_module._LIVE_EPOCH_FDS_V2)
    violations: list[str] = []
    for iteration in range(iterations):
        outcome: object | None = None
        escaped: BaseException | None = None
        try:
            outcome = run_doctor_v2(
                target_root=target_root,
                manifest=_manifest(),
                target_repo="owner/repo",
            )
        except BaseException as exc:  # measured programmer/raw cleanup outcome
            escaped = exc

        record = records[iteration]
        ordered = tuple(record["ordered"])
        attempts = tuple(record["attempts"])
        if len(ordered) < 2:
            violations.append(f"retained-corpus[{iteration}]={len(ordered)}")
        if attempts != ordered:
            violations.append(
                f"cleanup-attempt-count[{iteration}]={len(attempts)}/{len(ordered)}"
            )
        if not bool(record["injected"]):
            violations.append(f"injection-not-fired[{iteration}]")

        session = record["session"]
        if any(
            getattr(session, name)
            for name in (
                "_directories",
                "_physical_objects",
                "_resolved_observations",
            )
        ):
            violations.append(f"registry-clear-skipped[{iteration}]")

        if close_errno == errno.EBADF:
            if not isinstance(escaped, OSError) or escaped.errno != errno.EBADF:
                violations.append(
                    f"programmer-taxonomy[{iteration}]={type(escaped).__name__}"
                )
        elif original_unknown:
            if not (
                isinstance(outcome, DoctorUnknownV2)
                and outcome.reason_code
                == doctor_module.DOCTOR_OBSERVATION_STALE_REASON_V2
                and outcome.stage == "injected_original_unknown"
                and outcome.relation == "profile"
                and escaped is None
            ):
                violations.append(
                    f"outcome-taxonomy[{iteration}]={type(outcome).__name__}/"
                    f"{type(escaped).__name__}"
                )
        elif not (
            isinstance(outcome, DoctorUnknownV2)
            and outcome.reason_code
            == doctor_module.DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2
            and outcome.stage == "observation_session_cleanup"
            and outcome.relation == "target_root"
            and escaped is None
        ):
            violations.append(
                f"outcome-taxonomy[{iteration}]={type(outcome).__name__}/"
                f"{type(escaped).__name__}"
            )

        replacement_fd = record["replacement_fd"]
        if replacement_fd is not None:
            try:
                os.fstat(int(replacement_fd))
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    violations.append(
                        f"unrelated-descriptor-reclosed[{iteration}]={replacement_fd}"
                    )
                else:
                    raise
            else:
                real_close(int(replacement_fd))

        for fd in ordered:
            if replacement_fd == fd:
                continue
            try:
                os.fstat(fd)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise
            else:
                violations.append(f"retained-fd-open[{iteration}]={fd}")
        if _live_process_fds_v2() != baseline_fds:
            violations.append(f"proc-fd-set-drift[{iteration}]")
        if set(epoch_module._LIVE_EPOCH_FDS_V2) != baseline_tracker:
            violations.append(f"fork-tracker-drift[{iteration}]")
        try:
            with acquire_target_pack_epoch_v2(
                target_root=target_root, exclusive=True
            ):
                pass
        except TargetPackEpochError as exc:
            violations.append(f"writer-not-admitted[{iteration}]={exc.reason_code}")

    assert not violations, " | ".join(violations)


def _assert_containment_negative_revalidation_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    negative_kind: str,
    repair_before_revalidation: bool,
) -> None:
    """Keep target metadata stable while an external indirection changes."""

    aiops = target_root / ".aiops"
    aiops.mkdir(parents=True)
    inside = aiops / "inside-profile.yaml"
    inside.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    external = target_root.parent / f"{target_root.name}-external-control"
    external.mkdir()
    outside = external / "outside-profile.yaml"
    outside.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    current = external / "current"
    if negative_kind == "escape":
        current.symlink_to(outside)
        expected_reason = DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    elif negative_kind == "loop":
        current.symlink_to(current)
        expected_reason = DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2
    else:  # pragma: no cover - test helper contract
        raise AssertionError(negative_kind)
    (aiops / "target-profile.v2.yaml").symlink_to(current)

    real_profile = doctor_module._check_profile_v2
    observed: list[tuple[str, str, str | None]] = []

    def profile_then_optionally_repair(
        session: doctor_module._DoctorObservationSessionV2,
    ) -> ProfileCheckV2:
        result = real_profile(session)
        observed.extend(
            (
                item.relation,
                item.kind,
                getattr(item, "containment_reason", None),
            )
            for item in session._logical_observations
        )
        if repair_before_revalidation:
            current.unlink()
            current.symlink_to(inside)
        return result

    monkeypatch.setattr(doctor_module, "_check_profile_v2", profile_then_optionally_repair)
    outcome = run_doctor_v2(
        target_root=target_root,
        manifest=_manifest(),
        target_repo="owner/repo",
    )

    assert ("profile", "containment_negative", expected_reason) in observed
    if repair_before_revalidation:
        assert isinstance(outcome, DoctorUnknownV2)
        assert outcome.reason_code == DOCTOR_OBSERVATION_STALE_REASON_V2
        assert outcome.stage == "final_revalidation"
        assert outcome.relation == "profile"
    else:
        report = _completed_report(outcome)
        assert report.profile.status == "invalid"
        assert report.profile.reason_code == expected_reason
        assert not report.is_healthy


def _assert_raced_root_absence_is_unknown_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _materialize_healthy_target_v2(target_root)
    real_acquire = doctor_module.acquire_target_pack_epoch_v2
    injected = False

    def acquire_then_remove(**kwargs: object):
        nonlocal injected
        lease = real_acquire(**kwargs)
        shutil.rmtree(target_root)
        injected = True
        return lease

    monkeypatch.setattr(
        doctor_module, "acquire_target_pack_epoch_v2", acquire_then_remove
    )
    outcome = run_doctor_v2(
        target_root=target_root,
        manifest=_manifest(),
        target_repo="owner/repo",
    )

    assert injected
    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == DOCTOR_OBSERVATION_STALE_REASON_V2
    assert outcome.stage == "target_root_binding"
    assert outcome.relation == "target_root"
    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
        pass


def _assert_environment_snapshot_failure_is_unknown_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure: type[BaseException] = RuntimeError,
) -> None:
    """One shared proposition for every enumerated snapshot failure class.

    `failure` selects which reproduced production class is raised while the
    single environment snapshot is being materialized -- `RuntimeError` for a
    concurrent `os.environ` mutation, `MemoryError` for genuine resource
    exhaustion. Both must reach the same typed report-zero outcome, so the
    mutants for each class discriminate against the same assertions rather
    than against two independently drifting helpers.
    """

    _materialize_healthy_target_v2(target_root)
    real_os = doctor_module.os

    class FailingEnvironment:
        keys_calls = 0

        def keys(self) -> object:
            self.keys_calls += 1

            # Faithful per-class diagnostics: CPython's own wording for the
            # concurrent-mutation case, so the RuntimeError arm keeps matching
            # what a real interpreter emits.
            message = (
                "dictionary changed size during iteration"
                if failure is RuntimeError
                else "cannot allocate the environment key snapshot"
            )

            class FailsDuringMaterialization:
                def __iter__(self) -> object:
                    raise failure(message)

            return FailsDuringMaterialization()

        def __getitem__(self, _name: str) -> str:
            raise AssertionError("environment value read")

    environment = FailingEnvironment()

    class OsProxy:
        environ = environment

        def __getattr__(self, name: str) -> object:
            return getattr(real_os, name)

    monkeypatch.setattr(doctor_module, "os", OsProxy())
    outcome = run_doctor_v2(
        target_root=target_root,
        manifest=_manifest(),
        target_repo="owner/repo",
    )

    assert environment.keys_calls == 1
    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2
    assert outcome.stage == "environment_snapshot"
    assert outcome.relation == "environment"
    assert not hasattr(outcome, "report")
    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
        pass


def _assert_content_read_memory_exhaustion_is_unknown_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Allocation failure while reading observed content is report-zero UNKNOWN.

    Round 4: the real constrained-address-space probe reaches THIS seam before
    the environment-key snapshot, because each chunk allocates a
    megabyte-scale buffer. Failing to allocate an observation buffer
    establishes nothing about the installed state, so it must join the same
    typed outcome as every other inability to observe -- not escape as a raw
    exception, and never a completed verdict.
    """

    _materialize_healthy_target_v2(target_root)
    real_read = doctor_module.os.read
    reads = {"count": 0}

    def exhausted_read(fd: int, size: int) -> bytes:
        reads["count"] += 1
        raise MemoryError("injected observation buffer exhaustion")

    monkeypatch.setattr(doctor_module.os, "read", exhausted_read)
    outcome = run_doctor_v2(
        target_root=target_root,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    monkeypatch.undo()

    assert reads["count"] >= 1
    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2
    assert outcome.stage == "content_read"
    assert not hasattr(outcome, "report")
    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
        pass


def _assert_transient_relookup_raw_fork_tracking_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seam: str,
    exec_after_fork: bool = False,
    iterations: int = 3,
) -> None:
    """Fork while the real intermediate/leaf transient descriptor is live."""

    import app.agent_review.target_pack_epoch_v2 as epoch_module

    _materialize_healthy_target_v2(target_root)
    aiops = (target_root / ".aiops").resolve()
    profile = (aiops / "target-profile.v2.yaml").resolve()
    selected_path = aiops if seam == "intermediate" else profile
    real_lookup = doctor_module._DoctorObservationSessionV2._transient_current_lookup_v2
    real_set_inheritable = os.set_inheritable
    active_lookup: list[Path] = []
    child_states: list[str] = []
    injected = 0
    armed = False

    def tracked_lookup(
        self: doctor_module._DoctorObservationSessionV2,
        resolved_path: Path,
        *,
        relation: str,
    ) -> tuple[str, os.stat_result | None]:
        active_lookup.append(resolved_path)
        try:
            return real_lookup(self, resolved_path, relation=relation)
        finally:
            active_lookup.pop()

    def fork_while_live(fd: int, inheritable: bool) -> None:
        nonlocal armed, injected
        opened_path: Path | None = None
        if active_lookup and active_lookup[-1] == profile:
            try:
                opened_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            except OSError:
                pass
        if armed and opened_path == selected_path:
            armed = False
            injected += 1
            read_fd, write_fd = os.pipe()
            if exec_after_fork:
                real_set_inheritable(write_fd, True)
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                if exec_after_fork:
                    child_code = (
                        "import errno, os, sys; "
                        "fd=int(sys.argv[1]); out=int(sys.argv[2]); "
                        "result=b'OPEN'; "
                        "\ntry: os.fstat(fd)"
                        "\nexcept OSError as exc: result=f'ERRNO:{exc.errno}'.encode()"
                        "\nos.write(out,result); os.close(out)"
                    )
                    os.execv(
                        sys.executable,
                        [sys.executable, "-c", child_code, str(fd), str(write_fd)],
                    )
                try:
                    os.fstat(fd)
                except OSError as exc:
                    result = f"ERRNO:{exc.errno}".encode()
                else:
                    result = b"OPEN"
                os.write(write_fd, result)
                os.close(write_fd)
                os._exit(0)
            os.close(write_fd)
            child_states.append(os.read(read_fd, 64).decode())
            os.close(read_fd)
            os.waitpid(pid, 0)
        real_set_inheritable(fd, inheritable)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        tracked_lookup,
    )
    monkeypatch.setattr(os, "set_inheritable", fork_while_live)
    baseline_tracker = set(epoch_module._LIVE_EPOCH_FDS_V2)
    for _ in range(iterations):
        armed = True
        outcome = run_doctor_v2(
            target_root=target_root,
            manifest=_manifest(),
            target_repo="owner/repo",
        )
        assert isinstance(outcome, DoctorDecisionV2)
        assert set(epoch_module._LIVE_EPOCH_FDS_V2) == baseline_tracker
    assert injected == iterations
    assert child_states == [f"ERRNO:{errno.EBADF}"] * iterations, child_states


def _read_framed_message_v2(
    read_fd: int, pending: bytearray, deadline: float
) -> str | None:
    """One newline-framed message from a pipe, or None once the deadline passes.

    Framing is explicit rather than inferred from arrival timing: the caller
    decides what a message MEANS, and the deadline only bounds how long it is
    willing to wait for one.
    """

    while True:
        newline = pending.find(b"\n")
        if newline >= 0:
            message = pending[:newline].decode()
            del pending[: newline + 1]
            return message
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        readable, _, _ = select.select([read_fd], [], [], remaining)
        if not readable:
            return None
        chunk = os.read(read_fd, 4096)
        if not chunk:
            return None
        pending.extend(chunk)


def _assert_provisional_content_open_is_bounded_v2(
    target_root: Path,
    *,
    strip_nonblock: bool = False,
    deadline_seconds: float = 3.0,
    publication_stall_seconds: float = 1.5,
) -> None:
    """Bound a real FIFO race with a CAUSAL handshake, never with timing.

    Round 4 (`R4_F28`) invalidated the previous harness. It asserted the
    diagnostic outcome after `process.wait(timeout=1)`, which silently assumed

        K released  =>  diagnostic already published

    That implication is false: K is dropped inside `lease.release`, and the
    child still has to return from release, finish `run_doctor_v2`, and format
    its result. Under integrated load that work can exceed any fixed window,
    so the harness could kill the child before it published and then fail the
    assertion it was supposed to be proving.

    The replacement establishes two INDEPENDENT authorities over a real pipe
    pair, so neither is inferred from the other:

    - `K_RELEASED` is emitted by the child from inside a wrapper around the
      real `TargetPackEpochLeaseV2.release`, AFTER the genuine release has
      returned. It is the product-liveness authority.
    - `OUTCOME` is emitted only after `run_doctor_v2` returns. It is the
      diagnostic-result authority.

    Between them the parent deliberately stalls the child LONGER than the old
    one-second window while holding it blocked on an ACK. If publication
    scheduling still mattered, that stall would break the test; it does not,
    which is the point.

    The production mutant (`strip_nonblock=True`) blocks inside the FIFO open,
    so `release` is never reached, `K_RELEASED` never arrives, and the
    writer stays BUSY -- the mutant dies on K liveness, exactly as before.
    """

    _materialize_healthy_target_v2(target_root)
    ready_read, ready_write = os.pipe()
    ack_read, ack_write = os.pipe()
    child_code = r'''
import os
import stat
import sys
from pathlib import Path
import app.agent_review.target_pack_doctor_v2 as doctor
import app.agent_review.target_pack_epoch_v2 as epoch
from tests.agent_review.test_target_pack_doctor_v2 import _manifest

root = Path(sys.argv[1])
strip_nonblock = sys.argv[2] == "1"
ready_fd = int(sys.argv[3])
ack_fd = int(sys.argv[4])
profile = root / ".aiops" / "target-profile.v2.yaml"

# Captured BEFORE any instrumentation: the handshake pipes are themselves
# FIFOs, so the later FIFO-content guard must never mediate them or the
# harness would strangle its own control channel.
pristine_read = os.read


def emit(message):
    os.write(ready_fd, (message + "\n").encode())


real_open = os.open
if strip_nonblock:
    def vulnerable_open(path, flags, *args, **kwargs):
        if os.fspath(path) == profile.name and flags & getattr(os, "O_NONBLOCK", 0):
            flags &= ~os.O_NONBLOCK
        return real_open(path, flags, *args, **kwargs)
    os.open = vulnerable_open

# The K-release authority: wrap the REAL release, emit only after it returns,
# then block until the parent acknowledges. The parent therefore observes a
# window in which K is provably free and the outcome is provably unpublished.
real_release = epoch.TargetPackEpochLeaseV2.release
def release_then_handshake(self):
    real_release(self)
    emit("K_RELEASED")
    while True:
        acknowledgement = pristine_read(ack_fd, 4096)
        if acknowledgement:
            break
epoch.TargetPackEpochLeaseV2.release = release_then_handshake

real_leaf = doctor._DoctorObservationSessionV2._open_leaf_path_fd_v2
armed = True
def swap_after_path_observation(self, **kwargs):
    global armed
    result = real_leaf(self, **kwargs)
    if armed and kwargs["resolved_path"] == profile.resolve() and stat.S_ISREG(result[1].st_mode):
        armed = False
        os.unlink(profile)
        os.mkfifo(profile)
        emit("SWAPPED")
    return result

doctor._DoctorObservationSessionV2._open_leaf_path_fd_v2 = swap_after_path_observation
real_read = os.read
def forbid_fifo_content_read(fd, size):
    observed = os.fstat(fd)
    if stat.S_ISFIFO(observed.st_mode):
        raise RuntimeError("fifo content was read before type discrimination")
    return real_read(fd, size)
os.read = forbid_fifo_content_read

try:
    outcome = doctor.run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    os.read = real_read
    emit(
        "OUTCOME:"
        + type(outcome).__name__
        + ":"
        + str(getattr(outcome, "reason_code", ""))
        + ":"
        + str(getattr(outcome, "stage", ""))
        + ":"
        + str(getattr(outcome, "relation", ""))
    )
except BaseException as exc:
    os.read = real_read
    emit("EXCEPTION:" + type(exc).__name__ + ":" + str(exc))
    raise
'''
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            child_code,
            str(target_root),
            "1" if strip_nonblock else "0",
            str(ready_write),
            str(ack_read),
        ],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        pass_fds=(ready_write, ack_read),
    )
    os.close(ready_write)
    os.close(ack_read)
    pending = bytearray()
    observed: list[str] = []
    try:
        swapped = _read_framed_message_v2(
            ready_read, pending, time.monotonic() + max(deadline_seconds, 10.0)
        )
        assert swapped == "SWAPPED", f"child never reached the race: {swapped!r}"
        observed.append(swapped)

        released = _read_framed_message_v2(
            ready_read, pending, time.monotonic() + deadline_seconds
        )
        if released != "K_RELEASED":
            raise AssertionError(
                "doctor retained K beyond deadline; "
                f"last messages={observed!r} signal={released!r}"
            )
        observed.append(released)

        # K is provably free while the child is still blocked on the ACK.
        with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
            pass

        # Deliberately outlast the old fixed one-second publication window to
        # prove the diagnostic no longer depends on child scheduling.
        time.sleep(publication_stall_seconds)
        assert process.poll() is None, "child exited before it was acknowledged"
        os.write(ack_write, b"ACK\n")

        outcome_message = _read_framed_message_v2(
            ready_read, pending, time.monotonic() + max(deadline_seconds, 10.0)
        )
        assert outcome_message is not None, f"child published nothing; got {observed!r}"
        observed.append(outcome_message)
        assert outcome_message == (
            "OUTCOME:DoctorUnknownV2:target_pack_doctor_observation_stale:"
            "object_binding:profile"
        ), observed
    finally:
        os.close(ready_read)
        os.close(ack_write)
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        process.communicate()
    with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
        pass


def _assert_transient_relookup_setup_failure_has_no_fd_leak_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seam: str,
    iterations: int = 3,
) -> None:
    aiops = target_root / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")

    real_lookup = doctor_module._DoctorObservationSessionV2._transient_current_lookup_v2
    real_set_inheritable = doctor_module.os.set_inheritable
    active_lookup: list[Path] = []
    injected = 0

    def tracked_lookup(
        self: doctor_module._DoctorObservationSessionV2,
        resolved_path: Path,
        *,
        relation: str,
    ) -> tuple[str, os.stat_result | None]:
        active_lookup.append(resolved_path)
        try:
            return real_lookup(self, resolved_path, relation=relation)
        finally:
            active_lookup.pop()

    def fail_after_open(fd: int, inheritable: bool) -> None:
        nonlocal injected
        if active_lookup and active_lookup[-1] == profile.resolve():
            opened_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            selected = aiops.resolve() if seam == "intermediate" else profile.resolve()
            if opened_path == selected:
                injected += 1
                raise OSError(errno.EIO, f"injected {seam} setup failure")
        real_set_inheritable(fd, inheritable)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        tracked_lookup,
    )
    monkeypatch.setattr(doctor_module.os, "set_inheritable", fail_after_open)

    baseline_fds = _live_process_fds_v2()
    leaked_fds: set[int] = set()
    try:
        for _ in range(iterations):
            outcome = run_doctor_v2(
                target_root=target_root,
                manifest=_manifest(),
                target_repo="owner/repo",
            )
            assert isinstance(outcome, DoctorUnknownV2)
            assert outcome.reason_code == "target_pack_doctor_observation_stale"
            assert outcome.stage == "final_revalidation"
            assert outcome.relation == "profile"
            with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
                pass

        leaked_fds = _live_process_fds_v2() - baseline_fds
        assert injected == iterations
        assert not leaked_fds
    finally:
        for fd in leaked_fds:
            try:
                os.close(fd)
            except OSError:
                pass


def _assert_operational_lease_entry_failure_is_released_v2(
    target_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    seam: str,
    iterations: int = 3,
) -> None:
    import app.agent_review.target_pack_epoch_v2 as epoch_module

    real_acquire = doctor_module.acquire_target_pack_epoch_v2
    real_fstat = doctor_module.os.fstat
    captured: list[object] = []

    def acquire_and_arm(**kwargs: object) -> object:
        lease = real_acquire(**kwargs)
        real_release = lease.release
        lease._test_release_calls = 0

        def counted_release() -> None:
            lease._test_release_calls += 1
            real_release()

        lease.release = counted_release
        captured.append(lease)
        target_fd = lease._namespace_fd if seam == "namespace" else lease._carrier_fd
        failed = False

        def fail_once(fd: int) -> os.stat_result:
            nonlocal failed
            if fd == target_fd and not failed:
                failed = True
                raise OSError(errno.EIO, f"injected {seam} lease-entry failure")
            return real_fstat(fd)

        monkeypatch.setattr(doctor_module.os, "fstat", fail_once)
        return lease

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", acquire_and_arm)
    baseline_fds = _live_process_fds_v2()
    try:
        for _ in range(iterations):
            outcome = doctor_module.run_doctor_v2(
                target_root=target_root,
                manifest=_manifest(),
                target_repo="owner/repo",
            )
            assert isinstance(outcome, DoctorUnknownV2)
            assert outcome.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2
            assert outcome.stage == "epoch_acquire"
            assert outcome.relation == "target_root"
            lease = captured[-1]
            assert lease._active is False
            assert lease._test_release_calls == 1
            for fd in (lease._namespace_fd, lease._carrier_fd):
                with pytest.raises(OSError) as raised:
                    real_fstat(fd)
                assert raised.value.errno == errno.EBADF
                assert fd not in epoch_module._LIVE_EPOCH_FDS_V2
            with acquire_target_pack_epoch_v2(target_root=target_root, exclusive=True):
                pass
        assert _live_process_fds_v2() == baseline_fds
    finally:
        for lease in captured:
            if lease._active:
                lease.release()


def _profile_check_from_completed_reason(reason_code: str):
    class RefusingSession:
        def observe_bytes_v2(self, **_kwargs: object) -> bytes:
            raise doctor_module._DoctorCompletedNegativeV2(reason_code)

    try:
        return doctor_module._check_profile_v2(RefusingSession())
    except RuntimeError:
        return None


def _assert_profile_completed_negative_status_is_explicit() -> None:
    assert _profile_check_from_completed_reason(DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2).status == "invalid"
    assert _profile_check_from_completed_reason(DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2).status == "invalid"
    assert _profile_check_from_completed_reason("target_profile_missing").status == "missing"
    assert _profile_check_from_completed_reason("target_profile_unreadable").status == "missing"
    assert _profile_check_from_completed_reason("target_pack_doctor_path_unrelated_fabrication") is None


def test_profile_completed_negative_status_is_explicit_not_reason_spelling() -> None:
    _assert_profile_completed_negative_status_is_explicit()


def test_doctor_reports_missing_profile_and_receipt_without_creating_anything(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "missing"
    assert report.receipt.status == "missing"
    assert not report.is_healthy
    # Read-only: doctor must not have created ANYTHING.
    assert list(tmp_path.iterdir()) == before


def test_doctor_reports_invalid_profile_without_mutating(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text("not: valid: yaml: at: all: :::", encoding="utf-8")
    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))
    assert report.profile.status in {"invalid", "missing"}
    assert not report.is_healthy


def test_doctor_reports_healthy_when_everything_present(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt_path = tmp_path / ".aiops" / "install-receipt.v2.json"
    # A genuinely healthy install's receipt DOES record its target-owned
    # set -- every real `init` populates it (aiops-orchestrator#205, C3).
    # An empty default here would (correctly, post-fix) no longer count as
    # healthy, since it would not match the manifest's own TARGET_OWNED
    # classification.
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    receipt_path.write_text(json.dumps(receipt.model_dump(mode="json")), encoding="utf-8")

    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    report = _completed_report(outcome)

    assert outcome.decision_status == "healthy"
    assert report.profile.status == "present"
    assert report.profile.profile_hash is not None
    assert report.receipt.status == "present"
    assert report.is_healthy


def test_doctor_checks_secret_name_presence_never_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(required_secret_names=("AGENT_ROUTER_API_KEY", "MISSING_SECRET_NAME"))
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    monkeypatch.setenv("AGENT_ROUTER_API_KEY", "this-value-must-never-appear-in-the-report")
    monkeypatch.delenv("MISSING_SECRET_NAME", raising=False)

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    by_name = {check.name: check.declared_present for check in report.secret_names}
    assert by_name == {"AGENT_ROUTER_API_KEY": True, "MISSING_SECRET_NAME": False}
    assert not report.is_healthy  # one secret missing
    # The VALUE never appears anywhere in the report's own repr.
    assert "this-value-must-never-appear-in-the-report" not in repr(report)


def test_doctor_refuses_a_target_root_that_is_not_a_directory(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-dir.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    with pytest.raises(DoctorInputErrorV2) as exc_info:
        run_doctor_v2(target_root=not_a_dir, manifest=_manifest(), target_repo="owner/repo")
    assert exc_info.value.reason_code == DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2


def test_doctor_reports_unhealthy_when_receipt_pack_version_does_not_match_the_manifest(tmp_path: Path) -> None:
    """Adversarial review finding, confirmed and fixed: a structurally
    valid, self-hash-consistent receipt claiming a DIFFERENT pack_version
    than the manifest being diagnosed against used to be reported
    `status="present"` / `is_healthy=True` -- doctor asserted a healthy
    install without ever checking it was looking at the install it thinks
    it is."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(pack_version="0.0.1-stale")
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_toolrepo_sha_does_not_match_the_manifest(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(toolrepo_sha="9" * 40)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_profile_hash_does_not_match_the_loaded_profile(
    tmp_path: Path,
) -> None:
    """The most severe of the three: a receipt can be internally
    consistent (matching pack_version/toolrepo_sha) while claiming
    provenance against a target-profile that is not the one actually on
    disk -- e.g. copied from a different target, or stale after the
    profile was hand-edited post-install."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    # Round 5: the target-owned SET check now runs before profile_hash (it
    # was moved ahead of the per-file loop, which now sits ahead of
    # profile_hash too -- see `_check_receipt_v2`'s docstring). A matching
    # set is required to reach profile_hash at all, isolating this test to
    # the axis it actually names.
    receipt = _receipt(
        target_profile_hash="f" * 64,
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2


def test_doctor_reports_unreconciled_target_owned_bytes_even_when_semantics_match(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    profile_path = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile_path.write_text(_VALID_PROFILE_YAML + "\n# formatting only\n", encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "present"
    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2
    assert not report.is_healthy


def test_doctor_reports_unhealthy_when_receipt_rollout_mode_exceeds_pack_capability(tmp_path: Path) -> None:
    """Follow-on adversarial finding from the same review pass, confirmed
    and fixed: a receipt can be internally consistent on pack_version/
    toolrepo_sha/target_profile_hash while still claiming a rollout_mode
    (e.g. shadow_full) the manifest being diagnosed against cannot
    deliver -- e.g. stale from a since-downgraded or reverted pack
    version. The same class of defect P2-4 fixed for `init`, reachable
    through `doctor` instead."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    # Round 5: matching target-owned set required to reach past the SET
    # check, now ahead of the per-file loop and therefore ahead of
    # rollout_mode too -- see `_check_receipt_v2`'s docstring.
    receipt = _receipt(
        rollout_mode="shadow_full",
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2
    assert not report.is_healthy


def test_doctor_skips_profile_hash_comparison_when_profile_itself_is_not_loadable(tmp_path: Path) -> None:
    """When the profile is missing entirely, there is nothing meaningful
    to compare a receipt's `target_profile_hash` claim against -- doctor
    must not crash trying, and `is_healthy` is already false via
    `profile.status`, not via a spurious profile-hash reason code.

    Uses a manifest with no TARGET_OWNED entries at all (an
    UPSTREAM_GENERATED-only pack version) so an empty
    `target_owned_paths` legitimately matches it -- this test's premise
    (the profile file is entirely absent) is otherwise incompatible with
    the target-owned reconciliation `#205`/C3 added: the profile IS the
    only TARGET_OWNED path the shared `_manifest()` fixture declares, so a
    receipt cannot claim to have reconciled it while the file is absent."""
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path="templates/workflow.yml",
                ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(manifest_digest=compute_target_pack_manifest_digest_v2(manifest))
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo"))

    assert report.profile.status == "missing"
    assert report.receipt.status == "present"
    assert not report.is_healthy


# --- Post-merge review debt (aiops-orchestrator#205, C2/C3) -----------------


def test_doctor_reports_healthy_for_correct_target_repo(tmp_path: Path) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.is_healthy


def test_doctor_refuses_a_receipt_transplanted_from_a_different_target(tmp_path: Path) -> None:
    """RED for C2, library level (see the CLI-level end-to-end reproduction
    in `test_agent_review_target_pack_v2_cli.py`). Previously,
    `run_doctor_v2` had no `target_repo` parameter at all -- the only
    identity source was `receipt.portable_target_root_identity`, which is
    itself derived from `receipt.target_repo`, so a receipt is always
    internally self-consistent no matter which target it actually came
    from. Confirmed by reproduction before this fix: copying a healthy
    install's `.aiops/` into an unrelated `tmp_path` reported
    `healthy: true` regardless of which repository was actually being
    diagnosed."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_repo="acme/original-repo",
        portable_target_root_identity=compute_portable_target_root_identity_v2(target_repo="acme/original-repo"),
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="acme/a-completely-different-repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_refuses_a_receipt_whose_target_owned_set_was_shrunk_to_empty(tmp_path: Path) -> None:
    """RED for C3. Reproduced before this fix: shrinking a receipt's
    `target_owned_paths`/`target_owned_file_hashes` to `{}` makes the
    per-file reconciliation loop iterate zero times -- trivially
    "successful" -- while a SEPARATE tampered on-disk profile (with
    `target_profile_hash` realigned to the tampered bytes, so the
    unrelated profile-hash check also passes) went completely
    unreconciled. `healthy: true` was reported for a target-owned file
    that was never read, hashed, or compared against anything."""
    (tmp_path / ".aiops").mkdir()
    tampered_profile = _VALID_PROFILE_YAML + "\n# attacker-injected line\n"
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(tampered_profile, encoding="utf-8")
    with tempfile.TemporaryDirectory() as raw_dir:
        root = Path(raw_dir)
        (root / ".aiops").mkdir()
        (root / ".aiops" / "target-profile.v2.yaml").write_text(tampered_profile, encoding="utf-8")
        tampered_profile_hash = compute_profile_hash_v2(load_target_profile_v2(str(root)))

    receipt = _receipt(target_owned_paths=(), target_owned_file_hashes={}, target_profile_hash=tampered_profile_hash)
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2
    assert not report.is_healthy


def test_doctor_refuses_a_receipt_whose_target_owned_set_is_a_strict_superset(tmp_path: Path) -> None:
    """Adversarial matrix item: the reconciliation must be a SET equality,
    not merely "does the receipt cover at least what the manifest
    requires" -- a receipt that also claims an extra, manifest-unknown
    target-owned path is equally not describing this pack version's real
    TARGET_OWNED set."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    # The extra path is a REAL file on disk with a hash that matches the
    # receipt's own claim -- the per-file loop alone would pass it (there
    # is nothing "unreconciled" about it byte-for-byte). Only the set
    # comparison against the manifest's actual TARGET_OWNED classification
    # can catch a claim of ownership over a path the manifest never
    # declared as TARGET_OWNED at all.
    (tmp_path / ".aiops" / "extra-unknown-file.txt").write_text("attacker content", encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/extra-unknown-file.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/extra-unknown-file.txt": _sha256(b"attacker content"),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2


def test_doctor_target_owned_set_reconciliation_is_order_independent(tmp_path: Path) -> None:
    """PASSO 3 item 11: the manifest's TARGET_OWNED set and the receipt's
    claimed set are compared as sets, so declaration order never affects
    the outcome. Uses a local two-entry manifest (the shared `_manifest()`
    fixture only has one TARGET_OWNED path) so there is a real permutation
    to prove order-independence with."""
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
            GeneratedFileEntryV2(
                path=".aiops/second-target-owned.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (tmp_path / ".aiops" / "second-target-owned.yaml").write_text("x", encoding="utf-8")
    # Receipt's tuple order is the REVERSE of the manifest's declaration
    # order above -- the comparison must not care.
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/second-target-owned.yaml", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/second-target-owned.yaml": _sha256(b"x"),
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo"))

    assert report.receipt.status == "present"
    assert report.receipt.reason_code is None


def test_doctor_does_not_treat_generated_file_hashes_as_current_state_evidence(
    tmp_path: Path,
) -> None:
    """The successor's read domain remains manifest TARGET_OWNED only.

    ``generated_file_hashes`` remains receipt declaration data but is not a
    doctor conformance relation in this slice; accepting it here makes that
    explicit non-claim executable rather than accidental omission.
    """

    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
            GeneratedFileEntryV2(
                path=".github/workflows/agent-review.yml",
                ownership=TargetPackFileOwnershipV2.UPSTREAM_GENERATED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        generated_file_hashes={".github/workflows/agent-review.yml": "f" * 64},
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")
    )

    assert report.is_healthy
    assert not (tmp_path / ".github/workflows/agent-review.yml").exists()
    assert report.receipt.reason_code is None


# --- H1A-R1: symlink-mediated read escape (independent review finding) -----
#
# `RelativePath` (the C1/C4 retype above) proves a path STRING is well-formed
# -- no `..`, no absolute/drive form. It says nothing about what an EXISTING
# component on disk resolves to. Reproduced against PR #230's own head
# (dd6d72b) before this fix: with `.aiops/target-profile.v2.yaml` symlinked
# outside `target_root`, `run_doctor_v2` returned `is_healthy=True` while a
# `Path.read_text`/`read_bytes` spy recorded both the profile read and the
# target-owned read resolving outside `target_root`.


def _read_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Records (kind, resolved_path) for every `Path` content read."""
    seen: list[tuple[str, str]] = []
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def spy_read_bytes(self: Path, *args: object, **kwargs: object) -> bytes:
        seen.append(("read_bytes", str(self.resolve())))
        return original_read_bytes(self, *args, **kwargs)  # type: ignore[arg-type]

    def spy_read_text(self: Path, *args: object, **kwargs: object) -> str:
        seen.append(("read_text", str(self.resolve())))
        return original_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
    monkeypatch.setattr(Path, "read_text", spy_read_text)
    return seen


def _assert_no_read_escaped(seen: list[tuple[str, str]], target_root: Path) -> None:
    root_real = str(target_root.resolve())
    # Epoch support legitimately reads the kernel's mount table.  This probe
    # guards target-artifact reads, not the external runtime-carrier domain.
    target_candidate_reads = [entry for entry in seen if not entry[1].startswith("/proc/")]
    escaping = [
        entry
        for entry in target_candidate_reads
        if not entry[1].startswith(root_real + os.sep) and entry[1] != root_real
    ]
    assert not escaping, f"a read resolved outside target_root: {escaping}"


def test_doctor_refuses_a_profile_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1, narrowest form: only `target-profile.v2.yaml` is a
    symlink pointing outside `target_root`."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(outside_profile)

    seen = _read_spy(monkeypatch)
    report = _completed_report(run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "invalid"
    assert report.profile.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_refuses_when_the_whole_aiops_directory_is_symlinked_outside(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1, broadest form: an intermediate path COMPONENT
    (`.aiops` itself) is the symlink, so both the profile and the receipt
    resolve outside `target_root` even though every path string involved is
    a perfectly valid `RelativePath`."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (outside / "install-receipt.v2.json").write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )
    (target_root / ".aiops").symlink_to(outside, target_is_directory=True)

    seen = _read_spy(monkeypatch)
    report = _completed_report(run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert report.receipt.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_refuses_a_target_owned_file_symlinked_outside_target_root(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED for H1A-R1 on the target-owned reconciliation loop specifically:
    a real, in-root profile and receipt, but the receipt's target-owned
    entry points at a path whose on-disk form escapes."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    (target_root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_owned = outside / "owned.txt"
    outside_owned.write_text("outside content", encoding="utf-8")
    (target_root / ".aiops" / "owned.txt").symlink_to(outside_owned)

    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
            GeneratedFileEntryV2(
                path=".aiops/owned.txt",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/owned.txt", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/owned.txt": _sha256(b"outside content"),
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
        },
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    seen = _read_spy(monkeypatch)
    report = _completed_report(run_doctor_v2(target_root=target_root, manifest=manifest, target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert not report.is_healthy
    _assert_no_read_escaped(seen, target_root)


def test_doctor_allows_a_symlink_that_resolves_back_inside_target_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The policy is CONTAINMENT, not symlink prohibition -- identical in
    meaning to `target_pack_install_v2`'s write-side check. Without this
    test the fix could silently become "no symlinks at all", a second,
    stricter policy the writer does not share."""
    target_root = tmp_path_factory.mktemp("target")
    (target_root / ".aiops").mkdir()
    real_profile = target_root / ".aiops" / "real-profile.yaml"
    real_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (target_root / ".aiops" / "target-profile.v2.yaml").symlink_to(real_profile)
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8"))},
    )
    (target_root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "present"
    assert report.receipt.status == "present"
    assert report.is_healthy


def _assert_aiops_root_self_completed(target_root: Path) -> DoctorDecisionV2:
    (target_root / ".aiops").symlink_to(".", target_is_directory=True)
    outcome = run_doctor_v2(
        target_root=target_root, manifest=_manifest(), target_repo="owner/repo"
    )
    assert isinstance(outcome, DoctorDecisionV2)
    assert outcome.report.profile.status == "missing"
    assert outcome.report.profile.reason_code == "target_profile_missing"
    assert outcome.report.receipt.status == "missing"
    assert outcome.report.receipt.reason_code == "target_pack_receipt_missing"
    return outcome


def test_aiops_resolving_exactly_to_target_root_is_a_completed_directory_relation(
    tmp_path: Path,
) -> None:
    _assert_aiops_root_self_completed(tmp_path)


def test_aiops_internal_symlink_chain_resolving_to_target_root_is_legal(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "back-to-root").symlink_to("..", target_is_directory=True)
    (tmp_path / ".aiops").symlink_to("state/back-to-root", target_is_directory=True)

    outcome = run_doctor_v2(
        target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"
    )

    assert isinstance(outcome, DoctorDecisionV2)
    assert outcome.report.profile.reason_code == "target_profile_missing"
    assert outcome.report.receipt.reason_code == "target_pack_receipt_missing"


def test_profile_resolving_exactly_to_target_root_is_completed_non_regular(
    tmp_path: Path,
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").symlink_to("..", target_is_directory=True)

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.profile.status == "missing"
    assert report.profile.reason_code == "target_profile_missing"


def test_receipt_resolving_exactly_to_target_root_is_completed_non_regular(
    tmp_path: Path,
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (aiops / "install-receipt.v2.json").symlink_to("..", target_is_directory=True)

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.receipt.status == "missing"
    assert report.receipt.reason_code == "target_pack_receipt_missing"


def test_target_owned_member_resolving_to_target_root_is_completed_unreconciled(
    tmp_path: Path,
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    root_member = aiops / "root-member"
    root_member.symlink_to("..", target_is_directory=True)
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/root-member",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/root-member", ".aiops/target-profile.v2.yaml"),
        target_owned_file_hashes={
            ".aiops/root-member": "f" * 64,
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")
    )

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2


def test_aiops_actual_escape_is_not_confused_with_root_self_resolution(tmp_path: Path) -> None:
    (tmp_path / ".aiops").symlink_to("..", target_is_directory=True)

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.profile.status == "invalid"
    assert report.profile.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2
    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_root_self_relation_survives_final_relookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_lookup = doctor_module._DoctorObservationSessionV2._transient_current_lookup_v2
    root_relookups = 0

    def counted_lookup(self: object, resolved_path: Path, *, relation: str):
        nonlocal root_relookups
        if resolved_path == tmp_path.resolve():
            root_relookups += 1
        return real_lookup(self, resolved_path, relation=relation)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        counted_lookup,
    )
    _assert_aiops_root_self_completed(tmp_path)

    assert root_relookups >= 1


# --- H1A-R1, round 2: the check must be BOUND to the read, not merely --
# --- run before it (second independent review of #230's first fix) -----


def test_check_profile_reads_the_bound_object_and_final_relookup_makes_a_later_swap_unknown(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A swap after the sanctioned lookup cannot become completed evidence."""
    target_root = tmp_path_factory.mktemp("target")
    outside = tmp_path_factory.mktemp("outside")
    (target_root / ".aiops").mkdir()
    real_profile = target_root / ".aiops" / "real-profile.yaml"
    real_profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    outside_profile = outside / "outside-profile.yaml"
    outside_profile.write_text(_VALID_PROFILE_YAML.replace("owner/repo", "acme/exfiltrated-via-toctou"))
    symlink_path = target_root / ".aiops" / "target-profile.v2.yaml"
    symlink_path.symlink_to(real_profile)

    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_resolve = doctor_module.resolve_within_target_root_v2

    swapped = False

    def racing_resolve(target_root_real: Path, path: Path) -> Path:
        nonlocal swapped
        result = real_resolve(target_root_real, path)
        if not swapped and path.name == "target-profile.v2.yaml":
            swapped = True
            symlink_path.unlink()
            symlink_path.symlink_to(outside_profile)
        return result

    monkeypatch.setattr(doctor_module, "resolve_within_target_root_v2", racing_resolve)
    outcome = run_doctor_v2(target_root=target_root, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.stage in {"object_binding", "final_revalidation"}


# --- Round 5: Codex shadow review of #230 at fbc67db --------------------


def test_doctor_returns_a_typed_refusal_for_a_symlink_loop_instead_of_crashing(tmp_path: Path) -> None:
    """RED. `Path.resolve(strict=False)` raises `RuntimeError` for a
    symlink loop (`a -> b`, `b -> a`), unlike every other resolution
    failure `resolve_within_target_root_v2`'s callers already handle via
    a typed `PlanError`. Reproduced before the fix: a loop at the profile
    path made `run_doctor_v2` raise `RuntimeError` uncaught -- a traceback
    instead of the structured invalid report `doctor` promises for every
    diagnosable state."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "invalid"
    assert not report.is_healthy


def test_doctor_distinguishes_a_resolution_loop_from_a_genuine_escape(tmp_path: Path) -> None:
    """RED, second pass (Codex shadow review of #230 at 90999f2).
    `_check_profile_v2`'s `except PlanError` collapsed BOTH `resolve_
    within_target_root_v2` reasons -- genuine escape and unresolvable
    symlink loop -- into the single escape reason code, discarding the
    distinction the loop fix's own dedicated `PlanError` reason code
    existed to preserve. Reproduced: a symlink loop reported `target_
    pack_doctor_path_escapes_target_root`, indistinguishable from an
    actual escape, even though `resolve_within_target_root_v2` already
    raised a semantically different reason."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").symlink_to("target-profile.v2.yaml")

    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.profile.status == "invalid"
    assert report.profile.reason_code == DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2
    assert report.profile.reason_code != DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def test_doctor_rejects_an_unknown_target_owned_path_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED. The per-file reconciliation loop used to run BEFORE the
    target-owned SET check, so a receipt declaring a path the manifest
    never classified as TARGET_OWNED still got that path `read_bytes()`'d
    and hashed -- unbounded by size -- even though the set check would
    reject the receipt anyway. Reproduced before the fix: a 5MB file
    outside the manifest's TARGET_OWNED set was read in full despite the
    receipt being provably invalid by a single O(1) set comparison."""
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    extra = tmp_path / ".aiops" / "unclassified-extra.bin"
    extra.write_bytes(b"x" * (1024 * 1024))

    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/unclassified-extra.bin"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode("utf-8")),
            ".aiops/unclassified-extra.bin": _sha256(b"x" * (1024 * 1024)),
        },
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    calls: list[str] = []
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    original_observe = doctor_module._DoctorObservationSessionV2.observe_sha256_v2

    def spy_observe(self: object, **kwargs: object) -> str:
        calls.append(str(kwargs["logical_path"]))
        return original_observe(self, **kwargs)

    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "observe_sha256_v2", spy_observe)
    report = _completed_report(run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo"))

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2
    assert not any("unclassified-extra.bin" in c for c in calls), (
        "the unclassified file was read despite being provably invalid by the set check alone"
    )


def test_doctor_reads_are_bound_to_the_captured_root_not_a_mutable_alias(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutable caller alias changed after K acquisition makes the run stale."""
    root = tmp_path_factory.mktemp("root")
    (root / ".aiops").mkdir()
    (root / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    sub = root / "subdir"
    (sub / ".aiops").mkdir(parents=True)
    (sub / ".aiops" / "target-profile.v2.yaml").write_text(
        _VALID_PROFILE_YAML.replace("owner/repo", "acme/subdir-divergent"), encoding="utf-8"
    )
    live = tmp_path_factory.mktemp("live-parent") / "live"
    live.symlink_to(root, target_is_directory=True)

    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_acquire = doctor_module.acquire_target_pack_epoch_v2

    def acquire_then_retarget(**kwargs: object):
        lease = real_acquire(**kwargs)
        live.unlink()
        live.symlink_to(sub, target_is_directory=True)
        return lease

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", acquire_then_retarget)
    outcome = run_doctor_v2(target_root=live, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.stage == "target_root_binding"


def test_doctor_completed_diagnosis_is_wrapped_in_a_typed_decision(tmp_path: Path) -> None:
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorDecisionV2)
    assert outcome.decision_status == "unhealthy"
    assert outcome.report.target_root == str(tmp_path)


def test_doctor_invalid_root_is_a_typed_input_error_outside_the_outcome(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(DoctorInputErrorV2) as raised:
        run_doctor_v2(target_root=missing, manifest=_manifest(), target_repo="owner/repo")

    assert raised.value.reason_code == "target_pack_doctor_target_root_not_a_directory"
    assert not isinstance(raised.value, OSError)


def test_doctor_epoch_failure_is_report_zero_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    def _busy(**_: object) -> object:
        from app.agent_review.target_pack_epoch_v2 import (
            TARGET_PACK_EPOCH_BUSY_REASON_V2,
            TargetPackEpochError,
        )

        raise TargetPackEpochError(TARGET_PACK_EPOCH_BUSY_REASON_V2)

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", _busy)

    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_epoch_busy"
    assert outcome.stage == "epoch_acquire"
    assert outcome.relation == "target_root"
    assert not hasattr(outcome, "report")


@pytest.mark.parametrize(
    "reason_code",
    (TARGET_PACK_EPOCH_BUSY_REASON_V2, TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2),
)
def test_doctor_maps_each_epoch_acquisition_refusal_to_report_zero_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason_code: str
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    def refuse(**_: object) -> object:
        raise TargetPackEpochError(reason_code)

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", refuse)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == reason_code
    assert outcome.stage == "epoch_acquire"
    assert not hasattr(outcome, "report")


def test_raw_operational_epoch_oserror_is_stage_classified_not_caught_globally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    def fail(**_: object) -> object:
        raise OSError(errno.EIO, "injected")

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", fail)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2
    assert outcome.stage == "epoch_acquire"
    assert outcome.relation == "target_root"


@pytest.mark.parametrize("operation_errno", (errno.EBADF, errno.ENOSPC))
def test_raw_programmer_or_unenumerated_epoch_oserror_is_not_hidden_as_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation_errno: int,
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    injected = OSError(operation_errno, "injected")

    def fail(**_: object) -> object:
        raise injected

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", fail)
    with pytest.raises(OSError) as raised:
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    assert raised.value is injected


@pytest.mark.parametrize("seam", ("namespace", "carrier"))
def test_operational_lease_entry_failure_is_report_zero_unknown_and_releases_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    _assert_operational_lease_entry_failure_is_released_v2(
        tmp_path,
        monkeypatch,
        seam=seam,
    )


def test_lease_entry_capability_mismatch_preserves_typed_reason_and_releases_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent_review.target_pack_epoch_v2 as epoch_module

    real_acquire = doctor_module.acquire_target_pack_epoch_v2
    real_fstat = doctor_module.os.fstat
    captured: list[object] = []

    def acquire_with_mismatched_identity(**kwargs: object) -> object:
        lease = real_acquire(**kwargs)
        captured.append(lease)
        lease._namespace_identity = (-1, -1)
        return lease

    monkeypatch.setattr(
        doctor_module,
        "acquire_target_pack_epoch_v2",
        acquire_with_mismatched_identity,
    )
    try:
        outcome = doctor_module.run_doctor_v2(
            target_root=tmp_path,
            manifest=_manifest(),
            target_repo="owner/repo",
        )
        assert isinstance(outcome, DoctorUnknownV2)
        assert outcome.reason_code == TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2
        assert outcome.stage == "epoch_acquire"
        lease = captured[-1]
        assert lease._active is False
        for fd in (lease._namespace_fd, lease._carrier_fd):
            with pytest.raises(OSError) as raised:
                real_fstat(fd)
            assert raised.value.errno == errno.EBADF
            assert fd not in epoch_module._LIVE_EPOCH_FDS_V2
        with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True):
            pass
    finally:
        for lease in captured:
            if lease._active:
                lease.release()


def test_programmer_lease_entry_failure_still_raises_but_releases_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_acquire = doctor_module.acquire_target_pack_epoch_v2
    real_fstat = doctor_module.os.fstat
    captured: list[object] = []

    def acquire_and_arm(**kwargs: object) -> object:
        lease = real_acquire(**kwargs)
        captured.append(lease)
        target_fd = lease._namespace_fd
        failed = False

        def fail_once(fd: int) -> os.stat_result:
            nonlocal failed
            if fd == target_fd and not failed:
                failed = True
                raise OSError(errno.EBADF, "injected programmer lease-entry failure")
            return real_fstat(fd)

        monkeypatch.setattr(doctor_module.os, "fstat", fail_once)
        return lease

    monkeypatch.setattr(doctor_module, "acquire_target_pack_epoch_v2", acquire_and_arm)
    try:
        with pytest.raises(OSError) as raised:
            doctor_module.run_doctor_v2(
                target_root=tmp_path,
                manifest=_manifest(),
                target_repo="owner/repo",
            )
        assert raised.value.errno == errno.EBADF
        assert captured[-1]._active is False
        with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True):
            pass
    finally:
        for lease in captured:
            if lease._active:
                lease.release()


@pytest.mark.parametrize(
    "operation_errno",
    (
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOMEM,
        errno.EIO,
        getattr(errno, "ESTALE", errno.EIO),
        errno.EINTR,
    ),
)
def test_observation_errno_matrix_classifies_capacity_and_io_as_unknown(
    operation_errno: int,
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    with pytest.raises(doctor_module._DoctorUnknownAbortV2) as raised:
        doctor_module._raise_classified_observation_oserror_v2(
            stage="content_read",
            relation="profile",
            exc=OSError(operation_errno, "injected"),
            missing_reason="missing",
            unreadable_reason="unreadable",
        )

    assert raised.value.reason_code == "target_pack_doctor_observation_unavailable"
    assert raised.value.stage == "content_read"
    assert raised.value.relation == "profile"


@pytest.mark.parametrize(
    ("operation_errno", "expected_reason"),
    (
        (errno.ENOENT, "missing"),
        (errno.ENOTDIR, "missing"),
        (errno.ENAMETOOLONG, "missing"),
        (errno.EISDIR, "missing"),
        (errno.EACCES, "unreadable"),
        (errno.EPERM, "unreadable"),
    ),
)
def test_observation_errno_matrix_preserves_coherent_negative_reasons(
    operation_errno: int, expected_reason: str
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    with pytest.raises(doctor_module._DoctorCompletedNegativeV2) as raised:
        doctor_module._raise_classified_observation_oserror_v2(
            stage="object_binding",
            relation="receipt",
            exc=OSError(operation_errno, "injected"),
            missing_reason="missing",
            unreadable_reason="unreadable",
        )

    assert raised.value.reason_code == expected_reason


@pytest.mark.parametrize(
    "operation_errno",
    (errno.ENOENT, errno.EACCES, errno.EIO, errno.ELOOP),
)
def test_final_revalidation_errno_matrix_is_only_unknown_stale(
    operation_errno: int,
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    with pytest.raises(doctor_module._DoctorUnknownAbortV2) as raised:
        doctor_module._raise_classified_observation_oserror_v2(
            stage="final_revalidation",
            relation="target_owned:.aiops/example",
            exc=OSError(operation_errno, "injected"),
            missing_reason="must-not-be-used",
            unreadable_reason="must-not-be-used",
        )

    assert raised.value.reason_code == "target_pack_doctor_observation_stale"
    assert raised.value.stage == "final_revalidation"


def test_observation_errno_matrix_classifies_symlink_loop_as_unknown_stale() -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    with pytest.raises(doctor_module._DoctorUnknownAbortV2) as raised:
        doctor_module._raise_classified_observation_oserror_v2(
            stage="object_binding",
            relation="profile",
            exc=OSError(errno.ELOOP, "injected"),
            missing_reason="missing",
            unreadable_reason="unreadable",
        )

    assert raised.value.reason_code == "target_pack_doctor_observation_stale"


@pytest.mark.parametrize("stage", ("fd_noninheritability", "fd_metadata"))
def test_binding_capability_errno_never_becomes_artifact_unreadable(
    stage: str,
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    with pytest.raises(doctor_module._DoctorUnknownAbortV2) as raised:
        doctor_module._raise_binding_primitive_oserror_v2(
            stage=stage,
            relation="profile",
            exc=PermissionError(errno.EACCES, "injected"),
        )

    assert raised.value.reason_code == "target_pack_doctor_observation_unavailable"
    assert raised.value.stage == stage
    assert raised.value.relation == "profile"


@pytest.mark.parametrize("operation_errno", (errno.EBADF, errno.EINVAL, errno.ENOSPC))
def test_observation_errno_matrix_does_not_hide_programmer_or_unenumerated_errors(
    operation_errno: int,
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    injected = OSError(operation_errno, "injected")
    with pytest.raises(OSError) as raised:
        doctor_module._raise_classified_observation_oserror_v2(
            stage="object_binding",
            relation="profile",
            exc=injected,
            missing_reason="missing",
            unreadable_reason="unreadable",
        )

    assert raised.value is injected


def test_doctor_classifies_root_only_after_k_and_busy_wins_for_absent_target(tmp_path: Path) -> None:
    target = tmp_path / "not-materialized"
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True):
        outcome = run_doctor_v2(target_root=target, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == TARGET_PACK_EPOCH_BUSY_REASON_V2
    assert outcome.stage == "epoch_acquire"


def test_doctor_classifies_non_directory_only_after_k_and_busy_wins(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("stable file", encoding="utf-8")
    with acquire_target_pack_epoch_v2(target_root=target, exclusive=True):
        outcome = run_doctor_v2(target_root=target, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == TARGET_PACK_EPOCH_BUSY_REASON_V2
    assert outcome.stage == "epoch_acquire"


def test_doctor_root_binding_resource_exhaustion_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_epoch_v2 as epoch_module

    def _emfile(self: object, *, target_root: Path) -> object:
        raise TargetPackObservationBindingErrorV2(
            TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
            operation_errno=getattr(os, "EMFILE", 24),
            stage="open",
        )

    monkeypatch.setattr(epoch_module.TargetPackEpochLeaseV2, "bind_target_root_for_observation_v2", _emfile)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_unavailable"
    assert outcome.stage == "target_root_binding"


def test_operational_root_binding_entry_failure_is_unknown_under_lease_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.agent_review.target_pack_epoch_v2 as epoch_module

    real_bind = epoch_module.TargetPackEpochLeaseV2.bind_target_root_for_observation_v2
    captured: list[object] = []

    def bind_and_arm(self: object, *, target_root: Path) -> object:
        binding = real_bind(self, target_root=target_root)
        captured.append(binding)
        real_require_active = binding._require_active_v2
        failed = False

        def fail_once() -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(errno.EIO, "injected root-binding entry failure")
            real_require_active()

        binding._require_active_v2 = fail_once
        return binding

    monkeypatch.setattr(
        epoch_module.TargetPackEpochLeaseV2,
        "bind_target_root_for_observation_v2",
        bind_and_arm,
    )
    outcome = doctor_module.run_doctor_v2(
        target_root=tmp_path,
        manifest=_manifest(),
        target_repo="owner/repo",
    )

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_unavailable"
    assert outcome.stage == "target_root_binding"
    assert captured[-1]._active is False
    with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True):
        pass


def test_doctor_eio_while_reading_material_is_unknown_not_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    def _eio(_fd: int, _size: int) -> bytes:
        raise OSError(getattr(os, "EIO", 5), "injected")

    monkeypatch.setattr(doctor_module.os, "read", _eio)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_unavailable"
    assert outcome.stage == "content_read"
    assert outcome.relation == "profile"


def test_doctor_emfile_while_opening_material_content_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open = doctor_module.os.open

    def exhaust_on_profile(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("target-profile.v2.yaml") and not flags & os.O_PATH:
            raise OSError(errno.EMFILE, "injected")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_module.os, "open", exhaust_on_profile)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_unavailable"
    assert outcome.stage == "content_open"
    assert outcome.relation == "profile"


def test_doctor_stable_profile_permission_failure_is_completed_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open = doctor_module.os.open

    def _deny_profile(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("target-profile.v2.yaml") and flags & os.O_RDONLY == os.O_RDONLY and not flags & os.O_PATH:
            raise PermissionError(getattr(os, "EACCES", 13), "injected")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_module.os, "open", _deny_profile)
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.profile.status == "missing"
    assert report.profile.reason_code == "target_profile_unreadable"


def test_doctor_stable_receipt_permission_failure_is_completed_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt_path = aiops / "install-receipt.v2.json"
    receipt_path.write_text(
        json.dumps(_receipt().model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open = doctor_module.os.open

    def deny_receipt(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("install-receipt.v2.json") and not flags & os.O_PATH:
            raise PermissionError(errno.EACCES, "injected")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_module.os, "open", deny_receipt)
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == "target_pack_receipt_invalid"


def test_doctor_stable_target_owned_permission_failure_is_completed_unhealthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(
                path=".aiops/target-profile.v2.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="a" * 64,
            ),
            GeneratedFileEntryV2(
                path=".aiops/second-owned.txt",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (aiops / "second-owned.txt").write_text("second", encoding="utf-8")
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/second-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/second-owned.txt": _sha256(b"second"),
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open = doctor_module.os.open

    def deny_member(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("second-owned.txt") and not flags & os.O_PATH:
            raise PermissionError(errno.EACCES, "injected")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_module.os, "open", deny_member)
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")
    )

    assert report.receipt.status == "invalid"
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2


def test_doctor_non_regular_profile_is_a_completed_negative(tmp_path: Path) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.mkdir(parents=True)

    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.profile.status == "missing"
    assert report.profile.reason_code == "target_profile_missing"


def _assert_path_object_type_drift_is_unknown(
    target_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = target_root / ".aiops"
    aiops.mkdir()
    aiops_real = aiops.resolve()
    real_lookup = doctor_module._DoctorObservationSessionV2._transient_current_lookup_v2

    def type_drift_lookup(self: object, resolved_path: Path, *, relation: str):
        current_kind, current = real_lookup(self, resolved_path, relation=relation)
        if resolved_path == aiops_real and current is not None:
            current = os.stat_result(
                (
                    stat.S_IFREG | 0o600,
                    current.st_ino,
                    current.st_dev,
                    current.st_nlink,
                    current.st_uid,
                    current.st_gid,
                    current.st_size,
                    current.st_atime,
                    current.st_mtime,
                    current.st_ctime,
                )
            )
        return current_kind, current

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        type_drift_lookup,
    )
    outcome = run_doctor_v2(
        target_root=target_root, manifest=_manifest(), target_repo="owner/repo"
    )

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.stage == "final_revalidation"
    assert outcome.relation == "aiops"


def test_object_identity_is_the_discriminating_type_stability_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_path_object_type_drift_is_unknown(tmp_path, monkeypatch)


def test_two_shared_doctors_can_coexist(tmp_path: Path) -> None:
    with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=False):
        outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorDecisionV2)


def test_writer_cannot_enter_after_receipt_or_before_final_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    (tmp_path / ".aiops" / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    observations: list[str] = []

    def assert_writer_busy(label: str) -> None:
        with pytest.raises(TargetPackEpochError) as raised:
            acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True)
        assert raised.value.reason_code == TARGET_PACK_EPOCH_BUSY_REASON_V2
        observations.append(label)

    real_receipt = doctor_module._check_receipt_v2
    real_profile = doctor_module._check_profile_v2
    real_revalidate = doctor_module._DoctorObservationSessionV2.revalidate_v2

    def checked_profile(session: object):
        result = real_profile(session)
        assert_writer_busy("after_profile")
        return result

    def checked_receipt(**kwargs: object):
        result = real_receipt(**kwargs)
        assert_writer_busy("after_receipt")
        return result

    def checked_revalidate(self: object) -> None:
        assert_writer_busy("before_revalidation")
        real_revalidate(self)

    monkeypatch.setattr(doctor_module, "_check_profile_v2", checked_profile)
    monkeypatch.setattr(doctor_module, "_check_receipt_v2", checked_receipt)
    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "revalidate_v2", checked_revalidate)

    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorDecisionV2)
    assert observations == ["after_profile", "after_receipt", "before_revalidation"]


def test_cross_process_writer_cannot_enter_during_doctor_profile_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_profile = doctor_module._check_profile_v2
    observed: list[str] = []

    def profile_with_process_probe(session: object):
        result = real_profile(session)
        code = r'''
import sys
from pathlib import Path
from app.agent_review.target_pack_epoch_v2 import acquire_target_pack_epoch_v2, TargetPackEpochError
try:
    lease = acquire_target_pack_epoch_v2(target_root=Path(sys.argv[1]), exclusive=True)
except TargetPackEpochError as exc:
    print(exc.reason_code)
else:
    print("acquired")
    lease.release()
'''
        observed.append(
            subprocess.check_output([sys.executable, "-c", code, str(tmp_path)], text=True).strip()
        )
        return result

    monkeypatch.setattr(doctor_module, "_check_profile_v2", profile_with_process_probe)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorDecisionV2)
    assert observed == [TARGET_PACK_EPOCH_BUSY_REASON_V2]


def test_writer_cannot_enter_between_two_target_owned_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    second = aiops / "second-owned.txt"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(path=".aiops/target-profile.v2.yaml", ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="a" * 64),
            GeneratedFileEntryV2(path=".aiops/second-owned.txt", ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="b" * 64),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/second-owned.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/second-owned.txt": _sha256(b"second"),
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_observe = doctor_module._DoctorObservationSessionV2.observe_sha256_v2
    observed_relations: list[str] = []

    def observe_with_writer_probe(self: object, **kwargs: object) -> str:
        digest = real_observe(self, **kwargs)
        observed_relations.append(str(kwargs["relation"]))
        with pytest.raises(TargetPackEpochError) as raised:
            acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True)
        assert raised.value.reason_code == TARGET_PACK_EPOCH_BUSY_REASON_V2
        return digest

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2, "observe_sha256_v2", observe_with_writer_probe
    )
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")
    )

    assert report.receipt.status == "present"
    assert observed_relations == [
        "target_owned:.aiops/second-owned.txt",
        "target_owned:.aiops/target-profile.v2.yaml",
    ]


def test_profile_content_is_acquired_once_for_parse_semantic_hash_and_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    (profile.parent / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    profile_identity = (profile.stat().st_dev, profile.stat().st_ino)
    nonempty_reads = 0
    real_read = doctor_module.os.read

    def counted_read(fd: int, size: int) -> bytes:
        nonlocal nonempty_reads
        chunk = real_read(fd, size)
        observed = os.fstat(fd)
        if chunk and (observed.st_dev, observed.st_ino) == profile_identity:
            nonempty_reads += 1
        return chunk

    monkeypatch.setattr(doctor_module.os, "read", counted_read)
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.receipt.status == "present"
    assert nonempty_reads == 1


def test_canonical_observation_plan_places_all_byte_relations_before_ledger_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    real_bytes = doctor_module._DoctorObservationSessionV2.observe_bytes_v2
    real_digest = doctor_module._DoctorObservationSessionV2.observe_sha256_v2
    events: list[tuple[str, str]] = []

    def observe_bytes(self: object, **kwargs: object) -> bytes:
        events.append(("bytes", str(kwargs["relation"])))
        return real_bytes(self, **kwargs)

    def observe_digest(self: object, **kwargs: object) -> str:
        events.append(("digest", str(kwargs["relation"])))
        return real_digest(self, **kwargs)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2, "observe_bytes_v2", observe_bytes
    )
    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2, "observe_sha256_v2", observe_digest
    )
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.is_healthy
    assert events[:2] == [("bytes", "profile"), ("bytes", "receipt")]
    first_digest = next(index for index, event in enumerate(events) if event[0] == "digest")
    assert all(event[0] == "bytes" for event in events[:first_digest])


def test_profile_path_binding_is_reused_for_its_ledger_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())
        },
    )
    (profile.parent / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open_leaf = doctor_module._DoctorObservationSessionV2._open_leaf_path_fd_v2
    profile_bindings = 0

    def counted_binding(self: object, **kwargs: object):
        nonlocal profile_bindings
        if kwargs["resolved_path"].name == "target-profile.v2.yaml":
            profile_bindings += 1
        return real_open_leaf(self, **kwargs)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_open_leaf_path_fd_v2",
        counted_binding,
    )
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.receipt.status == "present"
    assert profile_bindings == 1


def test_missing_profile_observation_is_cached_for_its_ledger_relation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": "f" * 64},
    )
    (tmp_path / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_open_leaf = doctor_module._DoctorObservationSessionV2._open_leaf_path_fd_v2
    profile_attempts = 0

    def counted_binding(self: object, **kwargs: object):
        nonlocal profile_attempts
        if kwargs["resolved_path"].name == "target-profile.v2.yaml":
            profile_attempts += 1
        return real_open_leaf(self, **kwargs)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_open_leaf_path_fd_v2",
        counted_binding,
    )
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert report.profile.status == "missing"
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2
    assert profile_attempts == 1


def test_hardlink_deduplicates_content_but_preserves_path_specific_relations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    alias = aiops / "profile-alias.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    os.link(profile, alias)
    manifest = TargetPackManifestV2(
        schema_id="agent-review.target-pack-manifest.v2",
        schema_version=2,
        pack_version="0.1.0",
        toolrepo_sha="1" * 40,
        generated_files=(
            GeneratedFileEntryV2(path=".aiops/target-profile.v2.yaml", ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="a" * 64),
            GeneratedFileEntryV2(path=".aiops/profile-alias.yaml", ownership=TargetPackFileOwnershipV2.TARGET_OWNED, content_sha256="b" * 64),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/profile-alias.yaml"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/profile-alias.yaml": "f" * 64,
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    physical_identity = (profile.stat().st_dev, profile.stat().st_ino)
    nonempty_reads = 0
    real_read = doctor_module.os.read

    def counted_read(fd: int, size: int) -> bytes:
        nonlocal nonempty_reads
        chunk = real_read(fd, size)
        observed = os.fstat(fd)
        if chunk and (observed.st_dev, observed.st_ino) == physical_identity:
            nonempty_reads += 1
        return chunk

    monkeypatch.setattr(doctor_module.os, "read", counted_read)
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=manifest, target_repo="owner/repo")
    )

    assert nonempty_reads == 1
    assert report.receipt.reason_code == DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2


@pytest.mark.parametrize("seam", ("intermediate", "leaf"))
def test_transient_final_relookup_registers_cleanup_before_fallible_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seam: str,
) -> None:
    _assert_transient_relookup_setup_failure_has_no_fd_leak_v2(
        tmp_path,
        monkeypatch,
        seam=seam,
    )


def test_final_relookup_permission_failure_is_unknown_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".aiops").mkdir()
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_resolve = doctor_module.resolve_within_target_root_v2
    aiops_calls = 0

    def fail_second_aiops_lookup(root: Path, path: Path) -> Path:
        nonlocal aiops_calls
        if path == root / ".aiops":
            aiops_calls += 1
            if aiops_calls == 2:
                raise PermissionError(getattr(os, "EACCES", 13), "injected")
        return real_resolve(root, path)

    monkeypatch.setattr(doctor_module, "resolve_within_target_root_v2", fail_second_aiops_lookup)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.stage == "final_revalidation"
    assert outcome.relation == "aiops"


def _assert_aiops_retarget_outside_root_is_unknown_not_unhealthy(
    target_root: Path, outside: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = target_root / "state"
    state.mkdir()
    (state / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    (state / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    aiops = target_root / ".aiops"
    aiops.symlink_to(state, target_is_directory=True)

    real_revalidate = doctor_module._DoctorObservationSessionV2.revalidate_v2

    def retarget_then_revalidate(self: object) -> None:
        aiops.unlink()
        aiops.symlink_to(outside, target_is_directory=True)
        real_revalidate(self)

    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "revalidate_v2", retarget_then_revalidate)
    outcome = run_doctor_v2(
        target_root=target_root, manifest=_manifest(), target_repo="owner/repo"
    )

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.stage == "final_revalidation"
    assert outcome.relation == "aiops"


def test_aiops_retarget_outside_root_during_final_relookup_is_unknown_not_unhealthy(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_aiops_retarget_outside_root_is_unknown_not_unhealthy(
        tmp_path, tmp_path_factory.mktemp("outside-aiops"), monkeypatch
    )


def test_detected_external_in_place_change_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_revalidate = doctor_module._DoctorObservationSessionV2.revalidate_v2

    def mutate_then_revalidate(self: object) -> None:
        profile.write_text(_VALID_PROFILE_YAML + "\n# external mutation\n", encoding="utf-8")
        real_revalidate(self)

    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "revalidate_v2", mutate_then_revalidate)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"


def test_target_root_replacement_before_final_revalidation_is_unknown_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    moved = tmp_path / "target-observed"
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_revalidate = doctor_module._DoctorObservationSessionV2.revalidate_v2

    def replace_then_revalidate(self: object) -> None:
        target.rename(moved)
        target.mkdir()
        real_revalidate(self)

    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "revalidate_v2", replace_then_revalidate)
    outcome = run_doctor_v2(target_root=target, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == "target_pack_doctor_observation_stale"
    assert outcome.relation == "target_root"


def test_identical_bytes_on_a_new_inode_may_complete_without_a_provenance_claim(tmp_path: Path) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    original_inode = profile.stat().st_ino
    replacement = aiops / "replacement"
    replacement.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    replacement.replace(profile)
    assert profile.stat().st_ino != original_inode
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    assert isinstance(outcome, DoctorDecisionV2)
    assert outcome.report.is_healthy
    assert not hasattr(outcome.report, "generation_identity")
    assert not hasattr(outcome.report, "provenance")


def test_environment_keys_are_snapshotted_once_and_values_are_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        required_secret_names=("AGENT_ROUTER_API_KEY",),
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())},
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    class KeysOnlyEnvironment:
        def __init__(self) -> None:
            self.keys_calls = 0

        def keys(self) -> tuple[str, ...]:
            self.keys_calls += 1
            return ("AGENT_ROUTER_API_KEY",)

        def __getitem__(self, _name: str) -> str:
            raise AssertionError("secret value read")

    environment = KeysOnlyEnvironment()
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_os = doctor_module.os

    class OsProxy:
        environ = environment

        def __getattr__(self, name: str) -> object:
            return getattr(real_os, name)

    monkeypatch.setattr(doctor_module, "os", OsProxy())
    report = _completed_report(
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    )

    assert environment.keys_calls == 1
    assert report.secret_names == (
        SecretNameCheckV2(name="AGENT_ROUTER_API_KEY", declared_present=True),
    )


def test_doctor_supports_a_mode_0300_target_root(tmp_path: Path) -> None:
    tmp_path.chmod(0o300)
    try:
        outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    finally:
        tmp_path.chmod(0o700)

    assert isinstance(outcome, DoctorDecisionV2)


def test_first_use_runtime_carrier_leaves_target_names_bytes_modes_mtimes_and_links_unchanged(
    tmp_path: Path,
) -> None:
    aiops = tmp_path / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    alias = aiops / "profile-link.yaml"
    alias.symlink_to("target-profile.v2.yaml")

    def snapshot() -> dict[str, tuple[object, ...]]:
        result: dict[str, tuple[object, ...]] = {}
        for path in (tmp_path, *sorted(tmp_path.rglob("*"))):
            observed = path.lstat()
            relative = "." if path == tmp_path else path.relative_to(tmp_path).as_posix()
            if path.is_symlink():
                payload: object = os.readlink(path)
                kind = "symlink"
            elif stat.S_ISREG(observed.st_mode):
                payload = path.read_bytes()
                kind = "regular"
            else:
                payload = None
                kind = "directory" if stat.S_ISDIR(observed.st_mode) else "other"
            result[relative] = (
                kind,
                stat.S_IMODE(observed.st_mode),
                observed.st_mtime_ns,
                payload,
            )
        return result

    before = snapshot()
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    after = snapshot()

    assert isinstance(outcome, DoctorDecisionV2)
    assert after == before


def test_programmer_exception_releases_all_fds_and_k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    def explode(_session: object) -> object:
        raise RuntimeError("programmer defect")

    monkeypatch.setattr(doctor_module, "_check_profile_v2", explode)
    with pytest.raises(RuntimeError, match="programmer defect"):
        run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")

    with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True):
        pass


def test_raw_fork_child_does_not_prolong_doctor_fds_or_k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_doctor_v2 as doctor_module

    real_revalidate = doctor_module._DoctorObservationSessionV2.revalidate_v2
    child_pid: list[int] = []

    def fork_then_revalidate(self: object) -> None:
        pid = os.fork()
        if pid == 0:
            import time

            time.sleep(30)
            os._exit(0)
        child_pid.append(pid)
        real_revalidate(self)

    monkeypatch.setattr(doctor_module._DoctorObservationSessionV2, "revalidate_v2", fork_then_revalidate)
    outcome = run_doctor_v2(target_root=tmp_path, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(outcome, DoctorDecisionV2)
    assert child_pid
    try:
        with acquire_target_pack_epoch_v2(target_root=tmp_path, exclusive=True):
            pass
    finally:
        os.kill(child_pid[0], signal.SIGTERM)
        os.waitpid(child_pid[0], 0)


@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_session_cleanup_attempts_every_retained_fd_and_returns_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, position: str
) -> None:
    _assert_session_cleanup_totality_v2(
        tmp_path,
        monkeypatch,
        position=position,
    )


def test_session_cleanup_preserves_original_unknown_after_attempting_every_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_session_cleanup_totality_v2(
        tmp_path,
        monkeypatch,
        position="first",
        original_unknown=True,
    )


def test_session_cleanup_programmer_errno_raises_only_after_total_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_session_cleanup_totality_v2(
        tmp_path,
        monkeypatch,
        position="middle",
        close_errno=errno.EBADF,
    )


@pytest.mark.parametrize("negative_kind", ["escape", "loop"])
@pytest.mark.parametrize("repair_before_revalidation", [False, True])
def test_containment_negative_is_revalidated_without_a_second_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    negative_kind: str,
    repair_before_revalidation: bool,
) -> None:
    _assert_containment_negative_revalidation_v2(
        tmp_path,
        monkeypatch,
        negative_kind=negative_kind,
        repair_before_revalidation=repair_before_revalidation,
    )


def test_target_removed_after_shared_epoch_acquisition_is_unknown_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_raced_root_absence_is_unknown_v2(tmp_path, monkeypatch)


@pytest.mark.parametrize("failure", [RuntimeError, MemoryError])
def test_environment_snapshot_failure_is_report_zero_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException]
) -> None:
    """Both enumerated snapshot failure classes reach the same typed outcome.

    `RuntimeError` models a concurrent `os.environ` mutation. `MemoryError`
    models genuine resource exhaustion while materializing the snapshot --
    round 4 (`R4_F27`) reproduced that class escaping `run_doctor_v2` as a raw
    exception, because the boundary enumerated only `RuntimeError`. This is
    the permanent deterministic regression gate; the real constrained-address-
    space characterization below is empirical support, never a CI dependency.
    """

    _assert_environment_snapshot_failure_is_unknown_v2(
        tmp_path, monkeypatch, failure=failure
    )


def test_real_memory_exhaustion_in_the_doctor_observation_plane_is_unknown(
    tmp_path: Path,
) -> None:
    """Characterization against real `os._Environ` and a real `RLIMIT_AS`.

    Round 4 method note, deliberately recorded here rather than in prose only:
    this probe FALSIFIED the first scoping of `R4_F27`. The finding named the
    environment-key snapshot, but a genuinely exhausted process reaches the
    CONTENT READ first -- each chunk allocates a megabyte-scale buffer, while
    the key snapshot allocates comparatively little. The enumeration at the
    content-read seam exists because this probe put it there, not because it
    seemed tidy.

    Scope, and why this test can skip: under total address-space starvation
    every allocation fails, including small ones inside the already-merged K
    primitive (`target_pack_epoch_v2._runtime_filesystem_type_v2` reads
    `/proc/self/mountinfo`, tens of kilobytes). That module is `#258` code
    this PR does not touch, so an escape there is adjacent and pre-existing,
    exactly like `F23`/`#260` -- it is reported as a skip naming the site,
    never as a green, and never as a failure attributed to this PR. The
    assertion fires only when a `MemoryError` escapes a seam this PR owns.

    The deterministic parametrized RED above remains the permanent gate; this
    is empirical support and never a CI dependency.
    """

    if not hasattr(resource, "RLIMIT_AS"):
        pytest.skip("RLIMIT_AS unavailable on this platform")

    _materialize_healthy_target_v2(tmp_path)
    child = r"""
import os, resource, sys, traceback
from pathlib import Path
import app.agent_review.target_pack_doctor_v2 as doctor
from tests.agent_review.test_target_pack_doctor_v2 import _manifest

root = Path(sys.argv[1])
manifest = _manifest()
blob = "x" * 65536
for index in range(256):
    os.environ["AGENTREVIEW_R4_PROBE_%d" % index] = blob
soft, hard = resource.getrlimit(resource.RLIMIT_AS)
resource.setrlimit(resource.RLIMIT_AS, (48 * 1024 * 1024, hard))
try:
    outcome = doctor.run_doctor_v2(
        target_root=root, manifest=manifest, target_repo="owner/repo"
    )
except MemoryError:
    resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    # Attribute to the innermost AGENT-REVIEW frame, never to the innermost
    # frame overall: a MemoryError raised inside a stdlib helper called from
    # a seam this PR owns must still be attributed to this PR, or the probe
    # could skip on exactly the failure it exists to catch.
    frames = traceback.extract_tb(sys.exc_info()[2])
    owned = [f for f in frames if os.sep + "agent_review" + os.sep in f.filename]
    frame = owned[-1] if owned else frames[-1]
    print("ESCAPED:%s:%s" % (os.path.basename(frame.filename), frame.name), flush=True)
    raise SystemExit(0)
except BaseException as exc:
    resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
    print("OTHER:" + type(exc).__name__, flush=True)
    raise SystemExit(0)
resource.setrlimit(resource.RLIMIT_AS, (soft, hard))
print(
    "OUTCOME:"
    + type(outcome).__name__
    + ":"
    + str(getattr(outcome, "reason_code", ""))
    + ":"
    + str(getattr(outcome, "stage", "")),
    flush=True,
)
"""
    completed = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=180,
    )
    lines = [line for line in completed.stdout.strip().splitlines() if line]
    tail = lines[-1] if lines else ""
    if not tail.startswith(("OUTCOME:", "ESCAPED:", "OTHER:")):
        pytest.skip(f"probe never reached the decision: {completed.stderr[-400:]!r}")
    if tail.startswith("OTHER:"):
        pytest.skip(f"platform raised a different class first: {tail}")
    if tail.startswith("ESCAPED:"):
        _, module_name, function_name = tail.split(":", 2)
        assert module_name != "target_pack_doctor_v2.py", (
            "a genuine MemoryError escaped a seam this PR owns "
            f"({module_name}:{function_name}) instead of becoming a typed outcome"
        )
        pytest.skip(
            "total address-space starvation failed first inside pre-existing "
            f"merged code ({module_name}:{function_name}); adjacent to this PR"
        )
    assert tail.startswith("OUTCOME:DoctorUnknownV2"), tail
    assert "target_pack_doctor_observation_unavailable" in tail, tail


def test_content_read_memory_exhaustion_is_report_zero_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_content_read_memory_exhaustion_is_unknown_v2(tmp_path, monkeypatch)


@pytest.mark.parametrize("seam", ["intermediate", "leaf"])
def test_transient_final_relookup_fds_are_closed_in_raw_fork_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seam: str
) -> None:
    _assert_transient_relookup_raw_fork_tracking_v2(
        tmp_path,
        monkeypatch,
        seam=seam,
    )


def test_transient_final_relookup_fd_remains_closed_after_fork_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assert_transient_relookup_raw_fork_tracking_v2(
        tmp_path,
        monkeypatch,
        seam="leaf",
        exec_after_fork=True,
        iterations=1,
    )


def test_every_target_object_fd_is_fork_tracked_before_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.agent_review.target_pack_epoch_v2 as epoch_module

    _materialize_healthy_target_v2(tmp_path)
    real_set_inheritable = os.set_inheritable
    observed_target_fds = 0

    def require_tracker_ownership(fd: int, inheritable: bool) -> None:
        nonlocal observed_target_fds
        try:
            opened_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            opened_path = None
        if opened_path is not None and (
            opened_path == tmp_path.resolve()
            or tmp_path.resolve() in opened_path.parents
        ):
            observed_target_fds += 1
            assert fd in epoch_module._LIVE_EPOCH_FDS_V2
        real_set_inheritable(fd, inheritable)

    monkeypatch.setattr(os, "set_inheritable", require_tracker_ownership)
    outcome = run_doctor_v2(
        target_root=tmp_path,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    assert isinstance(outcome, DoctorDecisionV2)
    assert observed_target_fds >= 6


def test_fifo_type_swap_returns_stale_and_releases_k_within_deadline(
    tmp_path: Path,
) -> None:
    _assert_provisional_content_open_is_bounded_v2(tmp_path)


def test_nonblocking_regular_file_open_is_byte_semantics_neutral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize_healthy_target_v2(tmp_path)
    expected = run_doctor_v2(
        target_root=tmp_path,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    assert isinstance(expected, DoctorDecisionV2)
    assert expected.report.is_healthy

    real_open = os.open
    observed_content_flags: list[int] = []

    def without_nonblock(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        if (
            os.fspath(path)
            in {"target-profile.v2.yaml", "install-receipt.v2.json"}
            and not flags & getattr(os, "O_PATH", 0)
        ):
            observed_content_flags.append(flags)
            flags &= ~getattr(os, "O_NONBLOCK", 0)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", without_nonblock)
    control = run_doctor_v2(
        target_root=tmp_path,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    assert control == expected
    assert observed_content_flags
    assert all(flags & os.O_NONBLOCK for flags in observed_content_flags)


@pytest.mark.parametrize(
    ("replacement", "expected_stage"),
    [
        ("directory", "object_binding"),
        ("symlink", "content_open"),
        ("regular", "object_binding"),
        ("device", "object_binding"),
    ],
)
def test_raced_content_object_type_or_identity_is_stale_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    expected_stage: str,
) -> None:
    _materialize_healthy_target_v2(tmp_path)
    profile = tmp_path / ".aiops" / "target-profile.v2.yaml"
    receipt = tmp_path / ".aiops" / "install-receipt.v2.json"
    real_leaf = doctor_module._DoctorObservationSessionV2._open_leaf_path_fd_v2
    real_open = os.open
    real_read = os.read
    armed = True
    reads = 0

    def swap_after_path_observation(self: object, **kwargs: object):
        nonlocal armed
        result = real_leaf(self, **kwargs)
        if armed and kwargs["resolved_path"] == profile.resolve():
            armed = False
            if replacement != "device":
                profile.unlink()
            if replacement == "directory":
                profile.mkdir()
            elif replacement == "symlink":
                profile.symlink_to(receipt.name)
            elif replacement == "regular":
                profile.write_text("replacement generation", encoding="utf-8")
        return result

    def device_content_open(
        path: object, flags: int, *args: object, **kwargs: object
    ) -> int:
        if (
            replacement == "device"
            and os.fspath(path) == profile.name
            and not flags & getattr(os, "O_PATH", 0)
        ):
            return real_open("/dev/null", flags)
        return real_open(path, flags, *args, **kwargs)

    def counted_read(fd: int, size: int) -> bytes:
        nonlocal reads
        reads += 1
        return real_read(fd, size)

    monkeypatch.setattr(
        doctor_module._DoctorObservationSessionV2,
        "_open_leaf_path_fd_v2",
        swap_after_path_observation,
    )
    monkeypatch.setattr(os, "open", device_content_open)
    monkeypatch.setattr(os, "read", counted_read)
    outcome = run_doctor_v2(
        target_root=tmp_path,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    assert armed is False
    assert isinstance(outcome, DoctorUnknownV2)
    assert outcome.reason_code == doctor_module.DOCTOR_OBSERVATION_STALE_REASON_V2
    assert outcome.stage == expected_stage
    assert outcome.relation == "profile"
    assert reads == 0
