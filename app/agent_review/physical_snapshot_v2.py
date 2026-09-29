"""`#301-S1-A` -- publish an authorized physical Git object-store snapshot.

Contract: `docs/engineering/agent-review-v2-301-s1a/ARCHITECTURE_FREEZE.md`
(integrated at `3aab1aad`, blob `6e89a64b`), as refined append-only by
`docs/engineering/agent-review-v2-301-s1a/IMPLEMENTATION_ADJUDICATION.md`
(C11 ownership redesign and its declared limitation, capability sealing,
duplicate-occurrence semantics). Those files are the authority for every
rule below; this docstring only maps them onto the code. Where they
disagree with it, they win.

## What this module does

```
(A, L, F, P, W)
  A = AuthorizedGitStorageSetV2    source READ authority (C2_A)
  L = SourceRepositoryLocatorV2    a hostile NAME inside A, never an authority
  F = DeclaredGitObjectFormatV2    declared by the caller, never read from the source
  P = PhysicalWorkBudgetV2         explicit, every axis mandatory
  W = SnapshotPublicationRootV2    publication WRITE authority, built from an fd
      -> root-outward physical layout resolution (.git, gitdir:, commondir, alternates)
      -> one PhysicalWorkTrackerV2 charging every source-side operation
      -> physical copy of loose objects and complete pack pairs into staging/<id>
      -> producer-authored constant skeleton + receipt
      -> renameat2(RENAME_NOREPLACE) into committed/<id>
      -> PublicationOutcomeV2 (Complete | Unconfirmed | Indeterminate | NotPublished)
```

## What it deliberately does not do

`PhysicalLayoutResolution != GitSemanticInterpretation`: no HEAD, refs,
packed-refs, source config, commit selection, ancestry, root tree, closure,
C3 semantics or `ExpectedCommit` provenance. No git process, no zlib
inflate, no pack parsing. `PublishedSnapshotReceipt != ObjectAuthenticityProof`:
the receipt is traceability only; S1-C authenticates what was copied.
`DeclaredObjectFormat != AuthenticatedObjectFormat`. Reader context,
`no_new_privs`, contained Git, framing and deadlines belong to S1-B; closure
accounting to S1-C; general availability and production budget values to
#320/U3; the provenance of A to #331 (C2_B). This module claims only
`S1_A_CORE_PHYSICAL_AUTHORITY`, `S1_A_STORAGE_ACCESS_CONTAINMENT`,
`S1_RES_01.physical_snapshot_subdomain` and `S1_PUBLISH_01.mechanism`.

## Supported domain (declared, not inferred)

Linux >= 5.8 (`statx` `STATX_MNT_ID`), x86_64 or aarch64 (explicit syscall
table), a local filesystem implementing `RENAME_NOREPLACE`, staging and
committed on the same mount. Anything else fails closed.
"""

from __future__ import annotations

import ctypes
import enum
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import struct
import sys
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Union

from app.agent_review.trusted_object_authority_v2 import (
    TRUSTED_OBJECT_AUTHORITY_STORAGE_CAPABILITY_CLOSED_REASON_V2,
    AuthorizedGitStorageSetV2,
    AuthorizedStorageRootDuplicateV2,
    TrustedObjectAuthorityError,
)

__all__ = [
    "PHYSICAL_SNAPSHOT_ALTERNATE_NESTING_EXCEEDS_GIT_LIMIT_REASON_V2",
    "PHYSICAL_SNAPSHOT_ALTERNATE_QUOTED_PATH_UNSUPPORTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_ALTERNATE_TARGET_MISSING_REASON_V2",
    "PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2",
    "PHYSICAL_SNAPSHOT_COMMIT_STATE_UNOBSERVABLE_REASON_V2",
    "PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2",
    "PHYSICAL_SNAPSHOT_INVALID_BUDGET_REASON_V2",
    "PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2",
    "PHYSICAL_SNAPSHOT_NOT_AN_OBJECT_STORE_REASON_V2",
    "PHYSICAL_SNAPSHOT_POINTER_ESCAPES_CAPABILITY_ROOT_REASON_V2",
    "PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2",
    "PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2",
    "PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_CROSS_MOUNT_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_MOUNT_IDENTITY_UNAVAILABLE_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_NAMESPACES_NOT_DISTINCT_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_CLOSED_REASON_V2",
    "PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_UNUSABLE_REASON_V2",
    "PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2",
    "PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_RENAME_COLLISION_REASON_V2",
    "PHYSICAL_SNAPSHOT_RENAME_CROSS_MOUNT_REASON_V2",
    "PHYSICAL_SNAPSHOT_RENAME_FAILED_REASON_V2",
    "PHYSICAL_SNAPSHOT_RENAME_ERROR_BUT_COMMITTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_REPOSITORY_LAYOUT_UNUSABLE_REASON_V2",
    "PHYSICAL_SNAPSHOT_REPOSITORY_LOCATOR_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2",
    "PHYSICAL_SNAPSHOT_SHORT_WRITE_NO_PROGRESS_REASON_V2",
    "PHYSICAL_SNAPSHOT_SNAPSHOT_ID_COLLISION_REASON_V2",
    "PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2",
    "PHYSICAL_SNAPSHOT_SOURCE_CAPABILITY_CLOSED_REASON_V2",
    "PHYSICAL_SNAPSHOT_SOURCE_CHANGED_DURING_READ_REASON_V2",
    "PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2",
    "PHYSICAL_SNAPSHOT_RECEIPT_SCHEMA_V2",
    "PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2",
    "CommittedSnapshotResidualV2",
    "CompletePublicationV2",
    "DeclaredGitObjectFormatV2",
    "IndeterminatePublicationV2",
    "KernelObjectIdentityV2",
    "NotPublishedV2",
    "PhysicalSnapshotErrorV2",
    "PhysicalWorkBudgetV2",
    "PhysicalWorkTrackerV2",
    "PublicationOutcomeV2",
    "PublicationResidualV2",
    "PublishedSnapshotBindingV2",
    "PublishedSnapshotReceiptV2",
    "PublishedSnapshotV2",
    "SnapshotPublicationRootV2",
    "SourceRepositoryLocatorV2",
    "UnconfirmedPublicationV2",
    "publish_physical_snapshot_v2",
]

# -- reason codes ---------------------------------------------------------------
#
# Content-free, like every other primitive in this package: no path, no errno
# text, nothing that can leak repository content or host layout.

# Caller-contract errors (raised as `PhysicalSnapshotErrorV2`).
PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2 = "physical_snapshot_invalid_input"
PHYSICAL_SNAPSHOT_INVALID_BUDGET_REASON_V2 = "physical_snapshot_invalid_budget"
PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2 = "physical_snapshot_forged_capability"
PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_UNUSABLE_REASON_V2 = "physical_snapshot_publication_root_unusable"
PHYSICAL_SNAPSHOT_PUBLICATION_MOUNT_IDENTITY_UNAVAILABLE_REASON_V2 = (
    "physical_snapshot_publication_mount_identity_unavailable"
)
# `SameStDev != SameRenameDomain`: rename(2) returns EXDEV across mount points
# even for one filesystem mounted twice, so W compares mount ids, not st_dev.
PHYSICAL_SNAPSHOT_PUBLICATION_CROSS_MOUNT_REASON_V2 = "physical_snapshot_publication_cross_mount"
PHYSICAL_SNAPSHOT_PUBLICATION_NAMESPACES_NOT_DISTINCT_REASON_V2 = (
    "physical_snapshot_publication_namespaces_not_distinct"
)

# Source-side refusals (returned as `NotPublishedV2`).
PHYSICAL_SNAPSHOT_SOURCE_CAPABILITY_CLOSED_REASON_V2 = "physical_snapshot_source_capability_closed"
PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2 = "physical_snapshot_source_acquisition_failed"
PHYSICAL_SNAPSHOT_REPOSITORY_LOCATOR_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2 = (
    "physical_snapshot_repository_locator_outside_authorized_storage"
)
PHYSICAL_SNAPSHOT_REPOSITORY_LAYOUT_UNUSABLE_REASON_V2 = "physical_snapshot_repository_layout_unusable"
PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2 = (
    "physical_snapshot_pointer_outside_authorized_storage"
)
PHYSICAL_SNAPSHOT_POINTER_ESCAPES_CAPABILITY_ROOT_REASON_V2 = "physical_snapshot_pointer_escapes_capability_root"
PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2 = "physical_snapshot_pointer_malformed"
PHYSICAL_SNAPSHOT_ALTERNATE_QUOTED_PATH_UNSUPPORTED_REASON_V2 = (
    "physical_snapshot_alternate_quoted_path_unsupported"
)
PHYSICAL_SNAPSHOT_ALTERNATE_TARGET_MISSING_REASON_V2 = "physical_snapshot_alternate_target_missing"
PHYSICAL_SNAPSHOT_ALTERNATE_NESTING_EXCEEDS_GIT_LIMIT_REASON_V2 = (
    "physical_snapshot_alternate_nesting_exceeds_git_limit"
)
PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2 = "physical_snapshot_symlink_rejected"
PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2 = "physical_snapshot_special_file_rejected"
PHYSICAL_SNAPSHOT_NOT_AN_OBJECT_STORE_REASON_V2 = "physical_snapshot_not_an_object_store"
PHYSICAL_SNAPSHOT_SOURCE_CHANGED_DURING_READ_REASON_V2 = "physical_snapshot_source_changed_during_read"
PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2 = "physical_snapshot_physical_budget_exceeded"

# Publication-side refusals and post-commit states.
PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_CLOSED_REASON_V2 = "physical_snapshot_publication_root_closed"
PHYSICAL_SNAPSHOT_SNAPSHOT_ID_COLLISION_REASON_V2 = "physical_snapshot_snapshot_id_collision"
PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2 = "physical_snapshot_publication_io_failed"
PHYSICAL_SNAPSHOT_SHORT_WRITE_NO_PROGRESS_REASON_V2 = "physical_snapshot_short_write_no_progress"
PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2 = "physical_snapshot_descriptor_close_failed"
PHYSICAL_SNAPSHOT_RENAME_COLLISION_REASON_V2 = "physical_snapshot_rename_collision"
PHYSICAL_SNAPSHOT_RENAME_CROSS_MOUNT_REASON_V2 = "physical_snapshot_rename_cross_mount"
PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2 = "physical_snapshot_publication_atomic_noreplace_unsupported"
PHYSICAL_SNAPSHOT_RENAME_FAILED_REASON_V2 = "physical_snapshot_rename_failed"
PHYSICAL_SNAPSHOT_RENAME_ERROR_BUT_COMMITTED_REASON_V2 = "physical_snapshot_rename_error_but_committed"
PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2 = "physical_snapshot_post_commit_sync_failed"
PHYSICAL_SNAPSHOT_COMMIT_STATE_UNOBSERVABLE_REASON_V2 = "physical_snapshot_commit_state_unobservable"
PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2 = "physical_snapshot_publication_interrupted"

PHYSICAL_SNAPSHOT_RECEIPT_SCHEMA_V2 = "ar301-s1a.physical-snapshot-receipt.v1"
PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2 = "agentreview-physical-snapshot-receipt.json"

#: Git's own alternates nesting limit (`depth > 5` when reading an alternates
#: file, depth 0 = the primary store's). A copied constant, declared in the
#: freeze (§9, §28.7); parity at the boundary is a mandatory test case.
_GIT_ALTERNATE_NESTING_LIMIT_V2 = 5

_DIR_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_PROBE_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_CREATE_FILE_FLAGS_V2 = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

_FILE_MODE_V2 = 0o444
_DIR_MODE_V2 = 0o555
_STAGING_DIR_MODE_V2 = 0o700
_STAGING_FILE_MODE_V2 = 0o600

_SNAPSHOT_ID_RE_V2 = re.compile(r"[0-9a-f]{32}")
_FANOUT_RE_V2 = re.compile(r"[0-9a-f]{2}")

_PUBLICATION_ROOT_SENTINEL_V2 = object()
_SNAPSHOT_SENTINEL_V2 = object()


class PhysicalSnapshotErrorV2(ValueError):
    """A caller-contract violation or a publication root that cannot be built.

    Refusals that happen while acquiring or publishing are NOT raised: they
    are returned as a typed `PublicationOutcomeV2` variant.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _RefusalV2(Exception):
    """Internal: a pre-commit refusal, turned into `NotPublishedV2` at the top."""

    def __init__(self, reason_code: str, exceeded_axis: str | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.exceeded_axis = exceeded_axis


# -- physical work budget (freeze §10) ------------------------------------------

_BUDGET_AXES_V2 = (
    "descriptor_opens",
    "path_components",
    "entries_scanned",
    "pointers_followed",
    "pointer_bytes",
    "pointer_lines",
    "alternate_depth",
    "source_bytes",
    "files_copied",
)
_CUMULATIVE_AXES_V2 = frozenset(_BUDGET_AXES_V2) - {"alternate_depth"}


@dataclass(frozen=True)
class PhysicalWorkBudgetV2:
    """Explicit physical-work policy. Every axis is mandatory and positive.

    There are deliberately no defaults: production values are owned by
    #320/U3 (`PLANNING_GAP`, not chosen here). `alternate_depth` bounds the
    deepest alternate hop; every other axis is cumulative.
    """

    max_descriptor_opens: int
    max_path_components: int
    max_entries_scanned: int
    max_pointers_followed: int
    max_pointer_bytes: int
    max_pointer_lines: int
    max_alternate_depth: int
    max_source_bytes: int
    max_files_copied: int

    def __post_init__(self) -> None:
        for axis in _BUDGET_AXES_V2:
            value = getattr(self, "max_" + axis)
            if type(value) is not int or value <= 0:
                raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_BUDGET_REASON_V2)


class PhysicalWorkTrackerV2:
    """The single tracker of one publication (`OnePhysicalAcquisition -> OnePhysicalWorkTracker`).

    `charge` is called BEFORE the operation it pays for. One occurrence is one
    event; an event may decrement several axes; an event over budget is
    refused whole and not applied (the operation it guarded never happens);
    no event is ever reversed, so output deduplication never returns budget
    (`OutputDeduplication != InputWorkDeduplication`).
    """

    def __init__(self, budget: PhysicalWorkBudgetV2) -> None:
        # Exact type: a subclass could answer differently at validation time
        # and at charge time.
        if type(budget) is not PhysicalWorkBudgetV2:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_BUDGET_REASON_V2)
        self._budget = budget
        self._consumed = dict.fromkeys(_BUDGET_AXES_V2, 0)
        self._events = 0

    @property
    def events(self) -> int:
        return self._events

    def consumed(self) -> dict[str, int]:
        return dict(self._consumed)

    def charge(self, **amounts: int) -> None:
        proposed: dict[str, int] = {}
        for axis, amount in amounts.items():
            if axis not in _CUMULATIVE_AXES_V2 or type(amount) is not int or amount < 0:
                raise ValueError("invalid physical work charge")
            proposed[axis] = self._consumed[axis] + amount
        for axis in _BUDGET_AXES_V2:
            if axis in proposed and proposed[axis] > getattr(self._budget, "max_" + axis):
                raise _RefusalV2(PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2, exceeded_axis=axis)
        self._consumed.update(proposed)
        self._events += 1

    def charge_alternate_depth(self, depth: int) -> None:
        """Charge an alternate hop before its target is opened."""
        if depth > self._budget.max_alternate_depth:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_BUDGET_EXCEEDED_REASON_V2, exceeded_axis="alternate_depth")
        self._consumed["alternate_depth"] = max(self._consumed["alternate_depth"], depth)
        self._events += 1


# -- inputs (freeze §3) -----------------------------------------------------------


class DeclaredGitObjectFormatV2(enum.Enum):
    """The object format DECLARED by the caller (`F`).

    Used only to classify physical names and to author the snapshot config;
    never read from the source and never authenticated here
    (`DeclaredObjectFormat != AuthenticatedObjectFormat`, S1-C).
    """

    SHA1 = "sha1"
    SHA256 = "sha256"

    @property
    def hex_length(self) -> int:
        return 40 if self is DeclaredGitObjectFormatV2.SHA1 else 64


def _split_normalized_absolute_v2(text: str) -> tuple[str, ...] | None:
    """Strict absolute name: '/' c1 ('/' ci)*, no '', '.', '..', NUL or trailing '/'."""
    if not text.startswith("/") or "\x00" in text:
        return None
    parts = text[1:].split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return tuple(parts)


@dataclass(frozen=True)
class SourceRepositoryLocatorV2:
    """`L`: where the repository sits INSIDE A. A name, never an authority.

    Either an absolute normalized path, matched lexically against the
    locators A captured (`RepoRootLocator != RepoRootAuthorization`), or a
    root index of A. A locator that matches no root is refused before any
    open.
    """

    root_index: int | None = None
    components: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if (self.root_index is None) == (self.components is None):
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        if self.root_index is not None and (type(self.root_index) is not int or self.root_index < 0):
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        if self.components is not None and (
            type(self.components) is not tuple
            or not self.components
            or any(type(c) is not str or c in ("", ".", "..") or "/" in c or "\x00" in c for c in self.components)
        ):
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)

    @classmethod
    def absolute(cls, path: str) -> SourceRepositoryLocatorV2:
        if type(path) is not str:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        parts = _split_normalized_absolute_v2(path)
        if parts is None:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        return cls(components=parts)

    @classmethod
    def root(cls, index: int) -> SourceRepositoryLocatorV2:
        return cls(root_index=index)


# -- Linux syscalls: renameat2, statx (freeze §12 D7, §6 D9) ------------------------

#: Explicit per-architecture numbers: the glibc wrapper version is not a
#: hidden premise. Any other architecture fails closed.
_SYSCALL_NUMBERS_V2: dict[str, dict[str, int]] = {
    "x86_64": {"renameat2": 316, "statx": 332},
    "aarch64": {"renameat2": 276, "statx": 291},
}
_RENAME_NOREPLACE_V2 = 1
_AT_SYMLINK_NOFOLLOW_V2 = 0x100
_AT_EMPTY_PATH_V2 = 0x1000
_STATX_BASIC_STATS_V2 = 0x07FF
_STATX_MNT_ID_V2 = 0x1000
_STATX_BUFFER_SIZE_V2 = 256
_STATX_OFFSET_MASK_V2 = 0
_STATX_OFFSET_INO_V2 = 32
_STATX_OFFSET_DEV_MAJOR_V2 = 136
_STATX_OFFSET_MNT_ID_V2 = 144


class _SyscallUnavailableV2(Exception):
    """The declared syscall cannot be issued on this platform."""


_LIBC_LOCK_V2 = threading.Lock()
_LIBC_V2: ctypes.CDLL | None = None


def _syscall_v2(name: str, *args: object) -> tuple[int, int]:
    """Issue syscall `name`; return `(result, errno)`. Every argument is
    passed at full register width (c_long / pointer) because `syscall(2)` is
    variadic."""
    if not sys.platform.startswith("linux"):
        raise _SyscallUnavailableV2(name)
    table = _SYSCALL_NUMBERS_V2.get(platform.machine())
    if table is None:
        raise _SyscallUnavailableV2(name)
    global _LIBC_V2
    with _LIBC_LOCK_V2:
        if _LIBC_V2 is None:
            libc = ctypes.CDLL(None, use_errno=True)
            libc.syscall.restype = ctypes.c_long
            _LIBC_V2 = libc
        libc = _LIBC_V2
    ctypes.set_errno(0)
    result = libc.syscall(ctypes.c_long(table[name]), *args)
    return int(result), ctypes.get_errno()


def _renameat2_noreplace_v2(old_dir_fd: int, old_name: str, new_dir_fd: int, new_name: str) -> int:
    """`renameat2(..., RENAME_NOREPLACE)`: 0 on success, else a positive errno.

    Never falls back to `rename`/`os.replace`, and never checks for existence
    first (`if not exists: rename()` is a TOCTOU the freeze forbids).
    """
    result, err = _syscall_v2(
        "renameat2",
        ctypes.c_long(old_dir_fd),
        ctypes.c_char_p(os.fsencode(old_name)),
        ctypes.c_long(new_dir_fd),
        ctypes.c_char_p(os.fsencode(new_name)),
        ctypes.c_ulong(_RENAME_NOREPLACE_V2),
    )
    if result == 0:
        return 0
    return err or errno.EIO


@dataclass(frozen=True)
class KernelObjectIdentityV2:
    """`(mount_id, st_dev, st_ino)` of an open descriptor, from `statx`."""

    mount_id: int
    st_dev: int
    st_ino: int


class _MountIdentityUnavailableV2(Exception):
    pass


def _statx_identity_v2(fd: int) -> KernelObjectIdentityV2:
    buffer = ctypes.create_string_buffer(_STATX_BUFFER_SIZE_V2)
    try:
        result, _err = _syscall_v2(
            "statx",
            ctypes.c_long(fd),
            ctypes.c_char_p(b""),
            ctypes.c_long(_AT_EMPTY_PATH_V2 | _AT_SYMLINK_NOFOLLOW_V2),
            ctypes.c_ulong(_STATX_BASIC_STATS_V2 | _STATX_MNT_ID_V2),
            buffer,
        )
    except _SyscallUnavailableV2 as exc:
        raise _MountIdentityUnavailableV2() from exc
    if result != 0:
        raise _MountIdentityUnavailableV2()
    raw = buffer.raw
    (mask,) = struct.unpack_from("=I", raw, _STATX_OFFSET_MASK_V2)
    if not mask & _STATX_MNT_ID_V2:
        raise _MountIdentityUnavailableV2()
    (ino,) = struct.unpack_from("=Q", raw, _STATX_OFFSET_INO_V2)
    dev_major, dev_minor = struct.unpack_from("=II", raw, _STATX_OFFSET_DEV_MAJOR_V2)
    (mount_id,) = struct.unpack_from("=Q", raw, _STATX_OFFSET_MNT_ID_V2)
    return KernelObjectIdentityV2(mount_id=mount_id, st_dev=os.makedev(dev_major, dev_minor), st_ino=ino)


def _same_rename_domain_v2(first: KernelObjectIdentityV2, second: KernelObjectIdentityV2) -> bool:
    """Same mount, hence a rename between them cannot be EXDEV. `st_dev` alone
    is not enough (`SameStDev != SameRenameDomain`)."""
    return first.mount_id == second.mount_id


def _distinct_namespaces_v2(first: KernelObjectIdentityV2, second: KernelObjectIdentityV2) -> bool:
    """`staging/` and `committed/` must be two kernel objects, or the commit
    point would not move anything between namespaces (P17)."""
    return first != second


# -- descriptor ownership (freeze §21; IMPLEMENTATION_ADJUDICATION C11) ---------------
#
# ONE ownership primitive for every descriptor S1-A receives:
#
#   owner state exists -> owner registered -> acquisition syscall
#     -> descriptor installed into the owner's pre-existing, empty slot
#
# The acquisition call and the slot store are the SAME statement, marked
# `fd-install`; nothing that can fail synchronously (no allocation, no
# `list.append`, no `dict[...] =`, no owner construction) runs between them.
# A transfer is a slot move between two owners that both already exist
# (`_FdSlotV2.move_to`), never "release the old owner, then build the new
# one". A release detaches the descriptor from its slot BEFORE `close()`
# (`DescriptorNumber != DescriptorIdentityAfterClose`: closed at most once; a
# close error means "released, and it failed", never "retry"); the detach and
# the close are marked `fd-release`.
#
# A LOCAL owner is released explicitly on the normal path; its `finally` is the
# failure path only (a release that itself fails must not leave the owner
# pinned to a frame a traceback keeps alive). Cleanup is idempotent and
# resumable, and an interrupted cleanup step is retried once.
#
# The marked statements are the only places where a descriptor is held by no
# owner, and they contain no operation that can fail synchronously. An
# asynchronous, interpreter-level interruption landing exactly there is the
# declared limitation `PYTHON_FD_OWNERSHIP_INSTALLATION_WINDOW`: no universal
# close claim is made for it, and no positive result is inferred from it.


def _require_v2(condition: bool, reason_code: str = PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) -> None:
    """Explicit invariant check (never a bare `assert`, which `-O` strips)."""
    if not condition:
        raise _RefusalV2(reason_code)


class _FdSlotV2:
    """The C11 primitive: one slot, at most one descriptor, exactly one owner.

    Created empty BEFORE the syscall that fills it. `__slots__` makes the
    slot store a plain member write that cannot allocate. The finalizer is
    the last-resort owner (an owner dropped while still holding a descriptor
    closes it once); no normal path relies on it.
    """

    __slots__ = ("fd",)

    def __init__(self) -> None:
        self.fd: int | None = None

    def move_to(self, owner: _FdSlotV2) -> None:
        """Transfer to an owner that already exists and is empty."""
        if owner.fd is not None or self.fd is None:
            raise RuntimeError("descriptor slot transfer requires a full source and an empty target")
        fd = self.fd
        self.fd = None  # fd-install
        owner.fd = fd  # fd-install

    def close_once(self, reason_code: str = PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2) -> None:
        """Detach, then close; a close error is a typed refusal (the descriptor
        is released either way)."""
        fd = self.fd
        if fd is None:
            return
        try:
            self.fd = None  # fd-release
            os.close(fd)  # fd-release
        except OSError as exc:
            raise _RefusalV2(reason_code) from exc

    def close_quietly(self) -> None:
        fd = self.fd
        if fd is None:
            return
        try:
            self.fd = None  # fd-release
            os.close(fd)  # fd-release
        except OSError:
            pass

    def __del__(self) -> None:
        try:
            if self.fd is not None:
                self.close_quietly()
        except Exception:  # pragma: no cover - interpreter teardown
            pass


class _SharedFdSlotV2(_FdSlotV2):
    """A slot handed to a caller (`PublishedSnapshotV2`, the residual): the
    same primitive, with reads and the single close serialized by a lock."""

    __slots__ = ("_lock",)

    def __init__(self) -> None:
        self.fd = None
        self._lock = threading.Lock()

    def get(self) -> int:
        with self._lock:
            fd = self.fd
        if fd is None:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        return fd

    def close_quietly(self) -> None:
        with self._lock:
            _FdSlotV2.close_quietly(self)


# -- sealed capability and state types (IMPLEMENTATION_ADJUDICATION, Codex 4133910390) --
#
# `ExactType + NoSubclassPolymorphism + FactoryInvariant`. A subclass could
# skip the sentinel and hand S1-A caller-chosen descriptors, so no S1-A type
# that carries authority or qualified state can be subclassed; boundaries
# admit by `type(x) is T`, never `isinstance` (which also trusts a spoofed
# `__class__`); the authority objects S1-A mints are registered by identity
# when their factory completes, so an instance made without the factory
# (`object.__new__`) is not admitted either. `ExactType(A) != Provenance(A)`.


class _SealedV2:
    """Marker base: its direct subclasses are sealed, and nothing may derive
    from them."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if any(base is not _SealedV2 and issubclass(base, _SealedV2) for base in cls.__mro__[1:]):
            raise TypeError("S1-A capability and state types are sealed; subclassing is refused")


_GENUINE_PUBLICATION_ROOTS_V2: weakref.WeakSet[SnapshotPublicationRootV2] = weakref.WeakSet()
_GENUINE_SNAPSHOTS_V2: weakref.WeakSet[PublishedSnapshotV2] = weakref.WeakSet()


# -- publication root W (freeze §6) --------------------------------------------------


class SnapshotPublicationRootV2(_SealedV2):
    """`W`: the only namespace S1-A may write in (`PublicationRootLocator != PublicationRootAuthority`).

    Built ONLY from a descriptor the caller already opened; there is no
    `from_path`, and the class is sealed. Who opened that descriptor, with
    which authority, and the ownership/modes of `staging/` and `committed/`
    are U3 premises, as is `W ∩ A = ∅`; none is proven here. What is checked:
    both children exist as real directories (no-follow), carry a kernel
    identity with a mount id, sit on the same mount, and are distinct kernel
    objects.
    """

    def __init__(self, *, _sentinel: object) -> None:
        if _sentinel is not _PUBLICATION_ROOT_SENTINEL_V2:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
        self._lock = threading.Lock()
        self._closed = False
        self._root = _FdSlotV2()
        self._staging = _FdSlotV2()
        self._committed = _FdSlotV2()
        self._identities: tuple[KernelObjectIdentityV2, KernelObjectIdentityV2, KernelObjectIdentityV2] | None = None

    @classmethod
    def from_directory_fd(cls, fd: int) -> SnapshotPublicationRootV2:
        if type(fd) is not int or fd < 0:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
        root = SnapshotPublicationRootV2(_sentinel=_PUBLICATION_ROOT_SENTINEL_V2)
        try:
            try:
                root._root.fd = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 0)  # fd-install
                if not stat.S_ISDIR(os.fstat(root._root.fd).st_mode):
                    raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_UNUSABLE_REASON_V2)
                root._staging.fd = os.open("staging", _DIR_OPEN_FLAGS_V2, dir_fd=root._root.fd)  # fd-install
                root._committed.fd = os.open("committed", _DIR_OPEN_FLAGS_V2, dir_fd=root._root.fd)  # fd-install
            except OSError as exc:
                raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_UNUSABLE_REASON_V2) from exc
            try:
                identities = (
                    _statx_identity_v2(root._root.fd),
                    _statx_identity_v2(root._staging.fd),
                    _statx_identity_v2(root._committed.fd),
                )
            except _MountIdentityUnavailableV2 as exc:
                raise PhysicalSnapshotErrorV2(
                    PHYSICAL_SNAPSHOT_PUBLICATION_MOUNT_IDENTITY_UNAVAILABLE_REASON_V2
                ) from exc
            if not _same_rename_domain_v2(identities[1], identities[2]):
                raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_PUBLICATION_CROSS_MOUNT_REASON_V2)
            if not _distinct_namespaces_v2(identities[1], identities[2]):
                raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_PUBLICATION_NAMESPACES_NOT_DISTINCT_REASON_V2)
            root._identities = identities
            _GENUINE_PUBLICATION_ROOTS_V2.add(root)
        except BaseException:
            root.close()
            raise
        return root

    @property
    def root_identity(self) -> KernelObjectIdentityV2 | None:
        return None if self._identities is None else self._identities[0]

    @property
    def staging_identity(self) -> KernelObjectIdentityV2 | None:
        return None if self._identities is None else self._identities[1]

    @property
    def committed_identity(self) -> KernelObjectIdentityV2 | None:
        return None if self._identities is None else self._identities[2]

    @property
    def closed(self) -> bool:
        return self._closed

    def _duplicate_publication_fds_into(self, staging: _FdSlotV2, committed: _FdSlotV2) -> None:
        """Install caller-owned duplicates of (staging, committed) into the
        caller's pre-existing slots, under the lock, so closing W later cannot
        recycle a descriptor number the publication is still using. On a
        failure the caller's slots own whatever was installed."""
        with self._lock:
            if self._closed:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_CLOSED_REASON_V2)
            _require_v2(
                self._staging.fd is not None and self._committed.fd is not None,
                PHYSICAL_SNAPSHOT_PUBLICATION_ROOT_CLOSED_REASON_V2,
            )
            try:
                staging.fd = fcntl.fcntl(self._staging.fd, fcntl.F_DUPFD_CLOEXEC, 0)  # fd-install
                committed.fd = fcntl.fcntl(self._committed.fd, fcntl.F_DUPFD_CLOEXEC, 0)  # fd-install
            except OSError as exc:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc

    def close(self) -> None:
        """Idempotent and resumable: each slot is detached, then closed once."""
        with self._lock:
            self._closed = True
            self._root.close_quietly()
            self._staging.close_quietly()
            self._committed.close_quietly()

    def __enter__(self) -> SnapshotPublicationRootV2:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def _admit_publication_root_v2(publication_root: object) -> SnapshotPublicationRootV2:
    """Exact type AND minted by `from_directory_fd` (identity registry)."""
    if type(publication_root) is not SnapshotPublicationRootV2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    if publication_root not in _GENUINE_PUBLICATION_ROOTS_V2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
    return publication_root


# -- pointer grammar (freeze §9) -------------------------------------------------------


@dataclass(frozen=True)
class _ParsedPointerV2:
    absolute: bool
    ups: int
    parts: tuple[str, ...]


def _parse_pointer_path_v2(text: str, *, allow_parents_only: bool = False) -> _ParsedPointerV2:
    """Strict subset of Git's pointer paths. Anything else is refused, typed:
    the declared over-rejection of the freeze (`..` after a descending
    component, `.`, empty components, trailing '/', '//', surrounding
    whitespace, NUL, CR). A relative pointer needs at least one descending
    component (§9); a pointer made only of `..` is admitted solely where the
    freeze names it -- `commondir` (`../..`, §11.3)."""
    if not text or text != text.strip() or any(c in text for c in ("\x00", "\r", "\n")):
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    if text.startswith("/"):
        parts = _split_normalized_absolute_v2(text)
        if parts is None:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
        return _ParsedPointerV2(absolute=True, ups=0, parts=parts)
    raw = text.split("/")
    if any(part == "" for part in raw):
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    ups = 0
    while ups < len(raw) and raw[ups] == "..":
        ups += 1
    rest = tuple(raw[ups:])
    if any(part in (".", "..") for part in rest):
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    if not rest and not allow_parents_only:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    return _ParsedPointerV2(absolute=False, ups=ups, parts=rest)


def _decode_pointer_bytes_v2(data: bytes) -> str:
    if b"\x00" in data:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2) from exc


def _single_line_v2(tracker: PhysicalWorkTrackerV2, data: bytes) -> str:
    """Exactly one line, optionally terminated by one LF; charged as one line."""
    text = _decode_pointer_bytes_v2(data)
    if text.endswith("\n"):
        text = text[:-1]
    tracker.charge(pointer_lines=1)
    if "\n" in text:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    return text


def _parse_gitfile_v2(tracker: PhysicalWorkTrackerV2, data: bytes) -> _ParsedPointerV2:
    line = _single_line_v2(tracker, data)
    prefix = "gitdir: "
    if not line.startswith(prefix):
        raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
    pointer = _parse_pointer_path_v2(line[len(prefix):])
    tracker.charge(pointers_followed=1)
    return pointer


def _parse_commondir_v2(tracker: PhysicalWorkTrackerV2, data: bytes) -> _ParsedPointerV2:
    pointer = _parse_pointer_path_v2(_single_line_v2(tracker, data), allow_parents_only=True)
    tracker.charge(pointers_followed=1)
    return pointer


def _parse_alternates_v2(tracker: PhysicalWorkTrackerV2, data: bytes) -> list[_ParsedPointerV2]:
    """Every line is charged BEFORE it is interpreted (blank and comment lines
    included); every effective entry is charged as a followed pointer before
    it is resolved. Quoted (C-escaped) entries are refused, not decoded."""
    text = _decode_pointer_bytes_v2(data)
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    pointers: list[_ParsedPointerV2] = []
    for line in lines:
        tracker.charge(pointer_lines=1)
        if line == "" or line.startswith("#"):
            continue
        if line.startswith('"'):
            raise _RefusalV2(PHYSICAL_SNAPSHOT_ALTERNATE_QUOTED_PATH_UNSUPPORTED_REASON_V2)
        pointer = _parse_pointer_path_v2(line)
        tracker.charge(pointers_followed=1)
        pointers.append(pointer)
    return pointers


# -- charged source reads and listings (freeze §10, §11b) ------------------------------


def _read_charged_v2(tracker: PhysicalWorkTrackerV2, fd: int, *, pointer: bool) -> bytes:
    """`fstat` -> charge -> read at most size+1 bytes, all on the SAME fd.

    A size mismatch in either direction (grew or shrank) is refused
    (`source_changed_during_read`); nothing is truncated silently. Does NOT
    close `fd` (the caller owns it).
    """
    try:
        size = os.fstat(fd).st_size
    except OSError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    if pointer:
        tracker.charge(pointer_bytes=size, source_bytes=size)
    else:
        tracker.charge(source_bytes=size)
    chunks: list[bytes] = []
    remaining = size + 1
    try:
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 4 * 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    data = b"".join(chunks)
    if len(data) != size:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_CHANGED_DURING_READ_REASON_V2)
    return data


def _scan_directory_v2(
    tracker: PhysicalWorkTrackerV2, dir_fd: int, classify: Callable[[str], str | None]
) -> list[tuple[str, str]]:
    """List `dir_fd` and classify each entry BY NAME ONLY.

    `os.scandir(fd)` duplicates the descriptor internally: that dup is charged
    as a descriptor open before the listing, and the iterator (the owner of
    that dup) is closed on every path, exceptions included. Every entry --
    relevant, ignored, junk or duplicate -- is charged when it is yielded,
    before it is classified. No `DirEntry.is_*()`/`stat()` is used: those can
    issue hidden `fstatat` lookups.
    """
    tracker.charge(descriptor_opens=1)
    relevant: list[tuple[str, str]] = []
    try:
        with os.scandir(dir_fd) as iterator:
            for entry in iterator:
                tracker.charge(entries_scanned=1)
                kind = classify(entry.name)
                if kind is not None:
                    relevant.append((entry.name, kind))
    except OSError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    return relevant


def _classify_store_entry_v2(name: str) -> str | None:
    if _FANOUT_RE_V2.fullmatch(name):
        return "fanout"
    if name == "pack":
        return "pack"
    if name == "info":
        return "info"
    return None


# -- the root-outward resolver session (freeze §8) --------------------------------------


@dataclass(frozen=True)
class _AdmittedPositionV2:
    """Where an admitted directory sits: a root of A plus lexical components,
    each of which S1-A itself descended no-follow."""

    root_index: int
    components: tuple[str, ...]


# S1-A's own source acquisition primitives. They install straight into the
# caller's pre-existing slot (the G1C helpers they replace returned a bare
# descriptor after further fallible steps, i.e. resource before owner). Same
# observable rules: no-follow; a regular file is opened O_NONBLOCK, its type
# is checked on the SAME fd, and O_NONBLOCK is cleared only once S_ISREG holds.
_SOURCE_DIR_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_SOURCE_FILE_OPEN_FLAGS_V2 = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _open_source_dir_into_v2(slot: _FdSlotV2, dir_fd: int, name: str) -> bool:
    """One no-follow directory open into `slot`. False if `name` is absent;
    a symlink or a non-directory is refused, never treated as absent."""
    try:
        slot.fd = os.open(name, _SOURCE_DIR_OPEN_FLAGS_V2, dir_fd=dir_fd)  # fd-install
    except FileNotFoundError:
        return False
    except ValueError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2) from exc
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    return True


def _open_source_file_into_v2(slot: _FdSlotV2, dir_fd: int, name: str, *, missing_is_legitimate: bool) -> bool:
    """One no-follow regular-file open into `slot`. False only if `name` is
    absent AND that is legitimate; a vanished listed entry is refused. On any
    refusal after the open, the descriptor stays in `slot` for its owner."""
    try:
        slot.fd = os.open(name, _SOURCE_FILE_OPEN_FLAGS_V2, dir_fd=dir_fd)  # fd-install
    except FileNotFoundError as exc:
        if missing_is_legitimate:
            return False
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    except ValueError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2) from exc
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    try:
        if not stat.S_ISREG(os.fstat(slot.fd).st_mode):
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2)
        flags = fcntl.fcntl(slot.fd, fcntl.F_GETFL)
        fcntl.fcntl(slot.fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
    except OSError as exc:
        raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
    return True


class _AdmittedDirV2(_FdSlotV2):
    """One admitted source directory: one slot, registered in the session
    BEFORE the open that fills it."""

    __slots__ = ("position",)

    def __init__(self, position: _AdmittedPositionV2) -> None:
        self.fd = None
        self.position = position


class _SourceSessionV2:
    """The resolver session: owns the root duplicates and every admitted dir.

    `AuthorizationMustPrecedeTraversal`: every source open is either a charged
    dup of a root of A, or a charged `openat` of ONE component (never "..")
    under a descriptor obtained that way. A pointer is resolved LEXICALLY
    first; it is opened only once it names a position under a root of A,
    re-descending from that root. Nothing ever climbs.

    Ownership: the session exists (empty) before any source descriptor; the
    root duplicates are installed as one unit by the statement that receives
    them; every admitted directory is registered before its open. `close()`
    is idempotent and resumable: an interrupted close leaves every
    not-yet-released descriptor with its owner.
    """

    def __init__(self, tracker: PhysicalWorkTrackerV2) -> None:
        self._tracker = tracker
        self._roots: tuple[AuthorizedStorageRootDuplicateV2, ...] = ()
        self._roots_released = 0
        self._live: list[_AdmittedDirV2] = []

    def acquire_roots(self, storage: AuthorizedGitStorageSetV2) -> None:
        _require_v2(not self._roots)
        self._tracker.charge(descriptor_opens=storage.root_count)
        try:
            self._roots = storage.duplicate_authorized_roots()  # fd-install
        except TrustedObjectAuthorityError as exc:
            if exc.reason_code == TRUSTED_OBJECT_AUTHORITY_STORAGE_CAPABILITY_CLOSED_REASON_V2:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_CAPABILITY_CLOSED_REASON_V2) from exc
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc

    @property
    def root_identities(self) -> tuple[tuple[int, int], ...]:
        return tuple(root.dev_ino for root in self._roots)

    # ownership ------------------------------------------------------------

    def _new_admitted(self, position: _AdmittedPositionV2) -> _AdmittedDirV2:
        """Owner first: built and registered while it is still empty."""
        admitted = _AdmittedDirV2(position)
        self._live.append(admitted)
        return admitted

    def release(self, admitted: _AdmittedDirV2) -> None:
        """Close (detach first), THEN forget; forgetting an empty owner is
        harmless if it fails."""
        admitted.close_once()
        try:
            self._live.remove(admitted)
        except ValueError:
            pass

    def _release_roots(self) -> bool:
        failed = False
        roots = self._roots
        while self._roots_released < len(roots):
            fd = roots[self._roots_released].fd
            try:
                self._roots_released += 1  # fd-release
                os.close(fd)  # fd-release
            except OSError:
                failed = True
        return failed

    def close(self) -> None:
        """Release every remaining descriptor exactly once; report a close
        failure only after all of them were released."""
        failed = False
        while self._live:
            admitted = self._live[-1]
            try:
                admitted.close_once()
            except _RefusalV2:
                failed = True
            self._live.pop()
        if self._release_roots():
            failed = True
        if failed:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_DESCRIPTOR_CLOSE_FAILED_REASON_V2)

    def close_quietly(self) -> None:
        try:
            self.close()
        except _RefusalV2:
            pass

    # descent --------------------------------------------------------------

    def admit(self, position: _AdmittedPositionV2, missing_reason: str) -> _AdmittedDirV2:
        """Re-descend from the root dup to `position`, one charged openat per
        component. The successor is owned (by `successor`) before its
        predecessor is released, then moved into the admitted owner
        (`NewOwnerAcquisition` before `PreviousOwnerRelease`)."""
        root = self._roots[position.root_index]
        admitted = self._new_admitted(position)
        if not position.components:
            self._tracker.charge(descriptor_opens=1)
            try:
                admitted.fd = fcntl.fcntl(root.fd, fcntl.F_DUPFD_CLOEXEC, 0)  # fd-install
            except OSError as exc:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
            return admitted
        successor = _FdSlotV2()
        try:
            current = root.fd
            for component in position.components:
                if component in ("", ".", "..") or "/" in component or "\x00" in component:
                    raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
                self._tracker.charge(descriptor_opens=1, path_components=1)
                if not _open_source_dir_into_v2(successor, current, component):
                    raise _RefusalV2(missing_reason)
                admitted.close_once()
                successor.move_to(admitted)
                current = admitted.fd
        finally:
            successor.close_quietly()
        return admitted

    def child(self, parent: _AdmittedDirV2, name: str, missing_reason: str) -> _AdmittedDirV2:
        """One charged descent from an already-admitted directory."""
        if name in ("", ".", "..") or "/" in name or "\x00" in name or parent.fd is None:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_MALFORMED_REASON_V2)
        admitted = self._new_admitted(
            _AdmittedPositionV2(parent.position.root_index, parent.position.components + (name,))
        )
        self._tracker.charge(descriptor_opens=1, path_components=1)
        if not _open_source_dir_into_v2(admitted, parent.fd, name):
            raise _RefusalV2(missing_reason)
        return admitted

    def resolve_locator(self, locator: SourceRepositoryLocatorV2) -> _AdmittedDirV2:
        refusal = PHYSICAL_SNAPSHOT_REPOSITORY_LOCATOR_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2
        if locator.root_index is not None:
            if locator.root_index >= len(self._roots):
                raise _RefusalV2(refusal)
            position = _AdmittedPositionV2(locator.root_index, ())
        else:
            position = self._match_absolute(locator.components or (), refusal)
        return self.admit(position, PHYSICAL_SNAPSHOT_REPOSITORY_LAYOUT_UNUSABLE_REASON_V2)

    def _match_absolute(self, parts: tuple[str, ...], refusal: str) -> _AdmittedPositionV2:
        """Longest captured root locator that is a lexical prefix of `parts`.
        Nothing is opened to decide this."""
        best: tuple[int, int] | None = None
        for root in self._roots:
            if root.locator is None:
                continue
            root_parts = root.locator.parts[1:]
            if parts[: len(root_parts)] == root_parts and (best is None or len(root_parts) > best[1]):
                best = (root.index, len(root_parts))
        if best is None:
            raise _RefusalV2(refusal)
        return _AdmittedPositionV2(best[0], parts[best[1]:])

    def resolve(
        self, base: _AdmittedPositionV2, pointer: _ParsedPointerV2, missing_reason: str
    ) -> _AdmittedDirV2:
        """Resolve a pointer lexically, then admit it root-outward (D2)."""
        outside = PHYSICAL_SNAPSHOT_POINTER_OUTSIDE_AUTHORIZED_STORAGE_REASON_V2
        if pointer.absolute:
            return self.admit(self._match_absolute(pointer.parts, outside), missing_reason)
        if pointer.ups <= len(base.components):
            kept = base.components[: len(base.components) - pointer.ups]
            return self.admit(_AdmittedPositionV2(base.root_index, kept + pointer.parts), missing_reason)
        locator = self._roots[base.root_index].locator
        if locator is None:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_ESCAPES_CAPABILITY_ROOT_REASON_V2)
        absolute_base = locator.parts[1:] + base.components
        if pointer.ups > len(absolute_base):
            raise _RefusalV2(PHYSICAL_SNAPSHOT_POINTER_ESCAPES_CAPABILITY_ROOT_REASON_V2)
        named = absolute_base[: len(absolute_base) - pointer.ups] + pointer.parts
        return self.admit(self._match_absolute(named, outside), missing_reason)

    # reads ----------------------------------------------------------------

    def read_optional_pointer(self, parent: _AdmittedDirV2, name: str) -> bytes | None:
        _require_v2(parent.fd is not None)
        slot = _FdSlotV2()
        try:
            self._tracker.charge(descriptor_opens=1)
            if not _open_source_file_into_v2(slot, parent.fd, name, missing_is_legitimate=True):
                return None
            data = _read_charged_v2(self._tracker, slot.fd, pointer=True)
            slot.close_once()
        finally:
            slot.close_quietly()
        return data

    def check_listed_candidate(self, parent: _AdmittedDirV2, name: str) -> None:
        """Classify an object-looking name that will NOT be copied (an
        incomplete pack pair, Codex 4133784323): one charged no-follow open,
        type checked on the same fd, closed; no byte is read. A skipped
        DUPLICATE occurrence never comes here (§10: not reacquired)."""
        _require_v2(parent.fd is not None)
        slot = _FdSlotV2()
        try:
            self._tracker.charge(descriptor_opens=1)
            _open_source_file_into_v2(slot, parent.fd, name, missing_is_legitimate=False)
            slot.close_once()
        finally:
            slot.close_quietly()

    def read_listed_object(self, parent: _AdmittedDirV2, name: str) -> bytes:
        """A file already seen in a listing: charged as a copied file and an
        open BEFORE it is opened; its bytes are charged from `fstat` before
        they are read. A vanished or swapped entry is refused."""
        _require_v2(parent.fd is not None)
        slot = _FdSlotV2()
        try:
            self._tracker.charge(files_copied=1, descriptor_opens=1)
            _open_source_file_into_v2(slot, parent.fd, name, missing_is_legitimate=False)
            data = _read_charged_v2(self._tracker, slot.fd, pointer=False)
            slot.close_once()
        finally:
            slot.close_quietly()
        return data

    def probe_dotgit(self, repo: _AdmittedDirV2) -> tuple[str, _AdmittedDirV2 | bytes | None]:
        """ONE charged open of `.git` (no O_DIRECTORY, O_NONBLOCK), classified
        on the same fd: a directory becomes the gitdir owner itself; a regular
        file is read as the `gitdir:` pointer; absence means a bare layout.
        The gitdir owner is registered before the open; if `.git` turns out
        not to be a directory it is released like any other owner."""
        _require_v2(repo.fd is not None)
        gitdir = self._new_admitted(
            _AdmittedPositionV2(repo.position.root_index, repo.position.components + (".git",))
        )
        self._tracker.charge(descriptor_opens=1, path_components=1)
        try:
            gitdir.fd = os.open(".git", _PROBE_OPEN_FLAGS_V2, dir_fd=repo.fd)  # fd-install
        except FileNotFoundError:
            self.release(gitdir)
            return "absent", None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_SYMLINK_REJECTED_REASON_V2) from exc
            if exc.errno in (errno.ENXIO, errno.EOPNOTSUPP):
                raise _RefusalV2(PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2) from exc
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
        try:
            mode = os.fstat(gitdir.fd).st_mode
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
        if stat.S_ISDIR(mode):
            return "dir", gitdir
        if not stat.S_ISREG(mode):
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SPECIAL_FILE_REJECTED_REASON_V2)
        data = _read_charged_v2(self._tracker, gitdir.fd, pointer=True)
        self.release(gitdir)
        return "file", data


def _resolve_primary_store_v2(session: _SourceSessionV2, locator: SourceRepositoryLocatorV2) -> _AdmittedDirV2:
    """Physical topology only: repository -> gitdir -> commondir -> objects/."""
    layout = PHYSICAL_SNAPSHOT_REPOSITORY_LAYOUT_UNUSABLE_REASON_V2
    tracker = session._tracker
    repo = session.resolve_locator(locator)
    kind, value = session.probe_dotgit(repo)
    if kind == "dir":
        _require_v2(type(value) is _AdmittedDirV2)
        gitdir = value  # type: ignore[assignment]
        session.release(repo)
    elif kind == "file":
        _require_v2(type(value) is bytes)
        pointer = _parse_gitfile_v2(tracker, value)  # type: ignore[arg-type]
        position = repo.position
        session.release(repo)
        gitdir = session.resolve(position, pointer, layout)
    else:
        gitdir = repo
    commondir = session.read_optional_pointer(gitdir, "commondir")
    if commondir is not None:
        pointer = _parse_commondir_v2(tracker, commondir)
        position = gitdir.position
        session.release(gitdir)
        common = session.resolve(position, pointer, layout)
    else:
        common = gitdir
    objects = session.child(common, "objects", layout)
    session.release(common)
    return objects


# -- staging writer (freeze §12, publication side) ---------------------------------------


def _write_all_v2(fd: int, data: bytes) -> None:
    """Exact write loop: a short write continues from where it stopped; a
    zero-progress write is refused (`short_write_no_progress`)."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SHORT_WRITE_NO_PROGRESS_REASON_V2)
        view = view[written:]


class _StagingWriterV2:
    """Everything written for one snapshot, all relative to `staging/<id>`.

    Files: O_EXCL create 0600 -> exact write loop -> fchmod 0444 -> fsync ->
    close once. Directories are created 0700 and kept open until they are
    finalized bottom-up (fchmod 0555 -> fsync). The snapshot root stays 0700
    until after the commit point: moving a directory to another parent needs
    write permission on the directory itself.

    Ownership: every bookkeeping structure exists before the first syscall
    (the constructor makes none); each directory slot is registered before
    its `mkdir`/`open`; the staging directory's cleanup obligation (`created`)
    is recorded by the statement right after its `mkdir`. `abort()` is
    idempotent and resumable.
    """

    def __init__(self, staging: _FdSlotV2, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        self._staging = staging
        self.stage = _FdSlotV2()
        self._dirs: dict[str, _FdSlotV2] = {}
        self._manifest: dict[str, tuple[str, str, int, int, str]] = {".": ("dir", ".", _DIR_MODE_V2, 0, "")}
        self.copied_files = 0
        self.copied_bytes = 0
        self.created = False
        self._aborted = False

    @property
    def stage_fd(self) -> int | None:
        return self.stage.fd

    def create_stage(self) -> None:
        _require_v2(self._staging.fd is not None and not self.created, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
        try:
            os.mkdir(self.snapshot_id, _STAGING_DIR_MODE_V2, dir_fd=self._staging.fd)
            self.created = True  # fd-install
        except FileExistsError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SNAPSHOT_ID_COLLISION_REASON_V2) from exc
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc

    def open_stage(self) -> None:
        """Open the directory `create_stage` made, into the pre-existing slot."""
        _require_v2(self.created and self.stage.fd is None, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
        try:
            self.stage.fd = os.open(self.snapshot_id, _DIR_OPEN_FLAGS_V2, dir_fd=self._staging.fd)  # fd-install
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc

    def ensure_dir(self, relpath: str) -> int:
        if relpath == ".":
            _require_v2(self.stage.fd is not None, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
            return self.stage.fd  # type: ignore[return-value]
        existing = self._dirs.get(relpath)
        if existing is not None:
            _require_v2(existing.fd is not None, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
            return existing.fd  # type: ignore[return-value]
        parent_rel, _, name = relpath.rpartition("/")
        parent_fd = self.ensure_dir(parent_rel or ".")
        slot = _FdSlotV2()
        self._dirs[relpath] = slot
        self._manifest[relpath] = ("dir", relpath, _DIR_MODE_V2, 0, "")
        try:
            os.mkdir(name, _STAGING_DIR_MODE_V2, dir_fd=parent_fd)
            slot.fd = os.open(name, _DIR_OPEN_FLAGS_V2, dir_fd=parent_fd)  # fd-install
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc
        return slot.fd

    def write_file(self, dir_rel: str, name: str, data: bytes, *, copied: bool) -> None:
        dir_fd = self.ensure_dir(dir_rel)
        relpath = name if dir_rel == "." else dir_rel + "/" + name
        slot = _FdSlotV2()
        try:
            try:
                slot.fd = os.open(name, _CREATE_FILE_FLAGS_V2, _STAGING_FILE_MODE_V2, dir_fd=dir_fd)  # fd-install
                _write_all_v2(slot.fd, data)
                digest = hashlib.sha256(data).hexdigest()
                os.fchmod(slot.fd, _FILE_MODE_V2)
                os.fsync(slot.fd)
            except OSError as exc:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc
            slot.close_once()
        finally:
            slot.close_quietly()
        if relpath != PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2:
            self._manifest[relpath] = ("file", relpath, _FILE_MODE_V2, len(data), digest)
        if copied:
            self.copied_files += 1
            self.copied_bytes += len(data)

    def write_skeleton(self, object_format: DeclaredGitObjectFormatV2) -> None:
        """Producer-authored constants (`AuthoredConstantSkeleton != CopiedSourceMetadata`)."""
        if object_format is DeclaredGitObjectFormatV2.SHA256:
            config = (
                "[core]\n\trepositoryformatversion = 1\n\tbare = true\n"
                "[extensions]\n\tobjectformat = sha256\n"
            )
        else:
            config = "[core]\n\trepositoryformatversion = 0\n\tbare = true\n"
        self.ensure_dir("objects")
        self.ensure_dir("objects/pack")
        self.ensure_dir("objects/info")
        self.ensure_dir("refs")
        self.ensure_dir("refs/heads")
        self.write_file(".", "config", config.encode("ascii"), copied=False)
        self.write_file(".", "HEAD", b"ref: refs/heads/none\n", copied=False)

    def fileset_digest(self) -> str:
        entries = sorted(self._manifest.values(), key=lambda entry: entry[1])
        canonical = json.dumps([list(entry) for entry in entries], separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def finalize_subdirectories(self) -> None:
        """Bottom-up: fchmod 0555 -> fsync -> close once, deepest first; the
        root is left 0700. A finalized slot stays registered, empty."""
        for relpath in sorted(self._dirs, key=lambda rel: (-rel.count("/"), rel)):
            slot = self._dirs[relpath]
            _require_v2(slot.fd is not None, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
            try:
                os.fchmod(slot.fd, _DIR_MODE_V2)  # type: ignore[arg-type]
                os.fsync(slot.fd)  # type: ignore[arg-type]
            except OSError as exc:
                raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc
            slot.close_once()

    def fsync_stage_root(self) -> None:
        _require_v2(self.stage.fd is not None, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
        try:
            os.fsync(self.stage.fd)  # type: ignore[arg-type]
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2) from exc

    def release_descriptors(self) -> None:
        """Close every descriptor still owned, removing nothing (used once the
        commit point was attempted: the tree may already be committed)."""
        for slot in list(self._dirs.values()):
            slot.close_quietly()
        self.stage.close_quietly()

    def abort(self) -> bool:
        """Pre-commit cleanup, descriptor-relative. Returns True if staging
        residue may remain (reported, never hidden). Idempotent; an
        interrupted abort leaves what it did not release with its owner."""
        if self._aborted:
            return False
        for slot in list(self._dirs.values()):
            slot.close_quietly()
        residue = False
        if self.stage.fd is None:
            if self.created:
                residue = not _remove_tree_by_name_v2(self._staging, self.snapshot_id)
        else:
            try:
                os.fchmod(self.stage.fd, _STAGING_DIR_MODE_V2)
                residue = not _remove_children_v2(self.stage.fd)
            except OSError:
                residue = True
            self.stage.close_quietly()
            if self._staging.fd is None:
                residue = True
            else:
                try:
                    os.rmdir(self.snapshot_id, dir_fd=self._staging.fd)
                except OSError:
                    residue = True
        self._aborted = True
        return residue


def _remove_children_v2(dir_fd: int) -> bool:
    """Remove everything below an owned staging directory: fchmod 0700
    top-down (finalized dirs are 0555), unlinkat bottom-up. Publication side
    only. Returns False if anything could not be removed."""
    ok = True
    try:
        with os.scandir(dir_fd) as iterator:
            names = [entry.name for entry in iterator]
    except OSError:
        return False
    for name in names:
        try:
            info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError:
            ok = False
            continue
        if stat.S_ISDIR(info.st_mode):
            child = _FdSlotV2()
            try:
                try:
                    child.fd = os.open(name, _DIR_OPEN_FLAGS_V2, dir_fd=dir_fd)  # fd-install
                    os.fchmod(child.fd, _STAGING_DIR_MODE_V2)
                    ok = _remove_children_v2(child.fd) and ok
                except OSError:
                    ok = False
                child.close_quietly()
            finally:
                child.close_quietly()
            try:
                os.rmdir(name, dir_fd=dir_fd)
            except OSError:
                ok = False
        else:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except OSError:
                ok = False
    return ok


def _remove_tree_by_name_v2(parent: _FdSlotV2, name: str) -> bool:
    if parent.fd is None:
        return False
    slot = _FdSlotV2()
    try:
        try:
            slot.fd = os.open(name, _DIR_OPEN_FLAGS_V2, dir_fd=parent.fd)  # fd-install
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            os.fchmod(slot.fd, _STAGING_DIR_MODE_V2)
            ok = _remove_children_v2(slot.fd)
        except OSError:
            ok = False
        slot.close_quietly()
    finally:
        slot.close_quietly()
    try:
        os.rmdir(name, dir_fd=parent.fd)
    except OSError:
        ok = False
    return ok


# -- physical copy and alternate flattening (freeze §11b) --------------------------------


class _PhysicalCopierV2:
    """Copies loose objects and complete pack pairs, primary store first, then
    alternates in order; the first physical occurrence of a name wins (Git's
    own lookup order). The alternates pointer is never published."""

    def __init__(
        self,
        session: _SourceSessionV2,
        writer: _StagingWriterV2,
        object_format: DeclaredGitObjectFormatV2,
    ) -> None:
        self._session = session
        self._tracker = session._tracker
        self._writer = writer
        hex_length = object_format.hex_length
        self._loose_name = re.compile(r"[0-9a-f]{%d}" % (hex_length - 2))
        self._pack_name = re.compile(r"(pack-[0-9a-f]{%d})\.(pack|idx)" % hex_length)
        self._seen_stores: set[tuple[int, int]] = set()
        self._loose: set[str] = set()
        self._packs: set[str] = set()
        self.alternate_sources = 0

    def _classify_loose(self, name: str) -> str | None:
        return "loose" if self._loose_name.fullmatch(name) else None

    def _classify_pack(self, name: str) -> str | None:
        return "pack" if self._pack_name.fullmatch(name) else None

    def copy_store(self, store: _AdmittedDirV2, hop: int) -> None:
        session = self._session
        _require_v2(store.fd is not None)
        try:
            identity = os.fstat(store.fd)
        except OSError as exc:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_SOURCE_ACQUISITION_FAILED_REASON_V2) from exc
        key = (identity.st_dev, identity.st_ino)
        if key in self._seen_stores:
            session.release(store)
            return
        self._seen_stores.add(key)
        if hop > 0:
            self.alternate_sources += 1
        entries = _scan_directory_v2(self._tracker, store.fd, _classify_store_entry_v2)
        kinds = {kind for _, kind in entries}
        if not kinds:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_NOT_AN_OBJECT_STORE_REASON_V2)
        changed = PHYSICAL_SNAPSHOT_SOURCE_CHANGED_DURING_READ_REASON_V2
        for fanout in sorted(name for name, kind in entries if kind == "fanout"):
            fanout_dir = session.child(store, fanout, changed)
            try:
                for name, _ in sorted(_scan_directory_v2(self._tracker, fanout_dir.fd, self._classify_loose)):
                    object_key = fanout + name
                    if object_key in self._loose:
                        # SkippedDuplicateOccurrence (§10, adjudicated): charged as an
                        # entry, not reacquired, not copied, not followed -- hence no
                        # filesystem-type claim about it, and no open to make one.
                        continue
                    data = session.read_listed_object(fanout_dir, name)
                    self._writer.write_file("objects/" + fanout, name, data, copied=True)
                    self._loose.add(object_key)
            finally:
                session.release(fanout_dir)
        if "pack" in kinds:
            pack_dir = session.child(store, "pack", changed)
            try:
                listed = _scan_directory_v2(self._tracker, pack_dir.fd, self._classify_pack)
                present: dict[str, set[str]] = {}
                for name, _ in listed:
                    match = self._pack_name.fullmatch(name)
                    _require_v2(match is not None)
                    present.setdefault(match.group(1), set()).add(match.group(2))
                for base in sorted(present):
                    if base in self._packs:
                        continue  # SkippedDuplicateOccurrence (§10): charged, not reacquired, no type claim
                    if present[base] != {"pack", "idx"}:
                        # Incomplete pair: not copied, but an object-looking name is
                        # still classified by a charged no-follow open, so a symlink or
                        # special file is refused rather than skipped silently (§11b;
                        # Codex 4133784323).
                        for suffix in sorted(present[base]):
                            session.check_listed_candidate(pack_dir, base + "." + suffix)
                        continue
                    for suffix in ("idx", "pack"):
                        data = session.read_listed_object(pack_dir, base + "." + suffix)
                        self._writer.write_file("objects/pack", base + "." + suffix, data, copied=True)
                    self._packs.add(base)
            finally:
                session.release(pack_dir)
        pointers: list[_ParsedPointerV2] = []
        if "info" in kinds:
            info_dir = session.child(store, "info", changed)
            try:
                raw = session.read_optional_pointer(info_dir, "alternates")
            finally:
                session.release(info_dir)
            if raw is not None:
                pointers = _parse_alternates_v2(self._tracker, raw)
        position = store.position
        session.release(store)
        if pointers and hop > _GIT_ALTERNATE_NESTING_LIMIT_V2:
            raise _RefusalV2(PHYSICAL_SNAPSHOT_ALTERNATE_NESTING_EXCEEDS_GIT_LIMIT_REASON_V2)
        for pointer in pointers:
            self._tracker.charge_alternate_depth(hop + 1)
            target = session.resolve(position, pointer, PHYSICAL_SNAPSHOT_ALTERNATE_TARGET_MISSING_REASON_V2)
            self.copy_store(target, hop + 1)


# -- receipt, binding and the publication type-state (freeze §13) ------------------------


@dataclass(frozen=True)
class PublishedSnapshotReceiptV2(_SealedV2):
    """Traceability only: `PublishedSnapshotReceipt != ObjectAuthenticityProof`.

    Describes the INTENDED final state (root 0555 included); it is asserted
    true only inside `CompletePublicationV2`. Carries no commit, root tree,
    ref, source HEAD/config, remote, hook, promisor, alternate pointer text,
    locator or path.
    """

    schema_version: str
    snapshot_id: str
    declared_object_format: str
    physical_work: tuple[tuple[str, int], ...]
    alternate_sources: int
    max_alternate_depth_seen: int
    files_copied: int
    copied_bytes: int
    source_root_identities: tuple[tuple[int, int], ...]
    snapshot_fileset_digest: str

    def canonical_bytes(self) -> bytes:
        body = {
            "schema_version": self.schema_version,
            "snapshot_id": self.snapshot_id,
            "declared_object_format": self.declared_object_format,
            "physical_work": dict(self.physical_work),
            "alternate_sources": self.alternate_sources,
            "max_alternate_depth_seen": self.max_alternate_depth_seen,
            "copied_totals": {"files_copied": self.files_copied, "copied_bytes": self.copied_bytes},
            "source_root_identities": [list(identity) for identity in self.source_root_identities],
            "snapshot_fileset_digest": self.snapshot_fileset_digest,
        }
        return (json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


@dataclass(frozen=True)
class PublishedSnapshotBindingV2(_SealedV2):
    """Observed kernel identity of the committed snapshot; an observation,
    not a proof of who produced it."""

    snapshot_id: str
    mount_id: int
    st_dev: int
    st_ino: int
    st_uid: int
    st_gid: int
    committed_parent_identity: KernelObjectIdentityV2


class PublishedSnapshotV2(_SealedV2):
    """The qualified capability. Exists ONLY inside `CompletePublicationV2`
    (`PublishedSnapshotV2 <=> PUBLISHED_SYNC_COMPLETE`). Sealed; the
    constructor refuses any caller without this module's sentinel and
    registers the instance by identity; its descriptor slot is created empty
    and filled by a slot move only once the whole outcome exists."""

    def __init__(
        self,
        *,
        _sentinel: object,
        binding: PublishedSnapshotBindingV2,
        receipt: PublishedSnapshotReceiptV2,
    ) -> None:
        if _sentinel is not _SNAPSHOT_SENTINEL_V2:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
        if type(binding) is not PublishedSnapshotBindingV2 or type(receipt) is not PublishedSnapshotReceiptV2:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
        self._descriptor = _SharedFdSlotV2()
        self._binding = binding
        self._receipt = receipt
        _GENUINE_SNAPSHOTS_V2.add(self)

    @property
    def binding(self) -> PublishedSnapshotBindingV2:
        return self._binding

    @property
    def receipt(self) -> PublishedSnapshotReceiptV2:
        return self._receipt

    @property
    def committed_dir_fd(self) -> int:
        return self._descriptor.get()

    @property
    def closed(self) -> bool:
        return self._descriptor.fd is None

    def close(self) -> None:
        self._descriptor.close_quietly()

    def __enter__(self) -> PublishedSnapshotV2:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class CommittedSnapshotResidualV2(_SealedV2):
    """A snapshot that IS in committed/ but whose publication was not
    confirmed. Holds its own descriptor for quarantine/diagnosis only; it is
    not a `PublishedSnapshotV2` and is not accepted by S1-B/S1-C. Sealed and
    sentinel-gated like the capability it must never be mistaken for."""

    def __init__(
        self, *, _sentinel: object, snapshot_id: str, receipt: PublishedSnapshotReceiptV2, reason_code: str
    ) -> None:
        if _sentinel is not _SNAPSHOT_SENTINEL_V2:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
        if type(receipt) is not PublishedSnapshotReceiptV2 or type(reason_code) is not str:
            raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
        self._descriptor = _SharedFdSlotV2()
        self._snapshot_id = snapshot_id
        self._receipt = receipt
        self._reason_code = reason_code

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def receipt(self) -> PublishedSnapshotReceiptV2:
        return self._receipt

    @property
    def reason_code(self) -> str:
        return self._reason_code

    @property
    def fd(self) -> int:
        return self._descriptor.get()

    @property
    def closed(self) -> bool:
        return self._descriptor.fd is None

    def close(self) -> None:
        self._descriptor.close_quietly()

    def __enter__(self) -> CommittedSnapshotResidualV2:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


@dataclass(frozen=True)
class PublicationResidualV2(_SealedV2):
    """What is known when the commit state could not be established."""

    snapshot_id: str
    reason_code: str
    observation: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CompletePublicationV2(_SealedV2):
    snapshot: PublishedSnapshotV2

    def __post_init__(self) -> None:
        if type(self.snapshot) is not PublishedSnapshotV2 or self.snapshot not in _GENUINE_SNAPSHOTS_V2:
            raise TypeError("CompletePublicationV2 requires a PublishedSnapshotV2 minted by this module")


@dataclass(frozen=True)
class UnconfirmedPublicationV2(_SealedV2):
    residual: CommittedSnapshotResidualV2

    def __post_init__(self) -> None:
        if type(self.residual) is not CommittedSnapshotResidualV2:
            raise TypeError("UnconfirmedPublicationV2 carries a CommittedSnapshotResidualV2, never a PublishedSnapshotV2")


@dataclass(frozen=True)
class IndeterminatePublicationV2(_SealedV2):
    residual: PublicationResidualV2

    def __post_init__(self) -> None:
        if type(self.residual) is not PublicationResidualV2:
            raise TypeError("IndeterminatePublicationV2 carries a PublicationResidualV2")


@dataclass(frozen=True)
class NotPublishedV2(_SealedV2):
    reason_code: str
    exceeded_axis: str | None
    staging_residue: bool

    def __post_init__(self) -> None:
        if type(self.reason_code) is not str or type(self.staging_residue) is not bool:
            raise TypeError("NotPublishedV2 requires a reason code and a staging_residue flag")


PublicationOutcomeV2 = Union[CompletePublicationV2, UnconfirmedPublicationV2, IndeterminatePublicationV2, NotPublishedV2]


# -- the publication run: one owner tree for one call (C11) ---------------------------------


class _PublicationRunV2:
    """Every owner one publication will ever need, built BEFORE its first
    syscall: the two publication duplicates, the source session, the staging
    writer. `settle()` runs on every exit path and releases whatever is still
    owned -- nothing is released by building a new owner."""

    __slots__ = ("tracker", "snapshot_id", "staging", "committed", "session", "writer", "receipt", "committed_identity", "attempted")

    def __init__(self, tracker: PhysicalWorkTrackerV2, snapshot_id: str, committed_identity: KernelObjectIdentityV2) -> None:
        self.tracker = tracker
        self.snapshot_id = snapshot_id
        self.staging = _FdSlotV2()
        self.committed = _FdSlotV2()
        self.session = _SourceSessionV2(tracker)
        self.writer = _StagingWriterV2(self.staging, snapshot_id)
        self.receipt: PublishedSnapshotReceiptV2 | None = None
        self.committed_identity = committed_identity
        self.attempted = False

    def _settle_writer(self) -> None:
        if self.attempted:
            self.writer.release_descriptors()
        else:
            self.writer.abort()

    def settle(self) -> None:
        """Pre-commit leftovers are aborted (removed); once the commit point
        was attempted nothing is removed, only descriptors are released.

        Every step is idempotent and resumable, and every step runs even if
        an earlier one was interrupted (nested `finally`); an interrupted step
        is retried once, so a single failure inside cleanup cannot strand what
        the interrupted attempt had not yet released."""
        try:
            _retry_once_v2(self.session.close_quietly)
        finally:
            try:
                _retry_once_v2(self._settle_writer)
            finally:
                try:
                    _retry_once_v2(self.staging.close_quietly)
                finally:
                    _retry_once_v2(self.committed.close_quietly)


def _retry_once_v2(step: Callable[[], object]) -> None:
    """Run an idempotent, resumable cleanup step; if it is interrupted, run it
    again (releasing what the first attempt did not), then re-raise."""
    try:
        step()
    except BaseException:
        step()
        raise


# -- commit point and post-commit (freeze §12 steps 8-11, §13) -----------------------------


def _observe_commit_v2(run: _PublicationRunV2) -> str:
    """Decide from descriptors, never from errno: 'committed', 'staging' or
    'indeterminate'. `ours` is the inode behind the staging descriptor, which
    a rename does not change."""
    stage_fd, staging_fd, committed_fd = run.writer.stage.fd, run.staging.fd, run.committed.fd
    if stage_fd is None or staging_fd is None or committed_fd is None:
        return "indeterminate"
    try:
        ours = os.fstat(stage_fd)
    except OSError:
        return "indeterminate"

    def holds_ours(dir_fd: int) -> bool | None:
        try:
            info = os.stat(run.snapshot_id, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return None
        return (info.st_dev, info.st_ino) == (ours.st_dev, ours.st_ino)

    in_committed = holds_ours(committed_fd)
    in_staging = holds_ours(staging_fd)
    if in_committed is True and in_staging is False:
        return "committed"
    if in_staging is True and in_committed is False:
        return "staging"
    return "indeterminate"


def _rename_reason_v2(err: int) -> str:
    if err == errno.EEXIST or err == errno.ENOTEMPTY:
        return PHYSICAL_SNAPSHOT_RENAME_COLLISION_REASON_V2
    if err == errno.EXDEV:
        return PHYSICAL_SNAPSHOT_RENAME_CROSS_MOUNT_REASON_V2
    if err in (errno.EINVAL, errno.ENOSYS):
        return PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2
    return PHYSICAL_SNAPSHOT_RENAME_FAILED_REASON_V2


def _unconfirmed_v2(run: _PublicationRunV2, reason_code: str) -> UnconfirmedPublicationV2:
    """The committed tree, not confirmed: the residual and its outcome are
    built first; the staging descriptor moves into them last."""
    _require_v2(type(run.receipt) is PublishedSnapshotReceiptV2, PHYSICAL_SNAPSHOT_PUBLICATION_IO_FAILED_REASON_V2)
    residual = CommittedSnapshotResidualV2(
        _sentinel=_SNAPSHOT_SENTINEL_V2, snapshot_id=run.snapshot_id, receipt=run.receipt, reason_code=reason_code
    )
    outcome = UnconfirmedPublicationV2(residual=residual)
    run.writer.stage.move_to(residual._descriptor)
    return outcome


def _classify_after_attempt_v2(run: _PublicationRunV2, reason_code: str) -> PublicationOutcomeV2:
    """The only way to report a state after a commit attempt: observe first."""
    state = _observe_commit_v2(run)
    if state == "staging":
        residue = run.writer.abort()
        return NotPublishedV2(reason_code=reason_code, exceeded_axis=None, staging_residue=residue)
    if state == "committed":
        return _unconfirmed_v2(
            run,
            reason_code
            if reason_code in (PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2, PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2)
            else PHYSICAL_SNAPSHOT_RENAME_ERROR_BUT_COMMITTED_REASON_V2,
        )
    outcome = IndeterminatePublicationV2(
        residual=PublicationResidualV2(
            snapshot_id=run.snapshot_id,
            reason_code=PHYSICAL_SNAPSHOT_COMMIT_STATE_UNOBSERVABLE_REASON_V2,
            observation=(("attempt_reason", reason_code),),
        )
    )
    run.writer.stage.close_quietly()
    return outcome


def _on_rename_failure_v2(run: _PublicationRunV2, err: int) -> PublicationOutcomeV2:
    """`rename returned error` does NOT imply `nothing happened`."""
    return _classify_after_attempt_v2(run, _rename_reason_v2(err))


def _post_commit_v2(run: _PublicationRunV2) -> PublicationOutcomeV2:
    stage_fd = run.writer.stage.fd
    if stage_fd is None or run.staging.fd is None or run.committed.fd is None:
        raise RuntimeError("descriptor missing at the commit point")
    try:
        os.fchmod(stage_fd, _DIR_MODE_V2)
        os.fsync(stage_fd)
        os.fsync(run.committed.fd)
        os.fsync(run.staging.fd)
        info = os.fstat(stage_fd)
        identity = _statx_identity_v2(stage_fd)
    except (OSError, _MountIdentityUnavailableV2):
        return _unconfirmed_v2(run, PHYSICAL_SNAPSHOT_POST_COMMIT_SYNC_FAILED_REASON_V2)
    binding = PublishedSnapshotBindingV2(
        snapshot_id=run.snapshot_id,
        mount_id=identity.mount_id,
        st_dev=info.st_dev,
        st_ino=info.st_ino,
        st_uid=info.st_uid,
        st_gid=info.st_gid,
        committed_parent_identity=run.committed_identity,
    )
    snapshot = PublishedSnapshotV2(_sentinel=_SNAPSHOT_SENTINEL_V2, binding=binding, receipt=run.receipt)  # type: ignore[arg-type]
    outcome = CompletePublicationV2(snapshot=snapshot)
    run.writer.stage.move_to(snapshot._descriptor)
    return outcome


def _commit_v2(run: _PublicationRunV2) -> PublicationOutcomeV2:
    try:
        try:
            run.attempted = True
            err = _renameat2_noreplace_v2(run.staging.fd, run.snapshot_id, run.committed.fd, run.snapshot_id)  # type: ignore[arg-type]
        except _SyscallUnavailableV2:
            run.attempted = False
            residue = run.writer.abort()
            return NotPublishedV2(
                reason_code=PHYSICAL_SNAPSHOT_NOREPLACE_UNSUPPORTED_REASON_V2, exceeded_axis=None, staging_residue=residue
            )
        if err != 0:
            return _on_rename_failure_v2(run, err)
        return _post_commit_v2(run)
    except Exception:
        return _classify_after_attempt_v2(run, PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2)
    except BaseException as interruption:
        if run.attempted:
            outcome = _classify_after_attempt_v2(run, PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2)
        else:
            outcome = NotPublishedV2(
                reason_code=PHYSICAL_SNAPSHOT_PUBLICATION_INTERRUPTED_REASON_V2,
                exceeded_axis=None,
                staging_residue=run.writer.abort(),
            )
        # Ownership of any descriptor moved to the attached variant; nothing
        # it holds has been closed behind its back.
        interruption.physical_snapshot_outcome = outcome  # type: ignore[attr-defined]
        raise


# -- entry point ---------------------------------------------------------------------------


def _publish_run_v2(
    run: _PublicationRunV2,
    source_authority: AuthorizedGitStorageSetV2,
    source_locator: SourceRepositoryLocatorV2,
    object_format: DeclaredGitObjectFormatV2,
    publication_root: SnapshotPublicationRootV2,
) -> PublicationOutcomeV2:
    try:
        publication_root._duplicate_publication_fds_into(run.staging, run.committed)
    except _RefusalV2 as refusal:
        return NotPublishedV2(reason_code=refusal.reason_code, exceeded_axis=None, staging_residue=False)
    session, writer, tracker = run.session, run.writer, run.tracker
    try:
        session.acquire_roots(source_authority)
        primary = _resolve_primary_store_v2(session, source_locator)
        writer.create_stage()
        writer.open_stage()
        copier = _PhysicalCopierV2(session, writer, object_format)
        copier.copy_store(primary, hop=0)
        writer.write_skeleton(object_format)
        consumed = tracker.consumed()
        run.receipt = PublishedSnapshotReceiptV2(
            schema_version=PHYSICAL_SNAPSHOT_RECEIPT_SCHEMA_V2,
            snapshot_id=run.snapshot_id,
            declared_object_format=object_format.value,
            physical_work=tuple((axis, consumed[axis]) for axis in _BUDGET_AXES_V2),
            alternate_sources=copier.alternate_sources,
            max_alternate_depth_seen=consumed["alternate_depth"],
            files_copied=writer.copied_files,
            copied_bytes=writer.copied_bytes,
            source_root_identities=session.root_identities,
            snapshot_fileset_digest=writer.fileset_digest(),
        )
        writer.write_file(".", PHYSICAL_SNAPSHOT_RECEIPT_FILENAME_V2, run.receipt.canonical_bytes(), copied=False)
        session.close()  # step 5b: no source close can fail after the commit point
        writer.finalize_subdirectories()
        writer.fsync_stage_root()
    except _RefusalV2 as refusal:
        session.close_quietly()
        residue = writer.abort()
        return NotPublishedV2(reason_code=refusal.reason_code, exceeded_axis=refusal.exceeded_axis, staging_residue=residue)
    return _commit_v2(run)


def publish_physical_snapshot_v2(
    *,
    source_authority: AuthorizedGitStorageSetV2,
    source_locator: SourceRepositoryLocatorV2,
    object_format: DeclaredGitObjectFormatV2,
    physical_budget: PhysicalWorkBudgetV2,
    publication_root: SnapshotPublicationRootV2,
    _snapshot_id: str | None = None,
) -> PublicationOutcomeV2:
    """Publish a physical snapshot of the object store `source_locator` names
    inside `source_authority`, under `publication_root`, within
    `physical_budget`. See the module docstring and the freeze.

    Raises `PhysicalSnapshotErrorV2` only for caller-contract violations
    (wrong or forged types, invalid budget). Every acquisition/publication
    refusal is a returned `PublicationOutcomeV2` variant.

    Admission is by EXACT type (`ExactType(A) != Provenance(A)`: a subclass
    of `AuthorizedGitStorageSetV2` is refused here without changing C2_A;
    who produced A stays C2_B/#331), and W must also be one
    `from_directory_fd` minted.
    """
    if type(source_authority) is not AuthorizedGitStorageSetV2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    if type(source_locator) is not SourceRepositoryLocatorV2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    if type(object_format) is not DeclaredGitObjectFormatV2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    root = _admit_publication_root_v2(publication_root)
    tracker = PhysicalWorkTrackerV2(physical_budget)
    snapshot_id = secrets.token_hex(16) if _snapshot_id is None else _snapshot_id
    if type(snapshot_id) is not str or not _SNAPSHOT_ID_RE_V2.fullmatch(snapshot_id):
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_INVALID_INPUT_REASON_V2)
    committed_identity = root.committed_identity
    if type(committed_identity) is not KernelObjectIdentityV2:
        raise PhysicalSnapshotErrorV2(PHYSICAL_SNAPSHOT_FORGED_CAPABILITY_REASON_V2)
    run = _PublicationRunV2(tracker, snapshot_id, committed_identity)
    outcome: PublicationOutcomeV2 | None = None
    try:
        outcome = _publish_run_v2(run, source_authority, source_locator, object_format, root)
    finally:
        try:
            run.settle()
        except BaseException as interruption:
            # Settling is resumable: finish it, then report. An outcome that
            # already exists is never lost behind a cleanup failure (a
            # committed snapshot must stay visible to the caller, C10).
            run.settle()
            if outcome is not None:
                interruption.physical_snapshot_outcome = outcome  # type: ignore[attr-defined]
            raise
    return outcome
