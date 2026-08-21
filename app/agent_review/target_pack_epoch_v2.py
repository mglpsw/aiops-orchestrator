"""Private cooperative runtime epochs for the #203 target-pack writer.

The carrier is deliberately outside a target repository.  It coordinates
participating same-EUID processes on one Linux host and mount namespace; it
does not identify an install, authorise an operation, or provide a durable
mutation/journal vocabulary.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


TARGET_PACK_EPOCH_PROTOCOL_VERSION_V2 = "agentreview.target-epoch-k.v1"
_RUNTIME_PARENT_PATH_V2 = Path("/tmp")
_RUNTIME_PARENT_EXPECTED_OWNER_V2 = 0
_RUNTIME_NAMESPACE_PREFIX_V2 = "agentreview-target-locks-v1-"
_SUPPORTED_RUNTIME_FILESYSTEMS_V2 = frozenset({"ext2", "ext3", "ext4", "tmpfs"})

TARGET_PACK_EPOCH_BUSY_REASON_V2 = "target_pack_epoch_busy"
TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2 = "target_pack_epoch_unavailable"
TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2 = "target_pack_epoch_target_subject_changed"
TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2 = "target_pack_epoch_capability_invalid"

# `O_CLOEXEC` closes an FD at exec, but a raw Python `fork()` first duplicates
# the open file description.  Track live protocol FDs so the child closes its
# copies immediately after fork without issuing LOCK_UN (which would also
# unlock the parent's shared OFD).  This is process-lifetime hygiene, not a
# durable recovery protocol.
_LIVE_EPOCH_FDS_V2: set[int] = set()


def _track_epoch_fd_v2(fd: int) -> int:
    _LIVE_EPOCH_FDS_V2.add(fd)
    return fd


def _close_inherited_epoch_fds_after_fork_v2() -> None:
    for fd in tuple(_LIVE_EPOCH_FDS_V2):
        try:
            os.close(fd)
        except OSError:
            pass
    _LIVE_EPOCH_FDS_V2.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_close_inherited_epoch_fds_after_fork_v2)


class TargetPackEpochError(ValueError):
    """A typed refusal while establishing or consuming a private epoch."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class TargetPackObservationBindingErrorV2(TargetPackEpochError):
    """Causal failure from the additive doctor-only root binding.

    The writer-facing ``bind_target_root_v2`` deliberately keeps its merged
    exception contract.  Doctor needs the original errno to distinguish a
    stable invalid subject from unavailable observation capacity, so the
    observation entry point carries that information without changing the
    predecessor API.
    """

    def __init__(self, reason_code: str, *, operation_errno: int | None, stage: str) -> None:
        super().__init__(reason_code)
        self.operation_errno = operation_errno
        self.stage = stage


def _frame_v2(value: bytes) -> bytes:
    """Return a length-framed binary field; never concatenate ambiguously."""

    return len(value).to_bytes(8, "big") + value


def compute_target_pack_epoch_key_from_components_v2(
    *, euid: int, mount_namespace_identity: tuple[int, int], canonical_target_subject: bytes
) -> str:
    """Compute the private K address from already-observed components.

    Keeping this pure helper separate gives known-answer tests a way to prove
    framing without fabricating a mount namespace at runtime.
    """

    fields = (
        TARGET_PACK_EPOCH_PROTOCOL_VERSION_V2.encode("ascii"),
        str(euid).encode("ascii"),
        str(mount_namespace_identity[0]).encode("ascii"),
        str(mount_namespace_identity[1]).encode("ascii"),
        canonical_target_subject,
    )
    return hashlib.sha256(b"".join(_frame_v2(field) for field in fields)).hexdigest()


def runtime_carrier_root_v2(*, euid: int | None = None) -> Path:
    """Return the sole v1 carrier root; environment never participates."""

    effective_uid = os.geteuid() if euid is None else euid
    return _RUNTIME_PARENT_PATH_V2 / f"{_RUNTIME_NAMESPACE_PREFIX_V2}{effective_uid}"


def _canonical_target_subject_v2(target_root: Path) -> bytes:
    try:
        return os.fsencode(target_root.resolve(strict=False))
    except (OSError, RuntimeError) as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc


def _mount_namespace_identity_v2() -> tuple[int, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open("/proc/self/ns/mnt", flags)
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
    try:
        os.set_inheritable(fd, False)
        observed = os.fstat(fd)
        return (observed.st_dev, observed.st_ino)
    finally:
        os.close(fd)


_MOUNT_ESCAPE_RE_V2 = re.compile(r"\\\\([0-7]{3})")


def _unescape_mountinfo_path_v2(value: str) -> str:
    return _MOUNT_ESCAPE_RE_V2.sub(lambda match: chr(int(match.group(1), 8)), value)


def _runtime_filesystem_type_v2(path: Path) -> str | None:
    """Return the mountinfo filesystem type containing *path*, if known."""

    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        candidate = str(path.resolve(strict=True))
    except (OSError, RuntimeError):
        return None

    matches: list[tuple[int, str]] = []
    for line in text.splitlines():
        try:
            before_separator, after_separator = line.split(" - ", 1)
            before = before_separator.split()
            after = after_separator.split()
            mount_point = _unescape_mountinfo_path_v2(before[4])
            filesystem_type = after[0]
        except (IndexError, ValueError):
            continue
        if candidate == mount_point or candidate.startswith(mount_point.rstrip("/") + "/"):
            matches.append((len(mount_point), filesystem_type))
    return max(matches, default=(0, None))[1]


def _same_identity_v2(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_runtime_parent_v2(*, euid: int) -> tuple[int, tuple[int, int]]:
    """Open and validate the fixed `/tmp` root without following aliases."""

    if sys.platform != "linux" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        path_stat = os.lstat(_RUNTIME_PARENT_PATH_V2)
        fd = os.open(_RUNTIME_PARENT_PATH_V2, flags)
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc

    try:
        os.set_inheritable(fd, False)
        fd_stat = os.fstat(fd)
        mode = stat.S_IMODE(fd_stat.st_mode)
        if (
            not stat.S_ISDIR(path_stat.st_mode)
            or stat.S_ISLNK(path_stat.st_mode)
            or not stat.S_ISDIR(fd_stat.st_mode)
            or not _same_identity_v2(path_stat, fd_stat)
            or fd_stat.st_uid != _RUNTIME_PARENT_EXPECTED_OWNER_V2
            or not (mode & stat.S_ISVTX)
            or not os.access(_RUNTIME_PARENT_PATH_V2, os.W_OK | os.X_OK)
            or _runtime_filesystem_type_v2(_RUNTIME_PARENT_PATH_V2) not in _SUPPORTED_RUNTIME_FILESYSTEMS_V2
        ):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)
        return fd, (fd_stat.st_dev, fd_stat.st_ino)
    except BaseException:
        os.close(fd)
        raise


def _protocol_directory_name_v2(euid: int) -> str:
    return f"{_RUNTIME_NAMESPACE_PREFIX_V2}{euid}"


def _validate_protocol_directory_v2(*, parent_fd: int, namespace_fd: int, name: str, euid: int) -> tuple[int, int]:
    try:
        entry_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        fd_stat = os.fstat(namespace_fd)
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
    if (
        not stat.S_ISDIR(entry_stat.st_mode)
        or stat.S_ISLNK(entry_stat.st_mode)
        or entry_stat.st_uid != euid
        or stat.S_IMODE(entry_stat.st_mode) != 0o700
        or not stat.S_ISDIR(fd_stat.st_mode)
        or not _same_identity_v2(entry_stat, fd_stat)
    ):
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)
    return (fd_stat.st_dev, fd_stat.st_ino)


def _open_protocol_directory_v2(*, parent_fd: int, euid: int) -> tuple[int, str, tuple[int, int]]:
    name = _protocol_directory_name_v2(euid)
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
    try:
        os.set_inheritable(fd, False)
        identity = _validate_protocol_directory_v2(parent_fd=parent_fd, namespace_fd=fd, name=name, euid=euid)
        return fd, name, identity
    except BaseException:
        os.close(fd)
        raise


def _lock_nonblocking_v2(fd: int, mode: int) -> None:
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EAGAIN}:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_BUSY_REASON_V2) from exc
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc


def _unlock_and_close_v2(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(fd)


def _validate_carrier_v2(*, namespace_fd: int, carrier_fd: int, name: str, euid: int) -> tuple[int, int]:
    try:
        entry_stat = os.stat(name, dir_fd=namespace_fd, follow_symlinks=False)
        fd_stat = os.fstat(carrier_fd)
    except OSError as exc:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
    if (
        not stat.S_ISREG(entry_stat.st_mode)
        or stat.S_ISLNK(entry_stat.st_mode)
        or entry_stat.st_uid != euid
        or stat.S_IMODE(entry_stat.st_mode) != 0o600
        or entry_stat.st_nlink != 1
        or not stat.S_ISREG(fd_stat.st_mode)
        or fd_stat.st_nlink != 1
        or not _same_identity_v2(entry_stat, fd_stat)
    ):
        raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)
    return (fd_stat.st_dev, fd_stat.st_ino)


@dataclass
class TargetPackTargetBindingV2:
    """A held O_PATH directory object, never a lock authority."""

    _lease: "TargetPackEpochLeaseV2"
    _fd: int
    _identity: tuple[int, int]
    _active: bool = True

    @property
    def fd(self) -> int:
        self._require_active_v2()
        return self._fd

    @property
    def target_root_real(self) -> str:
        self._require_active_v2()
        return os.fsdecode(self._lease.canonical_target_subject)

    @property
    def object_identity(self) -> tuple[int, int]:
        self._require_active_v2()
        return self._identity

    def retain_observation_fd_v2(self, fd: int) -> int:
        """Attach one non-inheritable descendant FD to this live lease.

        Observation sessions normally close descendants explicitly before
        releasing the root binding.  Lease ownership is also recorded as an
        exception-safety backstop and for the existing post-fork tracker.
        """

        self._require_active_v2()
        try:
            os.set_inheritable(fd, False)
        except OSError as exc:
            raise TargetPackObservationBindingErrorV2(
                TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                operation_errno=exc.errno,
                stage="retain_observation_fd",
            ) from exc
        self._lease._observation_fds.append(fd)
        return _track_epoch_fd_v2(fd)

    def release_observation_fd_v2(self, fd: int) -> None:
        if fd in self._lease._observation_fds:
            self._lease._observation_fds.remove(fd)
            try:
                os.close(fd)
            finally:
                _LIVE_EPOCH_FDS_V2.discard(fd)

    def _require_active_v2(self) -> None:
        self._lease._require_active_v2()
        if not self._active:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)
        observed = os.fstat(self._fd)
        if (observed.st_dev, observed.st_ino) != self._identity or not stat.S_ISDIR(observed.st_mode):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)

    def close(self) -> None:
        if self._active:
            self._active = False
            os.close(self._fd)
            _LIVE_EPOCH_FDS_V2.discard(self._fd)

    def __enter__(self) -> "TargetPackTargetBindingV2":
        self._require_active_v2()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


@dataclass
class TargetPackEpochLeaseV2:
    """Live private capability for one K SH/EX epoch."""

    _namespace_fd: int
    _carrier_fd: int
    _namespace_identity: tuple[int, int]
    _carrier_identity: tuple[int, int]
    canonical_target_subject: bytes
    key: str
    euid: int
    mount_namespace_identity: tuple[int, int]
    mode: Literal["shared", "exclusive"]
    _active: bool = True
    _bindings: list[TargetPackTargetBindingV2] = field(default_factory=list, repr=False)
    _observation_fds: list[int] = field(default_factory=list, repr=False)

    def _require_active_v2(self) -> None:
        if not self._active:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)
        namespace_stat = os.fstat(self._namespace_fd)
        carrier_stat = os.fstat(self._carrier_fd)
        if (
            (namespace_stat.st_dev, namespace_stat.st_ino) != self._namespace_identity
            or (carrier_stat.st_dev, carrier_stat.st_ino) != self._carrier_identity
        ):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)

    def require_exclusive_v2(self, *, target_root: Path) -> None:
        self._require_active_v2()
        if self.mode != "exclusive" or _canonical_target_subject_v2(target_root) != self.canonical_target_subject:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)

    def require_exclusive_binding_v2(self, *, binding: TargetPackTargetBindingV2, expected_target_root_real: str) -> None:
        """Require an active EX lease and a binding minted by this lease.

        A directory FD alone is never a capability.  In particular, a caller
        must not be able to construct a lookalike binding for another root and
        pair it with this lease's canonical subject.
        """

        self.require_exclusive_v2(target_root=Path(expected_target_root_real))
        if not any(binding is registered for registered in self._bindings):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2)
        binding._require_active_v2()

    def materialize_and_bind_target_root_v2(self, *, target_root: Path) -> TargetPackTargetBindingV2:
        """Create allowed missing prefixes only after the caller's plan gate."""

        self.require_exclusive_v2(target_root=target_root)
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
        if _canonical_target_subject_v2(target_root) != self.canonical_target_subject:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2)
        return self.bind_target_root_v2(target_root=target_root)

    def bind_target_root_v2(self, *, target_root: Path) -> TargetPackTargetBindingV2:
        """Bind the current target directory object with O_PATH only."""

        self._require_active_v2()
        if _canonical_target_subject_v2(target_root) != self.canonical_target_subject:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2)
        if not hasattr(os, "O_PATH"):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)
        flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(os.fsdecode(self.canonical_target_subject), flags)
        except OSError as exc:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
        try:
            os.set_inheritable(fd, False)
            observed = os.fstat(fd)
            if not stat.S_ISDIR(observed.st_mode):
                raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)
            binding = TargetPackTargetBindingV2(self, fd, (observed.st_dev, observed.st_ino))
            self._bindings.append(binding)
            _track_epoch_fd_v2(fd)
            return binding
        except BaseException:
            os.close(fd)
            raise

    def bind_target_root_for_observation_v2(self, *, target_root: Path) -> TargetPackTargetBindingV2:
        """Bind the target for doctor while preserving the causal errno.

        This is intentionally additive.  ``bind_target_root_v2`` remains the
        exact writer primitive merged in #258 and continues collapsing open
        failures to its historical epoch-unavailable reason.
        """

        self._require_active_v2()
        try:
            canonical_subject = _canonical_target_subject_v2(target_root)
        except TargetPackEpochError as exc:
            raise TargetPackObservationBindingErrorV2(
                exc.reason_code, operation_errno=None, stage="canonical_subject"
            ) from exc
        if canonical_subject != self.canonical_target_subject:
            raise TargetPackObservationBindingErrorV2(
                TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2,
                operation_errno=None,
                stage="canonical_subject",
            )
        if not hasattr(os, "O_PATH"):
            raise TargetPackObservationBindingErrorV2(
                TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                operation_errno=None,
                stage="open",
            )
        flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(os.fsdecode(self.canonical_target_subject), flags)
        except OSError as exc:
            raise TargetPackObservationBindingErrorV2(
                TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                operation_errno=exc.errno,
                stage="open",
            ) from exc
        try:
            try:
                os.set_inheritable(fd, False)
            except OSError as exc:
                raise TargetPackObservationBindingErrorV2(
                    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                    operation_errno=exc.errno,
                    stage="set_inheritable",
                ) from exc
            try:
                observed = os.fstat(fd)
            except OSError as exc:
                raise TargetPackObservationBindingErrorV2(
                    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                    operation_errno=exc.errno,
                    stage="fstat",
                ) from exc
            if not stat.S_ISDIR(observed.st_mode):
                raise TargetPackObservationBindingErrorV2(
                    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
                    operation_errno=errno.ENOTDIR,
                    stage="fstat",
                )
            binding = TargetPackTargetBindingV2(self, fd, (observed.st_dev, observed.st_ino))
            self._bindings.append(binding)
            _track_epoch_fd_v2(fd)
            return binding
        except BaseException:
            os.close(fd)
            raise

    def release(self) -> None:
        if not self._active:
            return
        self._active = False
        for fd in tuple(reversed(self._observation_fds)):
            try:
                os.close(fd)
            except OSError:
                pass
            _LIVE_EPOCH_FDS_V2.discard(fd)
        self._observation_fds.clear()
        for binding in tuple(self._bindings):
            binding.close()
        self._bindings.clear()
        _unlock_and_close_v2(self._carrier_fd)
        _LIVE_EPOCH_FDS_V2.discard(self._carrier_fd)
        _unlock_and_close_v2(self._namespace_fd)
        _LIVE_EPOCH_FDS_V2.discard(self._namespace_fd)

    def __enter__(self) -> "TargetPackEpochLeaseV2":
        self._require_active_v2()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def acquire_target_pack_epoch_v2(*, target_root: Path, exclusive: bool) -> TargetPackEpochLeaseV2:
    """Acquire namespace SH then K SH/EX, or return a typed refusal.

    The caller must hold the returned capability through all observations or
    writes in the epoch.  No carrier is removed on release.
    """

    euid = os.geteuid()
    canonical_subject = _canonical_target_subject_v2(target_root)
    mount_identity = _mount_namespace_identity_v2()
    key = compute_target_pack_epoch_key_from_components_v2(
        euid=euid, mount_namespace_identity=mount_identity, canonical_target_subject=canonical_subject
    )
    parent_fd, _ = _open_runtime_parent_v2(euid=euid)
    namespace_fd: int | None = None
    carrier_fd: int | None = None
    try:
        namespace_fd, namespace_name, namespace_identity = _open_protocol_directory_v2(parent_fd=parent_fd, euid=euid)
        _lock_nonblocking_v2(namespace_fd, fcntl.LOCK_SH)
        _validate_protocol_directory_v2(
            parent_fd=parent_fd, namespace_fd=namespace_fd, name=namespace_name, euid=euid
        )

        carrier_name = f"{key}.lock"
        carrier_flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            carrier_fd = os.open(carrier_name, carrier_flags, 0o600, dir_fd=namespace_fd)
        except OSError as exc:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2) from exc
        os.set_inheritable(carrier_fd, False)
        carrier_identity = _validate_carrier_v2(
            namespace_fd=namespace_fd, carrier_fd=carrier_fd, name=carrier_name, euid=euid
        )
        _lock_nonblocking_v2(carrier_fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        _validate_carrier_v2(namespace_fd=namespace_fd, carrier_fd=carrier_fd, name=carrier_name, euid=euid)
        if _canonical_target_subject_v2(target_root) != canonical_subject:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2)
        lease = TargetPackEpochLeaseV2(
            _namespace_fd=namespace_fd,
            _carrier_fd=carrier_fd,
            _namespace_identity=namespace_identity,
            _carrier_identity=carrier_identity,
            canonical_target_subject=canonical_subject,
            key=key,
            euid=euid,
            mount_namespace_identity=mount_identity,
            mode="exclusive" if exclusive else "shared",
        )
        _track_epoch_fd_v2(namespace_fd)
        _track_epoch_fd_v2(carrier_fd)
        return lease
    except BaseException:
        if carrier_fd is not None:
            _unlock_and_close_v2(carrier_fd)
        if namespace_fd is not None:
            _unlock_and_close_v2(namespace_fd)
        raise
    finally:
        os.close(parent_fd)
