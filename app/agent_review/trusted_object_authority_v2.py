"""`#200-G1C` -- isolated immutable Git object-store authority.

## Predecessor and why it was refuted

PR #302 (`fix/200-g1-post-merge-codex-debt`, frozen forensic, preserved
unmerged, DO NOT reuse) tried to make a bounded git invocation against the
*live, hostile checkout* safe against lazy-fetch/partial-clone contamination
by **detecting** contamination after the fact:

1. `remote.origin.promisor=false` (hardcoded) -- covers only a remote
   literally named `origin`.
2. every `remote.*.promisor` boolean spelling -- `partialclonefilter` set
   with `promisor` entirely unset still enables the fetch.
3. before/after object-store snapshot (`BoundedGitSessionV2`) -- refuted
   twice more: a rejected call's fetched object outlives any single
   operation's trust boundary (a fresh top-level retry silently trusts it,
   `TEMPORAL_TRUST_EPOCH_MISMATCH`), and the session object itself was not
   bound to the repository it was used against (`None`-fielded or
   cross-repository construction silently accepted,
   `FORGEABLE_UNBOUND_PROOF_CARRIER`).

Every one of those four attempts shares a root cause: they treat the live,
mutable, hostile checkout as the trust root and try to *prove it wasn't
contaminated*, after already letting it run a command capable of being
contaminated. That strategy does not converge -- PR #302's own four-round
history is the falsifier corpus proving it (see `#303` for the full
disposition).

## This module's design -- Git as discovery, not trust root

```
hostile/mutable Git discovery (the target repo checkout)
        |
open/read exact local object bytes under bounded acquisition
        |
copy into a private, content-addressed, remote-less object authority
        |
retain a private capability bound to that authority only
        |
ALL later commit/tree/blob/history reads go through the capability,
never the live repo again
```

The acquisition step (`open_trusted_object_authority_v2`) never invokes
*any* git subcommand against the live repository at all -- not even one
that only reads path metadata. Locating the shared object-store directory
(worktree-aware: `.git`-file-vs-directory, `commondir`) is done by parsing
those few, small, fixed-format pointer files directly in Python; copying
objects and refs is a plain filesystem copy of whatever bytes are
*already physically present* on disk: loose objects, pack files, and
(recursively flattened, never chained) alternate object directories. A
partial clone's missing blobs are, definitionally, not physically present
-- they are simply absent from the copy, structurally, not detected and
rejected after the fact. There is no code path in this module that can ask
git to fetch anything, because no git process of any kind is ever started
against the live repository -- not even to read config, which a naive
`rev-parse --git-common-dir` call would still need to parse successfully
even though it never touches object content (a hostile or merely
malformed live `config` can make that parse fail for reasons that have
nothing to do with trust; avoiding it removes that failure mode too, not
just the security one).

The copy target is authored from scratch (a minimal, hand-built bare
repository skeleton) -- never by copying the live repository's own
`config`, `hooks/`, `info/`, or `logs/`. A hostile `config`,
`.git/info/grafts`, `.git/shallow` marker, or `git replace` ref in the live
repository is therefore never read into the authority at all; there is
nothing there to detect because there is nothing there to copy. This is
why most of the historical falsifier list (non-origin promisor remote, any
promisor boolean spelling, `partialclonefilter` alone, an actual lazy
fetch attempt, retry-after-rejection, process/session restart, hostile
config, `git replace`, an alternate object directory, a fake `git` earlier
in `PATH`) dissolves rather than needing a dedicated guard: none of those
mechanisms has anything left to act on once the authority is built.

## What still needs its own handling, and why

**Shallow history / a deleted or corrupted parent object / `.git/info/grafts`.**
These are not detected by a dedicated check either. The authority never
copies `.git/shallow`, `.git/info/grafts`, or any `refs/replace/*` ref, so
a commit whose real parent object is genuinely absent from the copied
object store (whether because the source was shallow, because a parent
object was corrupted/deleted, or because a graft/replace redirected it) is
walked *honestly* by `git rev-list` inside the authority: if the walk
cannot reach a parent object, it fails loudly (non-zero exit, a real git
diagnostic), never silently stopping at a synthetic boundary the authority
was never told about. `authorize_commit_for_execution_v2`
(`commit_derived_execution_identity_v2.py`) turns that loud failure into
`IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2` -- a positive graph proof
is required for both `AUTHORIZED TRUE` (the candidate is present in a
*successfully, completely* enumerated ancestor set) and `AUTHORIZED FALSE`
(the ancestor set was completely enumerated and the candidate is genuinely
absent from it); an incomplete walk is neither, and is refused.

**A linked worktree.** The filesystem-level resolution above follows a
linked worktree's `.git` FILE and its private git-dir's `commondir`
pointer to the *shared* object store (`objects/`, `refs/heads`,
`refs/tags`, `packed-refs`), not the worktree-private directory that also
holds `index` and per-worktree refs. This is PR #302's own round-3
point-fix, carried forward on its underlying insight, not its surrounding
(refuted) snapshot mechanism. Correction round 2 (independent human
review) sharpened this further: `HEAD` itself is worktree-PRIVATE, not
shared -- `_GitDirectoriesV2` keeps the private git-dir and the shared
common-dir as two genuinely distinct values precisely so `HEAD` can be
read from the former while everything else is read from the latter;
collapsing both into one value (round 1's shape) silently returned the
*main* worktree's `HEAD` for a caller that pointed `repo_root` at a
different, linked worktree.

**A forged/unbound capability.** `TrustedObjectAuthorityV2` cannot be
constructed except by `open_trusted_object_authority_v2`, which is the only
holder of the private build sentinel its `__init__` requires, and every
operation re-verifies a random marker file written into the authority's own
private directory at build time before doing anything else. A
caller-fabricated instance (no sentinel, or a sentinel-bearing instance
pointed at an arbitrary directory that was never built by this module) is
therefore either impossible to construct or fails its first operation.
There is also no method on this class that accepts an external `cwd`/path
argument naming *which* repository to operate against -- every operation
is against the exact directory this instance built for itself, so "a
session opened for repo A supplied to a call targeting repo B" has no call
shape left to express: there is no parameter to supply repo B's path to.

## What this module deliberately does NOT do

It does not reimplement Git's object/pack *format*. Loose objects and pack
files are copied as opaque bytes, verbatim; their content is never parsed
or reconstructed by this module. Correction round 2 (independent human
review) sharpened an overclaim in this section's own predecessor text: it
used to say object-identity verification was "delegated entirely" to
`cat-file`/`ls-tree` against the resulting authority -- true for a TREE's
shape, but false for a PACKED object's claimed identity, which ordinary
`cat-file` reads do not re-verify against the pack's own index (see the
now-fixed Finding 1 in `_verify_pack_integrity_v2`'s docstring). The actual
position is narrower and more precise: object *content interpretation*
(what bytes a commit/tree/blob's sha actually decodes to, once its identity
is trusted) is delegated to git's own reading primitives, never
re-implemented here; object *identity re-verification* (does this path's
claimed sha genuinely match this path's content) is this module's own,
explicit responsibility for both loose objects
(`_verify_loose_object_hash_v2`, byte-level, Python) and packed objects
(`_verify_pack_integrity_v2`, delegated to git's own `verify-pack`
plumbing rather than reimplementing pack/delta parsing).

It does not decide *materialisation faithfulness* (symlink/TOCTOU-safe
writing of a commit's tree onto disk) -- that is `#200-G1D` (issue #304), a
different, downstream layer this module does not conflate with. This
module's job stops at producing a trustworthy source for reads; what a
caller subsequently *writes* from those reads is that caller's own
concern.

## Threat scope

In scope: a hostile/mutable LIVE repository checkout under a DIFFERENT
privilege boundary than this process (a different UID, a network peer, or
a compromised checkout this process merely has read access to) -- lazy
fetch, promisor/partial-clone contamination, symlink escapes, forged loose
or packed object identity, hostile config/grafts/replace, alternates
pointing outside the repository, retry/restart contamination. All of
`#302`/`#306`'s falsifier corpus and this module's own two correction
rounds are about this boundary.

Out of scope, explicitly, matching the identical `host_arbitrary_code_
attacker` boundary already declared in `commit_derived_execution_identity_
v2.py` (the sibling module this one composes with -- not a new or
separate decision, an application of an existing one): a SAME-UID
adversary who can already write to this process's own filesystem
namespace while an authority is open. The capability's marker proves the
CAS directory was genuinely built by `open_trusted_object_authority_v2`;
it does NOT prove -- and this module does not claim it proves -- that
`objects/`/`refs/` under `trusted_repo_root` remain byte-identical to what
was verified at build time for the remainder of the `with` block's
lifetime against a same-UID writer. CAEM's own ADR 0012 predecessor
explicitly closes an analogous same-UID gap (`memfd` + seal + reopen
read-only), but for a categorically higher-assurance TCB: a detached
launcher producing attestations for consumption by parties who trust
neither this host nor its operator. This module's threat scope is the one
already declared by the module it composes with -- a same-UID attacker
does not need to race this authority's private `/tmp` directory when they
could tamper with the calling process directly, exactly as `host_arbitrary_
code_attacker` already reasons. A future phase that widens this process's
own trust boundary to include a same-UID adversary (the CAEM shape) is a
genuinely new TCB floor, carried forward as an explicit prerequisite for
whichever of `#200-G1B`/`#200-G5` needs it, not silently assumed here.

## CAEM design reference -- not authority for this repository

`config/caem/caem-3.0-f0.pin.json` in this repository pins CAEM 3.0 F0 with
`authority.authority_effect: "none"`. ADR 0011/0012 (quoted in #303) live in
`caem-3.0-c8`, past that pin, `maturity: candidate, published: false`. They
are read here as **design reference / prior art only** -- the trust-root
inversion this module implements ("never let a mutable discovery source be
the trust root") is the same shape ADR 0011 states for verifier/tool bytes
("resolve from a trusted local CAS by exact digest without PATH or
network") and ADR 0012 states for a launcher's discovery inode ("mutation
of the discovery inode after authentication cannot change the executed
bytes"). This module is an independent implementation of that shape for a
different subject (a Git object store, not a launcher/verifier binary), not
a consumer of CAEM's own machinery, and claims no CAEM authorization.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import shutil
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

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
# Correction round 1 (post-review, both lanes): a loose object's fanout
# path is git's OWN content-addressing claim -- "the sha1/sha256 of
# 'type size\0content' is this path" -- and nothing in the ordinary git
# read path used elsewhere in this codebase (`cat-file`, the `rev-list`
# walk) re-verifies that claim; only `git fsck --strict` does, and nothing
# here runs it. Raised when a loose object's decompressed content does not
# hash to the sha its path claims.
TRUSTED_OBJECT_AUTHORITY_OBJECT_HASH_MISMATCH_REASON_V2 = "trusted_object_authority_object_hash_mismatch"
# Correction round 1: a symlink anywhere under the live repository's
# objects/refs tree is refused outright rather than followed -- a
# legitimate git objects/refs tree never contains one.
TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2 = "trusted_object_authority_symlink_rejected"
# Correction round 1: an `objects/info/alternates` entry that does not even
# have the minimal structural shape of a real git object store (see
# `_looks_like_git_objects_directory_v2`) is refused rather than flattened
# in as if it were one.
TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2 = "trusted_object_authority_alternate_rejected"
# Correction round 2 (independent human review of round 1's own fix):
# ordinary git reads (`cat-file`, the `rev-list` walk `prove_ancestry`
# performs) do not re-verify a PACKED object's identity against its pack
# index entry -- only `git fsck --strict`/`git verify-pack` do. Raised when
# `git verify-pack`, run against the CAS itself after every pack/idx has
# been copied in, reports any pack as invalid.
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


def _read_regular_file_charged_v2(path: Path, tracker: _ObjectCopyBudgetTrackerV2) -> bytes:
    """Read one file's bytes under TWO correction-round-1 hardenings at once:

    1. **No-follow, TOCTOU-safe.** Opened with `O_NOFOLLOW`: a symlink at
       `path` -- including one planted *after* an earlier `is_symlink()`
       check found a regular file there (a listing-to-open race) -- fails
       to open at all, rather than being transparently followed. A
       legitimate git objects/refs tree never contains a symlink; nothing
       here needs to distinguish "attacker-planted" from "unexpected" --
       both are refused identically.
    2. **Budget charged from `fstat`, before any content is read into
       memory.** The prior design read the whole file with `read_bytes()`
       and charged the budget only afterwards -- reactive, not preventive:
       a single oversized file (reachable, pre-hardening, via a followed
       symlink to an arbitrary host file) would be fully loaded into memory
       before the budget check ever ran. `fstat` on the already-open,
       already-non-symlink descriptor is charged first; a budget-exceeding
       size is refused before a single content byte is read.

    A short read relative to the size `fstat` reported (a concurrent
    truncation) is also refused -- the budget check must have seen the
    same size the actual read produced, or it protected nothing.
    """
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
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


def _verify_loose_object_hash_v2(*, expected_sha_hex: str, compressed: bytes) -> None:
    """Correction round 1 (Lane B, independently reproduced twice through
    the real `authorize_commit_for_execution_v2` production path): a loose
    object's fanout directory + filename IS git's own content-addressing
    claim about that object -- "the sha1 (or sha256, for a sha256-format
    repository) of `type size\\0content` is this exact path". Ordinary git
    reads (`cat-file`, the `rev-list` walk this module's own
    `prove_ancestry` performs) do not re-verify that claim for loose
    objects; only `git fsck --strict` does, and nothing in this pipeline
    runs it. Without this check, overwriting a loose object's bytes in the
    LIVE repository at a fixed, predictable path (no symlink, no alternates,
    no directory trick -- just different content at the same content-addressed
    filename) got copied into the "trusted" CAS byte-for-byte and trusted
    exactly as if it were the real object the path claims to be: a genuine
    ancestor's commit object overwritten this way silently flips
    `authorized=True` to a confident `authorized=False`, and a forged
    `parent` line spliced into an existing commit's content (tree/author/
    committer otherwise untouched) silently flips an unrelated,
    never-integrated commit to `authorized=True`. Both were reproduced
    against this exact production call path before this check existed.

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


def _read_gitdir_pointer_v2(dotgit_file: Path) -> Path:
    """Parse a `.git` FILE's `gitdir: <path>` line (the shape a linked
    worktree's checkout root has -- its real per-worktree git directory
    lives elsewhere)."""
    try:
        raw = dotgit_file.read_text(encoding="utf-8", errors="surrogateescape")
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2) from exc
    first_line = raw.splitlines()[0] if raw else ""
    prefix = "gitdir:"
    if not first_line.startswith(prefix):
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
    pointed = Path(first_line[len(prefix) :].strip())
    if not pointed.is_absolute():
        pointed = (dotgit_file.parent / pointed).resolve()
    return pointed


@dataclass(frozen=True)
class _GitDirectoriesV2:
    """`git_dir` (worktree-PRIVATE: `HEAD`, index, per-worktree refs) and
    `common_dir` (SHARED across every worktree: `objects/`, `refs/heads`,
    `refs/tags`, `packed-refs`) -- identical for an ordinary, non-worktree
    repository, genuinely different for a linked worktree. Correction
    round 2 (independent human review of round 1's own fix): round 1
    collapsed both into a single `common_dir` and read `HEAD` from it,
    which is the MAIN worktree's `HEAD`, not necessarily the one
    `repo_root` (a possibly-linked worktree) actually points at.
    """

    git_dir: Path
    common_dir: Path


def _resolve_git_directories_v2(repo_root: Path) -> _GitDirectoriesV2:
    """Resolve both the worktree-private and shared git directories,
    worktree-aware -- by PLAIN FILESYSTEM READS, never a git invocation.

    Earlier drafts of this module resolved this via a bounded
    `git rev-parse --git-common-dir` call against the live repository. That
    is pure path metadata and cannot itself trigger a lazy fetch, but it
    still requires git to successfully parse the live repository's
    `config` -- and a hostile or merely malformed `config` can make that
    invocation fail outright, for reasons that have nothing to do with
    trust. Since the git-dir/common-dir relationship is itself a documented,
    simple, git-version-independent file format (a `.git` file's single
    `gitdir: <path>` line, and a per-worktree git-dir's own `commondir`
    file), resolving it directly removes the live repository's `config`
    from this module's read path entirely -- there is now no git
    invocation of any kind against the live repository, only plain file
    reads of small, fixed-format pointer files.
    """
    dotgit = repo_root / ".git"
    if dotgit.is_dir():
        git_dir = dotgit
    elif dotgit.is_file():
        git_dir = _read_gitdir_pointer_v2(dotgit)
    elif (repo_root / "HEAD").is_file() and (repo_root / "objects").is_dir():
        # `repo_root` is itself a bare git directory.
        git_dir = repo_root
    else:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)

    if not git_dir.is_dir():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)

    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            raw = commondir_file.read_text(encoding="utf-8", errors="surrogateescape").strip()
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2) from exc
        common_dir = Path(raw)
        if not common_dir.is_absolute():
            common_dir = (git_dir / common_dir).resolve()
    else:
        common_dir = git_dir

    if not common_dir.is_dir():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)
    return _GitDirectoriesV2(git_dir=git_dir, common_dir=common_dir)


def _copy_pack_file_v2(source: Path, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one pack file (`.pack`/`.idx`/`.rev`/`.bitmap`/...), no-follow
    and pre-charged (see `_read_regular_file_charged_v2`).

    NAMED LIMITATION, stated plainly rather than silently assumed: unlike
    loose objects (see `_verify_loose_object_hash_v2`), individual objects
    inside a pack are NOT independently re-hashed here -- doing so would
    mean parsing git's pack/delta format, which this module deliberately
    does not reimplement (see the module docstring's "what this module
    deliberately does NOT do"). A pack's own trailing checksum and its
    `.idx` CRC32s are git-internal integrity aids consulted by git itself
    when it later reads from the copied pack (via the existing, unchanged
    `bounded_git_v2`/`git_commit_subject_v2` primitives against the CAS),
    not by this module. This is a real, narrower residual surface than the
    loose-object path and is not claimed to be closed by this correction.
    """
    data = _read_regular_file_charged_v2(source, tracker)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.read_bytes() != data:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2)
        return
    dest.write_bytes(data)


def _copy_loose_object_v2(
    source: Path, dest: Path, *, expected_sha_hex: str, tracker: _ObjectCopyBudgetTrackerV2
) -> None:
    """Copy one loose object, no-follow/pre-charged, AND content-hash
    verified against the exact sha its own fanout path claims (see
    `_verify_loose_object_hash_v2` -- this is the correction-round-1 fix
    for both of Lane B's independently-reproduced findings)."""
    data = _read_regular_file_charged_v2(source, tracker)
    _verify_loose_object_hash_v2(expected_sha_hex=expected_sha_hex, compressed=data)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if dest.read_bytes() != data:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2)
        return
    dest.write_bytes(data)


def _copy_named_file_v2(source: Path, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one NAMED-POINTER file (`HEAD`, a ref, `packed-refs`),
    no-follow and pre-charged.

    Unlike an object file, the destination name here is not derived from
    the content -- an existing destination (e.g. this module's own
    hand-authored placeholder `HEAD`) is expected and simply overwritten,
    never treated as a collision.
    """
    data = _read_regular_file_charged_v2(source, tracker)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _looks_like_git_objects_directory_v2(objects_dir: Path) -> bool:
    """Minimal structural sanity check for an ALTERNATE objects directory
    (never applied to the primary/top-level source, which is already
    reached through `_resolve_git_directories_v2`'s own validation).

    Real git always keeps `objects/` as a direct sibling of `HEAD`, in
    both a bare repository root and a non-bare `.git` directory alike --
    this is a load-bearing structural fact about every git repository
    shape, not a heuristic invented for this check.

    Explicitly NOT claimed: that this proves the directory is a genuine,
    legitimately-related git repository. An attacker who already has
    write access to the live repository's `objects/info/alternates` file
    (in scope for this module's threat model) could construct a
    throwaway directory with both a `HEAD` file and an `objects/`
    subdirectory purely to pass this check. What this check closes is the
    much lower-effort degenerate case -- pointing alternates at an
    unrelated, ordinary host directory (`/etc`, a user's home directory)
    that was never shaped like a repository at all and was not
    necessarily even authored by the same attacker. Every object actually
    admitted from a passing alternate is still subject to the identical
    loose-object hash verification as everything else -- this check is
    defense in depth layered in front of that verification, never a
    substitute for it.

    The `HEAD` sibling itself is checked no-follow (correction round 2):
    a symlinked `HEAD` pointing at any other file that happens to exist
    would otherwise let an ordinary, non-repository-shaped directory pass
    this heuristic for free.
    """
    head_sibling = objects_dir.parent / "HEAD"
    if head_sibling.is_symlink():
        return False
    return head_sibling.is_file()


def _parse_alternates_v2(
    alternates_path: Path, *, owning_objects_dir: Path, tracker: _ObjectCopyBudgetTrackerV2
) -> list[Path]:
    """Each line is a path to another repository's `objects/` directory.

    Relative entries are relative to the *owning* objects directory itself
    (git's own documented convention for `objects/info/alternates`), not to
    the current working directory or to this module's cwd.

    The alternates file itself is read no-follow: a symlinked alternates
    file is refused outright (a legitimate one never is), not silently
    treated as absent.
    """
    if alternates_path.is_symlink():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
    if not alternates_path.is_file():
        return []
    raw = _read_regular_file_charged_v2(alternates_path, tracker)
    resolved: list[Path] = []
    for raw_line in raw.split(b"\n"):
        line = raw_line.decode("utf-8", "surrogateescape").strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line)
        if not candidate.is_absolute():
            candidate = owning_objects_dir / candidate
        resolved.append(candidate)
    return resolved


def _safe_scandir_no_symlinks_v2(directory: Path) -> list[os.DirEntry]:
    """Enumerate one directory, refusing (never silently skipping or
    transparently following) any entry that is itself a symlink.

    Correction round 1 (Lane A, independently reproduced): a legitimate
    git objects/refs tree never contains a symlink -- loose objects,
    packs, and refs are always regular files/directories. The prior
    design's directory walk used `Path.iterdir()` results with
    `.is_dir()`/`.is_file()`, both of which follow symlinks by default;
    a hostile checkout with e.g. `.git/objects/de` or
    `.git/refs/heads/<name>` symlinked to an arbitrary host path had its
    target's bytes copied into the "trusted" CAS verbatim, directly
    falsifying this module's own docstring claim of never reading outside
    the repository's git directory. `os.DirEntry.is_symlink()` does not
    itself follow the link (a `stat`, not an `lstat`-then-open), so this
    check is safe to perform before anything downstream ever opens the
    entry.
    """
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    for entry in entries:
        try:
            is_symlink = entry.is_symlink()
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
        if is_symlink:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
    return entries


def _walk_regular_files_no_symlinks_v2(directory: Path) -> list[Path]:
    """Recursive, symlink-refusing equivalent of `Path.rglob("*")` (which
    follows symlinked directories during traversal and is not usable here
    for the same reason `_safe_scandir_no_symlinks_v2` replaced
    `Path.iterdir()` above)."""
    discovered: list[Path] = []

    def _walk(current: Path) -> None:
        for entry in _safe_scandir_no_symlinks_v2(current):
            entry_path = Path(entry.path)
            if entry.is_dir(follow_symlinks=False):
                _walk(entry_path)
            elif entry.is_file(follow_symlinks=False):
                discovered.append(entry_path)

    _walk(directory)
    return discovered


def _copy_objects_dir_v2(
    *,
    source_objects_dir: Path,
    dest_objects_dir: Path,
    tracker: _ObjectCopyBudgetTrackerV2,
    visited_real_paths: set[str],
    depth: int,
    budget: _ObjectCopyBudgetV2,
) -> None:
    """Copy every object physically present under one `objects/` directory,
    then recursively flatten any alternates it declares into the SAME
    destination -- never chained, never written back out as a second
    `objects/info/alternates` in the authority. The authority ends up
    self-contained: nothing under it points outside it.

    Deliberately not a git invocation of any kind. Whatever is missing
    (a partial clone's filtered-out blobs, a shallow clone's pruned
    ancestors) is, definitionally, not a file on disk here -- it is simply
    absent from the copy, not detected-and-rejected after the fact.
    """
    # Correction round 2 (independent human review, Finding 2): checked
    # BEFORE `.is_dir()`, which follows a symlink at `source_objects_dir`
    # itself -- `_safe_scandir_no_symlinks_v2` only guards entries found
    # once a walk has already been entered with a real (non-symlink) root,
    # never the root path handed to it. A symlinked primary or alternate
    # `objects/` root had its target directory's contents silently copied
    # into the CAS as if they belonged to the repository.
    if source_objects_dir.is_symlink():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
    if not source_objects_dir.is_dir():
        return
    real = os.path.realpath(source_objects_dir)
    if real in visited_real_paths:
        return
    if depth > budget.max_alternate_depth:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)
    if depth > 0 and not _looks_like_git_objects_directory_v2(source_objects_dir):
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ALTERNATE_REJECTED_REASON_V2)
    visited_real_paths.add(real)

    # Loose objects: two-hex-prefix fanout directories only. Content-hash
    # verified against the exact sha the fanout-dir-name + filename claims.
    for child in _safe_scandir_no_symlinks_v2(source_objects_dir):
        if (
            child.is_dir(follow_symlinks=False)
            and len(child.name) == 2
            and all(c in "0123456789abcdef" for c in child.name)
        ):
            for loose_object in _safe_scandir_no_symlinks_v2(Path(child.path)):
                if loose_object.is_file(follow_symlinks=False):
                    expected_sha_hex = child.name + loose_object.name
                    _copy_loose_object_v2(
                        Path(loose_object.path),
                        dest_objects_dir / child.name / loose_object.name,
                        expected_sha_hex=expected_sha_hex,
                        tracker=tracker,
                    )

    # Pack files: copied verbatim, whatever extension git ships with them
    # -- see `_copy_pack_file_v2` for the named residual limitation here.
    pack_dir = source_objects_dir / "pack"
    # Correction round 2, Finding 2 (same reasoning as `source_objects_dir`
    # above): checked before `.is_dir()`, not only guarded once a walk has
    # already been entered with this as its root. In the CURRENT control
    # flow this specific check is provably redundant -- the loose-object
    # loop above already calls `_safe_scandir_no_symlinks_v2(
    # source_objects_dir)`, which blanket-rejects ANY symlinked entry under
    # `objects/`, "pack" included, before this line is ever reached
    # (mutation-tested: disabling this exact line leaves the whole test
    # suite green, entirely via that upstream catch). Kept anyway,
    # deliberately, as an explicit, self-documenting guard at the exact
    # root the finding named -- not a guess this module is entitled to
    # make about the upstream scan's enumeration order or blanket-ness
    # never changing under a future refactor.
    if pack_dir.is_symlink():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
    if pack_dir.is_dir():
        for pack_file in _safe_scandir_no_symlinks_v2(pack_dir):
            if pack_file.is_file(follow_symlinks=False):
                _copy_pack_file_v2(
                    Path(pack_file.path), dest_objects_dir / "pack" / pack_file.name, tracker
                )

    # Alternates: recursively flattened INTO this same destination. The
    # authority's own `objects/info/` is never populated with an alternates
    # file -- there is nothing in this module's output for a later reader
    # to chase.
    alternates_path = source_objects_dir / "info" / "alternates"
    for alternate_objects_dir in _parse_alternates_v2(
        alternates_path, owning_objects_dir=source_objects_dir, tracker=tracker
    ):
        _copy_objects_dir_v2(
            source_objects_dir=alternate_objects_dir,
            dest_objects_dir=dest_objects_dir,
            tracker=tracker,
            visited_real_paths=visited_real_paths,
            depth=depth + 1,
            budget=budget,
        )


def _copy_refs_v2(
    *, common_git_dir: Path, private_git_dir: Path, dest_git_dir: Path, tracker: _ObjectCopyBudgetTrackerV2
) -> None:
    """Copy `refs/heads/`, `refs/tags/`, and `packed-refs` verbatim from
    `common_git_dir` (SHARED across every worktree), and `HEAD` from
    `private_git_dir` (worktree-PRIVATE -- identical to `common_git_dir`
    for an ordinary, non-worktree repository; genuinely different for a
    linked worktree, see `_GitDirectoriesV2`).

    Correction round 2 (independent human review): reading `HEAD` from the
    common dir, as round 1 did, silently returns the MAIN worktree's `HEAD`
    for a caller that pointed `repo_root` at a linked worktree with its own,
    different, checked-out commit -- `materialise_commit_subject_v2(ref=
    "HEAD")` would then materialise the wrong commit, with no error.

    Deliberately excludes `refs/replace/*` (git replace is separately
    neutralised by every bounded git invocation's `--no-replace-objects`,
    and simply never exists in the authority at all here), `refs/notes/*`,
    and `refs/remotes/*` -- none of those are needed to resolve a commit sha
    or a branch/tag name, and copying them would be scope creep with no
    corresponding read path. Also excludes any other worktree-private ref
    namespace (e.g. `refs/bisect/*`) beyond `HEAD` itself -- not
    reproduced as a live gap, and not fabricated as a fix for one.
    """
    for namespace in ("heads", "tags"):
        source_dir = common_git_dir / "refs" / namespace
        # Correction round 2 (independent human review, Finding 2): a
        # `.is_dir()` check on the ROOT of a walk follows a symlink at
        # that root -- `_safe_scandir_no_symlinks_v2`/
        # `_walk_regular_files_no_symlinks_v2` only guard entries found
        # *during* a walk they have already been entered with, not the
        # root path handed to them. `refs/heads`/`refs/tags` symlinked to
        # an arbitrary host directory had that directory's contents
        # copied into the CAS as if they were real refs.
        if source_dir.is_symlink():
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
        if not source_dir.is_dir():
            continue
        for path in _walk_regular_files_no_symlinks_v2(source_dir):
            relative = path.relative_to(common_git_dir)
            _copy_named_file_v2(path, dest_git_dir / relative, tracker)

    packed_refs = common_git_dir / "packed-refs"
    if packed_refs.is_symlink():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_SYMLINK_REJECTED_REASON_V2)
    if packed_refs.is_file():
        kept_lines: list[bytes] = []
        packed_refs_bytes = _read_regular_file_charged_v2(packed_refs, tracker)
        for raw_line in packed_refs_bytes.split(b"\n"):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(b"#"):
                continue
            if stripped.startswith(b"^"):
                # Peeled-tag annotation line -- keep only if the preceding
                # kept line was itself kept (append unconditionally here,
                # trailing an accepted ref line; drop stray leading peels).
                if kept_lines:
                    kept_lines.append(stripped)
                continue
            # Correction round 1 (Lane A P2): anchored to the actual
            # ref-name FIELD (everything after the first space), not a
            # raw substring search anywhere in the line -- the prior
            # `b" refs/heads/" in stripped` form would also have kept a
            # line whose SHA or ref name merely happened to *contain* that
            # text elsewhere.
            parts = stripped.split(b" ", 1)
            if len(parts) != 2:
                continue
            object_id, ref_name = parts
            if len(object_id) not in (40, 64) or any(
                c not in b"0123456789abcdef" for c in object_id
            ):
                continue
            if ref_name.startswith(b"refs/heads/") or ref_name.startswith(b"refs/tags/"):
                kept_lines.append(stripped)
        if kept_lines:
            dest_packed_refs = dest_git_dir / "packed-refs"
            data = b"\n".join(kept_lines) + b"\n"
            tracker.charge(len(data))
            dest_packed_refs.write_bytes(data)

    # From `private_git_dir`, NOT `common_git_dir` -- see this function's
    # docstring. Identical to `common_git_dir` for a non-worktree repo.
    head_path = private_git_dir / "HEAD"
    if head_path.is_file():
        _copy_named_file_v2(head_path, dest_git_dir / "HEAD", tracker)


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
    """Correction round 2 (independent human review, Finding 1): ordinary
    git reads (`cat-file`, the `rev-list` walk `prove_ancestry` performs)
    trust a pack's own `.idx` object-name table without re-verifying it --
    a pack containing object B's real compressed bytes, paired with an
    `.idx` whose name table and recomputed checksum claim that same byte
    range is object A instead, makes `git cat-file -p <A>` return B's
    bytes under A's identity. `git fsck --strict --full` catches this;
    ordinary reads do not. This is the packed analogue of the loose-object
    finding correction round 1 already closed
    (`_verify_loose_object_hash_v2`) -- reimplementing an equivalent
    byte-level check for the pack/delta format would mean reimplementing
    git's pack format, explicitly out of this module's scope (see the
    module docstring's "what this module deliberately does NOT do").
    Delegating to git's own `verify-pack` -- a bounded, local,
    network-incapable plumbing command, run here against the CAS itself,
    never the live repo, strictly after every pack/idx has already been
    copied in and strictly before the authority is ever yielded to a
    caller -- closes the same property without that reimplementation.

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
    build sentinel this class's `__init__` requires.

    `frozen=True` (correction round 1, Lane A P2): every sibling
    dataclass in this package's threat-scoped modules is frozen; this one
    was not, which meant `_cas_root`/`_expected_marker` could be reassigned
    post-construction by anything holding a reference, inconsistent with
    this class's own "unforgeable" docstring claim even though nothing in
    the current call graph exercises that mutability.
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

        Scope note (correction round 2 -- see the module docstring's
        "Threat scope"): every object/ref under this path was positively
        verified (content-hash for loose objects, `verify-pack` for packed
        objects) at build time, against a LIVE repository under a
        different privilege boundary. That is this module's threat scope.
        It is NOT a claim that a SAME-UID writer to this process's own
        filesystem namespace cannot subsequently alter these bytes before
        a caller finishes reading them -- that boundary is explicitly out
        of scope here, matching `commit_derived_execution_identity_v2.py`'s
        own declared `host_arbitrary_code_attacker` exclusion.
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
    """
    if not Path(repo_root).is_dir():
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2)

    git_dirs = _resolve_git_directories_v2(Path(repo_root))
    budget = _ObjectCopyBudgetV2(
        max_total_bytes=max_total_bytes,
        max_object_count=max_object_count,
        max_alternate_depth=max_alternate_depth,
    )
    tracker = _ObjectCopyBudgetTrackerV2(budget)

    cas_dir = Path(tempfile.mkdtemp(prefix="agent_review_g1c_cas_v2_"))
    try:
        _write_minimal_bare_skeleton_v2(cas_dir)
        _copy_objects_dir_v2(
            source_objects_dir=git_dirs.common_dir / "objects",
            dest_objects_dir=cas_dir / "objects",
            tracker=tracker,
            visited_real_paths=set(),
            depth=0,
            budget=budget,
        )
        _copy_refs_v2(
            common_git_dir=git_dirs.common_dir,
            private_git_dir=git_dirs.git_dir,
            dest_git_dir=cas_dir,
            tracker=tracker,
        )
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
