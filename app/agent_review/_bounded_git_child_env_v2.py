"""`#200-E` -- the bounded child environment shared by the two subject
authorities (`controlled_subject_v2.py`, `toolrepo_execution_subject_v2.py`).

`#274`'s `_sealed_git_execution_v2.py` built its child environment by
starting from the calling process's OWN `os.environ` and subtracting a
named list of dangerous `GIT_*` variables. That blacklist had a real gap
(`GIT_CONFIG_PARAMETERS`, reproduced directly in `#274` round 3) and, more
fundamentally, could always have another gap: a blacklist is only as
complete as the list of things someone thought to distrust.

This module does the reverse. The child environment is built from an
explicit, small OS-level ALLOWLIST plus a fixed set of this authority's own
Git values -- nothing from the calling process's environment reaches the
child unless this module named it. There is no enumeration to have a gap
in, because nothing is inherited by default.

Spike evidence (`docs/checkpoints/AGENT_REVIEW_V2_200E_CONTROLLED_SUBJECT.md`):
`GIT_DIR`, `GIT_OBJECT_DIRECTORY`, `GIT_CONFIG_PARAMETERS` set in the
calling shell all had zero effect on operations run under this
construction, verified directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

# The only OS-level material a bounded git subprocess needs to function at
# all. Not `os.environ` filtered -- this list IS the environment, modulo
# the authority-owned values added below.
_ALLOWED_OS_ENV_NAMES_V2 = ("PATH",)


def bounded_child_env_v2(*, isolated_home: Path) -> dict[str, str]:
    """The child environment for every Git subprocess either subject
    authority in this package runs.

    ``isolated_home`` must be a directory owned by the caller (typically
    inside the scratch subject's own private root) -- it becomes `HOME`, so
    a per-user `~/.gitconfig` from the process's real home is never
    reachable either. Combined with `GIT_CONFIG_NOSYSTEM=1` and
    `GIT_CONFIG_GLOBAL=os.devnull`, the only Git configuration ever
    consulted is: this call's own `-c` values (none by default), and,
    for the TARGET authority only, the source repository's own
    repository-local config during the bounded read-only object-closure
    step -- never during any later operation against the scratch subject
    itself, which has its own freshly-initialized, reviewer-owned config.
    """

    env = {name: os.environ[name] for name in _ALLOWED_OS_ENV_NAMES_V2 if name in os.environ}
    env["HOME"] = str(isolated_home)
    env["LC_ALL"] = "C"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def run_bounded_git_v2(
    argv: list[str], *, cwd: Path, env: dict[str, str], input_bytes: bytes | None = None
) -> subprocess.CompletedProcess:
    """Run a `git` subprocess with a fixed argv (no shell) under a caller-
    supplied bounded environment. Converts `OSError` to `RuntimeError` with
    a stable, content-free message -- callers translate that into their own
    typed refusal family; this module raises nothing of its own reason
    codes, the same discipline `_run_git_v2` documented in `#274`.
    """

    if not argv or argv[0] != "git":
        raise ValueError("run_bounded_git_v2 expects an argv beginning with 'git'")
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell
            argv, cwd=cwd, env=env, input=input_bytes,
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise RuntimeError("bounded_git_subprocess_unavailable") from exc
