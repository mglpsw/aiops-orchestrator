"""Coherent, target-read-only diagnostics for the AgentReview v2 target pack.

One completed doctor decision consumes every material target-filesystem
observation inside one cooperative K-SH epoch.  The epoch excludes
participating writers; retained directory/file descriptors bind the decision
to the objects it actually observed.  The receipt remains a declaration to
evaluate, never authority over the current filesystem.

The strong claim is deliberately narrow: same host, EUID and mount namespace,
with every AgentReview writer participating in the merged external-K protocol.
External writers and undetectable ABA remain explicit non-claims.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, NoReturn

from app.agent_review.profile_loader_v2 import (
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TARGET_PROFILE_MISSING_REASON_V2,
    TARGET_PROFILE_UNREADABLE_REASON_V2,
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_text_v2,
)
from app.agent_review.target_pack_epoch_v2 import (
    TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2,
    TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2,
    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
    TargetPackEpochError,
    TargetPackObservationBindingErrorV2,
    TargetPackTargetBindingV2,
    acquire_target_pack_epoch_v2,
)
from app.agent_review.target_pack_manifest_v2 import (
    TargetPackFileOwnershipV2,
    TargetPackManifestV2,
    compute_target_pack_manifest_digest_v2,
)
from app.agent_review.target_pack_plan_v2 import (
    PLAN_PATH_RESOLUTION_FAILED_REASON_V2,
    PlanError,
    resolve_within_target_root_v2,
    rollout_mode_exceeds_pack_capability_v2,
)
from app.agent_review.target_pack_receipt_v2 import (
    RECEIPT_RELATIVE_PATH_V2,
    TargetInstallReceiptV2,
    compute_portable_target_root_identity_v2,
    load_target_install_receipt_bytes_v2,
)
from pydantic import ValidationError


DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2 = "target_pack_doctor_target_root_not_a_directory"
DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2 = "target_pack_doctor_path_escapes_target_root"
DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2 = "target_pack_doctor_path_resolution_failed"
DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_repo_mismatch"
DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_owned_set_mismatch"
DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_pack_version_mismatch"
DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_toolrepo_sha_mismatch"
DOCTOR_RECEIPT_MANIFEST_DIGEST_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_manifest_digest_mismatch"
DOCTOR_RECEIPT_TARGET_ROOT_IDENTITY_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_target_root_identity_mismatch"
DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2 = "target_pack_doctor_receipt_profile_hash_mismatch"
DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2 = "target_owned_identity_unreconciled"
DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2 = "target_pack_doctor_receipt_rollout_exceeds_pack_capability"
DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2 = "target_pack_doctor_observation_unavailable"
DOCTOR_OBSERVATION_STALE_REASON_V2 = "target_pack_doctor_observation_stale"

_PROFILE_RELATION_V2 = "profile"
_RECEIPT_RELATION_V2 = "receipt"
_AIOPS_RELATION_V2 = "aiops"
_ROOT_RELATION_V2 = "target_root"
_AIOPS_DIR_RELATIVE_V2 = DEFAULT_TARGET_PROFILE_RELATIVE_PATH.parent


class DoctorArtifactLocationContractError(RuntimeError):
    """Profile and receipt stopped sharing the one retained parent role."""


if PurePosixPath(RECEIPT_RELATIVE_PATH_V2).parent != PurePosixPath(_AIOPS_DIR_RELATIVE_V2):
    raise DoctorArtifactLocationContractError("profile and receipt no longer share one .aiops parent")


@dataclass(frozen=True)
class ProfileCheckV2:
    status: str  # "present" | "missing" | "invalid"
    profile_hash: str | None
    reason_code: str | None


@dataclass(frozen=True)
class ReceiptCheckV2:
    status: str  # "present" | "missing" | "invalid"
    receipt: TargetInstallReceiptV2 | None
    reason_code: str | None


@dataclass(frozen=True)
class SecretNameCheckV2:
    name: str
    declared_present: bool


@dataclass(frozen=True)
class DoctorReportV2:
    """A completed diagnosis only; observational failure has no report."""

    target_root: str
    profile: ProfileCheckV2
    receipt: ReceiptCheckV2
    secret_names: tuple[SecretNameCheckV2, ...]
    required_capabilities_declared: tuple[str, ...]

    @property
    def is_healthy(self) -> bool:
        return (
            self.profile.status == "present"
            and self.receipt.status == "present"
            and all(check.declared_present for check in self.secret_names)
        )


@dataclass(frozen=True)
class DoctorDecisionV2:
    report: DoctorReportV2

    @property
    def decision_status(self) -> Literal["healthy", "unhealthy"]:
        return "healthy" if self.report.is_healthy else "unhealthy"


@dataclass(frozen=True)
class DoctorUnknownV2:
    reason_code: str
    stage: str
    relation: str

    @property
    def decision_status(self) -> Literal["unknown"]:
        return "unknown"


DoctorRunOutcomeV2 = DoctorDecisionV2 | DoctorUnknownV2


class DoctorInputErrorV2(ValueError):
    """The invocation does not designate a diagnosable target subject."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _DoctorUnknownAbortV2(Exception):
    def __init__(self, reason_code: str, *, stage: str, relation: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.stage = stage
        self.relation = relation


class _DoctorCompletedNegativeV2(Exception):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


_RESOURCE_UNAVAILABLE_ERRNOS_V2 = frozenset(
    value
    for value in (
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOMEM,
        errno.EIO,
        getattr(errno, "ESTALE", None),
        errno.EINTR,
    )
    if value is not None
)
_PROGRAMMER_ERRNOS_V2 = frozenset({errno.EBADF, errno.EINVAL})
_STABLE_MISSING_ERRNOS_V2 = frozenset({errno.ENOENT, errno.ENOTDIR})
_STABLE_UNREADABLE_ERRNOS_V2 = frozenset({errno.EACCES, errno.EPERM})
_PROFILE_STATUS_BY_COMPLETED_NEGATIVE_REASON_V2: dict[str, Literal["missing", "invalid"]] = {
    TARGET_PROFILE_MISSING_REASON_V2: "missing",
    TARGET_PROFILE_UNREADABLE_REASON_V2: "missing",
    DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2: "invalid",
    DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2: "invalid",
}


def _raise_classified_observation_oserror_v2(
    *,
    stage: str,
    relation: str,
    exc: OSError,
    missing_reason: str,
    unreadable_reason: str,
) -> NoReturn:
    """The single stage/relation/errno classification authority.

    No caller is allowed to turn an ``OSError`` into a decision by
    superclass alone.  Final revalidation is intentionally asymmetric with
    initial diagnosis: it may only confirm stability or make the whole
    decision UNKNOWN.
    """

    operation_errno = exc.errno
    if operation_errno in _PROGRAMMER_ERRNOS_V2 or operation_errno is None:
        raise exc
    if stage == "final_revalidation":
        raise _DoctorUnknownAbortV2(
            DOCTOR_OBSERVATION_STALE_REASON_V2, stage=stage, relation=relation
        ) from exc
    if operation_errno in _RESOURCE_UNAVAILABLE_ERRNOS_V2:
        raise _DoctorUnknownAbortV2(
            DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2, stage=stage, relation=relation
        ) from exc
    if operation_errno == errno.ELOOP:
        raise _DoctorUnknownAbortV2(
            DOCTOR_OBSERVATION_STALE_REASON_V2, stage=stage, relation=relation
        ) from exc
    if operation_errno in _STABLE_MISSING_ERRNOS_V2:
        raise _DoctorCompletedNegativeV2(missing_reason) from exc
    if operation_errno in _STABLE_UNREADABLE_ERRNOS_V2:
        raise _DoctorCompletedNegativeV2(unreadable_reason) from exc
    if operation_errno in {errno.ENAMETOOLONG, errno.EISDIR}:
        raise _DoctorCompletedNegativeV2(missing_reason) from exc
    raise exc


def _raise_binding_primitive_oserror_v2(
    *, stage: str, relation: str, exc: OSError
) -> NoReturn:
    """Classify failure of the observation capability, not artifact content.

    Once an FD is open, failure to make it non-inheritable or to obtain its
    metadata cannot demonstrate an installed-state conformance negative.
    It means the observation capability could not be established safely.
    """

    if exc.errno in _PROGRAMMER_ERRNOS_V2 or exc.errno is None:
        raise exc
    raise _DoctorUnknownAbortV2(
        DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
        stage=stage,
        relation=relation,
    ) from exc


def _doctor_reason_for_plan_error_v2(exc: PlanError) -> str:
    if exc.reason_code == PLAN_PATH_RESOLUTION_FAILED_REASON_V2:
        return DOCTOR_PATH_RESOLUTION_FAILED_REASON_V2
    return DOCTOR_PATH_ESCAPES_TARGET_ROOT_REASON_V2


def _profile_status_for_completed_negative_v2(
    reason_code: str,
) -> Literal["missing", "invalid"]:
    try:
        return _PROFILE_STATUS_BY_COMPLETED_NEGATIVE_REASON_V2[reason_code]
    except KeyError as exc:
        raise RuntimeError("unclassified completed-negative profile reason") from exc


def _file_type_v2(mode: int) -> int:
    return stat.S_IFMT(mode)


def _object_identity_v2(observed: os.stat_result) -> tuple[int, int, int]:
    return (observed.st_dev, observed.st_ino, _file_type_v2(observed.st_mode))


def _metadata_identity_v2(observed: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        observed.st_dev,
        observed.st_ino,
        _file_type_v2(observed.st_mode),
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
        observed.st_nlink,
    )


@dataclass
class _RetainedObjectV2:
    fd: int
    identity: tuple[int, int, int]
    metadata: tuple[int, int, int, int, int, int, int]
    content_bytes: bytes | None = None
    sha256: str | None = None
    content_acquisitions: int = 0


@dataclass(frozen=True)
class _LogicalObservationV2:
    logical_path: Path
    relation: str
    kind: Literal["missing", "directory", "regular", "unreadable", "non_regular"]
    resolved_path: Path
    object_identity: tuple[int, int, int] | None


@dataclass(frozen=True)
class _ResolvedObservationV2:
    kind: Literal["missing", "directory", "regular", "unreadable", "non_regular"]
    object_identity: tuple[int, int, int] | None


class _DoctorObservationSessionV2:
    """One K-bound registry of logical relations and retained objects."""

    def __init__(
        self,
        *,
        target_root: Path,
        root_binding: TargetPackTargetBindingV2,
    ) -> None:
        self._target_root = target_root
        self._root_binding = root_binding
        try:
            self.target_root_real = Path(root_binding.target_root_real)
            self._root_object_identity = root_binding.object_identity
        except TargetPackEpochError as exc:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="observation_session_start",
                relation=_ROOT_RELATION_V2,
            ) from exc
        except OSError as exc:
            _raise_binding_primitive_oserror_v2(
                stage="observation_session_start",
                relation=_ROOT_RELATION_V2,
                exc=exc,
            )
        self._directories: dict[Path, _RetainedObjectV2] = {}
        self._physical_objects: dict[tuple[int, int, int], _RetainedObjectV2] = {}
        self._resolved_observations: dict[Path, _ResolvedObservationV2] = {}
        self._logical_observations: list[_LogicalObservationV2] = []
        self._closed = False

    def __enter__(self) -> "_DoctorObservationSessionV2":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
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

    def _resolve_initial_v2(
        self,
        *,
        logical_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
    ) -> Path:
        try:
            return resolve_within_target_root_v2(
                self.target_root_real, self.target_root_real / logical_path
            )
        except PlanError as exc:
            raise _DoctorCompletedNegativeV2(_doctor_reason_for_plan_error_v2(exc)) from exc
        except OSError as exc:
            _raise_classified_observation_oserror_v2(
                stage="initial_resolution",
                relation=relation,
                exc=exc,
                missing_reason=missing_reason,
                unreadable_reason=unreadable_reason,
            )

    def _prepare_fd_v2(
        self,
        *,
        fd: int,
        relation: str,
    ) -> os.stat_result:
        try:
            os.set_inheritable(fd, False)
        except OSError as exc:
            _raise_binding_primitive_oserror_v2(
                stage="fd_noninheritability", relation=relation, exc=exc
            )
        try:
            return os.fstat(fd)
        except OSError as exc:
            _raise_binding_primitive_oserror_v2(
                stage="fd_metadata", relation=relation, exc=exc
            )

    def _retain_fd_v2(
        self,
        *,
        fd: int,
        relation: str,
    ) -> None:
        try:
            self._root_binding.retain_observation_fd_v2(fd)
        except TargetPackObservationBindingErrorV2 as exc:
            if exc.operation_errno is None:
                raise
            _raise_binding_primitive_oserror_v2(
                stage=exc.stage,
                relation=relation,
                exc=OSError(exc.operation_errno, os.strerror(exc.operation_errno)),
            )
        except TargetPackEpochError as exc:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="object_binding",
                relation=relation,
            ) from exc
        except OSError as exc:
            _raise_binding_primitive_oserror_v2(
                stage="retain_observation_fd",
                relation=relation,
                exc=exc,
            )

    def _root_fd_v2(self, *, stage: str, relation: str) -> int:
        try:
            return self._root_binding.fd
        except TargetPackEpochError as exc:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage=stage,
                relation=relation,
            ) from exc
        except OSError as exc:
            if stage == "final_revalidation":
                _raise_classified_observation_oserror_v2(
                    stage=stage,
                    relation=relation,
                    exc=exc,
                    missing_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                    unreadable_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                )
            _raise_binding_primitive_oserror_v2(
                stage=stage,
                relation=relation,
                exc=exc,
            )

    def _is_root_self_v2(self, resolved_path: Path) -> bool:
        return resolved_path == self.target_root_real

    def _root_self_stat_v2(self, *, stage: str, relation: str) -> os.stat_result:
        root_fd = self._root_fd_v2(stage=stage, relation=relation)
        try:
            observed = os.fstat(root_fd)
        except OSError as exc:
            if stage == "final_revalidation":
                _raise_classified_observation_oserror_v2(
                    stage=stage,
                    relation=relation,
                    exc=exc,
                    missing_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                    unreadable_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                )
            _raise_binding_primitive_oserror_v2(stage=stage, relation=relation, exc=exc)
        if (observed.st_dev, observed.st_ino) != self._root_object_identity:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage=stage,
                relation=relation,
            )
        return observed

    def _diagnose_component_open_error_v2(
        self,
        *,
        parent_fd: int,
        name: str,
        relation: str,
        exc: OSError,
        missing_reason: str,
        unreadable_reason: str,
    ) -> NoReturn:
        if exc.errno == errno.ENOTDIR:
            try:
                component = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as stat_exc:
                _raise_classified_observation_oserror_v2(
                    stage="object_binding",
                    relation=relation,
                    exc=stat_exc,
                    missing_reason=missing_reason,
                    unreadable_reason=unreadable_reason,
                )
            if stat.S_ISLNK(component.st_mode):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                ) from exc
        _raise_classified_observation_oserror_v2(
            stage="object_binding",
            relation=relation,
            exc=exc,
            missing_reason=missing_reason,
            unreadable_reason=unreadable_reason,
        )

    def _open_parent_directory_v2(
        self,
        *,
        resolved_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
    ) -> int:
        try:
            relative_parent = resolved_path.parent.relative_to(self.target_root_real)
        except ValueError as exc:
            raise RuntimeError("resolved path escaped the containment authority") from exc

        parent_fd = self._root_fd_v2(stage="object_binding", relation=relation)
        prefix = self.target_root_real
        for component in relative_parent.parts:
            try:
                candidate_fd = os.open(
                    component,
                    os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                self._diagnose_component_open_error_v2(
                    parent_fd=parent_fd,
                    name=component,
                    relation=relation,
                    exc=exc,
                    missing_reason=missing_reason,
                    unreadable_reason=unreadable_reason,
                )
            try:
                observed = self._prepare_fd_v2(
                    fd=candidate_fd,
                    relation=relation,
                )
                if not stat.S_ISDIR(observed.st_mode):
                    raise _DoctorCompletedNegativeV2(missing_reason)
                prefix = prefix / component
                retained = self._directories.get(prefix)
                if retained is None:
                    self._retain_fd_v2(
                        fd=candidate_fd,
                        relation=relation,
                    )
                    retained = _RetainedObjectV2(
                        fd=candidate_fd,
                        identity=_object_identity_v2(observed),
                        metadata=_metadata_identity_v2(observed),
                    )
                    self._directories[prefix] = retained
                    candidate_fd = -1
                elif retained.identity != _object_identity_v2(observed):
                    raise _DoctorUnknownAbortV2(
                        DOCTOR_OBSERVATION_STALE_REASON_V2,
                        stage="object_binding",
                        relation=relation,
                    )
                parent_fd = retained.fd
            finally:
                if candidate_fd >= 0:
                    os.close(candidate_fd)
        return parent_fd

    def _open_leaf_path_fd_v2(
        self,
        *,
        parent_fd: int,
        resolved_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
    ) -> tuple[int, os.stat_result]:
        try:
            fd = os.open(
                resolved_path.name,
                os.O_PATH | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            _raise_classified_observation_oserror_v2(
                stage="object_binding",
                relation=relation,
                exc=exc,
                missing_reason=missing_reason,
                unreadable_reason=unreadable_reason,
            )
        try:
            observed = self._prepare_fd_v2(
                fd=fd,
                relation=relation,
            )
            if stat.S_ISLNK(observed.st_mode):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                )
            return fd, observed
        except BaseException:
            os.close(fd)
            raise

    def observe_directory_v2(self, *, logical_path: Path, relation: str) -> None:
        resolved = self._resolve_initial_v2(
            logical_path=logical_path,
            relation=relation,
            missing_reason=TARGET_PROFILE_MISSING_REASON_V2,
            unreadable_reason=TARGET_PROFILE_UNREADABLE_REASON_V2,
        )
        if self._is_root_self_v2(resolved):
            observed = self._root_self_stat_v2(stage="object_binding", relation=relation)
            if not stat.S_ISDIR(observed.st_mode):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                )
            identity = _object_identity_v2(observed)
            self._logical_observations.append(
                _LogicalObservationV2(logical_path, relation, "directory", resolved, identity)
            )
            self._resolved_observations[resolved] = _ResolvedObservationV2(
                "directory", identity
            )
            return
        parent_fd = self._open_parent_directory_v2(
            resolved_path=resolved,
            relation=relation,
            missing_reason=TARGET_PROFILE_MISSING_REASON_V2,
            unreadable_reason=TARGET_PROFILE_UNREADABLE_REASON_V2,
        )
        try:
            leaf_fd, observed = self._open_leaf_path_fd_v2(
                parent_fd=parent_fd,
                resolved_path=resolved,
                relation=relation,
                missing_reason=TARGET_PROFILE_MISSING_REASON_V2,
                unreadable_reason=TARGET_PROFILE_UNREADABLE_REASON_V2,
            )
        except _DoctorCompletedNegativeV2:
            self._resolved_observations[resolved] = _ResolvedObservationV2("missing", None)
            self._logical_observations.append(
                _LogicalObservationV2(logical_path, relation, "missing", resolved, None)
            )
            raise
        if not stat.S_ISDIR(observed.st_mode):
            try:
                self._retain_fd_v2(
                    fd=leaf_fd,
                    relation=relation,
                )
            except BaseException:
                os.close(leaf_fd)
                raise
            retained = _RetainedObjectV2(
                fd=leaf_fd,
                identity=_object_identity_v2(observed),
                metadata=_metadata_identity_v2(observed),
            )
            self._physical_objects.setdefault(retained.identity, retained)
            if self._physical_objects[retained.identity] is not retained:
                self._root_binding.release_observation_fd_v2(leaf_fd)
            self._logical_observations.append(
                _LogicalObservationV2(logical_path, relation, "non_regular", resolved, retained.identity)
            )
            self._resolved_observations[resolved] = _ResolvedObservationV2(
                "non_regular", retained.identity
            )
            raise _DoctorCompletedNegativeV2(TARGET_PROFILE_MISSING_REASON_V2)
        existing = self._directories.get(resolved)
        if existing is None:
            try:
                self._retain_fd_v2(
                    fd=leaf_fd,
                    relation=relation,
                )
            except BaseException:
                os.close(leaf_fd)
                raise
            existing = _RetainedObjectV2(
                fd=leaf_fd,
                identity=_object_identity_v2(observed),
                metadata=_metadata_identity_v2(observed),
            )
            self._directories[resolved] = existing
        else:
            os.close(leaf_fd)
            if existing.identity != _object_identity_v2(observed):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                )
        self._logical_observations.append(
            _LogicalObservationV2(logical_path, relation, "directory", resolved, existing.identity)
        )
        self._resolved_observations[resolved] = _ResolvedObservationV2(
            "directory", existing.identity
        )

    def _observe_regular_v2(
        self,
        *,
        logical_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
        require_bytes: bool,
    ) -> _RetainedObjectV2:
        resolved = self._resolve_initial_v2(
            logical_path=logical_path,
            relation=relation,
            missing_reason=missing_reason,
            unreadable_reason=unreadable_reason,
        )
        if self._is_root_self_v2(resolved):
            observed = self._root_self_stat_v2(stage="object_binding", relation=relation)
            identity = _object_identity_v2(observed)
            self._resolved_observations.setdefault(
                resolved, _ResolvedObservationV2("directory", identity)
            )
            self._logical_observations.append(
                _LogicalObservationV2(
                    logical_path, relation, "non_regular", resolved, identity
                )
            )
            raise _DoctorCompletedNegativeV2(missing_reason)
        cached = self._resolved_observations.get(resolved)
        if cached is not None:
            self._logical_observations.append(
                _LogicalObservationV2(
                    logical_path,
                    relation,
                    cached.kind,
                    resolved,
                    cached.object_identity,
                )
            )
            if cached.kind == "regular":
                if cached.object_identity is None:
                    raise RuntimeError("regular resolved observation has no object identity")
                retained = self._physical_objects.get(cached.object_identity)
                if retained is None:
                    raise RuntimeError("resolved observation lost its retained object")
                if require_bytes and retained.content_bytes is None:
                    raise RuntimeError("one-read registry would need to reread retained content")
                return retained
            if cached.kind == "unreadable":
                raise _DoctorCompletedNegativeV2(unreadable_reason)
            raise _DoctorCompletedNegativeV2(missing_reason)
        try:
            parent_fd = self._open_parent_directory_v2(
                resolved_path=resolved,
                relation=relation,
                missing_reason=missing_reason,
                unreadable_reason=unreadable_reason,
            )
            path_fd, path_stat = self._open_leaf_path_fd_v2(
                parent_fd=parent_fd,
                resolved_path=resolved,
                relation=relation,
                missing_reason=missing_reason,
                unreadable_reason=unreadable_reason,
            )
        except _DoctorCompletedNegativeV2:
            self._resolved_observations[resolved] = _ResolvedObservationV2("missing", None)
            self._logical_observations.append(
                _LogicalObservationV2(logical_path, relation, "missing", resolved, None)
            )
            raise

        if not stat.S_ISREG(path_stat.st_mode):
            try:
                self._retain_fd_v2(
                    fd=path_fd,
                    relation=relation,
                )
            except BaseException:
                os.close(path_fd)
                raise
            retained = _RetainedObjectV2(
                fd=path_fd,
                identity=_object_identity_v2(path_stat),
                metadata=_metadata_identity_v2(path_stat),
            )
            existing = self._physical_objects.setdefault(retained.identity, retained)
            if existing is not retained:
                self._root_binding.release_observation_fd_v2(path_fd)
            self._logical_observations.append(
                _LogicalObservationV2(logical_path, relation, "non_regular", resolved, retained.identity)
            )
            self._resolved_observations[resolved] = _ResolvedObservationV2(
                "non_regular", retained.identity
            )
            raise _DoctorCompletedNegativeV2(missing_reason)

        try:
            data_fd = os.open(
                resolved.name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            if exc.errno in _STABLE_UNREADABLE_ERRNOS_V2:
                try:
                    self._retain_fd_v2(
                        fd=path_fd,
                        relation=relation,
                    )
                except BaseException:
                    os.close(path_fd)
                    raise
                retained_unreadable = _RetainedObjectV2(
                    fd=path_fd,
                    identity=_object_identity_v2(path_stat),
                    metadata=_metadata_identity_v2(path_stat),
                )
                existing_unreadable = self._physical_objects.setdefault(
                    retained_unreadable.identity, retained_unreadable
                )
                if existing_unreadable is not retained_unreadable:
                    self._root_binding.release_observation_fd_v2(path_fd)
                self._resolved_observations[resolved] = _ResolvedObservationV2(
                    "unreadable", retained_unreadable.identity
                )
                self._logical_observations.append(
                    _LogicalObservationV2(
                        logical_path,
                        relation,
                        "unreadable",
                        resolved,
                        retained_unreadable.identity,
                    )
                )
                raise _DoctorCompletedNegativeV2(unreadable_reason) from exc
            os.close(path_fd)
            if exc.errno in _PROGRAMMER_ERRNOS_V2 or exc.errno is None:
                raise
            if exc.errno in _RESOURCE_UNAVAILABLE_ERRNOS_V2:
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
                    stage="content_open",
                    relation=relation,
                ) from exc
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="content_open",
                relation=relation,
            ) from exc
        try:
            data_stat = self._prepare_fd_v2(
                fd=data_fd,
                relation=relation,
            )
            if _object_identity_v2(data_stat) != _object_identity_v2(path_stat):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                )
        except BaseException:
            os.close(path_fd)
            os.close(data_fd)
            raise
        os.close(path_fd)

        physical_identity = _object_identity_v2(data_stat)
        retained = self._physical_objects.get(physical_identity)
        if retained is None:
            try:
                self._retain_fd_v2(
                    fd=data_fd,
                    relation=relation,
                )
            except BaseException:
                os.close(data_fd)
                raise
            retained = _RetainedObjectV2(
                fd=data_fd,
                identity=physical_identity,
                metadata=_metadata_identity_v2(data_stat),
            )
            self._physical_objects[physical_identity] = retained
        else:
            os.close(data_fd)
            if retained.metadata != _metadata_identity_v2(data_stat):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="object_binding",
                    relation=relation,
                )

        self._logical_observations.append(
            _LogicalObservationV2(logical_path, relation, "regular", resolved, physical_identity)
        )
        self._resolved_observations[resolved] = _ResolvedObservationV2(
            "regular", physical_identity
        )
        if retained.content_acquisitions == 0:
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            try:
                while True:
                    chunk = os.read(retained.fd, 1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    if require_bytes:
                        chunks.append(chunk)
                after_read = os.fstat(retained.fd)
            except OSError as exc:
                _raise_classified_observation_oserror_v2(
                    stage="content_read",
                    relation=relation,
                    exc=exc,
                    missing_reason=missing_reason,
                    unreadable_reason=unreadable_reason,
                )
            if retained.metadata != _metadata_identity_v2(after_read):
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="content_read",
                    relation=relation,
                )
            retained.sha256 = digest.hexdigest()
            retained.content_bytes = b"".join(chunks) if require_bytes else None
            retained.content_acquisitions = 1
        elif require_bytes and retained.content_bytes is None:
            raise RuntimeError("one-read registry would need to reread retained content")
        return retained

    def observe_bytes_v2(
        self,
        *,
        logical_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
    ) -> bytes:
        retained = self._observe_regular_v2(
            logical_path=logical_path,
            relation=relation,
            missing_reason=missing_reason,
            unreadable_reason=unreadable_reason,
            require_bytes=True,
        )
        if retained.content_bytes is None:
            raise RuntimeError("bytes observation missing from retained content")
        return retained.content_bytes

    def observe_sha256_v2(
        self,
        *,
        logical_path: Path,
        relation: str,
        missing_reason: str,
        unreadable_reason: str,
    ) -> str:
        retained = self._observe_regular_v2(
            logical_path=logical_path,
            relation=relation,
            missing_reason=missing_reason,
            unreadable_reason=unreadable_reason,
            require_bytes=False,
        )
        if retained.sha256 is None:
            raise RuntimeError("digest observation missing from retained content")
        return retained.sha256

    def _transient_current_lookup_v2(
        self, resolved_path: Path, *, relation: str
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
                    os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
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
            if exc.errno in _STABLE_MISSING_ERRNOS_V2:
                return ("missing", None)
            raise
        finally:
            for fd in reversed(opened):
                os.close(fd)

    def _revalidate_root_v2(self) -> None:
        try:
            self._root_binding._require_active_v2()
            if self._target_root.resolve(strict=False) != self.target_root_real:
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation=_ROOT_RELATION_V2,
                )
            current_fd = os.open(
                self.target_root_real,
                os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            )
            try:
                os.set_inheritable(current_fd, False)
                current = os.fstat(current_fd)
            finally:
                os.close(current_fd)
            held_identity = self._root_binding.object_identity
        except _DoctorUnknownAbortV2:
            raise
        except TargetPackEpochError as exc:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="final_revalidation",
                relation=_ROOT_RELATION_V2,
            ) from exc
        except (OSError, RuntimeError) as exc:
            if isinstance(exc, OSError) and exc.errno in _PROGRAMMER_ERRNOS_V2:
                raise
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="final_revalidation",
                relation=_ROOT_RELATION_V2,
            ) from exc
        current_identity = (current.st_dev, current.st_ino)
        if held_identity != self._root_object_identity or current_identity != held_identity:
            raise _DoctorUnknownAbortV2(
                DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="final_revalidation",
                relation=_ROOT_RELATION_V2,
            )

    def revalidate_v2(self) -> None:
        self._revalidate_root_v2()
        for logical in self._logical_observations:
            try:
                resolved = resolve_within_target_root_v2(
                    self.target_root_real, self.target_root_real / logical.logical_path
                )
                current_kind, current = self._transient_current_lookup_v2(
                    resolved, relation=logical.relation
                )
            except (PlanError, OSError, RuntimeError, ValueError) as exc:
                if isinstance(exc, OSError) and exc.errno in _PROGRAMMER_ERRNOS_V2:
                    raise
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation=logical.relation,
                ) from exc
            if logical.kind == "missing":
                if current_kind == "missing" and resolved == logical.resolved_path:
                    continue
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation=logical.relation,
                )
            if current_kind != "present" or current is None:
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation=logical.relation,
                )
            if _object_identity_v2(current) != logical.object_identity:
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation=logical.relation,
                )

        for retained in (*self._directories.values(), *self._physical_objects.values()):
            try:
                current = os.fstat(retained.fd)
            except OSError as exc:
                _raise_classified_observation_oserror_v2(
                    stage="final_revalidation",
                    relation="retained_object",
                    exc=exc,
                    missing_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                    unreadable_reason=DOCTOR_OBSERVATION_STALE_REASON_V2,
                )
            if _metadata_identity_v2(current) != retained.metadata:
                raise _DoctorUnknownAbortV2(
                    DOCTOR_OBSERVATION_STALE_REASON_V2,
                    stage="final_revalidation",
                    relation="retained_object",
                )


def _check_profile_v2(session: _DoctorObservationSessionV2) -> ProfileCheckV2:
    try:
        raw = session.observe_bytes_v2(
            logical_path=DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
            relation=_PROFILE_RELATION_V2,
            missing_reason=TARGET_PROFILE_MISSING_REASON_V2,
            unreadable_reason=TARGET_PROFILE_UNREADABLE_REASON_V2,
        )
    except _DoctorCompletedNegativeV2 as exc:
        status = _profile_status_for_completed_negative_v2(exc.reason_code)
        return ProfileCheckV2(status=status, profile_hash=None, reason_code=exc.reason_code)
    try:
        raw_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ProfileCheckV2(
            status="missing", profile_hash=None, reason_code=TARGET_PROFILE_UNREADABLE_REASON_V2
        )
    try:
        profile = load_target_profile_text_v2(raw_text)
    except TargetProfileLoadErrorV2 as exc:
        return ProfileCheckV2(status="missing", profile_hash=None, reason_code=exc.reason_code)
    except ValidationError:
        return ProfileCheckV2(status="invalid", profile_hash=None, reason_code="target_profile_invalid")
    return ProfileCheckV2(status="present", profile_hash=compute_profile_hash_v2(profile), reason_code=None)


def _check_receipt_v2(
    *,
    session: _DoctorObservationSessionV2,
    manifest: TargetPackManifestV2,
    profile_check: ProfileCheckV2,
    target_repo: str,
) -> ReceiptCheckV2:
    try:
        raw = session.observe_bytes_v2(
            logical_path=Path(RECEIPT_RELATIVE_PATH_V2),
            relation=_RECEIPT_RELATION_V2,
            missing_reason="target_pack_receipt_missing",
            unreadable_reason="target_pack_receipt_invalid",
        )
    except _DoctorCompletedNegativeV2 as exc:
        status = "missing" if exc.reason_code == "target_pack_receipt_missing" else "invalid"
        return ReceiptCheckV2(status=status, receipt=None, reason_code=exc.reason_code)
    try:
        receipt = load_target_install_receipt_bytes_v2(raw)
    except (ValidationError, ValueError):
        return ReceiptCheckV2(status="invalid", receipt=None, reason_code="target_pack_receipt_invalid")

    if receipt.target_repo != target_repo:
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_TARGET_REPO_MISMATCH_REASON_V2)
    if receipt.pack_version != manifest.pack_version:
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_PACK_VERSION_MISMATCH_REASON_V2)
    if receipt.toolrepo_sha != manifest.toolrepo_sha:
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_TOOLREPO_SHA_MISMATCH_REASON_V2)
    if receipt.manifest_digest != compute_target_pack_manifest_digest_v2(manifest):
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_MANIFEST_DIGEST_MISMATCH_REASON_V2)
    if receipt.portable_target_root_identity != compute_portable_target_root_identity_v2(
        target_repo=receipt.target_repo
    ):
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_TARGET_ROOT_IDENTITY_MISMATCH_REASON_V2)

    expected_target_owned_paths = frozenset(
        entry.path
        for entry in manifest.generated_files
        if entry.ownership is TargetPackFileOwnershipV2.TARGET_OWNED
    )
    if set(receipt.target_owned_paths) != expected_target_owned_paths:
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_TARGET_OWNED_SET_MISMATCH_REASON_V2)

    for relative_path in sorted(expected_target_owned_paths):
        try:
            observed_hash = session.observe_sha256_v2(
                logical_path=Path(relative_path),
                relation=f"target_owned:{relative_path}",
                missing_reason=DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2,
                unreadable_reason=DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2,
            )
        except _DoctorCompletedNegativeV2 as exc:
            return ReceiptCheckV2("invalid", receipt, exc.reason_code)
        if observed_hash != receipt.target_owned_file_hashes[relative_path]:
            return ReceiptCheckV2(
                "invalid", receipt, DOCTOR_TARGET_OWNED_IDENTITY_UNRECONCILED_REASON_V2
            )
    if profile_check.status == "present" and receipt.target_profile_hash != profile_check.profile_hash:
        return ReceiptCheckV2("invalid", receipt, DOCTOR_RECEIPT_PROFILE_HASH_MISMATCH_REASON_V2)
    if rollout_mode_exceeds_pack_capability_v2(
        mode=receipt.rollout_mode, max_supported=manifest.max_supported_rollout_mode
    ):
        return ReceiptCheckV2(
            "invalid", receipt, DOCTOR_RECEIPT_ROLLOUT_EXCEEDS_PACK_CAPABILITY_REASON_V2
        )
    return ReceiptCheckV2("present", receipt, None)


def _check_secret_names_v2(
    names: tuple[str, ...], *, environment_keys: frozenset[str]
) -> tuple[SecretNameCheckV2, ...]:
    return tuple(
        SecretNameCheckV2(name=name, declared_present=name in environment_keys) for name in names
    )


def _unknown_for_epoch_error_v2(exc: TargetPackEpochError) -> DoctorUnknownV2:
    return DoctorUnknownV2(reason_code=exc.reason_code, stage="epoch_acquire", relation=_ROOT_RELATION_V2)


def _unknown_for_epoch_oserror_v2(exc: OSError) -> DoctorUnknownV2:
    if exc.errno not in _RESOURCE_UNAVAILABLE_ERRNOS_V2:
        raise exc
    return DoctorUnknownV2(
        reason_code=TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
        stage="epoch_acquire",
        relation=_ROOT_RELATION_V2,
    )


def _classify_root_binding_failure_v2(
    exc: TargetPackObservationBindingErrorV2,
) -> DoctorUnknownV2:
    if exc.operation_errno in _STABLE_MISSING_ERRNOS_V2:
        raise DoctorInputErrorV2(DOCTOR_TARGET_ROOT_NOT_A_DIRECTORY_REASON_V2) from exc
    if exc.operation_errno in _PROGRAMMER_ERRNOS_V2:
        raise exc
    reason = (
        DOCTOR_OBSERVATION_STALE_REASON_V2
        if exc.reason_code
        in {TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2, TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2}
        else DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2
    )
    return DoctorUnknownV2(reason_code=reason, stage="target_root_binding", relation=_ROOT_RELATION_V2)


def run_doctor_v2(
    *, target_root: Path, manifest: TargetPackManifestV2, target_repo: str
) -> DoctorRunOutcomeV2:
    """Compile one completed decision or a report-zero observational failure.

    Root existence/type is intentionally classified only after K-SH is held:
    a writer holding K-EX before first materialization yields UNKNOWN/BUSY,
    never a premature input-error diagnosis.
    """

    target_display = str(target_root)
    caller_target = Path(target_root)
    caller_target_repo = str(target_repo)
    environment_keys = frozenset(os.environ.keys())

    try:
        lease = acquire_target_pack_epoch_v2(target_root=caller_target, exclusive=False)
    except TargetPackEpochError as exc:
        return _unknown_for_epoch_error_v2(exc)
    except OSError as exc:
        return _unknown_for_epoch_oserror_v2(exc)

    # Acquisition transferred two locked FDs to this caller.  Install cleanup
    # ownership before the capability's fallible entry validation so UNKNOWN
    # and programmer-error paths release the same lease exactly once.
    try:
        try:
            lease.__enter__()
        except TargetPackEpochError as exc:
            return _unknown_for_epoch_error_v2(exc)
        except OSError as exc:
            return _unknown_for_epoch_oserror_v2(exc)

        try:
            root_binding = lease.bind_target_root_for_observation_v2(
                target_root=caller_target
            )
        except TargetPackObservationBindingErrorV2 as exc:
            return _classify_root_binding_failure_v2(exc)
        except TargetPackEpochError as exc:
            return DoctorUnknownV2(
                reason_code=DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="target_root_binding",
                relation=_ROOT_RELATION_V2,
            )
        except OSError as exc:
            if exc.errno not in _RESOURCE_UNAVAILABLE_ERRNOS_V2:
                raise
            return DoctorUnknownV2(
                reason_code=DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
                stage="target_root_binding",
                relation=_ROOT_RELATION_V2,
            )

        # The lease registered this binding before returning it.  Entry is a
        # validation step only; lease.release remains the single cleanup
        # authority for root binding, carrier and namespace resources.
        try:
            root_binding.__enter__()
        except TargetPackEpochError:
            return DoctorUnknownV2(
                reason_code=DOCTOR_OBSERVATION_STALE_REASON_V2,
                stage="target_root_binding",
                relation=_ROOT_RELATION_V2,
            )
        except OSError as exc:
            if exc.errno not in _RESOURCE_UNAVAILABLE_ERRNOS_V2:
                raise
            return DoctorUnknownV2(
                reason_code=DOCTOR_OBSERVATION_UNAVAILABLE_REASON_V2,
                stage="target_root_binding",
                relation=_ROOT_RELATION_V2,
            )

        try:
            with _DoctorObservationSessionV2(
                target_root=caller_target, root_binding=root_binding
            ) as session:
                try:
                    session.observe_directory_v2(
                        logical_path=_AIOPS_DIR_RELATIVE_V2,
                        relation=_AIOPS_RELATION_V2,
                    )
                except _DoctorCompletedNegativeV2:
                    pass

                profile_check = _check_profile_v2(session)
                receipt_check = _check_receipt_v2(
                    session=session,
                    manifest=manifest,
                    profile_check=profile_check,
                    target_repo=caller_target_repo,
                )
                expected_secret_names = (
                    receipt_check.receipt.required_secret_names
                    if receipt_check.receipt
                    else ()
                )
                report = DoctorReportV2(
                    target_root=target_display,
                    profile=profile_check,
                    receipt=receipt_check,
                    secret_names=_check_secret_names_v2(
                        expected_secret_names, environment_keys=environment_keys
                    ),
                    required_capabilities_declared=tuple(
                        manifest.required_capabilities
                    ),
                )
                session.revalidate_v2()
                return DoctorDecisionV2(report=report)
        except _DoctorUnknownAbortV2 as exc:
            return DoctorUnknownV2(
                reason_code=exc.reason_code,
                stage=exc.stage,
                relation=exc.relation,
            )
    finally:
        lease.release()
