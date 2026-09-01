"""`#200-F` -- the only sanctioned way this product invokes git.

Ported from `#276` with revalidation, not inherited. Nothing here is qualified
by having survived the predecessor; every property has new tests.

## What the merged code does today

``diff_acquisition_v2._run_git_v2`` calls ``subprocess.run(argv, cwd=...)``
with no ``env=`` and with ``argv[0] == "git"``. That means the child inherits
the caller's entire environment and git is resolved through the caller's
``PATH``. Both of the predecessor's round-2 P0 vectors are therefore live on
merged code:

* a ``git`` planted earlier on ``PATH`` is executed instead of the real one;
* ambient ``GIT_*`` variables reach the child and change what git does.

## The two properties

**Allowlist, never blacklist.** The child receives exactly the variables built
here and nothing else. A blacklist has to enumerate every dangerous name, and
git has dozens; the first one anybody forgets is the hole. An allowlist is
wrong only in the direction of breaking loudly.

**Fixed executable resolution.** ``git`` is resolved against ``os.defpath``,
not the caller's ``PATH``, and the child is exec'd with that absolute path as
``argv[0]``. Resolving against a ``PATH`` the caller can influence would
reopen the vector this closes, so the search list is pinned to the platform's
own default.

## Configuration, neutralised at the command line

``-c`` beats every config file, so system, global and repository config cannot
re-enable what is switched off here. ``GIT_CONFIG_NOSYSTEM`` and a
``/dev/null`` global config remove the files themselves, and ``HOME`` is
pointed at a directory the caller does not control so ``~/.gitconfig`` and
``includeIf`` have nothing to resolve against.

Deliberately *not* claimed: this does not make git safe against a hostile
repository in general. It makes the specific, enumerated behaviours below
unreachable, and the red corpus states which.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2

__all__ = [
    "BOUNDED_GIT_COMMAND_FAILED_REASON_V2",
    "BOUNDED_GIT_IO_FAILED_REASON_V2",
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

#: The platform's own default search path -- ``/bin:/usr/bin`` on POSIX. Not
#: derived from ``os.environ["PATH"]``, which is precisely the value a caller
#: can poison.
_TRUSTED_GIT_SEARCH_PATH_V2 = os.defpath

#: Applied to every invocation. Each entry disables a mechanism by which a
#: repository's own content could influence what git does while reading it.
_BOUNDED_GIT_CONFIG_ARGUMENTS_V2 = (
    # A repository may not run programs while its objects are being read.
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    # Content filters run arbitrary commands on checkout/archive.
    "-c", "filter.lfs.smudge=",
    "-c", "filter.lfs.process=",
    "-c", "filter.lfs.required=false",
    # ext:: transports execute a shell command as a "protocol".
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.file.allow=user",
    # No network, ever, from an analysis path.
    "-c", "remote.origin.promisor=false",
    "-c", "core.attributesFile=/dev/null",
)


class BoundedGitError(ExpectedOperationalRefusalV2, ValueError):
    """A bounded git invocation could not be performed or did not succeed."""

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

    Nothing is copied from ``os.environ``. A caller's ``GIT_DIR``,
    ``GIT_INDEX_FILE``, ``GIT_OBJECT_DIRECTORY``, ``GIT_ALTERNATE_OBJECT_
    DIRECTORIES``, ``GIT_CONFIG*``, ``GIT_SSH*``, ``GIT_EXTERNAL_DIFF``,
    ``LD_PRELOAD`` and every other ambient name is simply absent, without this
    module needing to know they exist.
    """
    environment = {
        "PATH": _TRUSTED_GIT_SEARCH_PATH_V2,
        # Deterministic parsing: git localises some output.
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        # An analysis path never authenticates and never prompts.
        "GIT_ASKPASS": "",
        "GIT_SSH_COMMAND": "/bin/false",
    }
    # HOME must exist and must not be the caller's, or `~/.gitconfig` and any
    # `includeIf` it carries would be read despite the settings above.
    environment["HOME"] = str(home) if home is not None else os.devnull
    return environment


def run_bounded_git_v2(
    argv: list[str],
    *,
    cwd: Path,
    home: Path | None = None,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one git command under the bounded contract.

    ``argv`` is the git *sub*command and its arguments -- the executable is
    supplied here, so no caller can choose which binary runs.
    """
    if not Path(cwd).is_dir():
        raise BoundedGitError(BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2)

    executable = resolve_trusted_git_absolute_path_v2()
    resolved_argv = [
        executable,
        "--no-replace-objects",  # `git replace` refs cannot rewrite history here
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
        # The executable resolved a moment ago, so a missing file now is the
        # working directory, not git. Asked rather than assumed: the probe and
        # the exec are not atomic.
        if not Path(cwd).is_dir():
            raise BoundedGitError(BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2) from exc
        raise BoundedGitError(BOUNDED_GIT_UNAVAILABLE_REASON_V2) from exc
    except OSError as exc:
        raise BoundedGitError(BOUNDED_GIT_IO_FAILED_REASON_V2) from exc

    if check and completed.returncode != 0:
        # stderr is deliberately not attached: it can contain absolute paths
        # and, for some subcommands, file content.
        raise BoundedGitError(BOUNDED_GIT_COMMAND_FAILED_REASON_V2)
    return completed
