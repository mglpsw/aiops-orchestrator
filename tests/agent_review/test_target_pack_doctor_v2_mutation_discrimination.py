"""Executable mutation discrimination for the #203 doctor successor.

Runtime cases replace a production symbol and exercise ``run_doctor_v2``.
Structural cases mutate the production source text and feed the resulting AST
to the same architecture discriminator that protects the real module.  The
closed registry at the bottom executes every named mutant from the grant;
there is no separate, satisfiable-by-declaration coverage assertion.
"""

from __future__ import annotations

import ast
import errno
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

import app.agent_review.target_pack_doctor_v2 as doctor
import app.agent_review.target_pack_epoch_v2 as epoch
from app.agent_review.target_pack_doctor_v2 import (
    DOCTOR_OBSERVATION_STALE_REASON_V2,
    DoctorDecisionV2,
    DoctorReportV2,
    DoctorUnknownV2,
    ProfileCheckV2,
    ReceiptCheckV2,
    run_doctor_v2,
)
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_BUSY_REASON_V2,
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
from tests.agent_review import test_target_pack_arch_v2 as arch
from tests.agent_review.test_target_pack_doctor_v2 import (
    _VALID_PROFILE_YAML,
    _assert_aiops_retarget_outside_root_is_unknown_not_unhealthy,
    _assert_aiops_root_self_completed,
    _assert_containment_negative_revalidation_v2,
    _assert_environment_snapshot_failure_is_unknown_v2,
    _assert_operational_lease_entry_failure_is_released_v2,
    _assert_path_object_type_drift_is_unknown,
    _assert_profile_completed_negative_status_is_explicit,
    _assert_provisional_content_open_is_bounded_v2,
    _assert_raced_root_absence_is_unknown_v2,
    _assert_session_cleanup_totality_v2,
    _assert_transient_relookup_raw_fork_tracking_v2,
    _assert_transient_relookup_setup_failure_has_no_fd_leak_v2,
    _manifest,
    _materialize_healthy_target_v2,
    _receipt,
    _sha256,
)


MutationRunner = Callable[[Path, pytest.MonkeyPatch], None]


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def _doctor_source() -> str:
    return arch.DOCTOR_MODULE_PATH.read_text(encoding="utf-8")


def _epoch_source() -> str:
    return (arch.APP_DIR / "target_pack_epoch_v2.py").read_text(encoding="utf-8")


def _tree(source: str) -> ast.Module:
    return ast.parse(source)


def _healthy_install(root: Path, *, manifest: TargetPackManifestV2 | None = None) -> None:
    selected_manifest = manifest or _manifest()
    aiops = root / ".aiops"
    aiops.mkdir(parents=True)
    profile = aiops / "target-profile.v2.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    hashes = {
        entry.path: _sha256((root / entry.path).read_bytes())
        for entry in selected_manifest.generated_files
        if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED
    }
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(selected_manifest),
        target_owned_paths=tuple(sorted(hashes)),
        target_owned_file_hashes=hashes,
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )


def _two_hardlink_manifest() -> TargetPackManifestV2:
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
            GeneratedFileEntryV2(
                path=".aiops/profile-alias.yaml",
                ownership=TargetPackFileOwnershipV2.TARGET_OWNED,
                content_sha256="b" * 64,
            ),
        ),
        schema_digests={"x.json": "a" * 64},
        required_capabilities=("router_transport",),
        min_engine_contract_version=2,
        max_supported_rollout_mode="shadow_minimal",
    )


def _dummy_unhealthy_decision(root: Path) -> DoctorDecisionV2:
    return DoctorDecisionV2(
        report=DoctorReportV2(
            target_root=str(root),
            profile=ProfileCheckV2("missing", None, "mutant"),
            receipt=ReceiptCheckV2("missing", None, "mutant"),
            secret_names=(),
            required_capabilities_declared=(),
        )
    )


def _m_unknown_as_false_report(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(**_: object) -> object:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_BUSY_REASON_V2)

    monkeypatch.setattr(doctor, "acquire_target_pack_epoch_v2", refuse)
    monkeypatch.setattr(doctor, "_unknown_for_epoch_error_v2", lambda _exc: _dummy_unhealthy_decision(root))
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert mutated.report.is_healthy is False
    assert not isinstance(mutated, DoctorUnknownV2)


def _m_invalid_root_as_unknown(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = root / "missing"
    monkeypatch.setattr(
        doctor,
        "_classify_root_binding_failure_v2",
        lambda _exc, **_kwargs: DoctorUnknownV2(
            "mutant", "target_root_binding", "target_root"
        ),
    )
    mutated = run_doctor_v2(target_root=missing, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorUnknownV2)


def _m_all_oserror_unknown(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = root / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    real_open = doctor.os.open

    def all_unknown(**kwargs: object) -> None:
        raise doctor._DoctorUnknownAbortV2(
            doctor.DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
            stage=str(kwargs["stage"]),
            relation=str(kwargs["relation"]),
        )

    def deny_profile(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if str(path).endswith("target-profile.v2.yaml") and flags & os.O_PATH:
            raise PermissionError(errno.EACCES, "mutant")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor, "_raise_classified_observation_oserror_v2", all_unknown)
    monkeypatch.setattr(doctor.os, "open", deny_profile)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorUnknownV2)


def _m_eio_as_unhealthy(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profile = root / ".aiops" / "target-profile.v2.yaml"
    profile.parent.mkdir()
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    real_classifier = doctor._raise_classified_observation_oserror_v2

    def eio_negative(**kwargs: object) -> None:
        exc = kwargs["exc"]
        assert isinstance(exc, OSError)
        if exc.errno == errno.EIO:
            raise doctor._DoctorCompletedNegativeV2(str(kwargs["unreadable_reason"]))
        real_classifier(**kwargs)

    monkeypatch.setattr(doctor, "_raise_classified_observation_oserror_v2", eio_negative)
    monkeypatch.setattr(doctor.os, "read", lambda _fd, _size: (_ for _ in ()).throw(OSError(errno.EIO, "mutant")))
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert mutated.report.is_healthy is False


def _m_release_k_after_profile(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = doctor._check_profile_v2
    writer_entered = False

    def release(session: doctor._DoctorObservationSessionV2) -> ProfileCheckV2:
        nonlocal writer_entered
        result = real(session)
        session._root_binding._lease.release()
        with acquire_target_pack_epoch_v2(target_root=root, exclusive=True):
            writer_entered = True
        return result

    monkeypatch.setattr(doctor, "_check_profile_v2", release)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert writer_entered
    assert isinstance(mutated, DoctorUnknownV2)


def _m_release_k_before_ledger(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _healthy_install(root)
    real = doctor._DoctorObservationSessionV2.observe_sha256_v2
    writer_entered = False

    class MutantObserved(Exception):
        pass

    def release(self: doctor._DoctorObservationSessionV2, **kwargs: object) -> str:
        nonlocal writer_entered
        if str(kwargs["relation"]).startswith("target_owned:"):
            self._root_binding._lease.release()
            with acquire_target_pack_epoch_v2(target_root=root, exclusive=True):
                writer_entered = True
            raise MutantObserved
        return real(self, **kwargs)

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "observe_sha256_v2", release)
    with pytest.raises(MutantObserved):
        run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert writer_entered


def _m_lease_per_file(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _doctor_source(),
        "    ) -> _RetainedObjectV2:\n        resolved = self._resolve_initial_v2(\n",
        "    ) -> _RetainedObjectV2:\n"
        "        acquire_target_pack_epoch_v2(target_root=self._target_root, exclusive=False)\n"
        "        resolved = self._resolve_initial_v2(\n",
    )
    assert not arch._one_literal_shared_epoch_v2(_tree(source))


def _m_reread_profile_for_ledger(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _healthy_install(root)
    real = doctor._DoctorObservationSessionV2.observe_sha256_v2
    profile_identity = ((root / ".aiops" / "target-profile.v2.yaml").stat().st_dev,
                        (root / ".aiops" / "target-profile.v2.yaml").stat().st_ino)
    nonempty_reads = 0
    real_read = doctor.os.read

    def counted(fd: int, size: int) -> bytes:
        nonlocal nonempty_reads
        chunk = real_read(fd, size)
        observed = os.fstat(fd)
        if chunk and (observed.st_dev, observed.st_ino) == profile_identity:
            nonempty_reads += 1
        return chunk

    def reread(self: doctor._DoctorObservationSessionV2, **kwargs: object) -> str:
        result = real(self, **kwargs)
        if kwargs["logical_path"] == Path(".aiops/target-profile.v2.yaml"):
            fd = os.open(root / ".aiops" / "target-profile.v2.yaml", os.O_RDONLY)
            try:
                while counted(fd, 1024 * 1024):
                    pass
            finally:
                os.close(fd)
        return result

    monkeypatch.setattr(doctor.os, "read", counted)
    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "observe_sha256_v2", reread)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert nonempty_reads == 2


def _m_registry_path_only(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _two_hardlink_manifest()
    aiops = root / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    alias = aiops / "profile-alias.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    os.link(profile, alias)
    _healthy_receipt_for_hardlinks(root, manifest, alias_hash=_sha256(_VALID_PROFILE_YAML.encode()))
    identity = (profile.stat().st_dev, profile.stat().st_ino)
    real = doctor._DoctorObservationSessionV2._observe_regular_v2
    real_read = doctor.os.read
    nonempty_reads = 0

    def counted(fd: int, size: int) -> bytes:
        nonlocal nonempty_reads
        chunk = real_read(fd, size)
        observed = os.fstat(fd)
        if chunk and (observed.st_dev, observed.st_ino) == identity:
            nonempty_reads += 1
        return chunk

    def path_only(self: doctor._DoctorObservationSessionV2, **kwargs: object):
        if kwargs["logical_path"] == Path(".aiops/profile-alias.yaml"):
            self._physical_objects.clear()
        return real(self, **kwargs)

    monkeypatch.setattr(doctor.os, "read", counted)
    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "_observe_regular_v2", path_only)
    mutated = run_doctor_v2(target_root=root, manifest=manifest, target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert nonempty_reads == 2


def _healthy_receipt_for_hardlinks(
    root: Path, manifest: TargetPackManifestV2, *, alias_hash: str
) -> None:
    receipt = _receipt(
        manifest_digest=compute_target_pack_manifest_digest_v2(manifest),
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/profile-alias.yaml"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/profile-alias.yaml": alias_hash,
        },
    )
    (root / ".aiops" / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )


def _m_registry_inode_only_relations(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _two_hardlink_manifest()
    aiops = root / ".aiops"
    aiops.mkdir()
    profile = aiops / "target-profile.v2.yaml"
    alias = aiops / "profile-alias.yaml"
    profile.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    os.link(profile, alias)
    _healthy_receipt_for_hardlinks(root, manifest, alias_hash="f" * 64)
    real = doctor._DoctorObservationSessionV2.observe_sha256_v2

    def inode_only(self: doctor._DoctorObservationSessionV2, **kwargs: object) -> str:
        if kwargs["logical_path"] == Path(".aiops/profile-alias.yaml"):
            return "f" * 64
        return real(self, **kwargs)

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "observe_sha256_v2", inode_only)
    mutated = run_doctor_v2(target_root=root, manifest=manifest, target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert mutated.report.is_healthy


def _m_rederive_aiops_path(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _doctor_source(),
        "        raw = session.observe_bytes_v2(\n            logical_path=Path(RECEIPT_RELATIVE_PATH_V2),",
        "        raw = (session._target_root / Path(RECEIPT_RELATIVE_PATH_V2)).read_bytes()\n"
        "        _discarded = session.observe_bytes_v2(\n            logical_path=Path(RECEIPT_RELATIVE_PATH_V2),",
    )
    path_reads = [
        node
        for node in ast.walk(_tree(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"read_bytes", "read_text"}
    ]
    assert path_reads


def _m_skip_final_root_revalidation(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    moved = root.parent / f"{root.name}-observed"

    def skip(self: doctor._DoctorObservationSessionV2) -> None:
        root.rename(moved)
        root.mkdir()

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "_revalidate_root_v2", skip)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)


def _m_skip_final_aiops_revalidation(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _healthy_install(root)
    real = doctor._DoctorObservationSessionV2.revalidate_v2
    old = root / ".aiops-observed"

    def skip(self: doctor._DoctorObservationSessionV2) -> None:
        (root / ".aiops").rename(old)
        (root / ".aiops").mkdir()
        original = self._logical_observations
        original_directories = self._directories
        original_physical = self._physical_objects
        self._logical_observations = [
            item for item in original if not item.logical_path.parts or item.logical_path.parts[0] != ".aiops"
        ]
        self._directories = {}
        self._physical_objects = {}
        try:
            real(self)
        finally:
            self._logical_observations = original
            self._directories = original_directories
            self._physical_objects = original_physical

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "revalidate_v2", skip)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert mutated.report.is_healthy


def _m_receipt_expands_read_domain(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aiops = root / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    extra = aiops / "receipt-only-extra.txt"
    extra.write_text("must not be read", encoding="utf-8")
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml", ".aiops/receipt-only-extra.txt"),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode()),
            ".aiops/receipt-only-extra.txt": _sha256(b"must not be read"),
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    real = doctor._check_receipt_v2
    extra_read = False

    def expand(**kwargs: object) -> ReceiptCheckV2:
        nonlocal extra_read
        session = kwargs["session"]
        assert isinstance(session, doctor._DoctorObservationSessionV2)
        session.observe_sha256_v2(
            logical_path=Path(".aiops/receipt-only-extra.txt"),
            relation="mutant_receipt_extra",
            missing_reason="mutant",
            unreadable_reason="mutant",
        )
        extra_read = True
        return real(**kwargs)

    monkeypatch.setattr(doctor, "_check_receipt_v2", expand)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert extra_read
    assert mutated.report.receipt.reason_code == doctor.DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2


def _m_env_lookup_per_secret(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aiops = root / ".aiops"
    aiops.mkdir()
    (aiops / "target-profile.v2.yaml").write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    receipt = _receipt(
        required_secret_names=("MUTANT_SECRET",),
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )

    class ValueRead(Exception):
        pass

    class Environment:
        def keys(self) -> tuple[str, ...]:
            return ("MUTANT_SECRET",)

        def __getitem__(self, _name: str) -> str:
            raise ValueRead

    real_os = doctor.os

    class OsProxy:
        environ = Environment()

        def __getattr__(self, name: str) -> object:
            return getattr(real_os, name)

    def value_lookup(names: tuple[str, ...], *, environment_keys: frozenset[str]):
        del environment_keys
        return tuple(doctor.SecretNameCheckV2(name, bool(doctor.os.environ[name])) for name in names)

    monkeypatch.setattr(doctor, "os", OsProxy())
    monkeypatch.setattr(doctor, "_check_secret_names_v2", value_lookup)
    with pytest.raises(ValueRead):
        run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")


def _m_internal_symlink_rejected(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    aiops = root / ".aiops"
    aiops.mkdir()
    source = aiops / "profile-source.yaml"
    source.write_text(_VALID_PROFILE_YAML, encoding="utf-8")
    (aiops / "target-profile.v2.yaml").symlink_to(source.name)
    receipt = _receipt(
        target_owned_paths=(".aiops/target-profile.v2.yaml",),
        target_owned_file_hashes={
            ".aiops/target-profile.v2.yaml": _sha256(_VALID_PROFILE_YAML.encode())
        },
    )
    (aiops / "install-receipt.v2.json").write_text(
        json.dumps(receipt.model_dump(mode="json")), encoding="utf-8"
    )
    real = doctor._DoctorObservationSessionV2._resolve_initial_v2

    def reject(self: doctor._DoctorObservationSessionV2, **kwargs: object) -> Path:
        logical_path = kwargs["logical_path"]
        assert isinstance(logical_path, Path)
        if (self.target_root_real / logical_path).is_symlink():
            raise doctor._DoctorCompletedNegativeV2(doctor.DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2)
        return real(self, **kwargs)

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "_resolve_initial_v2", reject)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert not mutated.report.is_healthy


def _m_target_mutation_in_doctor(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _doctor_source(),
        "    def _observe_regular_v2(\n",
        "    def _mutant_target_write_v2(self) -> None:\n"
        "        self._target_root.mkdir(exist_ok=True)\n\n"
        "    def _observe_regular_v2(\n",
    )
    offenders = arch._read_only_offenders_v2(_tree(source), module_name="mutated_doctor")
    assert any("mkdir" in offender for offender in offenders)


def _m_k_exclusive_reader(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _doctor_source(),
        "lease = acquire_target_pack_epoch_v2(target_root=caller_target, exclusive=False)",
        "lease = acquire_target_pack_epoch_v2(target_root=caller_target, exclusive=True)",
    )
    assert not arch._one_literal_shared_epoch_v2(_tree(source))


def _m_fd_inheritable(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _doctor_source(),
        "        try:\n            os.set_inheritable(fd, False)\n        except OSError as exc:\n"
        "            _raise_binding_primitive_oserror_v2(",
        "        try:\n            os.set_inheritable(fd, True)\n        except OSError as exc:\n"
        "            _raise_binding_primitive_oserror_v2(",
    )
    assert arch._fd_inheritable_offenders_v2(_tree(source))


def _m_root_self_resolution_unhandled(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "_is_root_self_v2",
        lambda _self, _resolved_path: False,
    )
    with pytest.raises(RuntimeError, match="resolved path escaped the containment authority"):
        _assert_aiops_root_self_completed(root)


def _m_reason_prefix_classification(_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "_profile_status_for_completed_negative_v2",
        lambda reason_code: (
            "invalid" if reason_code.startswith("target_pack_doctor_path_") else "missing"
        ),
    )
    with pytest.raises(AssertionError):
        _assert_profile_completed_negative_status_is_explicit()


def _m_object_identity_omits_type(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor,
        "_object_identity_v2",
        lambda observed: (observed.st_dev, observed.st_ino),
    )
    with pytest.raises(AssertionError):
        _assert_path_object_type_drift_is_unknown(root, monkeypatch)


def _m_relookup_escape_as_unhealthy(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_revalidate = doctor._DoctorObservationSessionV2.revalidate_v2

    def escape_as_completed(self: doctor._DoctorObservationSessionV2) -> None:
        try:
            real_revalidate(self)
        except doctor._DoctorUnknownAbortV2 as exc:
            if exc.stage == "final_revalidation" and exc.relation == "aiops":
                return
            raise

    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "revalidate_v2",
        escape_as_completed,
    )
    outside = root.parent / f"{root.name}-outside"
    outside.mkdir()
    with pytest.raises(AssertionError):
        _assert_aiops_retarget_outside_root_is_unknown_not_unhealthy(
            root, outside, monkeypatch
        )


def _m_relookup_bespoke_resolver(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    old = (
        "                resolved = resolve_within_target_root_v2(\n"
        "                    self.target_root_real, self.target_root_real / logical.logical_path\n"
        "                )\n"
    )
    source = _replace_once(
        _doctor_source(),
        old,
        "                resolved = (self.target_root_real / logical.logical_path).resolve(strict=False)\n",
    )
    assert arch._containment_resolver_owners_v2(_tree(source)) == ["_resolve_initial_v2"]


def _m_revalidate_via_fstat_only(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _healthy_install(root)
    old = root / ".aiops-observed"

    def fstat_only(self: doctor._DoctorObservationSessionV2) -> None:
        (root / ".aiops").rename(old)
        (root / ".aiops").mkdir()
        for retained in (*self._directories.values(), *self._physical_objects.values()):
            assert stat.S_IFMT(os.fstat(retained.fd).st_mode)

    monkeypatch.setattr(doctor._DoctorObservationSessionV2, "revalidate_v2", fstat_only)
    mutated = run_doctor_v2(target_root=root, manifest=_manifest(), target_repo="owner/repo")
    assert isinstance(mutated, DoctorDecisionV2)
    assert mutated.report.is_healthy


def _m_carrier_domain_blanket_exclusion(_root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    source = _replace_once(
        _epoch_source(),
        "        self._require_active_v2()\n        try:\n            canonical_subject = _canonical_target_subject_v2(target_root)",
        "        self._require_active_v2()\n"
        "        target_root.mkdir(exist_ok=True)\n"
        "        try:\n            canonical_subject = _canonical_target_subject_v2(target_root)",
    )
    effects = arch._epoch_write_effects_v2(_tree(source))
    assert ("bind_target_root_for_observation_v2", "mkdir") in effects


def _m_bind_extension_breaks_writer(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with acquire_target_pack_epoch_v2(target_root=root, exclusive=True) as lease:
        monkeypatch.setattr(
            epoch.TargetPackEpochLeaseV2,
            "bind_target_root_v2",
            epoch.TargetPackEpochLeaseV2.bind_target_root_for_observation_v2,
        )
        real_open = epoch.os.open

        def emfile(path: object, flags: int, *args: object, **kwargs: object) -> int:
            if os.fspath(path) == os.fspath(root.resolve()) and flags & os.O_PATH:
                raise OSError(errno.EMFILE, "mutant")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(epoch.os, "open", emfile)
        with pytest.raises(TargetPackObservationBindingErrorV2) as raised:
            lease.bind_target_root_v2(target_root=root)
    assert type(raised.value) is TargetPackObservationBindingErrorV2
    assert raised.value.operation_errno == errno.EMFILE


def _m_transient_fd_registered_after_fallible_setup(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vulnerable_lookup(
        self: doctor._DoctorObservationSessionV2,
        resolved_path: Path,
        *,
        relation: str,
    ) -> tuple[str, os.stat_result | None]:
        relative = resolved_path.relative_to(self.target_root_real)
        parent_fd = self._root_fd_v2(
            stage="final_revalidation", relation=relation
        )
        opened: list[int] = []
        try:
            for component in relative.parts[:-1]:
                fd = os.open(
                    component,
                    os.O_PATH
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                os.set_inheritable(fd, False)
                opened.append(fd)
                parent_fd = fd
            leaf_fd = os.open(
                relative.parts[-1],
                os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            os.set_inheritable(leaf_fd, False)
            opened.append(leaf_fd)
            observed = os.fstat(leaf_fd)
            return ("present", observed)
        finally:
            for fd in reversed(opened):
                os.close(fd)

    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        vulnerable_lookup,
    )
    with pytest.raises(AssertionError):
        _assert_transient_relookup_setup_failure_has_no_fd_leak_v2(
            root,
            monkeypatch,
            seam="leaf",
            iterations=1,
        )


def _m_lease_entry_failure_without_explicit_release(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vulnerable_run(
        *,
        target_root: Path,
        manifest: TargetPackManifestV2,
        target_repo: str,
    ) -> DoctorDecisionV2:
        del manifest, target_repo
        lease = doctor.acquire_target_pack_epoch_v2(
            target_root=target_root, exclusive=False
        )
        with lease:
            return _dummy_unhealthy_decision(target_root)

    monkeypatch.setattr(doctor, "run_doctor_v2", vulnerable_run)
    with pytest.raises(OSError) as raised:
        _assert_operational_lease_entry_failure_is_released_v2(
            root,
            monkeypatch,
            seam="namespace",
            iterations=1,
        )
    assert raised.value.errno == errno.EIO


def _m_session_cleanup_aborts_on_first_close_failure(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vulnerable_close(self: doctor._DoctorObservationSessionV2) -> None:
        if self._closed:
            return
        self._closed = True
        retained_fds = {
            retained.fd
            for retained in (*self._directories.values(), *self._physical_objects.values())
        }
        for fd in sorted(retained_fds, reverse=True):
            self._root_binding.release_observation_fd_v2(fd)
        self._directories.clear()
        self._physical_objects.clear()
        self._resolved_observations.clear()

    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "close",
        vulnerable_close,
    )
    with pytest.raises(AssertionError) as raised:
        _assert_session_cleanup_totality_v2(
            root,
            monkeypatch,
            position="first",
            iterations=1,
        )
    message = str(raised.value)
    assert "cleanup-attempt-count" in message
    assert "outcome-taxonomy" in message


def _m_transient_relookup_fd_not_fork_tracked(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vulnerable_lookup(
        self: doctor._DoctorObservationSessionV2,
        resolved_path: Path,
        *,
        relation: str,
    ) -> tuple[str, os.stat_result | None]:
        if self._is_root_self_v2(resolved_path):
            return (
                "present",
                self._root_self_stat_v2(
                    stage="final_revalidation", relation=relation
                ),
            )
        relative = resolved_path.relative_to(self.target_root_real)
        parent_fd = self._root_fd_v2(
            stage="final_revalidation", relation=relation
        )
        opened: list[int] = []
        try:
            for component in relative.parts[:-1]:
                fd = os.open(
                    component,
                    os.O_PATH
                    | os.O_DIRECTORY
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                opened.append(fd)
                os.set_inheritable(fd, False)
                parent_fd = fd
            leaf_fd = os.open(
                relative.parts[-1],
                os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            opened.append(leaf_fd)
            os.set_inheritable(leaf_fd, False)
            observed = os.fstat(leaf_fd)
            if stat.S_ISLNK(observed.st_mode):
                raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))
            return ("present", observed)
        except OSError as exc:
            if exc.errno in doctor._STABLE_MISSING_ERRNOS_V2:
                return ("missing", None)
            raise
        finally:
            for fd in reversed(opened):
                os.close(fd)

    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "_transient_current_lookup_v2",
        vulnerable_lookup,
    )

    no_fork_root = root / "no-fork"
    _materialize_healthy_target_v2(no_fork_root)
    baseline_tracker = set(epoch._LIVE_EPOCH_FDS_V2)
    no_fork = run_doctor_v2(
        target_root=no_fork_root,
        manifest=_manifest(),
        target_repo="owner/repo",
    )
    assert isinstance(no_fork, DoctorDecisionV2)
    assert set(epoch._LIVE_EPOCH_FDS_V2) == baseline_tracker

    with pytest.raises(AssertionError, match="OPEN"):
        _assert_transient_relookup_raw_fork_tracking_v2(
            root / "raw-fork",
            monkeypatch,
            seam="leaf",
            iterations=1,
        )


def _m_provisional_content_open_can_block_on_type_swap(
    root: Path, _monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AssertionError, match="retained K beyond deadline"):
        _assert_provisional_content_open_is_bounded_v2(
            root,
            strip_nonblock=True,
            deadline_seconds=0.25,
        )


def _m_retry_descriptor_after_failed_close(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def vulnerable_release(self: object, fd: int) -> None:
        if fd in self._lease._observation_fds:
            try:
                os.close(fd)
            except OSError:
                # Mutant: retain the numeric descriptor for lease.release,
                # which can now refer to an unrelated resource.
                raise
            else:
                self._lease._observation_fds.remove(fd)
                epoch._LIVE_EPOCH_FDS_V2.discard(fd)

    monkeypatch.setattr(
        epoch.TargetPackTargetBindingV2,
        "release_observation_fd_v2",
        vulnerable_release,
    )
    with pytest.raises(AssertionError, match="unrelated-descriptor-reclosed"):
        _assert_session_cleanup_totality_v2(
            root,
            monkeypatch,
            position="first",
            iterations=1,
        )


def _m_containment_negative_not_revalidated(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resolve = doctor._DoctorObservationSessionV2._resolve_initial_v2

    def omit_negative(self: object, **kwargs: object) -> Path:
        before = len(self._logical_observations)
        try:
            return real_resolve(self, **kwargs)
        except doctor._DoctorCompletedNegativeV2:
            del self._logical_observations[before:]
            raise

    monkeypatch.setattr(
        doctor._DoctorObservationSessionV2,
        "_resolve_initial_v2",
        omit_negative,
    )
    with pytest.raises(AssertionError, match="containment_negative"):
        _assert_containment_negative_revalidation_v2(
            root,
            monkeypatch,
            negative_kind="escape",
            repair_before_revalidation=True,
        )


def _m_raced_root_absence_as_input_error(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def old_classification(
        exc: TargetPackObservationBindingErrorV2,
        *,
        initial_subject: str,
    ) -> DoctorUnknownV2:
        del initial_subject
        if exc.operation_errno in doctor._STABLE_MISSING_ERRNOS_V2:
            raise doctor.DoctorInputErrorV2(
                doctor.DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2
            ) from exc
        return DoctorUnknownV2(
            doctor.DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
            "target_root_binding",
            "target_root",
        )

    monkeypatch.setattr(
        doctor,
        "_classify_root_binding_failure_v2",
        old_classification,
    )
    with pytest.raises(doctor.DoctorInputErrorV2):
        _assert_raced_root_absence_is_unknown_v2(root, monkeypatch)


def _m_env_snapshot_outside_typed_boundary(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        doctor,
        "_snapshot_environment_keys_v2",
        lambda: frozenset(doctor.os.environ.keys()),
    )
    with pytest.raises(RuntimeError, match="dictionary changed size"):
        _assert_environment_snapshot_failure_is_unknown_v2(root, monkeypatch)


_MUTATION_IMPLEMENTATIONS: dict[str, MutationRunner] = {
    "M_UNKNOWN_AS_FALSE_REPORT": _m_unknown_as_false_report,
    "M_INVALID_ROOT_AS_UNKNOWN": _m_invalid_root_as_unknown,
    "M_ALL_OSERROR_UNKNOWN": _m_all_oserror_unknown,
    "M_EIO_AS_UNHEALTHY": _m_eio_as_unhealthy,
    "M_RELEASE_K_AFTER_PROFILE": _m_release_k_after_profile,
    "M_RELEASE_K_BEFORE_LEDGER": _m_release_k_before_ledger,
    "M_LEASE_PER_FILE": _m_lease_per_file,
    "M_REREAD_PROFILE_FOR_LEDGER": _m_reread_profile_for_ledger,
    "M_REGISTRY_PATH_ONLY": _m_registry_path_only,
    "M_REGISTRY_INODE_ONLY_RELATIONS": _m_registry_inode_only_relations,
    "M_REDERIVE_AIOPS_PATH": _m_rederive_aiops_path,
    "M_SKIP_FINAL_ROOT_REVALIDATION": _m_skip_final_root_revalidation,
    "M_SKIP_FINAL_AIOPS_REVALIDATION": _m_skip_final_aiops_revalidation,
    "M_RECEIPT_EXPANDS_READ_DOMAIN": _m_receipt_expands_read_domain,
    "M_ENV_LOOKUP_PER_SECRET": _m_env_lookup_per_secret,
    "M_INTERNAL_SYMLINK_REJECTED": _m_internal_symlink_rejected,
    "M_TARGET_MUTATION_IN_DOCTOR": _m_target_mutation_in_doctor,
    "M_K_EXCLUSIVE_READER": _m_k_exclusive_reader,
    "M_FD_INHERITABLE": _m_fd_inheritable,
    "M_ROOT_SELF_RESOLUTION_UNHANDLED": _m_root_self_resolution_unhandled,
    "M_REASON_PREFIX_CLASSIFICATION": _m_reason_prefix_classification,
    "M_OBJECT_IDENTITY_OMITS_TYPE": _m_object_identity_omits_type,
    "M_RELOOKUP_ESCAPE_AS_UNHEALTHY": _m_relookup_escape_as_unhealthy,
    "M_RELOOKUP_BESPOKE_RESOLVER": _m_relookup_bespoke_resolver,
    "M_REVALIDATE_VIA_FSTAT_ONLY": _m_revalidate_via_fstat_only,
    "M_CARRIER_DOMAIN_BLANKET_EXCLUSION": _m_carrier_domain_blanket_exclusion,
    "M_BIND_EXTENSION_BREAKS_WRITER": _m_bind_extension_breaks_writer,
    "M_TRANSIENT_FD_REGISTERED_AFTER_FALLIBLE_SETUP": (
        _m_transient_fd_registered_after_fallible_setup
    ),
    "M_LEASE_ENTRY_FAILURE_WITHOUT_EXPLICIT_RELEASE": (
        _m_lease_entry_failure_without_explicit_release
    ),
    "M_SESSION_CLEANUP_ABORTS_ON_FIRST_CLOSE_FAILURE": (
        _m_session_cleanup_aborts_on_first_close_failure
    ),
    "M_TRANSIENT_RELOOKUP_FD_NOT_FORK_TRACKED": (
        _m_transient_relookup_fd_not_fork_tracked
    ),
    "M_PROVISIONAL_CONTENT_OPEN_CAN_BLOCK_ON_TYPE_SWAP": (
        _m_provisional_content_open_can_block_on_type_swap
    ),
    "M_RETRY_DESCRIPTOR_AFTER_FAILED_CLOSE": (
        _m_retry_descriptor_after_failed_close
    ),
    "M_CONTAINMENT_NEGATIVE_NOT_REVALIDATED": (
        _m_containment_negative_not_revalidated
    ),
    "M_RACED_ROOT_ABSENCE_AS_INPUT_ERROR": (
        _m_raced_root_absence_as_input_error
    ),
    "M_ENV_SNAPSHOT_OUTSIDE_TYPED_BOUNDARY": (
        _m_env_snapshot_outside_typed_boundary
    ),
}


@pytest.mark.parametrize("mutation_name", sorted(_MUTATION_IMPLEMENTATIONS))
def test_each_granted_mutant_is_executed_and_discriminated(
    mutation_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _MUTATION_IMPLEMENTATIONS[mutation_name](tmp_path, monkeypatch)
