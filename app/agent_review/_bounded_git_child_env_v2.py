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

Correction, found by independent review (round 2): the shipped `PATH`
handling diverged from the spike's own tested primitive. The spike used a
FIXED `PATH` for its allowlist env; this module instead copied the
CALLER's ambient `PATH` verbatim, and every call site passes bare `"git"`
as argv[0] -- `subprocess.run` resolves a slash-free argv[0] via the
`env["PATH"]` it is given, so an attacker who controls the calling
shell's `PATH` (the single most ordinary form of ambient-environment
control, and exactly the class of threat this module exists to defeat)
could substitute their own `git` for every git call either subject
authority makes, including the byte-identity oracle itself (both sides of
an `archive` vs `cat-file` comparison would come from the same
attacker-controlled binary). Reproduced directly against the real product
CLI before this fix: 140+ git invocations across the full toolrepo-
materialization pipeline routed through a planted fake `git`.

Fixed the same way the spike always worked: `PATH` is no longer read from
`os.environ` at all -- `env["PATH"]` is always `os.defpath`
(`'/bin:/usr/bin'` on this platform), Python's own fixed, non-environment-
derived default search path, never the caller's. `git` itself is resolved
to an absolute path exactly once, via `shutil.which("git",
path=os.defpath)` -- the SAME trusted, fixed path, not `os.environ["PATH"]`
-- and that resolved absolute path is substituted for every call's `"git"`
argv[0] before the subprocess actually runs, so no git invocation this
module makes is ever subject to `PATH`-based resolution against anything
the caller controls. Fails closed (`RuntimeError`) if `git` cannot be
found on the fixed path -- never silently falls back to an unresolved
bare name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Deliberately NOT `_ALLOWED_OS_ENV_NAMES_V2` / `os.environ`-derived
# anymore -- see the module docstring's "Correction" section. `PATH` is a
# fixed OS default, identical on every invocation, never the caller's own.
_TRUSTED_GIT_SEARCH_PATH_V2 = os.defpath


def _resolve_trusted_git_absolute_path_v2() -> str:
    resolved = shutil.which("git", path=_TRUSTED_GIT_SEARCH_PATH_V2)
    if resolved is None:
        raise RuntimeError("bounded_git_subprocess_unavailable")
    return resolved


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

    env = {"PATH": _TRUSTED_GIT_SEARCH_PATH_V2}
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

    The public contract (``argv[0] == "git"``) is unchanged -- every
    existing call site keeps passing the bare name -- but the actual
    argv[0] handed to `subprocess.run` is always the absolute path
    resolved by `_resolve_trusted_git_absolute_path_v2`, never the bare
    name, so no PATH-based resolution against ``env`` (or anything else)
    ever happens at the OS level for this call.
    """

    if not argv or argv[0] != "git":
        raise ValueError("run_bounded_git_v2 expects an argv beginning with 'git'")
    resolved_argv = [_resolve_trusted_git_absolute_path_v2(), *argv[1:]]
    try:
        return subprocess.run(  # noqa: S603 -- fixed, resolved argv, no shell
            resolved_argv, cwd=cwd, env=env, input=input_bytes,
            capture_output=True, check=False,
        )
    except OSError as exc:
        raise RuntimeError("bounded_git_subprocess_unavailable") from exc
