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
pointer to the *shared* object store, not the worktree-private directory
that holds only `HEAD`, `index`, and per-worktree refs. This is PR #302's
own round-3 point-fix,
carried forward on its underlying insight, not its surrounding (refuted)
snapshot mechanism.

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

It does not reimplement Git's object/pack format. Loose objects and pack
files are copied as opaque bytes, verbatim; their content is never parsed
or reconstructed by this module -- verification that the copied bytes are
what they claim to be is delegated entirely to the *existing*, unchanged
`bounded_git_v2`/`git_commit_subject_v2` reading primitives running
against the resulting authority (git's own `cat-file`/`ls-tree` are
authoritative for object identity; re-deriving that independently in pure
Python would be reimplementing Git, which is explicitly out of scope).

It does not decide *materialisation faithfulness* (symlink/TOCTOU-safe
writing of a commit's tree onto disk) -- that is `#200-G1D` (issue #304), a
different, downstream layer this module does not conflate with. This
module's job stops at producing a trustworthy source for reads; what a
caller subsequently *writes* from those reads is that caller's own
concern.

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
import os
import secrets
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.agent_review.bounded_git_v2 import BoundedGitError, run_bounded_git_v2

__all__ = [
    "TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_ANCESTRY_UNDETERMINED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2",
    "TRUSTED_OBJECT_AUTHORITY_REPOSITORY_UNUSABLE_REASON_V2",
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


def _resolve_git_common_dir_v2(repo_root: Path) -> Path:
    """The shared object-store directory, worktree-aware -- resolved by
    PLAIN FILESYSTEM READS, never a git invocation.

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
    return common_dir


def _copy_file_bytes_v2(source: Path, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one CONTENT-ADDRESSED object file (loose object or pack file).

    Only used for paths under `objects/`, where the destination name is
    itself derived from the content's own hash -- so if `dest` already
    exists (copied from the primary store or an earlier alternate in the
    flattened chain), its bytes are expected to be identical by
    construction, and any difference can only mean a genuine hash
    collision. Refused rather than silently keeping whichever copy landed
    first. Not appropriate for named-pointer files (`HEAD`, refs) where two
    sources legitimately differing is not a collision at all -- see
    `_copy_named_file_v2` for those.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = source.read_bytes()
    except OSError as exc:
        # A concurrent writer to the LIVE repository removed or replaced
        # this object between enumeration and read (TOCTOU). Acquisition
        # from a moving target is refused outright rather than silently
        # copying a partial/incoherent snapshot -- a caller that needs a
        # copy can simply retry, and a fresh retry builds an entirely new,
        # independent authority (see the module docstring's "no persistent
        # state" property).
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    tracker.charge(len(data))
    if dest.exists():
        if dest.read_bytes() != data:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_OBJECT_COLLISION_REASON_V2)
        return
    dest.write_bytes(data)


def _copy_named_file_v2(source: Path, dest: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy one NAMED-POINTER file (`HEAD`, a ref, `packed-refs`).

    Unlike an object file, the destination name here is not derived from
    the content -- an existing destination (e.g. this module's own
    hand-authored placeholder `HEAD`) is expected and simply overwritten,
    never treated as a collision.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
    tracker.charge(len(data))
    dest.write_bytes(data)


def _parse_alternates_v2(alternates_path: Path, *, owning_objects_dir: Path) -> list[Path]:
    """Each line is a path to another repository's `objects/` directory.

    Relative entries are relative to the *owning* objects directory itself
    (git's own documented convention for `objects/info/alternates`), not to
    the current working directory or to this module's cwd.
    """
    try:
        raw = alternates_path.read_bytes()
    except OSError:
        return []
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


def _safe_iterdir_v2(directory: Path) -> list[Path]:
    """`Path.iterdir()`, but a concurrent writer removing/replacing the
    directory mid-enumeration raises a typed refusal instead of a raw
    `OSError` escaping this module's otherwise-typed error surface."""
    try:
        return list(directory.iterdir())
    except OSError as exc:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc


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
    if not source_objects_dir.is_dir():
        return
    real = os.path.realpath(source_objects_dir)
    if real in visited_real_paths:
        return
    if depth > budget.max_alternate_depth:
        raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_BUDGET_EXCEEDED_REASON_V2)
    visited_real_paths.add(real)

    # Loose objects: two-hex-prefix fanout directories only.
    for child in _safe_iterdir_v2(source_objects_dir):
        if child.is_dir() and len(child.name) == 2 and all(c in "0123456789abcdef" for c in child.name):
            for loose_object in _safe_iterdir_v2(child):
                if loose_object.is_file():
                    _copy_file_bytes_v2(
                        loose_object, dest_objects_dir / child.name / loose_object.name, tracker
                    )

    # Pack files: copied verbatim, whatever extension git ships with them.
    pack_dir = source_objects_dir / "pack"
    if pack_dir.is_dir():
        for pack_file in _safe_iterdir_v2(pack_dir):
            if pack_file.is_file():
                _copy_file_bytes_v2(pack_file, dest_objects_dir / "pack" / pack_file.name, tracker)

    # Alternates: recursively flattened INTO this same destination. The
    # authority's own `objects/info/` is never populated with an alternates
    # file -- there is nothing in this module's output for a later reader
    # to chase.
    alternates_path = source_objects_dir / "info" / "alternates"
    for alternate_objects_dir in _parse_alternates_v2(alternates_path, owning_objects_dir=source_objects_dir):
        _copy_objects_dir_v2(
            source_objects_dir=alternate_objects_dir,
            dest_objects_dir=dest_objects_dir,
            tracker=tracker,
            visited_real_paths=visited_real_paths,
            depth=depth + 1,
            budget=budget,
        )


def _copy_refs_v2(*, source_git_dir: Path, dest_git_dir: Path, tracker: _ObjectCopyBudgetTrackerV2) -> None:
    """Copy `refs/heads/`, `refs/tags/`, `packed-refs`, and `HEAD` verbatim.

    Deliberately excludes `refs/replace/*` (git replace is separately
    neutralised by every bounded git invocation's `--no-replace-objects`,
    and simply never exists in the authority at all here), `refs/notes/*`,
    and `refs/remotes/*` -- none of those are needed to resolve a commit sha
    or a branch/tag name, and copying them would be scope creep with no
    corresponding read path.
    """
    for namespace in ("heads", "tags"):
        source_dir = source_git_dir / "refs" / namespace
        if not source_dir.is_dir():
            continue
        try:
            paths = list(source_dir.rglob("*"))
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
        for path in paths:
            if path.is_file():
                relative = path.relative_to(source_git_dir)
                _copy_named_file_v2(path, dest_git_dir / relative, tracker)

    packed_refs = source_git_dir / "packed-refs"
    if packed_refs.is_file():
        kept_lines: list[bytes] = []
        try:
            packed_refs_bytes = packed_refs.read_bytes()
        except OSError as exc:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_ACQUISITION_FAILED_REASON_V2) from exc
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
            if b" refs/heads/" in stripped or b" refs/tags/" in stripped:
                kept_lines.append(stripped)
        if kept_lines:
            dest_packed_refs = dest_git_dir / "packed-refs"
            data = b"\n".join(kept_lines) + b"\n"
            tracker.charge(len(data))
            dest_packed_refs.write_bytes(data)

    head_path = source_git_dir / "HEAD"
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


@dataclass
class TrustedObjectAuthorityV2:
    """The sole handle through which its own bound object store can be
    read. Never accepts an external path/cwd on any of its operations --
    there is no parameter shape by which a caller could point one instance
    at a different repository, and no way to construct a genuine instance
    except by `open_trusted_object_authority_v2`, which alone holds the
    build sentinel this class's `__init__` requires.
    """

    _cas_root: Path
    _expected_marker: bytes

    def __init__(self, *, cas_root: Path, expected_marker: bytes, _sentinel: object) -> None:
        if _sentinel is not _BUILD_SENTINEL_V2:
            raise TrustedObjectAuthorityError(TRUSTED_OBJECT_AUTHORITY_FORGED_CAPABILITY_REASON_V2)
        self._cas_root = cas_root
        self._expected_marker = expected_marker

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
        """The private, remote-less, immutable-for-the-duration-of-this-call
        copy's root. Re-verified on every access -- intended for immediate
        use as the `repo_root` argument to the existing, unchanged
        `git_commit_subject_v2`/`bounded_git_v2` reading primitives, not for
        storage or reuse beyond this authority's own lifetime.
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

    git_common_dir = _resolve_git_common_dir_v2(Path(repo_root))
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
            source_objects_dir=git_common_dir / "objects",
            dest_objects_dir=cas_dir / "objects",
            tracker=tracker,
            visited_real_paths=set(),
            depth=0,
            budget=budget,
        )
        _copy_refs_v2(source_git_dir=git_common_dir, dest_git_dir=cas_dir, tracker=tracker)

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
