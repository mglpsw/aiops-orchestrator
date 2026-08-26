"""Private cooperative runtime epochs for the #203 target-pack writer.

The carrier is deliberately outside a target repository.  It coordinates
participating same-EUID processes on one Linux host and mount namespace; it
does not identify an install, authorise an operation, or provide a durable
mutation/journal vocabulary.
"""

from __future__ import annotations

import errno
import array
import fcntl
import hashlib
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from typing import NamedTuple

from app.agent_review._mount_topology_raw_v2 import (
    MountRecordV2 as _MountRecordV2,
    RawMountTopologyRepresentationV2 as _RawMountTopologyRepresentationV2,
    TopologyQueryKindV2 as _TopologyQueryKindV2,
    TopologyQueryResolutionV2 as _TopologyQueryResolutionV2,
    TopologyQueryV2 as _TopologyQueryV2,
)
from app.agent_review._target_pack_epoch_contract_v2 import (
    TARGET_PACK_EPOCH_BUSY_REASON_V2,
    TARGET_PACK_EPOCH_CAPABILITY_INVALID_REASON_V2,
    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2,
    TARGET_PACK_EPOCH_CARRIER_OVERLAP_REASON_V2,
    TARGET_PACK_EPOCH_SUBJECT_CHANGED_REASON_V2,
    TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2,
    TargetPackEpochError,
)


TARGET_PACK_EPOCH_PROTOCOL_VERSION_V2 = "agentreview.target-epoch-k.v1"
_RUNTIME_PARENT_PATH_V2 = Path("/tmp")
_RUNTIME_PARENT_EXPECTED_OWNER_V2 = 0
_RUNTIME_NAMESPACE_PREFIX_V2 = "agentreview-target-locks-v1-"
_SUPPORTED_RUNTIME_FILESYSTEMS_V2 = frozenset({"ext2", "ext3", "ext4", "tmpfs"})

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


# Public typed topology contracts remain import-compatible. Their raw storage
# and traversal implementation live behind the one private-module boundary.
MountRecordV2 = _MountRecordV2
TopologyQueryKindV2 = _TopologyQueryKindV2
TopologyQueryV2 = _TopologyQueryV2
TopologyQueryResolutionV2 = _TopologyQueryResolutionV2
for _public_topology_contract_v2 in (
    MountRecordV2,
    TopologyQueryKindV2,
    TopologyQueryV2,
    TopologyQueryResolutionV2,
):
    _public_topology_contract_v2.__module__ = __name__


def _build_topology_capability_type_v2(
    raw_type: type[_RawMountTopologyRepresentationV2],
) -> type:
    """Close the raw implementation type outside the owner module namespace."""

    class _MountTopologySnapshotV2:
        """Opaque consumer capability for typed mount-topology operations.

        The object contains only typed callables. It has no instance dictionary
        and exposes no raw graph, record inventory, adjacency map, identity map,
        or raw traversal method. The captured closures are deliberately subject
        to the documented callable-closure nonclaim.
        """

        __slots__ = (
            "__resolve_query",
            "__governing_mount",
            "__visible_child_mounts",
            "__is_visible",
            "__project",
        )

        def __init__(self, records: tuple[MountRecordV2, ...]) -> None:
            initialize(self, raw_type(records))

        @classmethod
        def observe(cls) -> "MountTopologySnapshotV2":
            capability = object.__new__(cls)
            initialize(capability, raw_type.observe())
            return capability

        @classmethod
        def parse(cls, text: str) -> "MountTopologySnapshotV2":
            capability = object.__new__(cls)
            initialize(capability, raw_type.parse(text))
            return capability

        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError("typed topology capability is immutable")

        def __delattr__(self, name: str) -> None:
            raise AttributeError("typed topology capability is immutable")

        def resolve_query_v2(
            self, query: TopologyQueryV2
        ) -> TopologyQueryResolutionV2:
            return self.__resolve_query(query)

        def governing_mount_v2(self, path: str) -> MountRecordV2:
            return self.__governing_mount(path)

        def visible_child_mounts_v2(self, path: str) -> tuple[MountRecordV2, ...]:
            return self.__visible_child_mounts(path)

        def is_visible_v2(self, record: MountRecordV2) -> bool:
            return self.__is_visible(record)

        def project_v2(self, path: str) -> tuple[int, str]:
            return self.__project(path)

    def initialize(
        capability: _MountTopologySnapshotV2,
        raw: _RawMountTopologyRepresentationV2,
    ) -> None:
        """Capture raw state in typed closures, never in a consumer field."""

        def resolve_query(query: TopologyQueryV2) -> TopologyQueryResolutionV2:
            return raw.resolve_query_v2(query)

        def governing_mount(path: str) -> MountRecordV2:
            return raw.governing_mount_v2(path)

        def visible_child_mounts(path: str) -> tuple[MountRecordV2, ...]:
            return raw.visible_child_mounts_v2(path)

        def is_visible(record: MountRecordV2) -> bool:
            return raw.is_visible_v2(record)

        def project(path: str) -> tuple[int, str]:
            return raw.project_v2(path)

        object.__setattr__(
            capability, "_MountTopologySnapshotV2__resolve_query", resolve_query
        )
        object.__setattr__(
            capability, "_MountTopologySnapshotV2__governing_mount", governing_mount
        )
        object.__setattr__(
            capability,
            "_MountTopologySnapshotV2__visible_child_mounts",
            visible_child_mounts,
        )
        object.__setattr__(
            capability, "_MountTopologySnapshotV2__is_visible", is_visible
        )
        object.__setattr__(
            capability, "_MountTopologySnapshotV2__project", project
        )

    _MountTopologySnapshotV2.__name__ = "MountTopologySnapshotV2"
    _MountTopologySnapshotV2.__qualname__ = "MountTopologySnapshotV2"
    _MountTopologySnapshotV2.__module__ = __name__
    return _MountTopologySnapshotV2


MountTopologySnapshotV2 = _build_topology_capability_type_v2(
    _RawMountTopologyRepresentationV2
)
del _build_topology_capability_type_v2
del _RawMountTopologyRepresentationV2


def _normalize_absolute_v2(path: str) -> str:
    return os.path.normpath(path) if path.startswith("/") else os.path.normpath("/" + path)


def _within_v2(candidate: str, ancestor: str) -> bool:
    """Physical containment over two already-normalized internal paths."""

    if candidate == ancestor:
        return True
    return candidate.startswith(ancestor.rstrip("/") + "/")


def _runtime_filesystem_type_v2(
    path: Path, resolution: "TopologyQueryResolutionV2"
) -> str | None:
    """Return the mountinfo filesystem type containing *path*, if known.

    Consumes the single canonical topology observation rather than parsing
    `/proc/self/mountinfo` a second time: a second parser would mean a second
    decoding rule and a second error policy for the same table, and whichever
    ran first would decide what the caller sees.
    """

    try:
        path.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    # The resolution is passed in, already established through the typed
    # authority.  Resolving here would be a second semantic lookup of the same
    # subject, and calling the raw traversal would answer a filesystem type for
    # topology the authority refuses -- reported as `unavailable` rather than
    # as the topology UNKNOWN it actually is.
    return resolution.governing_mount.filesystem_type


def _same_identity_v2(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _open_runtime_parent_v2(
    *, euid: int, runtime_parent_resolution: "TopologyQueryResolutionV2"
) -> tuple[int, tuple[int, int]]:
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
            or _runtime_filesystem_type_v2(_RUNTIME_PARENT_PATH_V2, runtime_parent_resolution)
            not in _SUPPORTED_RUNTIME_FILESYSTEMS_V2
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


# Filesystems whose `(device, root, mount_point)` triple is known to describe
# where a write physically lands, so `project_v2` means what K-DISJOINT needs
# it to mean.  An ALLOWLIST, deliberately: an unrecognised filesystem is
# UNKNOWN rather than assumed direct.
#
# `overlay` is the case that forced this (`#262` F2).  Its mount row carries a
# device of its own and says nothing, in the fields this module models, about
# the upper and lower trees a write actually reaches -- so a carrier written
# through an overlay's upperdir appears in the target while the two projections
# compare as different devices.  The `upperdir=`/`lowerdir=` super-options do
# exist in mountinfo, but overlay semantics are richer than one backing path
# (multiple lowers, redirects, whiteouts, metacopy) and this slice does not own
# a general OverlayFS model.  Refusing is honest; modelling it badly would not
# be.
_DIRECT_PROJECTION_FILESYSTEMS_V2 = frozenset({"ext2", "ext3", "ext4", "tmpfs"})

# Name-semantics capability, per filesystem, as a CLOSED three-way
# classification (`#262` N17).  A two-way "capable / assume case-sensitive"
# split silently promotes an unrecognised filesystem into a proven one, and
# `ENOTTY` from `FS_IOC_GETFLAGS` is not proof that a filesystem cannot
# implement case-insensitive lookup some other way -- it only says this ioctl
# is unsupported.
#
#   CASEFOLD_FLAG_CAPABLE     inspect the directory's authoritative flag
#   ESTABLISHED_CASE_SENSITIVE  no probe needed; the type settles it
#   UNKNOWN_NAME_SEMANTICS    K-DISJOINT is UNKNOWN
_NAME_SEMANTICS_CASEFOLD_FLAG_CAPABLE_V2 = "casefold_flag_capable"
_NAME_SEMANTICS_ESTABLISHED_CASE_SENSITIVE_V2 = "established_case_sensitive"
_NAME_SEMANTICS_UNKNOWN_V2 = "unknown_name_semantics"

# Pseudo-filesystems with no on-disk name storage and no casefold support.
# Enumerated rather than inferred, and deliberately narrow.
_ESTABLISHED_CASE_SENSITIVE_FILESYSTEMS_V2 = frozenset({
    "proc", "sysfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
    "ext2", "ext3",
})


def _name_semantics_capability_v2(filesystem_type: str) -> str:
    if filesystem_type in _CASEFOLD_CAPABLE_FILESYSTEMS_V2:
        return _NAME_SEMANTICS_CASEFOLD_FLAG_CAPABLE_V2
    if filesystem_type in _ESTABLISHED_CASE_SENSITIVE_FILESYSTEMS_V2:
        return _NAME_SEMANTICS_ESTABLISHED_CASE_SENSITIVE_V2
    return _NAME_SEMANTICS_UNKNOWN_V2


# Filesystems in the direct-projection set that can carry a casefolded
# directory.  Casefolding makes two differently-spelled pathnames name ONE
# entry, which is exactly what the textual equality and containment in this
# module assume cannot happen (`#262` F6).
#
# tmpfs belongs here.  An earlier revision listed only ext4 and said so in a
# comment; that was stale -- current kernels take `-o casefold` on tmpfs and
# honour `+F` per directory, with new directories inheriting it, and this host
# reproduces exactly that.  The two authorities stay separate on purpose: a
# filesystem may be projection-applicable and still have unknown name
# semantics, and other casefold-capable filesystems (f2fs, bcachefs) are
# irrelevant here precisely because projection applicability already refuses
# them.
_CASEFOLD_CAPABLE_FILESYSTEMS_V2 = frozenset({"ext4", "tmpfs"})
_FS_IOC_GETFLAGS_V2 = 0x80086601
_FS_CASEFOLD_FL_V2 = 0x40000000


def _require_projection_applicable_v2(
    snapshot: "MountTopologySnapshotV2", *paths: str
) -> None:
    """Establish that a physical projection means what K-DISJOINT reads it to
    mean for every *path*, or refuse as UNKNOWN.

    Answers one question only -- can the later relation be established? -- and
    decides nothing about K itself.
    """

    for path in paths:
        governing = snapshot.resolve_query_v2(
            TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, path)).governing_mount
        if governing.filesystem_type not in _DIRECT_PROJECTION_FILESYSTEMS_V2:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )


def _directory_is_casefolded_v2(path: str) -> bool | None:
    """`True`/`False` where the flag can be read, `None` where it cannot.

    `None` is never read as "case sensitive": an unreadable flag is an
    unestablished predicate, and the caller refuses.
    """

    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        buffer = array.array("i", [0])
        fcntl.ioctl(fd, _FS_IOC_GETFLAGS_V2, buffer, True)
        return bool(buffer[0] & _FS_CASEFOLD_FL_V2)
    except OSError:
        return None
    finally:
        os.close(fd)


_DIRECTORY_PRESENT_V2 = "directory"
_DIRECTORY_ABSENT_V2 = "absent"
_DIRECTORY_SYMLINK_V2 = "symlink"
_DIRECTORY_OTHER_V2 = "other"
_DIRECTORY_UNKNOWN_V2 = "unknown"


def _observe_directory_v2(path: str) -> str:
    """`present` / `absent` / `unknown`, preserving the error classes.

    `os.path.isdir`, `exists` and friends collapse every `OSError` into
    `False`, which makes "this directory exists but I may not look at it"
    indistinguishable from "this directory is not there".  For a predicate
    whose whole purpose is to fail closed, that collapse is the bug.

    `ENOENT`/`ENOTDIR` establish absence.  `EACCES`, `EIO`, `ELOOP`, `ESTALE`
    and anything else establish nothing, and a non-directory where a lookup
    authority was expected is equally unestablished.
    """

    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return _DIRECTORY_ABSENT_V2
    except NotADirectoryError:
        return _DIRECTORY_ABSENT_V2
    except OSError:
        return _DIRECTORY_UNKNOWN_V2
    if stat.S_ISDIR(mode):
        return _DIRECTORY_PRESENT_V2
    if stat.S_ISLNK(mode):
        # A symlink is its OWN state, never folded into `absent`.
        #
        # An earlier revision reported `absent` here and justified it by the
        # later `O_NOFOLLOW` validation that refuses a symlinked protocol
        # directory.  That made this authority's correctness depend on a
        # downstream check it does not own: no fail-open followed, but the
        # argument was non-local and would have broken silently if the
        # ordering or that check ever changed.  The caller decides what a
        # symlink means for ITS question, locally, and the later check stays
        # as TOCTOU revalidation rather than as the first truth-maker.
        return _DIRECTORY_SYMLINK_V2
    return _DIRECTORY_OTHER_V2


def _lookup_authorities_v2(path: str) -> tuple[str, ...]:
    """The existing directories that RESOLVE the components of *path*.

    A pathname's own spelling is decided by the directory it is looked up IN.
    For `/a/b/c`, `c` is resolved by `/a/b`, `b` by `/a`, and `a` by `/` -- so
    those are the authorities, and `/a/b/c` itself is NOT one of them.

    An earlier revision probed the final object whenever it existed.  That is
    the wrong scope twice over: the target's own interior lookup semantics do
    not decide whether the target's NAME collides with another spelling, and
    opening it requires read permission the caller may legitimately not have.
    A mode-0300 target -- writable and searchable, deliberately unreadable --
    then answered "flag unreadable", which is UNKNOWN, and refused an
    acquisition the contract requires to succeed.  Running as root masked it
    entirely, because root bypasses the permission check.

    A component that does not exist yet resolves nothing, so only existing
    ancestors are returned; the nearest existing one governs whatever this
    primitive later creates beneath it, since the casefold attribute is
    inherited at creation and nothing here sets it.

    Bound, stated rather than assumed: the only caller-supplied spelling this
    decision consumes is the target root, plus the carrier path this module
    composes itself.  Every other path in the relation -- mount points -- comes
    from the kernel in canonical form and is compared against other kernel
    output, so no user-chosen spelling mediates those comparisons.
    """

    normalized = _normalize_absolute_v2(path)
    authorities: list[str] = []
    current = os.path.dirname(normalized)
    while True:
        observation = _observe_directory_v2(current)
        if observation is _DIRECTORY_SYMLINK_V2:
            # A symlink resolves names somewhere this walk has not established,
            # so the lookup semantics of the real resolving directory are not
            # in hand.  Locally unestablished -> UNKNOWN, decided here rather
            # than deferred to a later authority.
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        if observation is _DIRECTORY_UNKNOWN_V2:
            # `os.path.isdir` answers False for a directory that EXISTS but
            # cannot be observed, which is indistinguishable from absent -- so
            # an unreadable, possibly casefolded, authority was silently
            # dropped and the textual comparisons proceeded as though lookup
            # were case-sensitive.  An unobservable authority is UNKNOWN.
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        if observation is _DIRECTORY_PRESENT_V2:
            authorities.append(current)
        # `absent` and `other` establish that no lookup happens here yet
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return tuple(authorities)


def _require_name_semantics_applicable_v2(
    snapshot: "MountTopologySnapshotV2", *paths: str
) -> None:
    """Establish that pathname SPELLING is a valid discriminator, or refuse.

    This module compares and contains paths textually.  That is only sound
    where lookup is case-sensitive.  Scope bound, kept minimal on purpose: the
    directories whose lookup this decision actually consumes are the ones the
    carrier and target names are resolved in, so only those are probed -- not
    arbitrary descendants, which decide nothing here.

    ext2/ext3/tmpfs cannot carry a casefolded directory at all, so their
    case-sensitivity is established by the filesystem type and no probe is
    attempted -- which also avoids reading an `ENOTTY` from a filesystem that
    simply has no such flag as though it were an answer.
    """

    for path in paths:
        # `#262` N17.  Each component of a pathname is resolved by ITS OWN
        # parent directory, and those directories may sit on different
        # filesystems.  Taking the capability from the FINAL path's filesystem
        # and applying it to every ancestor probed authorities that cannot
        # casefold at all -- an ext4 target beneath `/proc/...` made
        # `FS_IOC_GETFLAGS` answer ENOTTY on `/proc`, which became a false
        # UNKNOWN.  The decision is per authority, from that authority's own
        # governing filesystem.
        for authority in _lookup_authorities_v2(path):
            governing = snapshot.resolve_query_v2(
                TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, authority)).governing_mount
            capability = _name_semantics_capability_v2(governing.filesystem_type)
            if capability is _NAME_SEMANTICS_ESTABLISHED_CASE_SENSITIVE_V2:
                continue
            if capability is _NAME_SEMANTICS_UNKNOWN_V2:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )
            casefolded = _directory_is_casefolded_v2(authority)
            if casefolded is None or casefolded:
                raise TargetPackEpochError(
                    TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
                )


class _PhysicalSegmentV2(NamedTuple):
    """A slice of one filesystem that is actually visible at some namespace
    path: everything under `internal_path` on `device`, MINUS the internal
    prefixes covered by visible child mounts."""

    device: int
    internal_path: str
    covered: tuple[str, ...]

    def intersects(self, device: int, internal_path: str) -> bool:
        """Do this visible segment and the subtree rooted at *internal_path*
        share any ground?

        Symmetric on purpose.  A carrier site can sit INSIDE the target (the
        pack asked to diagnose a directory beneath its own runtime carrier),
        and the target can sit inside a carrier site (the carrier root
        contains the target).  Both are the same failure, and testing only one
        direction misses the other.
        """

        if device != self.device:
            return False
        if _within_v2(internal_path, self.internal_path):
            # A visible child mount REPLACES the slice it covers, so storage
            # that still exists underneath it is no longer reachable there and
            # is not part of this segment.
            return not any(_within_v2(internal_path, hidden) for hidden in self.covered)
        if _within_v2(self.internal_path, internal_path):
            return True
        return False


def _visible_physical_domain_v2(
    snapshot: MountTopologySnapshotV2, path: str
) -> tuple[_PhysicalSegmentV2, ...]:
    """The physical ground actually reachable beneath *path*, as a PARTITION.

    Not a union of whole subtrees.  A naive union would count both the covered
    lower storage and the visible mount that replaced it, so an object that no
    longer appears anywhere under *path* would still be judged to be in its
    domain.  Each segment therefore excludes the slices its visible children
    cover, and each visible child contributes its own segment (`#262`, spike
    amendment 1).

    Hidden mounts contribute nothing: they are not reachable beneath *path* at
    all, so nothing they expose belongs to this domain either.
    """

    path = _normalize_absolute_v2(path)
    segments: list[_PhysicalSegmentV2] = []
    # The typed query is the authority for what topology matters here. This
    # builder performs no relevance scan of its own -- choosing that extension
    # per-consumer is exactly what produced the F3 -> N9 recurrence.
    resolution = snapshot.resolve_query_v2(
        TopologyQueryV2(TopologyQueryKindV2.VISIBLE_SUBTREE, path))
    children = resolution.visible_descendants

    # DOMAIN CLOSURE (`#262` N7).  Applicability must hold for EVERY segment
    # admitted into the domain, not merely for the governing mount of the
    # target root.  An earlier revision gated the root and the carrier sites
    # and then admitted child segments unchecked, so an overlay mounted inside
    # an ext4 target contributed a segment described by its own opaque device,
    # intersected nothing, and the carrier landed in ground visible beneath
    # the target.  A segment this module cannot project is not a segment it
    # may reason about.
    #
    # HIDDEN unsupported mounts are deliberately NOT consulted: they
    # contribute no segment, so they cannot poison a domain they are not part
    # of.  That distinction is load-bearing -- the alternative refuses every
    # host with an unrelated overlay parked somewhere out of view.
    _require_projection_applicable_v2(snapshot, path, *[c.mount_point for c in children])

    def covered_for(container_path: str, container_device: int, container_internal: str) -> tuple[str, ...]:
        covered: list[str] = []
        for child in children:
            if not _within_v2(child.mount_point, container_path):
                continue
            # only the child's own attachment point, projected into THIS
            # container's filesystem, is covered by it
            remainder = child.mount_point[len(container_path.rstrip("/")):]
            if not remainder:
                covered.append(container_internal)
                continue
            covered.append(_normalize_absolute_v2(container_internal.rstrip("/") + remainder))
        return tuple(covered)

    base_device, base_internal = snapshot.project_v2(path)
    segments.append(_PhysicalSegmentV2(
        base_device, base_internal,
        covered_for(path, base_device, base_internal)))

    for child in children:
        child_covered = covered_for(child.mount_point, child.device, child.root)
        segments.append(_PhysicalSegmentV2(
            child.device, child.root,
            tuple(c for c in child_covered if c != child.root)))
    return tuple(segments)


def _carrier_operational_sites_v2(euid: int, key: str) -> tuple[str, ...]:
    """Every filesystem object this acquisition actually OPERATES ON.

    "Operates on" rather than "mutates", because the two are not the same and
    conflating them understates the domain.  The `<key>.lock` carries the K
    SH/EX advisory lock; holding it is observable from any other opener of the
    same object even when no byte is ever written.  A contract that tracked
    only persistent writes would call that disjoint.

    An earlier revision named the protocol directory alone and asserted, in a
    comment, that projecting that one subtree covered `<key>.lock` too.  That
    is false the moment a mount sits at the lock path itself: bind a file from
    inside the target onto `<protocol_dir>/<key>.lock` and the lock K takes IS
    that file -- same `st_dev`/`st_ino`, contention observable from the target
    side -- while the protocol directory's own projection stays innocently
    outside the target.  Projecting a subtree root does not project mounts
    nested beneath it.

    So both real sites are named:

      protocol directory   created by `mkdir`, opened, holds the namespace SH
                           flock, and receives the lock's directory entry
      <key>.lock           opened `O_RDWR|O_CREAT`, holds the K SH/EX flock,
                           and may itself be a mount or an alias

    Exactly these two and nothing else.  A mount at some unrelated name inside
    the protocol directory is never touched by this acquisition and must not
    be refused for merely existing there -- which is why this is a precise
    operational domain rather than a blanket "anything beneath the carrier is
    UNKNOWN" rule.

    `key` is the K already computed for this acquisition and is threaded in
    rather than recomputed, so the site named here is the object actually
    opened later.
    """

    protocol_directory = _RUNTIME_PARENT_PATH_V2 / _protocol_directory_name_v2(euid)
    return (str(protocol_directory), str(protocol_directory / f"{key}.lock"))


def _declared_carrier_operation_sites_v2(euid: int, key: str) -> tuple[str, ...]:
    """The sites the implementation DECLARES it operates on.

    A named seam so the closure between what acquisition actually opens and
    what K-DISJOINT proves about can be asserted mechanically by a test rather
    than by prose, which drifts the first time an operation is added.  The
    regression this guards against is precisely the one that occurred: a
    second filesystem object (`<key>.lock`) was operated on while the domain
    still named only the first.
    """

    return _carrier_operational_sites_v2(euid, key)


def _require_carrier_object_shape_v2(euid: int, key: str) -> None:
    """Establish, locally and before any mutation, that the carrier objects
    have a shape this module is willing to operate on.

    The protocol directory may not be a symlink.  That has always been the
    contract -- `_validate_protocol_directory_v2` refuses it and the public
    reason is `target_pack_epoch_unavailable` -- but until now the earlier
    disjointness reasoning depended on that LATER check to be safe.  Deciding
    it here makes the earlier reasoning stand on its own: removing or
    reordering the `O_NOFOLLOW` validation can no longer silently turn this
    decision from sound to unsound, and that validation remains as TOCTOU
    revalidation of the same contract rather than as its first truth-maker.

    Only shapes already ESTABLISHED forbidden answer `unavailable` here.  A
    shape that cannot be observed is not this function's business -- it is an
    unestablished lookup predicate, and the disjointness authority answers it
    as UNKNOWN.
    """

    for site in _carrier_operational_sites_v2(euid, key):
        if _observe_directory_v2(site) is _DIRECTORY_SYMLINK_V2:
            raise TargetPackEpochError(TARGET_PACK_EPOCH_UNAVAILABLE_REASON_V2)


def _establish_carrier_disjoint_v2(
    *, canonical_target_subject: bytes, euid: int, key: str,
    snapshot: "MountTopologySnapshotV2 | None" = None,
) -> None:
    """Establish `CarrierVisibleMutationDomain(K) ∩ TargetVisiblePhysicalDomain(D) = ∅`,
    or refuse.  Called BEFORE any carrier materialization, so a refused target
    is never mutated first.

    This is ONE relation over visible physical ground, not a list of alias
    rules.  Each historical alias case -- target containing the carrier, a bind
    alias of the runtime parent, a deep alias inside the target, an ancestral
    alias, a pre-existing carrier mounted from the target, a runtime parent
    grafted from a target subtree -- is the same statement once both sides are
    projected through the visible mount topology, and none of them needs a rule
    of its own.

    Scope bound, stated rather than glossed: the topology is observed once,
    immediately before materialization.  A cooperating process does not remount
    underneath itself, and `#262` excludes external non-cooperating actors,
    distributed coordination and crash atomicity.  Against an adversary racing
    a mount into the window between this proof and the carrier write, this
    establishes nothing, and no claim to the contrary is made here.
    """

    try:
        target_path = os.fsdecode(canonical_target_subject)
    except (UnicodeDecodeError, ValueError) as exc:
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        ) from exc

    if snapshot is None:
        raise TargetPackEpochError(
            TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
        )

    # Carrier object shape is established here, locally, before anything else
    # consumes these paths (`#262`, symlink reason ownership).
    _require_carrier_object_shape_v2(euid, key)
    sites = _carrier_operational_sites_v2(euid, key)
    # Applicability BEFORE projection is consumed, for both sides.
    _require_projection_applicable_v2(snapshot, target_path, *sites)
    _require_name_semantics_applicable_v2(snapshot, target_path, *sites)

    target_domain = _visible_physical_domain_v2(snapshot, target_path)

    for site in sites:
        device, internal = snapshot.project_v2(site)
        for segment in target_domain:
            if segment.intersects(device, internal):
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
    # `#262` F4.  ONE observation per acquisition: runtime-parent eligibility
    # and the disjointness decision must be established from the SAME topology,
    # or they can describe different worlds.
    topology = MountTopologySnapshotV2.observe()
    # `#262`: ONE typed resolution of the runtime parent, threaded to every
    # consumer that needs it.  Eligibility, filesystem type and the FD identity
    # check then reason about a single coherent subject instead of performing
    # separate lookups that could disagree.
    runtime_parent_resolution = topology.resolve_query_v2(
        TopologyQueryV2(TopologyQueryKindV2.POINT_LOOKUP, str(_RUNTIME_PARENT_PATH_V2)))
    parent_fd, parent_identity = _open_runtime_parent_v2(
        euid=euid, runtime_parent_resolution=runtime_parent_resolution)
    namespace_fd: int | None = None
    carrier_fd: int | None = None
    try:
        # `K-DISJOINT` (#262).  The earliest authority: established here, before
        # the protocol directory is created, so a refused target is never
        # mutated first.  No consumer re-derives it.
        # the opened parent FD must be the object the captured topology
        # describes; a mismatch means the two disagree and is not resolvable here
        if parent_identity[0] != runtime_parent_resolution.governing_mount.device:
            raise TargetPackEpochError(
                TARGET_PACK_EPOCH_CARRIER_DISJOINTNESS_UNKNOWN_REASON_V2
            )
        _establish_carrier_disjoint_v2(
            canonical_target_subject=canonical_subject, euid=euid, key=key,
            snapshot=topology)
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
