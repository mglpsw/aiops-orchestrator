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
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

__all__ = [
    "BOUNDED_GIT_COMMAND_FAILED_REASON_V2",
    "BOUNDED_GIT_IO_FAILED_REASON_V2",
    "BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2",
    "BOUNDED_GIT_UNAVAILABLE_REASON_V2",
    "BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2",
    "BoundedGitError",
    "bounded_git_environment_v2",
    "resolve_trusted_git_absolute_path_v2",
    "run_bounded_git_v2",
]


BOUNDED_GIT_UNAVAILABLE_REASON_V2 = "bounded_git_unavailable"
BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2 = "bounded_git_worktree_unusable"
BOUNDED_GIT_IO_FAILED_REASON_V2 = "bounded_git_io_failed"
BOUNDED_GIT_COMMAND_FAILED_REASON_V2 = "bounded_git_command_failed"
BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2 = "bounded_git_promisor_remote_present"

#: The platform's own default search path. Not derived from
#: `os.environ["PATH"]`, which is precisely the value a caller can poison.
_TRUSTED_GIT_SEARCH_PATH_V2 = os.defpath

#: Applied to every invocation. Each entry disables a mechanism by which a
#: repository's own content could influence what git does while it is read.
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


def _refuse_if_promisor_remote_configured_v2(*, executable: str, cwd: Path) -> None:
    """Refuse outright if `cwd`'s repository has any promisor remote.

    `#200-G1-PM` finding 5 (Codex, PR #284 review of `18dc9e4f`):
    `remote.origin.promisor=false`, hardcoded below in
    `_BOUNDED_GIT_CONFIG_ARGUMENTS_V2`, only overrides a remote literally
    named `origin`. Renaming a partial clone's remote (`git remote rename
    origin evil` -- ordinary git, preserves the `promisor` flag under the
    new name) leaves it fully able to lazily fetch a missing object on
    demand, defeating the "offline, severed-from-source" property this
    whole primitive depends on.

    Proven empirically, not assumed: every mechanism this module could have
    used to *suppress* the fetch instead of refusing outright was tried
    against this git build and falsified --
    `-c remote.<name>.promisor=false` (even for the *correct* remote name),
    `-c protocol.file.allow=never`, `GIT_ALLOW_PROTOCOL=none`, and
    `GIT_NO_LAZY_FETCH=1` (present as a string in this git binary, and
    still did nothing) all left `cat-file --batch` able to silently fetch
    a blob absent from the local object store. None of those are
    trustworthy across git builds/versions. Detecting the precondition
    that makes the fetch *possible at all* -- any remote configured with
    `promisor = true`, discovered fresh from `cwd`'s own config on every
    call rather than assumed absent -- and refusing before the requested
    command ever runs is the only fail-closed option available: a subject
    materialised or verified from a repository that can pull bytes from
    outside its own object store is not severed from its source at all,
    regardless of whether this particular invocation happened to need the
    missing object.

    Runs `git config --get-regexp`, not `run_bounded_git_v2` itself --
    this function exists to be called *from inside* that function, and
    must not recurse into it.

    Deliberately does NOT turn a failure of the `config --get-regexp` probe
    itself (e.g. `cwd` not being a git repository at all, exit 128) into a
    refusal here: that is not this function's question to answer, and the
    actual command `run_bounded_git_v2` was asked to run will fail on the
    same underlying condition in its own right, with its own accurate
    reason code, immediately after this returns. This function's only job
    is to convert a *successfully read* `promisor = true` into a refusal.
    """
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable, no shell
            [executable, "config", "--get-regexp", r"^remote\..*\.promisor$"],
            cwd=cwd,
            env=bounded_git_environment_v2(),
            capture_output=True,
            text=False,
            check=False,
        )
    except OSError:
        # Same disposition as a failed probe below: not this function's
        # question to answer. `run_bounded_git_v2`'s own subprocess call,
        # immediately after this returns, will hit the identical condition
        # and report it accurately in its own right.
        return
    if completed.returncode != 0:
        # Includes exit 1 ("nothing matched" -- the common, non-error case
        # of no promisor remote at all) and any other failure (e.g. `cwd`
        # not being a repository), which is left for the real command to
        # report accurately in its own right.
        return
    for line in completed.stdout.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        _, _, value = line.partition(b" ")
        if value.strip().lower() == b"true":
            raise BoundedGitError(BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2)


def run_bounded_git_v2(
    argv: list[str],
    *,
    cwd: Path,
    home: Path | None = None,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one git command under the bounded contract.

    `argv` is the git *sub*command and its arguments -- the executable is
    supplied here, so no caller can choose which binary runs.
    """
    if not Path(cwd).is_dir():
        raise BoundedGitError(BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2)

    executable = resolve_trusted_git_absolute_path_v2()
    _refuse_if_promisor_remote_configured_v2(executable=executable, cwd=cwd)
    resolved_argv = [
        executable,
        "--no-replace-objects",
        *_BOUNDED_GIT_CONFIG_ARGUMENTS_V2,
        *argv,
    ]

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

    if check and completed.returncode != 0:
        raise BoundedGitError(BOUNDED_GIT_COMMAND_FAILED_REASON_V2)
    return completed
