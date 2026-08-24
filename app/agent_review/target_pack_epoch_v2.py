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
# `K-DISJOINT` (#262).  Two reasons, deliberately distinct: one says the carrier
# and the target were established to overlap, the other says disjointness could
# not be established at all.  Collapsing them would let "we could not look" be
# read as "we looked and it was fine", which is the exact fail-open this
# correction exists to remove.
TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2 = "target_pack_epoch_carrier_overlaps_target"
TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2 = (
    "target_pack_epoch_carrier_disjointness_unknown"
)

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


def _read_mount_table_v2() -> tuple[tuple[int, str], ...]:
    """Return `(device, mount_point)` for every mount in this mount namespace.

    `/proc/self/mountinfo` is the kernel's own record of what is mounted where.
    That is the authority this module consults; it does not re-derive mount
    topology from path strings, because a bind alias is invisible to
    `Path.resolve()` and shares `st_dev`/`st_ino` with its source.

    Raises rather than returning a partial table: a caller must never be able to
    read "the table could not be parsed" as "nothing is mounted there".
    """

    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        ) from exc

    entries: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            before = line.split(" - ", 1)[0].split()
            major_text, minor_text = before[2].split(":", 1)
            device = os.makedev(int(major_text), int(minor_text))
            mount_point = _unescape_mountinfo_path_v2(before[4])
        except (IndexError, ValueError) as exc:
            # A line this module cannot parse is a line whose mount point it
            # cannot rule out.  Refuse rather than skip it.
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            ) from exc
        entries.append((device, mount_point))
    if not entries:
        raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2)
    return tuple(entries)


def _is_at_or_beneath_v2(candidate: str, ancestor: str) -> bool:
    """Pure path containment over two already-canonical absolute paths."""

    if candidate == ancestor:
        return True
    return candidate.startswith(ancestor.rstrip("/") + "/")


def _is_ancestral_alias_mount_v2(*, mount_point: str, target_path: str, runtime_parent_path: str) -> bool:
    """Is *mount_point* a distinct same-device mount the target is reached
    through, that does not itself reach back to the runtime parent (`#262`
    F1)?  Pure over already-canonical paths; the caller supplies the device
    match.

    The runtime parent's own containing mount -- typically the shared root
    filesystem -- always satisfies `_is_at_or_beneath_v2(runtime_parent_path,
    mount_point)` (everything is at-or-beneath the root), so it never
    qualifies here.  A mount installed specifically over some other path,
    that does not reach the runtime parent, does.
    """

    return _is_at_or_beneath_v2(target_path, mount_point) and not _is_at_or_beneath_v2(
        runtime_parent_path, mount_point
    )


def _establish_carrier_disjoint_v2(
    *, canonical_target_subject: bytes, parent_fd: int, euid: int
) -> None:
    """Establish `RuntimeCarrierMutationDomain(K) ∩ TargetMutationDomain(D) = ∅`,
    or refuse.  Called BEFORE any carrier materialization, so a refused target is
    never mutated first.

    The carrier's mutation domain is the protocol directory this module creates
    under the runtime parent, plus the lock file inside it — so establishing that
    the runtime parent's device is not reachable at or beneath the target
    establishes disjointness for the whole domain.

    Four independent ways a target can reach the carrier, and what rules each
    out:

    - **by path** — the target is the runtime parent, the protocol directory, or
      an ancestor of either.  Canonical path containment, both directions.
    - **by object identity** — the target is a bind alias of the runtime parent
      or of the protocol directory itself, so its path differs while
      `st_dev`/`st_ino` agree.  Identity comparison against the already-
      validated parent descriptor.
    - **by a mount beneath the target** — some mount point at or beneath the
      target carries the runtime parent's device, which is how a deep bind
      alias exposes the carrier inside an otherwise unrelated target.  The
      kernel mount table decides this one; no path arithmetic can.
    - **by a distinct mount ancestral to the target** — the target is reached
      only through some *other* same-device mount that is not itself an
      ancestor of, or the runtime parent path (`#262` F1): the runtime parent
      was bind-mounted elsewhere, and the target is that alias or something
      inside it.  Object identity alone does not catch this once the target is
      a proper descendant of the alias mountpoint rather than the mountpoint
      itself — a descendant has its own inode, distinct from the runtime
      parent's, so it shares only the device.  The two mount directions are
      independent: a carrier-relevant mount nested inside the target, and the
      target itself nested inside a carrier-relevant alias, are different
      topologies and neither implies the other.

    The fourth rule must not fire for the ordinary case of a target that
    merely lives on the same filesystem as the runtime parent — sharing a
    device through the filesystem's own root mount is not an alias of
    anything.  It is scoped to mounts that do **not** also contain the
    runtime parent: the shared root mount always contains the runtime parent
    (everything does, being the root), so it never satisfies that condition,
    while a same-device mount installed specifically over some other path,
    that does not itself reach back to the runtime parent, is exactly the
    alias shape this rule exists to catch.

    This is conservative by construction: a mount beneath the target that
    carries the runtime parent's device is refused even when it happens to
    expose an unrelated subtree of that device, and a same-device ancestral
    alias mount is refused even when its own subtree beneath the target never
    happens to collide with the carrier's own name.  Over-refusal narrows the
    set of accepted topologies, which is a bounded cost; under-refusal would
    fabricate the property.

    Scope bound, stated rather than glossed: the mount table is read once,
    immediately before materialization.  A cooperating process does not remount
    underneath itself, and `#262` explicitly excludes external non-cooperating
    actors.  Against an adversary racing a bind mount into the window between
    this check and the carrier write, this establishes nothing, and no claim to
    the contrary is made here.
    """

    try:
        target_path = os.fsdecode(canonical_target_subject)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        ) from exc

    try:
        parent_stat = os.fstat(parent_fd)
    except OSError as exc:
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        ) from exc

    protocol_directory = str(_RUNTIME_PARENT_PATH_V2 / _protocol_directory_name_v2(euid))

    # -- by path, both directions -------------------------------------------
    # The mutation domain is the protocol directory and its contents, NOT the
    # whole runtime parent.  Creating the protocol directory does add one entry
    # to the runtime parent, but that only reaches a target which IS the runtime
    # parent or contains it -- and such a target already contains the protocol
    # directory, so the test below catches it.  Refusing every target that
    # merely sits somewhere under the runtime parent would reject ordinary
    # scratch targets without establishing anything.
    if _is_at_or_beneath_v2(protocol_directory, target_path) or _is_at_or_beneath_v2(
        target_path, protocol_directory
    ):
        raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)

    # -- by object identity (bind alias of the carrier's own location) -------
    # A target that does not exist yet cannot alias anything; the writer's
    # materialization path legitimately reaches here with a missing target, and
    # the path and mount rules above/below still apply to it.
    try:
        target_stat: os.stat_result | None = os.lstat(target_path)
    except FileNotFoundError:
        target_stat = None
    except OSError as exc:
        # EIO/EACCES/ELOOP while observing the target: disjointness is UNKNOWN.
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        ) from exc

    if target_stat is not None and _same_identity_v2(target_stat, parent_stat):
        raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)

    # -- by a mount at or beneath the target, or a distinct same-device mount
    #    ancestral to the target that does not itself reach the runtime parent
    #    (#262 F1) -----------------------------------------------------------
    runtime_parent_path = str(_RUNTIME_PARENT_PATH_V2)
    for device, mount_point in _read_mount_table_v2():
        if device != parent_stat.st_dev:
            continue
        if _is_at_or_beneath_v2(mount_point, target_path):
            # a carrier-relevant mount is nested inside (or equal to) the target
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)
        if _is_ancestral_alias_mount_v2(
            mount_point=mount_point, target_path=target_path, runtime_parent_path=runtime_parent_path
        ):
            raise TargetPackEpochError(TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2)


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

    def release(self) -> None:
        if not self._active:
            return
        self._active = False
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
        # `K-DISJOINT` (#262).  Established here, before the protocol directory
        # is created, so a refused target is never mutated first.  This is the
        # single disjointness authority: no consumer re-derives it.
        _establish_carrier_disjoint_v2(
            canonical_target_subject=canonical_subject, parent_fd=parent_fd, euid=euid
        )
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
