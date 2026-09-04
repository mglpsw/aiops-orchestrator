"""`#200-G1C2` -- descriptor-anchored Git object-store acquisition authority.

## Predecessor and why it was refuted

`#200-G1C` (issue #303, implementation PR #308, closed unmerged, preserved
as forensic evidence) built the trust-root-inversion architecture this
module still implements (Git as discovery, never trust root; copy into a
private, remote-less, content-addressed authority; retain a capability;
read everything through it afterward) and, across two correction rounds,
closed real, independently-reproduced gaps in *what* gets verified: loose
and packed object identity forgery, a withdrawn-S4-style silent
false-negative ancestry determination, and symlinks -- both statically
inside the objects/refs tree and statically at its namespace roots.

What did not converge, across three review rounds on PR #308, was *how*
acquisition decided a path was safe to use: every check added was a
**pathname observation** (`Path.is_symlink()`), and every later use
**re-resolved the same pathname a second time** (`Path.is_dir()`,
`os.scandir(path)`, `os.path.realpath(path)`), trusting whatever it found
then. Findings 1 and 2 on PR #308 were spatial coverage questions (*where*
could an unchecked symlink sit -- inside the tree, then at a namespace
root) and were closeable by extending the same truth-maker to more
locations. The third, terminal finding was temporal: a path that is a
genuine directory when a `is_symlink()` pre-check runs can be replaced with
a symlink *after* that check and *before* the later, separate call that
actually uses the same pathname. Deterministic reproduction (a monkeypatch
barrier performing the swap on the exact call following the pre-check, not
a timing race) through the real production path achieved full identity
substitution: a victim repository's own real commit became unresolvable
while an attacker's entirely unrelated commit became resolvable in its
place. No finite number of additional `is_symlink()` call sites closes
this -- the gap exists at every one of them individually, because
"observe a pathname's property, then later re-resolve and act on the same
pathname" cannot establish that property against a source this module's
own threat model already declares adversarial and concurrently mutable.
Disposed as `STOP_G1C_PATHNAME_ACQUISITION_NOT_CONVERGING` on PR #308; this
module is the successor issue's (#310) implementation.

## This module's design -- descriptor-anchored acquisition

```
open object -> authenticate the OPENED CAPABILITY -> use that SAME
capability, never re-resolve by pathname again
```

Concretely: open a root directory with `O_NOFOLLOW`, retain the resulting
file descriptor. Every subsequent step operates on that fd, or on a
descriptor opened *relative to* it (`os.open(name, ..., dir_fd=parent_fd)`,
Python's `openat()` equivalent) -- never a fresh absolute/relative pathname
lookup from the filesystem root. `O_NOFOLLOW` at every step means a symlink
anywhere in the chain fails to open rather than being followed. `fstat` and
`read` operate on that same already-open descriptor, never a fresh
`stat()`/`open()` by path. Multi-segment path strings this module must
still resolve (a `gitdir:` pointer's target, a `commondir` file's content,
an `objects/info/alternates` entry) are walked ONE COMPONENT AT A TIME,
each opened no-follow relative to the descriptor reached so far -- never
handed whole to a single `open()` call, which would let the OS resolve
intermediate components through its own (symlink-following) path walk.

This single design structurally dissolves every mechanism in PR #308's
three-round history, rather than needing one guard per item:

- **static symlink inside the objects/refs tree** (round 1) -- there is no
  code path left that opens a child by reconstructing and re-walking a
  path string; every child is opened via `dir_fd=` relative to an
  already-verified parent descriptor, `O_NOFOLLOW` at that exact step.
- **static symlink at a namespace root** (round 2) -- the root itself is
  opened the same way, by the same primitive, as every other level; there
  is no longer a distinct "root" call shape using a different (weaker)
  check than the "child" call shape.
- **temporal check-then-use swap** (round 3, this module's namesake) --
  there is no check separate from the use to race. Opening a descriptor
  with `O_NOFOLLOW` IS the check, atomically, in the same syscall that
  produces the handle everything afterward uses. A pathname swap after
  that syscall returns has nothing left to redirect: the retained
  descriptor refers to the kernel object that was open at open() time,
  never re-resolved by name again. Verified empirically, twice, as
  permanent, checked-in, re-runnable regression tests (correction round 1,
  Lane A/Lane B review reconciled this claim against an earlier revision
  that pointed only at session-only reproduction notes with no durable
  artifact) -- see `tests/agent_review/test_trusted_object_authority_v2.py`:
  `test_swapping_git_common_dir_at_the_earliest_possible_moment_still_refuses`
  (renaming the original `objects/` directory away and an attacker
  directory into its place, via a hook firing on the earliest possible
  `os.open` call this module makes against the live repository at all) and
  `test_racing_the_dotgit_classification_stat_still_refuses` (racing the
  one remaining classification-then-open pattern this module has, for
  `.git`). Both independently confirm a descriptor opened before a
  pathname swap is immune to that swap.
- **listing-to-open replacement** -- a directory entry observed via
  `os.scandir(dir_fd)` is opened by name relative to that SAME `dir_fd`
  with `O_NOFOLLOW`; even if the entry is swapped between listing and
  open, the open() call itself is the authoritative, atomic check.
- **ancestral-path retargeting** (an intermediate path component, not just
  the leaf, replaced between resolution steps) -- closed by per-component
  `dir_fd`-relative descent for every multi-segment string this module
  resolves (`gitdir:`/`commondir` pointers, alternates entries): each step
  is anchored to an already-open, already-verified parent descriptor,
  never a re-walked path string handed to a single `open()`.

## What still needs its own handling, and why

The identity-verification layer this module inherited from `#200-G1C`
(loose-object content-hash re-derivation, packed-object verification via
git's own `verify-pack`, positive-graph-proof ancestry determination,
symlink refusal as a *policy* rather than a detection mechanism, the
capability-binding discipline, worktree-aware `git_dir`/`common_dir`
separation) is not implicated by the TOCTOU finding and carries forward
essentially unchanged -- only *how bytes are acquired* changed, not what is
done with them once acquired. See each function's own docstring for the
specific property it establishes; this section does not re-derive them.

## Known residual availability gaps (`#312`, deferred, not silently dropped)

Two availability gaps adjacent to this fix are deliberately NOT closed here
-- named rather than left to disappear, and tracked in a narrow follow-up
issue, `#320` (`#200-G1C2-F3`). Both are reasoned from code inspection, not
live-reproduced; they involve different primitives from the special-file
open this module's fix addresses:

- `run_bounded_git_v2` (`bounded_git_v2.py`) invokes `subprocess.run`
  with no `timeout=`. `_verify_pack_integrity_v2`'s `git verify-pack` and
  `TrustedObjectAuthorityV2.prove_ancestry`'s `git rev-list` both go
  through it -- a sufficiently pathological pack (e.g. adversarial delta
  chains) could make git's own parser run for a very long time,
  unbounded by this module. Not reproduced live (constructing a genuinely
  pathological pack was judged out of proportion for this property);
  reasoned from code inspection only.
- `_read_and_close_fd_charged_v2`'s `os.read` loop runs against a
  fd already confirmed `S_ISREG` by this fix -- but a "regular" file on a
  stalled network/FUSE mount can still put `read(2)` into an
  uninterruptible sleep on Linux, independent of `O_NONBLOCK` (already
  cleared before this read by design, once `S_ISREG` is confirmed). Not
  reproducible in this sandbox (no `/dev/fuse`); reasoned, not
  confirmed.

Both are outside the specific "hostile checkout plants a special file at
a probe/listed-file site" property this module's own fix closes
structurally; closing them would mean bounding a different primitive
(`subprocess.run`, `os.read`) with its own new reason code and falsifier
corpus, not another instance of the same fix.

## Threat scope

Unchanged from `#200-G1C`: in scope is a hostile/mutable LIVE repository
checkout under a DIFFERENT privilege boundary than this process (a
different UID, a network peer, or a compromised checkout this process
merely has read access to) -- including a source that mutates itself
*during* this module's own acquisition call, which is exactly what round
3's finding demonstrated this module must defend against and what
descriptor-anchoring closes structurally rather than by detection.

Out of scope, explicitly, matching the identical `host_arbitrary_code_
attacker` boundary already declared in `commit_derived_execution_identity_
v2.py` (the sibling module this one composes with) and carried forward
from `#200-G1C`'s own "Threat scope" section: a SAME-UID adversary who can
already write to this process's own filesystem namespace. Retained
descriptors close the *pathname re-resolution* race a same-UID adversary
could otherwise exploit identically to a different-UID one, but this
module still does not seal or make read-only the private CAS directory
itself against a same-UID writer after acquisition completes -- that
remains the explicit, carried-forward prerequisite for `#200-G1B`/`#200-G5`
noted in `#200-G1C`'s own disposition, not silently assumed here.

## CAEM design reference -- not authority for this repository

`config/caem/caem-3.0-f0.pin.json` in this repository pins CAEM 3.0 F0 with
`authority.authority_effect: "none"`. ADR 0012 (quoted in #303/#310) lives
in `caem-3.0-c8`, past that pin, `maturity: candidate, published: false`.
Read here as **design reference / prior art only**, same disclaimer as
`#200-G1C`'s own citation: "runtime paths are discovery inputs only ...
mutation of the discovery inode after authentication cannot change the
executed bytes," and, on descent specifically, "each component of the
conventional dependency directory is opened relative to the runtime
descriptor with no-follow directory semantics ... any symlink, special
file, replacement, or metadata/content race fails closed." This module is
an independent implementation of that shape for a different subject (a Git
object store's acquisition, not a launcher/interpreter/dependency tree),
not a consumer of CAEM's own machinery, and claims no CAEM authorization.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import os
import secrets
import shutil
import stat
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.agent_review.bounded_git_v2 import BoundedGitError, run_bounded_git_v2

__all__ = [
    "TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_PACK_VERIFICATION_FAILED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_SPECIAL_FILE_REJECTED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2",
    "TrustedObjectAuthorityError",
    "TrustedObjectAuthorityV2",
    "open_trusted_object_authority_v2",
]


TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2 = "trusted_object_authority_repository_unusable"
TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2 = "trusted_object_authority_acquisition_failed"
TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2 = "trusted_object_authority_budget_exceeded"
TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2 = "trusted_object_authority_forged_capability"
TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2 = "trusted_object_authority_object_collision"
TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2 = "trusted_object_authority_ancestry_undetermined"
TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2 = "trusted_object_authority_object_hash_mismatch"
# G1C2: a symlink is refused wherever this module's descriptor-anchored
# opens encounter one (`O_NOFOLLOW` on every `open`/`openat`-equivalent
# call) -- never detected after the fact, never dependent on a separate,
# earlier `is_symlink()` observation of the same pathname.
TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2 = "trusted_object_authority_symlink_rejected"
# G1C2-F1 (#312): raised for a non-regular object that OPENED SUCCESSFULLY
# and was then rejected by classifying the SAME already-open fd that
# `open()` produced (`fstat` + `S_ISREG`, never a fresh path-based stat) --
# see `_open_regular_file_no_follow_v2`, which owns that branch. A FIFO and
# a device node take this path.
#
# Not every non-regular shape reaches it. An `AF_UNIX` socket fails at the
# `open()` syscall itself with `ENXIO` (measured on this platform), so no fd
# ever exists, `fstat` is never reached, and the generic `OSError` branch
# refuses it as `ACQUISITION_FAILED` instead -- still typed, still
# non-blocking, but a different reason code. Do not read this constant as
# covering every special-file type by name.
#
# Distinct from `SYMLINK_REJECTED`: a symlink is refused by `O_NOFOLLOW` at
# the `open()` call itself (`ELOOP`); this reason is for a target that
# opened successfully (it was not a symlink) but is not a genuine regular
# file either.
TRUSTED_OBJECT_AUTHORITY_SPECIAL_FILE_REJECTED_REASON_V2 = "trusted_object_authority_special_file_rejected"
TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2 = "trusted_object_authority_alternate_rejected"
TRUSTED_OBJECT_AUTHORITY_PACK_VERIFICATION_FAILED_REASON_V2 = "trusted_object_authority_pack_verification_failed"

#: Hard budgets, enforced *before* any copied byte is handed to git for
#: parsing (CAEM ADR 0011's "hard budgets precede untrusted parsing",
#: applied here to acquisition rather than bundle parsing). Generous enough
#: for any real toolrepo checkout; a caller with a genuinely larger
#: repository passes explicit, deliberately-chosen overrides.
_DEFAULT_MAX_TOTAL_BYTES_V2 = 2 * 1024 * 1024 * 1024
_DEFAULT_MAX_OBJECT_COUNT_V2 = 200_000
_DEFAULT_MAX_ALTERNATE_DEPTH_V2 = 8
_DEFAULT_MAX_REV_LIST_BYTES_V2 = 64 * 1024 * 1024
_DEFAULT_MAX_PATH_SEGMENTS_V2 = 256

_MARKER_FILENAME_V2 = ".trusted_object_authority_v2.marker"

#: Only `open_trusted_object_authority_v2` may construct a
#: `TrustedObjectAuthorityV2`. This sentinel is the gate: `__init__` refuses
#: any caller that does not present the exact object this module created.
_BUILD_SENTINEL_V2 = object()


class TrustedObjectAuthorityError(ValueError):
    """The trusted object authority could not be built or used.

    Content-free `reason_code` only, matching every other primitive in this
    package -- the underlying git stderr or a local filesystem path can
    leak repository content or host layout.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class _ObjectCopyBudgetV2:
    max_total_bytes: int
    max_object_count: int
    max_alternate_depth: int


class _ObjectCopyBudgetTrackerV2:
    """Mutable running total for one acquisition. Not shared across calls."""

    def __init__(self, budget: _ObjectCopyBudgetV2) -> None:
        self._budget = budget
        self.total_bytes = 0
        self.total_objects = 0

    def charge(self, num_bytes: int) -> None:
        self.total_bytes += num_bytes
        self.total_objects += 1
        if self.total_bytes > self._budget.max_total_bytes:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)
        if self.total_objects > self._budget.max_object_count:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)


# -- descriptor-anchored primitives -------------------------------------------
#
# Every function below either OPENS something (the sole authoritative check,
# atomic with obtaining the handle everything afterward uses) or OPERATES ON
# an already-open descriptor (`fstat`/`read`/`scandir`). None of them ever
# performs a fresh path-based `stat()`/`open()` on a pathname whose safety
# was established by an EARLIER, SEPARATE call -- that separation is exactly
# what round 3 on PR #308 falsified.


#: Empirically verified (see #310's implementation notes): `open(...,
#: O_DIRECTORY | O_NOFOLLOW)` on a path whose final component is a
#: symlink raises `ENOTDIR` (errno 20), NOT `ELOOP` (errno 40) -- the
#: kernel never gets to report "too many levels of symbolic links" because
#: `O_DIRECTORY` rejects the (unfollowed) symlink object as not-a-directory
#: first. `ELOOP` is what a symlink raises when `O_NOFOLLOW` is used
#: WITHOUT `O_DIRECTORY` (a file-shaped open). `ENOTDIR` is also what an
#: ordinary regular file (not a symlink at all) raises under the same
#: flags -- the two causes are not distinguishable from errno alone, and
#: this module does not need to distinguish them: either way, something
#: other than a real directory occupies a path this module required to be
#: one, and refusal is the correct response regardless of which.
_SYMLINK_OR_WRONG_TYPE_ERRNOS_V2 = frozenset({errno.ELOOP, errno.ENOTDIR})


def _open_dir_no_follow_v2(dir_fd: int | None, name: str) -> int:
    """Open a directory, `O_NOFOLLOW`, relative to `dir_fd` (or, if
    `dir_fd is None`, `name` is used as an absolute/cwd-relative path
    directly -- used only for a handful of top-level entry points such as
    `/` itself or a caller-supplied `repo_root`, never for anything found
    beneath an already-open descriptor). Raises `SYMLINK_REJECTED` if the
    final path component is a symlink (or any other non-directory --
    see `_SYMLINK_OR_WRONG_TYPE_ERRNOS_V2`), `REPOSITORY_UNUSABLE` if it
    does not exist. This IS the check -- there is no earlier, separate
    observation of `name` that this call merely repeats.

    `name` is untrusted content in most call sites (a path component
    parsed from a `gitdir:` pointer, `commondir` file, or `objects/info/
    alternates` entry -- all explicitly in the hostile-repo threat scope).
    `os.open` raises `ValueError` (not `OSError`) for a component
    containing an embedded NUL byte -- independently reproduced,
    end-to-end, through this module's own public entry point. Caught here
    alongside `OSError` so it cannot escape uncaught past this module's
    typed-error contract.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        if dir_fd is None:
            return os.open(name, flags)
        return os.open(name, flags, dir_fd=dir_fd)
    except ValueError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2) from exc
    except OSError as exc:
        if exc.errno in _SYMLINK_OR_WRONG_TYPE_ERRNOS_V2:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2) from exc
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2) from exc


def _try_open_dir_no_follow_v2(dir_fd: int, name: str) -> int | None:
    """Like `_open_dir_no_follow_v2`, but returns `None` (not an error) if
    `name` genuinely does not exist relative to `dir_fd` -- e.g. a
    repository that legitimately has no `objects/pack` directory yet. A
    symlink planted at `name` is still refused loudly, never silently
    treated as "absent". `ValueError` (an embedded NUL byte -- see
    `_open_dir_no_follow_v2`) is caught alongside `OSError` for the same
    reason."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    except OSError as exc:
        if exc.errno in _SYMLINK_OR_WRONG_TYPE_ERRNOS_V2:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2) from exc
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc


def _open_regular_file_no_follow_v2(dir_fd: int, name: str, *, missing_is_legitimate: bool) -> int | None:
    """THE single regular-file-open choke point in this module. Every call
    site that opens an untrusted, attacker-named regular file -- all 7
    metadata probes (`.git`-as-file for a linked worktree, `commondir`,
    `packed-refs`, the three `HEAD` probes, `objects/info/alternates`) AND
    the bulk data-copy path (every loose object, pack file, and ref file
    discovered via `os.scandir`, 3 more sites) -- goes through here, and
    here alone: 10 call sites total (verified by grepping every call to
    this function and its two thin wrappers, not merely asserted).

    G1C2-F1 (`#312`): the reason this is ONE function rather than two is a
    reproduced defect, not a style preference. Hardening only the
    metadata-probe half (then named `_try_open_file_no_follow_v2`) left a
    SEPARATE function (then named `_open_listed_file_no_follow_v2`, used at
    what are now the loose-object/pack-file/ref-file call sites) with the
    original unguarded `O_RDONLY | O_NOFOLLOW` open -- a real, reproduced
    gap. That function's own docstring claim -- "even if the
    entry is swapped between listing and open, the open() call itself is
    the authoritative, atomic check" -- is true for IDENTITY (`O_NOFOLLOW`
    means a symlink swapped in cannot be silently followed) but was FALSE
    for AVAILABILITY: a FIFO is not a symlink, so `O_NOFOLLOW` does not
    reject it, and a blocking `open()` on a FIFO with no writer present
    hangs forever -- squarely inside this module's own declared threat
    scope ("a source that mutates itself during this module's own
    acquisition call"). Two separately-maintained copies of the same fix is
    exactly the shape ("four fixed, two stragglers") that refuted the
    `#200-G1C` predecessor -- so both former functions are unified into
    this one, parameterized only by the one genuine
    behavioral difference between them (`missing_is_legitimate`), so a
    future fix to this choke point cannot again land in only one of two
    copies.

    `missing_is_legitimate=True` (the former `_try_open_file_no_follow_v2`
    shape): `name` is a file this module has NOT already committed to --
    "genuinely does not exist" is an expected, legitimate state, so a
    `FileNotFoundError` returns `None`.

    `missing_is_legitimate=False` (the former `_open_listed_file_no_
    follow_v2` shape): `name` is a file THIS MODULE ALREADY OBSERVED via a
    `scandir` listing moments ago. A vanished file here is REFUSED
    (`ACQUISITION_FAILED`), never silently treated as if it had never
    existed: a concurrent writer removing an object between listing and
    open is exactly the kind of live-source inconsistency this module's
    threat model requires failing closed on, not tolerating as equivalent
    to "was never there".

    Every other step is identical for both shapes and is the fix itself,
    applied atomically against ONE fd, never a fresh path-based reopen
    (which would reintroduce exactly the TOCTOU class this module's own
    `#200-G1C2` architecture exists to eliminate -- see the module
    docstring):

        open O_NONBLOCK | O_NOFOLLOW
        -> fstat the SAME fd
        -> require S_ISREG
        -> clear O_NONBLOCK (fcntl) before returning the fd for reading

    `O_NONBLOCK` at `open()` means a FIFO with no writer returns
    immediately instead of blocking -- there is no longer any blocking
    syscall in this probe path at all, so a caller-side watchdog is not
    needed to protect it (this is why #312's secondary `TimeoutError`-
    swallowed-by-`except OSError` finding does not get its own new reason
    code here: there is nothing left in this function for a watchdog to
    ever need to interrupt). `fstat` never blocks regardless of the fd's
    underlying file type. If the fd is not a genuine regular file (a FIFO, a
    device node, or anything else `S_ISREG` rejects), it is closed and
    refused via `SPECIAL_FILE_REJECTED` WITHOUT ever calling `os.read` on
    it. An `AF_UNIX` socket never reaches this branch -- `open()` fails
    first with `ENXIO`, so no fd exists to classify; see
    `SPECIAL_FILE_REJECTED`'s own comment for that distinction.
    `O_NONBLOCK` is cleared only once `S_ISREG` is confirmed, so every
    downstream reader (`_read_and_close_fd_charged_v2`) keeps assuming an
    ordinary blocking regular-file read, unchanged.

    Raises `SYMLINK_REJECTED` if `name` is a symlink (`ELOOP` at the
    `open()` call itself). `ValueError` (an embedded NUL byte -- see
    `_open_dir_no_follow_v2`; a real `scandir`-derived name cannot contain
    one, but this is still caught here for defensive consistency with
    every other open primitive in this module) is caught alongside
    `OSError` for the same reason.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError as exc:
        if missing_is_legitimate:
            return None
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    except ValueError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2) from exc
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc

    try:
        file_kind = os.fstat(fd)
    except OSError as exc:
        _close_ignoring_errors_v2(fd)
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc

    if not stat.S_ISREG(file_kind.st_mode):
        _close_ignoring_errors_v2(fd)
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SPECIAL_FILE_REJECTED_REASON_V2)

    try:
        current_status_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, current_status_flags & ~os.O_NONBLOCK)
    except OSError as exc:
        _close_ignoring_errors_v2(fd)
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc

    return fd


def _try_open_file_no_follow_v2(dir_fd: int, name: str) -> int | None:
    """Thin named entry point onto `_open_regular_file_no_follow_v2` for an
    OPTIONAL probe -- e.g. `commondir`, `packed-refs`, `objects/info/
    alternates`, a `gitdir:` pointer, the three `HEAD` probes -- where
    "genuinely does not exist" is an expected, legitimate state. NOT for a
    file already observed via a `scandir` listing moments ago -- see
    `_open_listed_file_no_follow_v2` for that case, where a vanished file
    is refused rather than silently tolerated. Kept as a distinct, named
    function (rather than inlining `missing_is_legitimate=True` at every
    call site) purely for call-site readability; all real behavior lives
    in the shared primitive."""
    return _open_regular_file_no_follow_v2(dir_fd, name, missing_is_legitimate=True)


def _open_listed_file_no_follow_v2(dir_fd: int, name: str) -> int:
    """Thin named entry point onto `_open_regular_file_no_follow_v2` for a
    file THIS MODULE ALREADY OBSERVED via a `scandir` listing moments ago
    (a loose object, a pack file, a ref file) -- see that function's
    docstring for why a vanished file here is refused rather than silently
    tolerated. `missing_is_legitimate=False` never returns `None`: an
    explicit `TrustedObjectAuthorityError`, not a bare `assert`, guards
    that invariant here, so the module's typed-error contract holds even
    under `-O`/`PYTHONOPTIMIZE` (which strips assertions) -- provably
    unreachable today by exhaustive case analysis of
    `_open_regular_file_no_follow_v2`'s own branches, but a bare `assert`
    would let that guarantee silently depend on an interpreter flag this
    module has no control over."""
    fd = _open_regular_file_no_follow_v2(dir_fd, name, missing_is_legitimate=False)
    if fd is None:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2)
    return fd


@contextlib.contextmanager
def _closing_fd_v2(fd: int) -> Iterator[int]:
    """`contextlib.closing` for a raw fd -- `os.close`, not `.close()`."""
    try:
        yield fd
    finally:
        os.close(fd)


def _read_and_close_fd_charged_v2(fd: int, tracker: _ObjectCopyBudgetTrackerV2) -> bytes:
    """`fstat` + budget-charge + full read, ALL against the SAME
    already-open descriptor `fd` (closed on every exit path) -- no fresh
    path-based stat or reopen anywhere in this function. `fd` was opened
    no-follow by the caller; nothing here re-resolves any pathname.

    Budget is charged from `fstat`, before any content is read into
    memory -- an oversized object is refused before a single content byte
    enters memory, not after a full `read()` already loaded it.
    """
    try:
        size = os.fstat(fd).st_size
        tracker.charge(size)
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 4 * 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    finally:
        os.close(fd)
    if len(data) != size:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2)
    return data


def _close_ignoring_errors_v2(fd: int) -> None:
    """Best-effort `os.close`, for cleanup paths that must attempt to
    close every fd they are responsible for even if one of those closes
    itself fails -- a failure here must never mask the ORIGINAL exception
    that triggered the cleanup, and must never stop the remaining fds in
    the same cleanup pass from also being closed."""
    try:
        os.close(fd)
    except OSError:
        pass


def _open_dir_by_segments_no_follow_v2(*, base_fd: int | None, path_str: str) -> int:
    """Resolve `path_str` (absolute or relative, possibly multi-segment --
    e.g. a `commondir` file's `../..`, or a `gitdir:` pointer's absolute
    target) ONE COMPONENT AT A TIME, each opened no-follow relative to the
    descriptor reached so far -- never a single multi-segment path handed
    to one `open()` call, which would let the OS resolve intermediate
    components through its own (symlink-following) walk. This is what
    closes "ancestral-path retargeting": every step is anchored to an
    already-open, already-verified parent descriptor, never a re-walked
    path string.

    An absolute `path_str` starts fresh from `/`; a relative one starts
    from `base_fd` (required in that case). A hard cap on the number of
    segments (`_DEFAULT_MAX_PATH_SEGMENTS_V2`) is a budget, not a security
    boundary -- `..` segments are ordinary directory entries opened the
    same no-follow way as any other component, not specially rejected
    (git's own `commondir`/`gitdir:` conventions legitimately use `..` to
    walk up from a private worktree gitdir to the shared one).

    Fd bookkeeping (fixed after independent review found a real defect in
    an earlier version of this function): `open_fds` always contains
    EXACTLY the fd(s) this function is currently responsible for closing.
    A superseded fd is popped out of that list -- untracked -- BEFORE its
    own `os.close()` is even attempted, not after. This closes two related
    bugs at once: if that `close()` call itself raises (a real, if
    low-likelihood, possibility -- signal interruption, an exotic
    filesystem error), the fd it was closing is already untracked (so the
    exception handler below cannot double-close it), and the NEWLY opened
    fd for the segment just resolved is still tracked in `open_fds` (so
    the exception handler DOES close it, rather than leaking it). The
    naive version of this loop closed the old fd and only reassigned the
    "current fd" variable afterward -- so a `close()` failure left the
    exception handler holding a stale reference to the just-closed fd
    (double-close risk) while the fd it should have cleaned up next was
    never referenced by anything (leak).
    """
    path = PurePosixPath(path_str)
    parts = list(path.parts)
    if path.is_absolute():
        first_fd = _open_dir_no_follow_v2(None, "/")
        remaining = parts[1:]
    else:
        if base_fd is None:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
        try:
            first_fd = os.dup(base_fd)
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
        remaining = parts

    if len(remaining) > _DEFAULT_MAX_PATH_SEGMENTS_V2:
        _close_ignoring_errors_v2(first_fd)
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)

    open_fds = [first_fd]
    try:
        for segment in remaining:
            next_fd = _open_dir_no_follow_v2(open_fds[-1], segment)
            open_fds.append(next_fd)
            stale_fd = open_fds.pop(0)
            try:
                os.close(stale_fd)
            except OSError as exc:
                raise TrustedObjectAuthorityError(
                    TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2
                ) from exc
    except BaseException:
        for fd in open_fds:
            _close_ignoring_errors_v2(fd)
        raise
    return open_fds[0]


def _verify_loose_object_hash_v2(*, expected_sha_hex: str, compressed: bytes) -> None:
    """Inherited from `#200-G1C` correction round 1 (Lane B, independently
    reproduced twice through the real `authorize_commit_for_execution_v2`
    production path): a loose object's fanout directory + filename IS
    git's own content-addressing claim about that object -- "the sha1 (or
    sha256, for a sha256-format repository) of `type size\\0content` is
    this exact path". Ordinary git reads (`cat-file`, the `rev-list` walk
    this module's own `prove_ancestry` performs) do not re-verify that
    claim for loose objects; only `git fsck --strict` does, and nothing in
    this pipeline runs it. Not implicated by the round-3/#310 TOCTOU
    finding -- that finding was about how bytes are ACQUIRED, this is
    about what is done with them once acquired -- carried forward
    unchanged.

    Decompression failure or a hash mismatch both refuse via the same
    reason code -- either way, the object copied into the CAS is not
    genuinely the object its own path claims to be, and nothing downstream
    should ever see it presented as such.
    """
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2) from exc
    algorithm = {40: hashlib.sha1, 64: hashlib.sha256}.get(len(expected_sha_hex))
    if algorithm is None:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2)
    actual_sha_hex = algorithm(raw, usedforsecurity=False).hexdigest()  # noqa: S324 -- matching git's own object id algorithm, not used as a security primitive here
    if actual_sha_hex != expected_sha_hex:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2)


@dataclass(frozen=True)
class _GitDirectoriesV2:
    """`git_dir_fd` (worktree-PRIVATE: `HEAD`, index, per-worktree refs)
    and `common_dir_fd` (SHARED across every worktree: `objects/`,
    `refs/heads`, `refs/tags`, `packed-refs`) -- identical for an
    ordinary, non-worktree repository, genuinely different for a linked
    worktree. Both are open descriptors; the caller (`open_trusted_object_
    authority_v2`) owns their lifetime and closes them once acquisition is
    complete."""

    git_dir_fd: int
    common_dir_fd: int


def _resolve_git_directories_fd_v2(repo_root: Path) -> _GitDirectoriesV2:
    """Resolve both the worktree-private and shared git directories,
    worktree-aware, ENTIRELY via descriptor-anchored opens -- never a git
    invocation (a hostile/malformed live `config` cannot make this fail,
    since nothing here parses it), and never a pathname re-resolved after
    an earlier check established something about it.
    """
    repo_root_fd = _open_dir_no_follow_v2(None, str(repo_root))
    try:
        # `.git` is legitimately EITHER a directory (ordinary repo) OR a
        # regular file (a `gitdir:` pointer, linked worktree) -- both
        # expected, common shapes, neither one hostile by itself. `open(...,
        # O_DIRECTORY | O_NOFOLLOW)` cannot itself distinguish "wrong type
        # because it's a symlink" from "wrong type because it's an ordinary
        # regular file" (both raise `ENOTDIR`) -- so which atomic open to
        # attempt is decided by a preliminary, NON-AUTHORITATIVE
        # `fstatat`-equivalent classification (`os.stat(..., dir_fd=...,
        # follow_symlinks=False)`, itself fd-relative, not a re-resolved
        # absolute pathname). This classification is a HINT only: whichever
        # branch it selects still goes through the same atomic, no-follow
        # open as every other path in this module, which is what actually
        # decides trust -- a symlink planted between the stat and the open
        # still fails closed (`ELOOP`) at the open, regardless of what the
        # stat guessed.
        try:
            dotgit_kind = os.stat(".git", dir_fd=repo_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            dotgit_kind = None
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2) from exc

        if dotgit_kind is not None and stat.S_ISDIR(dotgit_kind.st_mode):
            git_dir_fd = _open_dir_no_follow_v2(repo_root_fd, ".git")
        elif dotgit_kind is not None and stat.S_ISREG(dotgit_kind.st_mode):
            dotgit_file_fd = _try_open_file_no_follow_v2(repo_root_fd, ".git")
            if dotgit_file_fd is None:
                raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
            # `.git` is a FILE (linked worktree): a single `gitdir: <path>`
            # line. The read is a single atomic open+read of THIS file
            # (already no-follow); the path it names is then resolved
            # component-by-component, never as one string handed to a
            # single `open()`.
            content = _read_and_close_fd_charged_v2(
                dotgit_file_fd, _ObjectCopyBudgetTrackerV2(_ObjectCopyBudgetV2(1024 * 1024, 1, 0))
            )
            text = content.decode("utf-8", "surrogateescape")
            first_line = text.splitlines()[0] if text else ""
            prefix = "gitdir:"
            if not first_line.startswith(prefix):
                raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
            pointed = first_line[len(prefix) :].strip()
            git_dir_fd = _open_dir_by_segments_no_follow_v2(base_fd=repo_root_fd, path_str=pointed)
        elif dotgit_kind is not None:
            # Exists, but is neither a directory nor a regular file --
            # a symlink or something exotic. Refused loudly.
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
        else:
            # `repo_root` might itself be a bare git directory: no `.git`
            # at all, `HEAD`/`objects` directly present.
            head_fd = _try_open_file_no_follow_v2(repo_root_fd, "HEAD")
            objects_probe_fd = _try_open_dir_no_follow_v2(repo_root_fd, "objects")
            if head_fd is not None and objects_probe_fd is not None:
                os.close(head_fd)
                os.close(objects_probe_fd)
                git_dir_fd = os.dup(repo_root_fd)
            else:
                if head_fd is not None:
                    os.close(head_fd)
                if objects_probe_fd is not None:
                    os.close(objects_probe_fd)
                raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
    finally:
        os.close(repo_root_fd)

    try:
        commondir_file_fd = _try_open_file_no_follow_v2(git_dir_fd, "commondir")
        if commondir_file_fd is not None:
            raw = _read_and_close_fd_charged_v2(
                commondir_file_fd, _ObjectCopyBudgetTrackerV2(_ObjectCopyBudgetV2(1024 * 1024, 1, 0))
            )
            raw_text = raw.decode("utf-8", "surrogateescape").strip()
            common_dir_fd = _open_dir_by_segments_no_follow_v2(base_fd=git_dir_fd, path_str=raw_text)
        else:
            common_dir_fd = os.dup(git_dir_fd)
    except BaseException:
        os.close(git_dir_fd)
        raise
    return _GitDirectoriesV2(git_dir_fd=git_dir_fd, common_dir_fd=common_dir_fd)


def _copy_pack_file_fd_v2(source_fd: int, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one pack file (`.pack`/`.idx`/`.rev`/`.bitmap`/...) from an
    ALREADY-OPEN, no-follow descriptor.

    NAMED LIMITATION, stated plainly rather than silently assumed
    (inherited from `#200-G1C`): unlike loose objects, individual objects
    inside a pack are NOT independently re-hashed here -- doing so would
    mean parsing git's pack/delta format, which this module deliberately
    does not reimplement. A pack's own trailing checksum and its `.idx`
    CRC32s are git-internal integrity aids consulted by git itself when it
    later reads from the copied pack, not by this module; `_verify_pack_
    integrity_v2` closes packed-object *identity* forgery via git's own
    `verify-pack`, run against the CAS after every pack/idx is copied in.
    """
    data = _read_and_close_fd_charged_v2(source_fd, tracker)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.read_bytes() != data:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2)
        return
    dest.write_bytes(data)


def _copy_loose_object_fd_v2(
    source_fd: int, dest: Path, *, expected_sha_hex: str, tracker: _ObjectCopyBudgetTrackerV2
) -> None:
    """Copy one loose object from an ALREADY-OPEN, no-follow descriptor,
    content-hash verified against the exact sha its own fanout path
    claims (see `_verify_loose_object_hash_v2`)."""
    data = _read_and_close_fd_charged_v2(source_fd, tracker)
    _verify_loose_object_hash_v2(expected_sha_hex=expected_sha_hex, compressed=data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.read_bytes() != data:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2)
        return
    dest.write_bytes(data)


def _copy_named_file_fd_v2(source_fd: int, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one NAMED-POINTER file (`HEAD`, a ref) from an ALREADY-OPEN,
    no-follow descriptor. Unlike an object file, the destination name is
    not derived from content -- an existing destination (this module's
    own hand-authored placeholder `HEAD`) is expected and overwritten,
    never treated as a collision."""
    data = _read_and_close_fd_charged_v2(source_fd, tracker)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _looks_like_git_objects_directory_fd_v2(objects_fd: int) -> bool:
    """Minimal structural sanity check for an ALTERNATE objects directory
    (never applied to the primary/top-level source). Real git always
    keeps `objects/` as a direct sibling of `HEAD`, in both a bare
    repository root and a non-bare `.git` directory alike -- checked here
    by opening `..` relative to the already-open `objects_fd` (a plain,
    ordinary directory entry, not a symlink-resolution risk) and then
    `HEAD` relative to THAT, no-follow, atomically -- never a separate
    `is_symlink()` observation followed by a later reopen.

    Explicitly NOT claimed (inherited from `#200-G1C`): that this proves
    the directory is a genuine, legitimately-related git repository --
    only that it is not the much lower-effort degenerate case of an
    alternates entry pointing at an ordinary, unrelated host directory.
    Every object actually admitted from a passing alternate is still
    subject to the identical loose-object hash verification as
    everything else -- this check is defense in depth layered in front of
    that verification, never a substitute for it.
    """
    try:
        parent_fd = os.open("..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=objects_fd)
    except OSError:
        return False
    try:
        head_fd = _try_open_file_no_follow_v2(parent_fd, "HEAD")
        if head_fd is None:
            return False
        os.close(head_fd)
        return True
    except TrustedObjectAuthorityError:
        return False
    finally:
        os.close(parent_fd)


def _parse_alternates_v2(raw: bytes, *, owning_objects_fd: int) -> list[tuple[int | None, str]]:
    """Each line is a path to another repository's `objects/` directory.
    Returns `(base_fd_or_None, path_str)` pairs ready for
    `_open_dir_by_segments_no_follow_v2` -- relative entries resolve
    relative to the *owning* objects descriptor itself (git's own
    documented convention for `objects/info/alternates`), not to this
    module's cwd; `base_fd` is `None` for an absolute entry (resolved from
    `/`) and `owning_objects_fd` for a relative one."""
    resolved: list[tuple[int | None, str]] = []
    for raw_line in raw.split(b"\n"):
        line = raw_line.decode("utf-8", "surrogateescape").strip()
        if not line or line.startswith("#"):
            continue
        if PurePosixPath(line).is_absolute():
            resolved.append((None, line))
        else:
            resolved.append((owning_objects_fd, line))
    return resolved


def _copy_objects_dir_fd_v2(
    *,
    source_objects_fd: int,
    dest_objects_dir: Path,
    tracker: _ObjectCopyBudgetTrackerV2,
    visited_dev_ino: set[tuple[int, int]],
    depth: int,
    budget: _ObjectCopyBudgetV2,
) -> None:
    """Copy every object physically present under one ALREADY-OPEN,
    no-follow `objects/` descriptor, then recursively flatten any
    alternates it declares into the SAME destination -- never chained,
    never written back out as a second `objects/info/alternates` in the
    authority. Closes `source_objects_fd` on every exit path.

    Cycle detection uses the descriptor's own `(st_dev, st_ino)` (from
    `fstat` on the already-open fd), not `os.path.realpath()` of a
    pathname -- there is no pathname here to resolve in the first place.

    Deliberately not a git invocation of any kind. Whatever is missing
    (a partial clone's filtered-out blobs, a shallow clone's pruned
    ancestors) is, definitionally, not a file reachable here -- it is
    simply absent from the copy, not detected-and-rejected after the fact.
    """
    try:
        st = os.fstat(source_objects_fd)
        key = (st.st_dev, st.st_ino)
        if key in visited_dev_ino:
            return
        if depth > budget.max_alternate_depth:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)
        if depth > 0 and not _looks_like_git_objects_directory_fd_v2(source_objects_fd):
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2)
        visited_dev_ino.add(key)

        # Loose objects: two-hex-prefix fanout directories only.
        # Content-hash verified against the exact sha the fanout-dir-name
        # + filename claims. `entry.is_dir()`/`is_file()` here are a cheap
        # CLASSIFICATION HINT only, not a security decision -- even if
        # stale/raced, the actual `_open_dir_no_follow_v2`/
        # `_open_listed_file_no_follow_v2` call that follows is the sole
        # atomic authority, and fails closed on any type mismatch. A name
        # that LOOKS like it should be a fanout directory (two lowercase
        # hex characters) but is a symlink is refused explicitly and
        # loudly here -- `is_dir(follow_symlinks=False)` alone would
        # classify a symlink as neither a directory nor a file and this
        # loop would otherwise silently skip it, which is not this
        # module's policy anywhere else a symlink is found.
        for entry in os.scandir(source_objects_fd):
            if entry.name in (".", ".."):
                continue
            if len(entry.name) == 2 and all(c in "0123456789abcdef" for c in entry.name):
                if entry.is_symlink():
                    raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
                if not entry.is_dir(follow_symlinks=False):
                    continue
                fanout_fd = _open_dir_no_follow_v2(source_objects_fd, entry.name)
                try:
                    for obj_entry in os.scandir(fanout_fd):
                        if obj_entry.name in (".", ".."):
                            continue
                        if obj_entry.is_symlink():
                            raise TrustedObjectAuthorityError(
                                TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2
                            )
                        if not obj_entry.is_file(follow_symlinks=False):
                            continue
                        obj_fd = _open_listed_file_no_follow_v2(fanout_fd, obj_entry.name)
                        expected_sha_hex = entry.name + obj_entry.name
                        _copy_loose_object_fd_v2(
                            obj_fd,
                            dest_objects_dir / entry.name / obj_entry.name,
                            expected_sha_hex=expected_sha_hex,
                            tracker=tracker,
                        )
                finally:
                    os.close(fanout_fd)

        # Pack files: copied verbatim -- see `_copy_pack_file_fd_v2` for
        # the named residual limitation here.
        pack_fd = _try_open_dir_no_follow_v2(source_objects_fd, "pack")
        if pack_fd is not None:
            try:
                for entry in os.scandir(pack_fd):
                    if entry.name in (".", ".."):
                        continue
                    if entry.is_symlink():
                        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
                    if entry.is_file(follow_symlinks=False):
                        f_fd = _open_listed_file_no_follow_v2(pack_fd, entry.name)
                        _copy_pack_file_fd_v2(f_fd, dest_objects_dir / "pack" / entry.name, tracker)
            finally:
                os.close(pack_fd)

        # Alternates: recursively flattened INTO this same destination.
        # The authority's own `objects/info/` is never populated with an
        # alternates file -- there is nothing in this module's output for
        # a later reader to chase.
        info_fd = _try_open_dir_no_follow_v2(source_objects_fd, "info")
        if info_fd is not None:
            try:
                alternates_fd = _try_open_file_no_follow_v2(info_fd, "alternates")
                if alternates_fd is not None:
                    raw = _read_and_close_fd_charged_v2(alternates_fd, tracker)
                    for alt_base_fd, alt_path_str in _parse_alternates_v2(raw, owning_objects_fd=source_objects_fd):
                        alt_fd = _open_dir_by_segments_no_follow_v2(base_fd=alt_base_fd, path_str=alt_path_str)
                        _copy_objects_dir_fd_v2(
                            source_objects_fd=alt_fd,
                            dest_objects_dir=dest_objects_dir,
                            tracker=tracker,
                            visited_dev_ino=visited_dev_ino,
                            depth=depth + 1,
                            budget=budget,
                        )
            finally:
                os.close(info_fd)
    finally:
        os.close(source_objects_fd)


def _walk_regular_files_fd_v2(dir_fd: int, prefix: str) -> Iterator[tuple[str, int]]:
    """Recursively walk an ALREADY-OPEN, no-follow directory descriptor,
    yielding `(relative_posix_path, file_fd)` for every regular file --
    each file/subdirectory opened relative to the descriptor it was found
    under, `O_NOFOLLOW`, never a reconstructed/re-walked path string.
    Caller consumes and closes each yielded `file_fd`; this generator
    closes every directory descriptor it itself opens.

    A symlink anywhere in this tree is refused explicitly and loudly
    (`SYMLINK_REJECTED`) -- `is_dir(follow_symlinks=False)`/`is_file(
    follow_symlinks=False)` alone would classify a symlink as neither and
    silently skip it, which is not this module's policy anywhere else a
    symlink is found (a legitimate `refs/heads`/`refs/tags` tree never
    contains one).
    """
    for entry in os.scandir(dir_fd):
        if entry.name in (".", ".."):
            continue
        if entry.is_symlink():
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
        if entry.is_dir(follow_symlinks=False):
            child_fd = _open_dir_no_follow_v2(dir_fd, entry.name)
            try:
                yield from _walk_regular_files_fd_v2(child_fd, f"{prefix}{entry.name}/")
            finally:
                os.close(child_fd)
        elif entry.is_file(follow_symlinks=False):
            file_fd = _open_listed_file_no_follow_v2(dir_fd, entry.name)
            yield (f"{prefix}{entry.name}", file_fd)


def _copy_refs_fd_v2(
    *, common_dir_fd: int, git_dir_fd: int, dest_git_dir: Path, tracker: _ObjectCopyBudgetTrackerV2
) -> None:
    """Copy `refs/heads/`, `refs/tags/`, and `packed-refs` verbatim from
    `common_dir_fd` (SHARED across every worktree), and `HEAD` from
    `git_dir_fd` (worktree-PRIVATE -- identical to `common_dir_fd` for an
    ordinary, non-worktree repository; genuinely different for a linked
    worktree, see `_GitDirectoriesV2`; inherited from `#200-G1C`
    correction round 2).

    Deliberately excludes `refs/replace/*`, `refs/notes/*`, and
    `refs/remotes/*` -- none of those are needed to resolve a commit sha
    or a branch/tag name, and copying them would be scope creep with no
    corresponding read path.
    """
    refs_fd = _try_open_dir_no_follow_v2(common_dir_fd, "refs")
    if refs_fd is not None:
        try:
            for namespace in ("heads", "tags"):
                namespace_fd = _try_open_dir_no_follow_v2(refs_fd, namespace)
                if namespace_fd is None:
                    continue
                try:
                    for relative, file_fd in _walk_regular_files_fd_v2(namespace_fd, ""):
                        _copy_named_file_fd_v2(
                            file_fd, dest_git_dir / "refs" / namespace / relative, tracker
                        )
                finally:
                    os.close(namespace_fd)
        finally:
            os.close(refs_fd)

    packed_refs_fd = _try_open_file_no_follow_v2(common_dir_fd, "packed-refs")
    if packed_refs_fd is not None:
        packed_refs_bytes = _read_and_close_fd_charged_v2(packed_refs_fd, tracker)
        kept_lines: list[bytes] = []
        for raw_line in packed_refs_bytes.split(b"\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(b"#"):
                continue
            if stripped.startswith(b"^"):
                # Peeled-tag annotation line -- keep only if the preceding
                # kept line was itself kept.
                if kept_lines:
                    kept_lines.append(stripped)
                continue
            # Anchored to the actual ref-name FIELD (everything after the
            # first space), not a raw substring search anywhere in the
            # line (inherited from `#200-G1C` correction round 1 P2).
            parts = stripped.split(b" ", 1)
            if len(parts) != 2:
                continue
            object_id, ref_name = parts
            if len(object_id) not in (40, 64) or any(c not in b"0123456789abcdef" for c in object_id):
                continue
            if ref_name.startswith(b"refs/heads/") or ref_name.startswith(b"refs/tags/"):
                kept_lines.append(stripped)
        if kept_lines:
            dest_packed_refs = dest_git_dir / "packed-refs"
            data = b"\n".join(kept_lines) + b"\n"
            tracker.charge(len(data))
            dest_packed_refs.write_bytes(data)

    # From `git_dir_fd`, NOT `common_dir_fd` -- see this function's
    # docstring. Identical to `common_dir_fd` for a non-worktree repo.
    head_fd = _try_open_file_no_follow_v2(git_dir_fd, "HEAD")
    if head_fd is not None:
        _copy_named_file_fd_v2(head_fd, dest_git_dir / "HEAD", tracker)


def _write_minimal_bare_skeleton_v2(cas_dir: Path) -> None:
    """A hand-authored bare repository shell -- never `git init`, and never
    a copy of the live repository's own `config`. Nothing here is derived
    from anything the live repository could have influenced.
    """
    (cas_dir / "objects" / "info").mkdir(parents=True, exist_ok=True)
    (cas_dir / "objects" / "pack").mkdir(parents=True, exist_ok=True)
    (cas_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (cas_dir / "refs" / "tags").mkdir(parents=True, exist_ok=True)
    (cas_dir / "HEAD").write_text("ref: refs/heads/does-not-exist\n")
    (cas_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = true\n"
    )


def _verify_pack_integrity_v2(cas_dir: Path) -> None:
    """Inherited from `#200-G1C` correction round 2 (independent human
    review, Finding 1): ordinary git reads (`cat-file`, the `rev-list`
    walk `prove_ancestry` performs) trust a pack's own `.idx` object-name
    table without re-verifying it. `git fsck --strict --full` catches a
    forged pack/idx pair; ordinary reads do not. Reimplementing an
    equivalent byte-level check for the pack/delta format would mean
    reimplementing git's pack format, explicitly out of this module's
    scope. Delegating to git's own `verify-pack` -- a bounded, local,
    network-incapable plumbing command, run here against the CAS itself,
    never the live repo, strictly after every pack/idx has already been
    copied in and strictly before the authority is ever yielded to a
    caller -- closes the same property without that reimplementation. Not
    implicated by the round-3/#310 TOCTOU finding; carried forward
    unchanged.

    Every `.idx` under the CAS's own `objects/pack/` is independently,
    positively verified; the CAS's own `objects/pack/` is self-authored by
    this module's own copy step (never attacker-controlled at this point),
    so a plain `glob` here -- unlike anywhere upstream of the copy step --
    carries no symlink risk.
    """
    pack_dir = cas_dir / "objects" / "pack"
    if not pack_dir.is_dir():
        return
    for idx_path in sorted(pack_dir.glob("*.idx")):
        try:
            run_bounded_git_v2(["verify-pack", str(idx_path)], cwd=cas_dir)
        except BoundedGitError as exc:
            raise TrustedObjectAuthorityError(
                TRUSTED_OBJECT_AUTHORITY_PACK_VERIFICATION_FAILED_REASON_V2
            ) from exc


@dataclass(frozen=True)
class TrustedObjectAuthorityV2:
    """The sole handle through which its own bound object store can be
    read. Never accepts an external path/cwd on any of its operations --
    there is no parameter shape by which a caller could point one instance
    at a different repository, and no way to construct a genuine instance
    except by `open_trusted_object_authority_v2`, which alone holds the
    build sentinel this class's `__init__` requires. `frozen=True`:
    `_cas_root`/`_expected_marker` cannot be reassigned post-construction.
    """

    _cas_root: Path
    _expected_marker: bytes

    def __init__(self, *, cas_root: Path, expected_marker: bytes, _sentinel: object) -> None:
        if _sentinel is not _BUILD_SENTINEL_V2:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2)
        object.__setattr__(self, "_cas_root", cas_root)
        object.__setattr__(self, "_expected_marker", expected_marker)

    def _verify_capability_v2(self) -> None:
        marker_path = self._cas_root / _MARKER_FILENAME_V2
        try:
            observed = marker_path.read_bytes()
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2) from exc
        if not secrets.compare_digest(observed, self._expected_marker):
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2)

    @property
    def trusted_repo_root(self) -> Path:
        """The private, remote-less copy's root. Re-verified on every
        access -- intended for immediate use as the `repo_root` argument
        to the existing, unchanged `git_commit_subject_v2`/`bounded_git_v2`
        reading primitives, not for storage or reuse beyond this
        authority's own lifetime.

        Scope note (see the module docstring's "Threat scope"): every
        object/ref under this path was positively verified (content-hash
        for loose objects, `verify-pack` for packed objects) at build
        time, against a LIVE repository under a different privilege
        boundary, acquired via descriptors immune to that live
        repository's own subsequent pathname mutation. It is NOT a claim
        that a SAME-UID writer to this process's own filesystem namespace
        cannot subsequently alter these bytes before a caller finishes
        reading them -- that boundary is explicitly out of scope here.
        """
        self._verify_capability_v2()
        return self._cas_root

    def prove_ancestry(self, *, commit_sha: str, trusted_ref_sha: str) -> bool:
        """Positive graph proof only. `True` iff `commit_sha` is a member of
        a *completely, successfully* enumerated ancestor set of
        `trusted_ref_sha`; `False` iff that same complete enumeration
        finished and does not contain it. An incomplete enumeration --
        shallow history, a missing/corrupted parent object, anything that
        makes the walk itself fail -- raises
        `TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2` rather
        than being treated as a negative answer. Both `commit_sha` and
        `trusted_ref_sha` are expected to already be resolved, verified
        40-hex commit shas (see `resolve_commit_v2` against
        `trusted_repo_root`) -- this method does not itself resolve a
        symbolic ref.
        """
        self._verify_capability_v2()
        try:
            completed = run_bounded_git_v2(["rev-list", trusted_ref_sha], cwd=self._cas_root)
        except BoundedGitError as exc:
            raise TrustedObjectAuthorityError(
                TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2
            ) from exc
        if len(completed.stdout) > _DEFAULT_MAX_REV_LIST_BYTES_V2:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)
        ancestor_shas = frozenset(completed.stdout.decode("ascii", "strict").split())
        return commit_sha in ancestor_shas


@contextlib.contextmanager
def open_trusted_object_authority_v2(
    repo_root: Path,
    *,
    max_total_bytes: int = _DEFAULT_MAX_TOTAL_BYTES_V2,
    max_object_count: int = _DEFAULT_MAX_OBJECT_COUNT_V2,
    max_alternate_depth: int = _DEFAULT_MAX_ALTERNATE_DEPTH_V2,
) -> Iterator[TrustedObjectAuthorityV2]:
    """Build a private, disposable, remote-less object-store authority from
    whatever object bytes are physically present under `repo_root`'s shared
    git directory right now, and yield the sole capability that can read it.

    Every call builds a brand-new authority in a brand-new private
    directory: there is no persistent baseline, session, or cross-call
    state of any kind for a rejected or partial acquisition to leave
    behind. The private directory is removed on exit regardless of outcome.

    Acquisition is entirely descriptor-anchored (see the module docstring):
    `repo_root` and everything beneath it this module reads is opened
    exactly once, `O_NOFOLLOW`, and every subsequent operation uses that
    same retained descriptor (or one opened relative to it) -- never a
    pathname re-resolved a second time. Deliberately no separate
    `Path.is_dir()` pre-check on `repo_root` here (an earlier version had
    one): it would itself follow a symlink and would in any case be
    immediately superseded by `_resolve_git_directories_fd_v2`'s own
    authoritative, atomic no-follow open of the very same path -- keeping
    it would have been dead weight inconsistent with this module's own
    "the open is the check" principle, not a second layer of protection.
    """
    git_dirs = _resolve_git_directories_fd_v2(Path(repo_root))
    budget = _ObjectCopyBudgetV2(
        max_total_bytes=max_total_bytes,
        max_object_count=max_object_count,
        max_alternate_depth=max_alternate_depth,
    )
    tracker = _ObjectCopyBudgetTrackerV2(budget)

    cas_dir = Path(tempfile.mkdtemp(prefix="agent_review_g1c_cas_v2_"))
    try:
        objects_fd = _try_open_dir_no_follow_v2(git_dirs.common_dir_fd, "objects")
        try:
            _write_minimal_bare_skeleton_v2(cas_dir)
            if objects_fd is not None:
                # `_copy_objects_dir_fd_v2` takes ownership of and closes
                # `objects_fd` (and every fd it opens) on every exit path,
                # success OR exception -- so ownership is transferred (and
                # the outer `finally` told not to double-close) BEFORE the
                # call, not after: if the call raises, control never
                # reaches a line placed after it.
                transferred_fd, objects_fd = objects_fd, None
                _copy_objects_dir_fd_v2(
                    source_objects_fd=transferred_fd,
                    dest_objects_dir=cas_dir / "objects",
                    tracker=tracker,
                    visited_dev_ino=set(),
                    depth=0,
                    budget=budget,
                )
            _copy_refs_fd_v2(
                common_dir_fd=git_dirs.common_dir_fd,
                git_dir_fd=git_dirs.git_dir_fd,
                dest_git_dir=cas_dir,
                tracker=tracker,
            )
        finally:
            if objects_fd is not None:
                os.close(objects_fd)
            os.close(git_dirs.common_dir_fd)
            os.close(git_dirs.git_dir_fd)

        _verify_pack_integrity_v2(cas_dir)

        marker = secrets.token_bytes(32)
        (cas_dir / _MARKER_FILENAME_V2).write_bytes(marker)

        try:
            run_bounded_git_v2(["rev-parse", "--git-dir"], cwd=cas_dir)
        except BoundedGitError as exc:
            raise TrustedObjectAuthorityError(
                TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2
            ) from exc

        authority = TrustedObjectAuthorityV2(
            cas_root=cas_dir, expected_marker=marker, _sentinel=_BUILD_SENTINEL_V2
        )
        yield authority
    finally:
        shutil.rmtree(cas_dir, ignore_errors=True)
