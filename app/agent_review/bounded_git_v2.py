"""`#200-G1` -- the only sanctioned way this primitive invokes git.

Ported from the `#200-F` reconstruction (`operational_bounded_git_v2.py`,
commit `5703e5b` on the frozen-forensic branch
`feat/200-f-derivable-operational-boundary`) **with revalidation**, not
inherited. Surviving adversarial review there qualifies nothing here; this
module has its own new tests under `tests/agent_review/`.

## Why this exists

A naive `subprocess.run(["git", ...])` inherits the caller's whole
environment and resolves `git` through the caller's `PATH`. Both are
attacker-controllable in the threat scope this primitive defends
(`hostile_environment`, `ordinary_caller_forgery`):

* a `git` planted earlier on `PATH` would run instead of the real one;
* ambient `GIT_*` variables would change what git does (alternate object
  directories, replacement refs, external diff/filter hooks, ...).

## The two properties

**Allowlist, never blacklist.** The child receives exactly the variables
built here and nothing else copied from `os.environ`. A blacklist has to
enumerate every dangerous `GIT_*` name; the first one anybody forgets is the
hole.

**Fixed executable resolution.** `git` is resolved against `os.defpath`
(the platform's own default, e.g. `/bin:/usr/bin` on POSIX), never the
caller's `PATH`, and exec'd by that absolute path.

## Configuration, neutralised at the command line

`-c` beats every config file, so system/global/repository config cannot
re-enable what is switched off here. `--no-replace-objects` means a `git
replace` ref cannot substitute a different tree for the commit this module
reads.

Deliberately *not* claimed: this does not make git safe against a hostile
repository in general. It makes the specific, enumerated behaviours in the
docstrings below unreachable, and the test corpus states which.

## Offline is enforced by OUTCOME, not by enumerating causes

`#200-G1-PM` (post-merge Codex review debt reconciliation, and its own two
post-review recurrences on this same corrective PR): this module's
original defense against lazy fetch from a partial-clone/promisor remote
was a `-c`/config-inspection preflight -- first `remote.origin.promisor
=false` alone, then a check of every `remote.*.promisor` value in every
legal git-boolean spelling. Both were falsified in turn by a different,
independently-discovered marker (`remote.origin.partialclonefilter`
surviving `promisor` being unset entirely). Enumerating the config keys or
markers that COULD enable a fetch does not converge -- git's own
partial-clone machinery is not one switch, and there is no way to have
confidence any such enumeration is exhaustive.

`run_bounded_git_v2` therefore does not try to enumerate causes at all.
Every invocation is bracketed by a before/after snapshot of the object
store (`_object_store_snapshot_v2`); if anything new appears, the
invocation fails closed with `BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_
REASON_V2`, regardless of which config key or marker made the fetch
possible. This cannot PREVENT the underlying git process from attempting
the fetch -- only detect that it happened -- but nothing from an
invocation whose object store changed is ever treated as trustworthy. The
static `-c` overrides for `remote.origin.promisor` and `gc.auto` below
remain as cheap, harmless first-line defense for the common cases; they
are not this module's actual authority for the property.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BOUNDED_GIT_COMMAND_FAILED_REASON_V2",
    "BOUNDED_GIT_IO_FAILED_REASON_V2",
    "BOUNDED_GIT_UNAVAILABLE_REASON_V2",
    "BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2",
    "BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2",
    "BoundedGitError",
    "BoundedGitSessionV2",
    "bounded_git_environment_v2",
    "open_bounded_git_session_v2",
    "resolve_trusted_git_absolute_path_v2",
    "run_bounded_git_v2",
]


BOUNDED_GIT_UNAVAILABLE_REASON_V2 = "bounded_git_unavailable"
BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2 = "bounded_git_worktree_unusable"
BOUNDED_GIT_IO_FAILED_REASON_V2 = "bounded_git_io_failed"
BOUNDED_GIT_COMMAND_FAILED_REASON_V2 = "bounded_git_command_failed"
BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2 = "bounded_git_unexpected_object_store_write"

#: The platform's own default search path. Not derived from
#: `os.environ["PATH"]`, which is precisely the value a caller can poison.
_TRUSTED_GIT_SEARCH_PATH_V2 = os.defpath

#: Applied to every invocation. Each entry disables a mechanism by which a
#: repository's own content could influence what git does while it is read.
#: `remote.origin.promisor=false` and `gc.auto=0` remain here as cheap,
#: harmless defense-in-depth for the common/simple cases -- but neither is
#: this primitive's actual protection against lazy fetch or unexpected
#: repacking; see `_object_store_snapshot_v2` for why enumerating
#: config-key markers was abandoned as the authority for that.
_BOUNDED_GIT_CONFIG_ARGUMENTS_V2 = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "filter.lfs.smudge=",
    "-c", "filter.lfs.process=",
    "-c", "filter.lfs.required=false",
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.file.allow=user",
    "-c", "remote.origin.promisor=false",
    "-c", "core.attributesFile=/dev/null",
    "-c", "gc.auto=0",
)


class BoundedGitError(ValueError):
    """A bounded git invocation could not be performed or did not succeed.

    Content-free `reason_code` only: stderr can contain absolute paths and,
    for some subcommands, file content, so it is never attached.
    """

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def resolve_trusted_git_absolute_path_v2() -> str:
    """Find git without consulting anything the caller can influence."""
    resolved = shutil.which("git", path=_TRUSTED_GIT_SEARCH_PATH_V2)
    if resolved is None:
        raise BoundedGitError(BOUNDED_GIT_UNAVAILABLE_REASON_V2)
    return resolved


def bounded_git_environment_v2(*, home: Path | None = None) -> dict[str, str]:
    """Build the child's *entire* environment.

    Nothing is copied from `os.environ`. A caller's `GIT_DIR`,
    `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
    `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_CONFIG*`, `GIT_SSH*`,
    `GIT_EXTERNAL_DIFF`, `LD_PRELOAD` and every other ambient name is simply
    absent, without this module needing to know they exist.
    """
    environment = {
        "PATH": _TRUSTED_GIT_SEARCH_PATH_V2,
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ASKPASS": "",
        "GIT_SSH_COMMAND": "/bin/false",
    }
    # HOME must exist and must not be the caller's, or `~/.gitconfig` and any
    # `includeIf` it carries would be read despite the settings above.
    environment["HOME"] = str(home) if home is not None else os.devnull
    return environment


def _resolve_common_git_dir_v2(*, executable: str, cwd: Path) -> Path | None:
    """Resolve `cwd`'s actual SHARED git directory -- where objects really
    live -- via git's own `rev-parse --git-common-dir`, not `--git-dir` and
    not an assumed `cwd / ".git"`.

    External Codex review (`#200-G1-PM` round 3 on this PR): `--git-dir`
    returns the WORKTREE-PRIVATE administrative directory for a linked
    worktree (`<main>/.git/worktrees/<name>`), which has no `objects/`
    subdirectory of its own at all -- confirmed by direct inspection, not
    assumed: `ls` on that directory shows only `HEAD`, `commondir`,
    `gitdir`, `index`, `logs`. The earlier version of this function used
    `--git-dir` and therefore computed an `objects_dir` that never existed
    for a linked worktree, so `_object_store_snapshot_v2` silently returned
    an empty snapshot both before and after any command -- the entire
    invariant check was blind whenever `run_bounded_git_v2` ran with `cwd`
    inside a linked worktree, reproduced end-to-end against the real lazy
    -fetch scenario this module exists to catch. `--git-common-dir` is
    git's own answer to "where do objects/refs/etc. that are SHARED across
    every worktree of this repository actually live" -- the main
    repository's `.git` (or a bare repository's own path) for a linked
    worktree, and identical to `--git-dir` for an ordinary, non-worktree
    checkout, so this is a strict correction, not a narrower special case.

    Returns `None` if it cannot be determined (e.g. `cwd` is not a git
    repository at all): deliberately not an error here, matching the
    disposition this module already established for an earlier, now
    superseded, config-inspection preflight -- the invariant check this
    feeds is simply skipped, and the actual requested command
    (`run_bounded_git_v2`'s own subprocess call, right after this returns)
    will fail on the same underlying condition in its own right.
    """
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable, no shell
            [executable, "rev-parse", "--git-common-dir"],
            cwd=cwd,
            env=bounded_git_environment_v2(),
            capture_output=True,
            text=False,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    git_dir = Path(os.fsdecode(raw))
    if not git_dir.is_absolute():
        git_dir = Path(cwd) / git_dir
    return git_dir


def _object_store_snapshot_v2(objects_dir: Path) -> frozenset[str]:
    """Every file currently under `objects_dir`, as paths relative to it.

    `#200-G1-PM` finding 5 (Codex, PR #284 review of `18dc9e4f`), and its
    two post-review recurrences on this PR: `remote.origin.promisor=false`
    only covers a remote literally named `origin`; a `--type=bool`-aware
    check over every `remote.*.promisor` closed the boolean-spelling
    variant but not a repository whose `remote.origin.partialclonefilter`
    marker alone (with `promisor` itself explicitly unset) still let git
    lazily fetch -- reproduced empirically against this exact primitive,
    not assumed. Each fix closed the SPECIFIC mechanism found and left the
    general shape of the gap open: there is no way to enumerate every git
    config key or on-disk marker that can independently enable a lazy
    fetch and have confidence the enumeration is exhaustive, because git's
    own partial-clone/promisor machinery is not one single switch.

    This function -- and the before/after comparison in `run_bounded_git_v2`
    that uses it -- replaces that enumeration entirely with a check of the
    OBSERVABLE OUTCOME instead of the mechanism: rather than asking "is
    this repository configured in a way that COULD cause a fetch",
    `run_bounded_git_v2` asks "did the object store CHANGE while the
    command ran", by recording every file under `objects_dir` immediately
    before and after. Git's on-disk object-store layout -- loose objects at
    `objects/<prefix>/<rest>`, pack files and their `.idx`/`.promisor`/etc.
    siblings directly under `objects/pack/` -- is unaffected by which
    config keys or markers enable lazy fetch, has been stable for the
    entire history of the format (including git's newer SHA-256 repository
    variant, which uses the identical directory shape with longer object
    IDs), and requires no version-specific command-output parsing (unlike
    e.g. `git count-objects -v`, whose exact fields are not something this
    module wants to depend on matching across the git versions this
    primitive might run under in CI/production versus this development
    sandbox). If ANY file appears that was not there before -- regardless
    of why, or which config key or marker enabled it -- the command caused
    a write to the object store, and `run_bounded_git_v2` fails closed,
    structurally immune to the next unknown-marker gap the same way the
    previous two enumeration-based fixes were not.

    `gc.auto=0` (`_BOUNDED_GIT_CONFIG_ARGUMENTS_V2`) exists specifically to
    keep this invariant meaningful: an ordinary read command that happens
    to trigger automatic repacking would otherwise introduce new pack
    filenames (while removing old loose-object files) for content that was
    already fully local, which this check would otherwise be unable to
    distinguish from a genuine fetch of new content -- disabling automatic
    gc removes that ambiguity at the source rather than trying to resolve
    it after the fact.
    """
    if not objects_dir.is_dir():
        return frozenset()
    return frozenset(
        str(path.relative_to(objects_dir)) for path in objects_dir.rglob("*") if path.is_file()
    )


@dataclass(frozen=True)
class BoundedGitSessionV2:
    """A FIXED object-store baseline, captured once, shared across every
    `run_bounded_git_v2` call a caller passes it to.

    External Codex review (`#200-G1-PM` round 3 on this PR): without a
    session, every `run_bounded_git_v2` call took its OWN fresh before/after
    snapshot -- correctly catching a fetch that happened DURING that one
    call, but proving nothing about calls before or after it. Reproduced
    end-to-end: a first call against a partial-clone fixture correctly
    raised `BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2`, but the
    blob it fetched remained on disk, and an immediately-following SECOND
    call for the identical object succeeded silently -- its own fresh
    "before" snapshot already included the first call's fetch, so nothing
    looked new to it. A caller that catches the exception and retries (an
    ordinary pattern) would treat externally-fetched bytes from a REJECTED
    invocation as trusted local input.

    A session closes this by fixing the baseline once, at
    `open_bounded_git_session_v2`, and never re-snapshotting "before" for
    any call that shares it: every call's "after" is compared against that
    SAME original baseline, so an object that entered the store at ANY
    point since the session opened -- whether this call fetched it or an
    earlier, already-rejected one did -- is still "new relative to session
    start" on every subsequent call.

    Deliberately opt-in, not the default: a caller that does not create a
    session (passes `session=None`, the default) keeps today's per-call
    behaviour, which is still correct for a single, standalone invocation
    -- and callers that DO want cross-call/retry protection for a whole
    logical operation (a materialisation, a verification) must create one
    session and thread it through every git call that operation makes.
    """

    objects_dir: Path | None
    baseline_snapshot: frozenset[str] | None


def open_bounded_git_session_v2(*, cwd: Path) -> BoundedGitSessionV2:
    """Capture `cwd`'s object-store baseline ONCE, for a caller that will
    make multiple `run_bounded_git_v2` calls against the same repository
    (directly, or via higher-level helpers built on it) and wants every one
    of them protected against a fetch that any EARLIER call in the same
    session caused -- not just the call that happens to notice it -- see
    `BoundedGitSessionV2` for why a fresh per-call snapshot cannot do that.

    Uses `_resolve_common_git_dir_v2` (not `--git-dir`), so a session opened
    from inside a linked worktree still finds the real, shared object
    store.
    """
    executable = resolve_trusted_git_absolute_path_v2()
    git_dir = _resolve_common_git_dir_v2(executable=executable, cwd=cwd)
    objects_dir = (git_dir / "objects") if git_dir is not None else None
    baseline_snapshot = (
        _object_store_snapshot_v2(objects_dir) if objects_dir is not None else None
    )
    return BoundedGitSessionV2(objects_dir=objects_dir, baseline_snapshot=baseline_snapshot)


def run_bounded_git_v2(
    argv: list[str],
    *,
    cwd: Path,
    home: Path | None = None,
    check: bool = True,
    input_bytes: bytes | None = None,
    session: BoundedGitSessionV2 | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one git command under the bounded contract.

    `argv` is the git *sub*command and its arguments -- the executable is
    supplied here, so no caller can choose which binary runs.

    Brackets the actual subprocess call with a before/after snapshot of
    `cwd`'s object store (`_object_store_snapshot_v2`) and fails closed
    with `BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2` if anything
    new appears -- see that function's docstring for why this replaced an
    earlier config-key-enumeration approach to the same problem (lazy
    fetch from a promisor/partial-clone remote). This means the underlying
    git process CAN still perform the fetch (this check cannot prevent
    that -- only detect it after the command has already run), but nothing
    from an invocation whose object store changed is ever returned to the
    caller as if it were trustworthy: the exception is raised before
    `completed` is returned, and before the ordinary `check`/exit-code
    handling below, so an unexpected write is reported even for a command
    that otherwise "succeeded".

    `session`, if given, supplies a FIXED baseline captured once by
    `open_bounded_git_session_v2` rather than a fresh one taken right
    before this call -- see `BoundedGitSessionV2` for why a fresh per-call
    baseline cannot detect an object that an EARLIER, already-rejected call
    fetched. Without a session (the default), this call takes its own
    fresh before/after snapshot, exactly as before -- still correct for a
    single, standalone invocation, just without cross-call memory.
    """
    if not Path(cwd).is_dir():
        raise BoundedGitError(BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2)

    executable = resolve_trusted_git_absolute_path_v2()
    resolved_argv = [
        executable,
        "--no-replace-objects",
        *_BOUNDED_GIT_CONFIG_ARGUMENTS_V2,
        *argv,
    ]

    if session is not None:
        objects_dir = session.objects_dir
        before_snapshot = session.baseline_snapshot
    else:
        git_dir = _resolve_common_git_dir_v2(executable=executable, cwd=cwd)
        objects_dir = (git_dir / "objects") if git_dir is not None else None
        before_snapshot = (
            _object_store_snapshot_v2(objects_dir) if objects_dir is not None else None
        )

    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable, no shell
            resolved_argv,
            cwd=cwd,
            env=bounded_git_environment_v2(home=home),
            capture_output=True,
            text=False,
            check=False,
            input=input_bytes,
        )
    except FileNotFoundError as exc:
        if not Path(cwd).is_dir():
            raise BoundedGitError(BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2) from exc
        raise BoundedGitError(BOUNDED_GIT_UNAVAILABLE_REASON_V2) from exc
    except OSError as exc:
        raise BoundedGitError(BOUNDED_GIT_IO_FAILED_REASON_V2) from exc

    if objects_dir is not None and before_snapshot is not None:
        after_snapshot = _object_store_snapshot_v2(objects_dir)
        if after_snapshot - before_snapshot:
            raise BoundedGitError(BOUNDED_GIT_UNEXPECTED_OBJECT_STORE_WRITE_REASON_V2)

    if check and completed.returncode != 0:
        raise BoundedGitError(BOUNDED_GIT_COMMAND_FAILED_REASON_V2)
    return completed
