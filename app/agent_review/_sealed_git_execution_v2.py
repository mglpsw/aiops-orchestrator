"""`#200-D` correction: sealed Git execution boundary (issue #200).

## `GIT_SEMANTIC_EXECUTION_INVARIANT`

For every Git operation whose result participates in review subject
acquisition, immutable reference material, or toolrepo source identity, the
result must be a deterministic function of the explicitly named Git subject
and this module's explicitly frozen command policy. Ambient Git process
state, replacement refs, caller Git environment overrides, or global/system
Git configuration must not alter the semantic result.

Equivalently: `GitSHAIdentity + UnboundGitInterpretationEnvironment` is NOT
a closed subject. This module is the shared, low-level mechanism that
closes the *environment* half; `diff_acquisition_v2` and
`toolrepo_identity_v2` each remain the distinct authority over their own
subject and refusal semantics -- this module manufactures no reason code
and raises nothing of its own.

## What this closes, measured directly against this host's actual Git, not
## assumed from documentation

**Replacement objects (`git replace`).** `git cat-file`/`git ls-tree` honor
a replacement mapping by default, and the substitution is invisible at the
identity layer: `git ls-tree <head_sha> -- path` still names the ORIGINAL
blob SHA after `git replace <original> <malicious>`, while
`git cat-file -p <original>` returns the malicious bytes. Reproduced
directly before this module existed. `GIT_NO_REPLACE_OBJECTS=1` closes it
-- applied as an environment variable rather than a per-command
`--no-replace-objects` flag, so a future call site cannot reintroduce the
gap by forgetting one flag.

**Ambient repository/object-store redirection.** An ambient `GIT_DIR`
pointing at an unrelated repository silently redirects every Git command
run in this process to that repository, regardless of `cwd`/`-C` --
reproduced directly. The same class applies to `GIT_WORK_TREE`,
`GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY` (reproduced directly: injecting it
broke object resolution for the real repository's own HEAD entirely),
`GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR` and `GIT_NAMESPACE`.
All are stripped from the child environment.

**Diff-driver and attribute-source redirection.** `GIT_EXTERNAL_DIFF`,
`GIT_DIFF_OPTS` and `GIT_ATTR_SOURCE` can each change how content is
interpreted independent of the declared subject; stripped.

**Ad hoc config injection.** `GIT_CONFIG_COUNT` plus its indexed
`GIT_CONFIG_KEY_*`/`GIT_CONFIG_VALUE_*` companions let a caller inject
arbitrary config key/value pairs into the child process without touching
any file; stripped.

**Target-controlled hook execution.** `git worktree add` runs the target
repository's `post-checkout` hook -- reproduced directly: a hook planted at
`$GIT_DIR/hooks/post-checkout` executed, with the disposable worktree as
its cwd, during the very `git worktree add` this package uses to bind
attribute resolution to the declared subject. That made the attribute fix
itself a target-controlled code-execution path, contradicting the "never
executes untrusted code" boundary `diff_acquisition_v2` documents for
itself when it explains `--no-textconv`. A repository-local
`core.hooksPath` redirect reaches an arbitrary directory the same way,
also reproduced directly, and is NOT covered by neutralizing environment
variables -- Git has no `GIT_HOOKS_PATH` env var, and repository-local
`.git/config` is deliberately left reachable here.

**Target-controlled filter drivers.** A repository-local
`filter.<driver>.smudge`/`.clean`/`.process` command is executed by Git
whenever a path whose attributes assign that driver is checked out --
reproduced directly during the same `git worktree add`. The driver name is
attacker-chosen, so there is no `-c` closure, and `--no-checkout` is not an
alternative (verified: it leaves the worktree empty and attribute
resolution stops working, which is the worktree's entire purpose). Detected
and refused instead by `has_executable_local_filter_config_v2` -- the same
fail-closed shape already used for `$GIT_DIR/info/attributes`. This does
refuse repositories that legitimately configure a filter driver, `git-lfs`
being the common one; that operational cost is accepted deliberately, in
preference to executing a target-controlled command.

**Out-of-tree attribute redirection via `core.attributesFile`.** A
repository-local `core.attributesFile` points attribute resolution at an
arbitrary path, and the disposable worktree does not close it -- reproduced
directly, flipping a text diff to "Binary files differ", which is precisely
the corruption that worktree exists to prevent. Closed with
`-c core.attributesFile=<os.devnull>`, verified to leave a genuinely
committed `.gitattributes` at the subject commit fully effective.

**Target-controlled `core.fsmonitor`.** Holds a command Git executes to
enumerate working-tree changes; reproduced directly running during
`git status`. Closed with `-c core.fsmonitor=false`.

Closed on the command line instead, by `sealed_git_argv_v2`: `-c
core.hooksPath=<os.devnull>` takes precedence over both the default
`$GIT_DIR/hooks` lookup and any repository-local `core.hooksPath`,
verified directly against both vectors. It is an argv prefix rather than
a per-call-site flag for the same reason `GIT_NO_REPLACE_OBJECTS` is an
environment variable: a future call site cannot reintroduce the gap by
forgetting it.

**Global/system Git configuration.** `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`
set to `os.devnull` make Git consult neither -- verified supported on this
host's Git 2.39.5 (the mechanism shipped in Git 2.32). Only the target
repository's own repository-local `.git/config` is left reachable, since
that is part of the checkout under review, not ambient caller/machine
state.

## What this module deliberately does NOT close

`--attr-source=<tree-ish>` -- the Git-native mechanism to bind attribute
resolution to a tree instead of a working directory -- requires Git >=
2.40. This host's Git (2.39.5, Debian 12/bookworm stable) does not have it;
the flag was verified to fail with a usage error directly, not merely
absent from `--help`. This module therefore does not attempt to rely on it,
and claims nothing about attribute-source binding: see
`diff_acquisition_v2`'s own docstring for the mechanism it uses instead
(a disposable, detached `git worktree` checked out exactly at the declared
subject) and its explicit, separate handling of `$GIT_DIR/info/attributes`,
which is shared by every worktree of a repository -- including a freshly
created one -- and is NOT closed by anything in this module.

Repository-local `.git/config` is deliberately left reachable: it is part
of the checkout under review, not ambient state outside it.
"""

from __future__ import annotations

import os
from pathlib import Path

# Every one of these can redirect which repository, object store, index, or
# diff driver Git actually consults, independent of any `cwd`/`-C` argument
# -- or inject config that changes interpretation without touching a file.
_NEUTRALIZED_GIT_ENV_VARS_V2 = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_EXTERNAL_DIFF",
        "GIT_DIFF_OPTS",
        "GIT_ATTR_SOURCE",
        "GIT_CONFIG_COUNT",
    }
)
_NEUTRALIZED_GIT_ENV_PREFIXES_V2 = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")


def sealed_git_child_env_v2() -> dict[str, str]:
    """The child environment for every Git subprocess a semantic Git
    authority in this package runs.

    Starts from the current process environment -- `PATH` and other OS-level
    state Git needs merely to execute is preserved -- then strips every
    ambient `GIT_*` variable capable of redirecting which repository,
    object store, index, diff driver or config Git consults, disables
    replacement-object resolution unconditionally, and points global/system
    Git config at `os.devnull`.
    """

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in _NEUTRALIZED_GIT_ENV_VARS_V2
        and not key.startswith(_NEUTRALIZED_GIT_ENV_PREFIXES_V2)
    }
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


# Git resolves hooks from `$GIT_DIR/hooks` by default and from
# `core.hooksPath` when set. Neither is reachable through the environment
# (there is no `GIT_HOOKS_PATH`), and repository-local `.git/config` stays
# readable by design, so hook neutralization has to travel on the command
# line. `-c` beats repository-local config, verified directly against both
# a planted `$GIT_DIR/hooks/post-checkout` and a `core.hooksPath` redirect.
_HOOKS_DISABLED_CONFIG_V2 = f"core.hooksPath={os.devnull}"
# `core.fsmonitor` holds a command Git executes to enumerate working-tree
# changes; reproduced directly, a repository-local value ran during
# `git status`. `false` is Git's own documented "no fsmonitor" value, and
# `-c` beats the repository-local setting -- verified directly.
_FSMONITOR_DISABLED_CONFIG_V2 = "core.fsmonitor=false"
# `core.attributesFile` names an out-of-tree attributes file. Set in the
# repository-local config it redirects attribute resolution to an
# arbitrary path and the disposable worktree does not close it at all --
# reproduced directly: it flipped an ordinary text diff to "Binary files
# differ", the exact corruption the worktree exists to prevent. Pointing
# it at `os.devnull` restores the text diff while leaving a genuinely
# COMMITTED `.gitattributes` at the subject commit fully effective --
# both directions verified directly.
_ATTRIBUTES_FILE_DISABLED_CONFIG_V2 = f"core.attributesFile={os.devnull}"


def sealed_git_argv_v2(argv: list[str], *, trusted_repo_root: Path) -> list[str]:
    """The argv every Git subprocess a semantic Git authority in this
    package runs must actually execute.

    Takes a caller's ordinary ``["git", ...]`` command and returns it with
    this module's frozen command policy spliced in immediately after the
    executable, where Git requires its own `-c` options to appear.

    ``trusted_repo_root`` is the checkout the caller has already explicitly
    declared as its subject. It is named as `safe.directory` because
    `GIT_CONFIG_GLOBAL=os.devnull` (above) also discards any
    `safe.directory` the operator configured there, and Git then refuses
    outright -- `fatal: detected dubious ownership` -- on any checkout owned
    by a different uid than the running process. Reproduced directly; that
    is the ordinary case in a container or CI runner whose checkout is owned
    by a build user, so without this the seal would convert a working
    deployment into a total acquisition failure.

    Granting it on the command line is exactly scoped: verified directly
    that `-c safe.directory=<path>` admits that path and still refuses a
    DIFFERENT foreign-owned repository, so this trusts precisely the
    checkout the caller already named and nothing else. It is not a content
    trust decision either -- the subject's *content* remains hostile, which
    is what the rest of this module's policy is for.

    Raises ``ValueError`` -- not a refusal -- if ``argv`` does not start
    with ``git``: that is a defect in a call site inside this package, and
    a bug must never be laundered into a subject-level refusal.
    """

    if not argv or argv[0] != "git":
        raise ValueError("sealed_git_argv_v2 expects an argv beginning with 'git'")
    return [
        argv[0],
        "-c", _HOOKS_DISABLED_CONFIG_V2,
        "-c", _FSMONITOR_DISABLED_CONFIG_V2,
        "-c", _ATTRIBUTES_FILE_DISABLED_CONFIG_V2,
        "-c", f"safe.directory={Path(trusted_repo_root)}",
        *argv[1:],
    ]


# A `filter.<driver>.smudge`/`.clean`/`.process` command is executed by Git
# whenever a path whose attributes assign `filter=<driver>` is checked out
# or staged. Reproduced directly: a repository-local `filter.evil.smudge`
# executed during the `git worktree add` this package uses. Unlike hooks and
# fsmonitor there is no `-c` closure, because the driver NAME is chosen by
# whoever wrote the config and cannot be enumerated in advance; and
# `--no-checkout` is not an alternative, verified directly -- it leaves the
# worktree empty, so attribute resolution stops working entirely, which is
# the whole reason the worktree exists.
_EXECUTABLE_FILTER_CONFIG_SUFFIXES_V2 = (".smudge", ".clean", ".process")


def has_executable_local_filter_config_v2(repo_root: Path, *, env: dict[str, str]) -> bool:
    """Whether the repository-local config defines any filter driver that
    Git would execute during checkout.

    Repository-local `.git/config` is deliberately reachable (it is part of
    the checkout under review), and this is the one execution vector in it
    that no command-line override can close, so it is detected and refused
    instead -- the same fail-closed shape this module already uses for
    `$GIT_DIR/info/attributes`.

    Deliberately NOT scoped with `--local`. `git config --local --list` does
    not follow `include.path`/`includeIf`, while Git's actual filter lookup
    does -- reproduced directly: a filter driver moved into an included file
    was invisible to `--local --list` and still executed during
    `git worktree add`, bypassing this detector entirely. The unscoped
    `--list` resolves includes, and under `sealed_git_child_env_v2` it can
    still only report repository-local content, because global and system
    config are already pointed at `os.devnull`; the only other entries are
    this module's own command-line `-c` values, none of which are `filter.*`.
    """

    import subprocess

    result = subprocess.run(
        sealed_git_argv_v2(
            ["git", "config", "--list", "--name-only", "-z"],
            trusted_repo_root=repo_root,
        ),
        cwd=repo_root, env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        # Nothing readable to prove a driver is defined; the caller's own
        # git invocations will fail for the same reason if this is real.
        return False
    for key in result.stdout.split("\0"):
        key = key.strip().lower()
        if key.startswith("filter.") and key.endswith(_EXECUTABLE_FILTER_CONFIG_SUFFIXES_V2):
            return True
    return False


def has_semantically_active_info_attributes_v2(repo_root: Path, *, env: dict[str, str]) -> bool:
    """Whether `$GIT_DIR/info/attributes` exists and has content capable of
    influencing attribute resolution -- comments and blank lines do not
    count. Shared by every worktree of a repository, including one created
    solely to isolate the working-tree `.gitattributes` vector, so it is
    NOT closed by that isolation and must be checked separately.

    Resolved via `git rev-parse --git-path info/attributes` rather than a
    hardcoded `.git/info/attributes` join, so this is correct for a linked
    worktree or any non-standard `.git` layout (verified directly: from a
    linked worktree, this resolves to the shared common directory's file,
    not a nonexistent per-worktree one).
    """

    import subprocess

    result = subprocess.run(
        sealed_git_argv_v2(
            ["git", "rev-parse", "--git-path", "info/attributes"], trusted_repo_root=repo_root
        ),
        cwd=repo_root, env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return False
    info_attributes_path = (repo_root / result.stdout.strip()).resolve()
    if not info_attributes_path.is_file():
        return False
    try:
        raw_text = info_attributes_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        # Present but unreadable: cannot prove it is inactive. Fail closed
        # by treating it as active -- callers refuse rather than proceed.
        return True
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False
