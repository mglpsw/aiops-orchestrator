from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


SCRATCH = Path("/tmp/ar274-review-r3/laneA/q2-agent")
CASE = SCRATCH / "case"
SUBJECT = Path("/opt/agent-tools/ar-200d-successor")

sys.dont_write_bytecode = True
sys.path.insert(0, str(SUBJECT))

from app.agent_review._sealed_git_execution_v2 import (  # noqa: E402
    sealed_git_argv_v2,
    sealed_git_child_env_v2,
)


def run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def controlled_env() -> dict[str, str]:
    env = dict(os.environ)
    env["TMPDIR"] = str(SCRATCH)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


ENV = controlled_env()


def plain_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, env=ENV)


def sealed(repo: Path, argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    child_env = sealed_git_child_env_v2()
    child_env["TMPDIR"] = str(SCRATCH)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        child_env.update(env)
    return run(
        sealed_git_argv_v2(argv, trusted_repo_root=repo),
        cwd=(cwd or repo),
        env=child_env,
    )


def must(result: subprocess.CompletedProcess[str], label: str) -> subprocess.CompletedProcess[str]:
    if result.returncode != 0:
        raise RuntimeError(f"{label}: rc={result.returncode} stderr={result.stderr!r}")
    return result


def init_repo(path: Path, filename: str = "f.txt") -> tuple[str, str]:
    path.mkdir(parents=True)
    must(plain_git(path, "init", "-q"), "init")
    (path / filename).write_text("one\n", encoding="utf-8")
    must(plain_git(path, "add", filename), "add base")
    must(
        plain_git(
            path,
            "-c",
            "user.name=Reviewer",
            "-c",
            "user.email=reviewer@example.invalid",
            "commit",
            "-qm",
            "base",
        ),
        "commit base",
    )
    base = must(plain_git(path, "rev-parse", "HEAD"), "base sha").stdout.strip()
    (path / filename).write_text("two\n", encoding="utf-8")
    must(plain_git(path, "add", filename), "add head")
    must(
        plain_git(
            path,
            "-c",
            "user.name=Reviewer",
            "-c",
            "user.email=reviewer@example.invalid",
            "commit",
            "-qm",
            "head",
        ),
        "commit head",
    )
    head = must(plain_git(path, "rev-parse", "HEAD"), "head sha").stdout.strip()
    return base, head


def make_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def clean_case() -> None:
    if CASE.exists():
        shutil.rmtree(CASE)
    CASE.mkdir(parents=True)


def show_result(label: str, result: subprocess.CompletedProcess[str]) -> None:
    out = result.stdout.strip().replace("\n", "\\n")
    err = result.stderr.strip().replace("\n", "\\n")
    if len(out) > 180:
        out = out[:180] + "..."
    if len(err) > 180:
        err = err[:180] + "..."
    print(f"{label}: rc={result.returncode} out={out!r} err={err!r}")


def main() -> None:
    clean_case()
    repo = CASE / "repo"
    base, head = init_repo(repo)
    blob = must(plain_git(repo, "rev-parse", f"{head}:f.txt"), "blob").stdout.strip()
    version = must(run(["git", "--version"], cwd=CASE, env=ENV), "git version")
    print(version.stdout.strip())

    print("\nACTUAL_CALLSITE_SHAPES")
    actual_calls: list[tuple[str, list[str], Path | None]] = [
        ("config-list", ["git", "config", "--list", "--name-only", "-z"], None),
        ("rev-parse-git-path", ["git", "rev-parse", "--git-path", "info/attributes"], None),
        ("diff-unified", ["git", "diff", "--no-ext-diff", "--no-textconv", "--binary", "--src-prefix=a/", "--dst-prefix=b/", "--find-renames=50%", "--find-copies=50%", "-l1000", f"{base}...{head}"], None),
        ("diff-raw", ["git", "diff", "--no-ext-diff", "--raw", "-z", "--find-renames=50%", "--find-copies=50%", "-l1000", f"{base}...{head}"], None),
        ("ls-tree-entry", ["git", "ls-tree", head, "--", "f.txt"], None),
        ("cat-file", ["git", "cat-file", "-p", blob], None),
        ("rev-parse-head", ["git", "rev-parse", "--verify", "HEAD^{commit}"], None),
        ("ls-tree-recursive", ["git", "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", "app", "scripts/aiops-review-run-v2.py"], None),
        ("ls-files-others", ["git", "ls-files", "--others", "-z", "--", "app", "scripts/aiops-review-run-v2.py"], None),
        ("diff-name-only", ["git", "diff", "--name-only", "-z", "HEAD", "--", "app", "scripts/aiops-review-run-v2.py"], None),
    ]
    for label, argv, cwd in actual_calls:
        result = sealed(repo, argv, cwd=cwd)
        print(f"{label}: rc={result.returncode}")

    wt = CASE / "actual-wt"
    add = sealed(repo, ["git", "worktree", "add", "--quiet", "--detach", str(wt), head])
    print(f"worktree-add: rc={add.returncode} materialized={(wt / 'f.txt').is_file()}")
    remove = sealed(repo, ["git", "worktree", "remove", "--force", str(wt)])
    print(f"worktree-remove: rc={remove.returncode} removed={not wt.exists()}")

    print("\nGLOBAL_POSITION_MATRIX")
    position_calls = [
        ("no-args", ["git"]),
        ("version", ["git", "--version"]),
        ("exec-path", ["git", "--exec-path"]),
        ("caller-no-pager", ["git", "--no-pager", "status", "--short"]),
        ("caller-C", ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]),
        ("caller-c-before-C", ["git", "-c", "q2.value=first", "-C", str(repo), "config", "--get", "q2.value"]),
        ("caller-C-before-c", ["git", "-C", str(repo), "-c", "q2.value=second", "config", "--get", "q2.value"]),
        ("caller-git-dir", ["git", f"--git-dir={repo / '.git'}", "rev-parse", "--show-toplevel"]),
        ("caller-literal-pathspecs", ["git", "--literal-pathspecs", "ls-files", "--", "f.txt"]),
        ("caller-no-optional-locks", ["git", "--no-optional-locks", "status", "--short"]),
    ]
    for label, argv in position_calls:
        show_result(label, sealed(repo, argv))

    print("\nLATER_CALLER_CONFIG_OVERRIDES")
    evil_hooks = CASE / "evil-hooks-c"
    evil_hooks.mkdir()
    hook_marker_c = CASE / "hook-ran-c"
    make_executable(
        evil_hooks / "post-checkout",
        f"#!/bin/sh\n: > {hook_marker_c}\n",
    )
    wt_c = CASE / "wt-c"
    hook_c = sealed(
        repo,
        ["git", "-c", f"core.hooksPath={evil_hooks}", "worktree", "add", "--quiet", "--detach", str(wt_c), head],
    )
    print(f"later-c-hooksPath: rc={hook_c.returncode} marker={hook_marker_c.exists()}")
    if wt_c.exists():
        sealed(repo, ["git", "worktree", "remove", "--force", str(wt_c)])

    evil_hooks_env = CASE / "evil-hooks-env"
    evil_hooks_env.mkdir()
    hook_marker_env = CASE / "hook-ran-config-env"
    make_executable(
        evil_hooks_env / "post-checkout",
        f"#!/bin/sh\n: > {hook_marker_env}\n",
    )
    wt_env = CASE / "wt-config-env"
    hook_env = sealed(
        repo,
        ["git", "--config-env=core.hooksPath=Q2_HOOKS", "worktree", "add", "--quiet", "--detach", str(wt_env), head],
        env={"Q2_HOOKS": str(evil_hooks_env)},
    )
    print(f"later-config-env-hooksPath: rc={hook_env.returncode} marker={hook_marker_env.exists()}")
    if wt_env.exists():
        sealed(repo, ["git", "worktree", "remove", "--force", str(wt_env)])

    fsmonitor_marker = CASE / "fsmonitor-ran"
    fsmonitor_script = CASE / "fsmonitor.sh"
    make_executable(
        fsmonitor_script,
        f"#!/bin/sh\n: > {fsmonitor_marker}\nprintf '\\n'\n",
    )
    fsmonitor = sealed(
        repo,
        ["git", "-c", f"core.fsmonitor={fsmonitor_script}", "status", "--short"],
    )
    print(f"later-c-fsmonitor: rc={fsmonitor.returncode} marker={fsmonitor_marker.exists()}")

    evil_attributes = CASE / "evil.attributes"
    evil_attributes.write_text("f.txt -diff\n", encoding="utf-8")
    normal_diff = sealed(repo, ["git", "diff", "--no-ext-diff", "--no-textconv", "--binary", f"{base}...{head}", "--", "f.txt"])
    overridden_diff = sealed(repo, ["git", "-c", f"core.attributesFile={evil_attributes}", "diff", "--no-ext-diff", "--no-textconv", "--binary", f"{base}...{head}", "--", "f.txt"])
    print(f"later-c-attributesFile: rc={overridden_diff.returncode} normal_binary_patch={'GIT binary patch' in normal_diff.stdout} overridden_binary_patch={'GIT binary patch' in overridden_diff.stdout}")

    duplicate_values = sealed(
        repo,
        ["git", "-c", "safe.directory=*", "config", "--get-all", "safe.directory"],
    )
    print("later-c-safe-directory-values=" + repr(duplicate_values.stdout.splitlines()))

    print("\nCALLER_C_REDIRECTION_AND_SAFE_DIRECTORY")
    other = CASE / "other"
    init_repo(other, "other.txt")
    redirected = sealed(repo, ["git", "-C", str(other), "rev-parse", "--show-toplevel"])
    show_result("trusted-repo-but-caller-C-other", redirected)

    grep_c = sealed(repo, ["git", "grep", "-c", "two", "HEAD"])
    grep_C = sealed(repo, ["git", "grep", "-C", "1", "two", "HEAD"])
    show_result("subcommand-local-c-is-not-global-config", grep_c)
    show_result("subcommand-local-C-is-not-global-redirection", grep_C)

    print("\nEXECUTABLE_TOKEN")
    resolved_git = shutil.which("git")
    print(f"which-git={resolved_git!r}")
    for token in [resolved_git, str(Path(resolved_git).resolve()) if resolved_git else None, "git"]:
        if token is None:
            continue
        try:
            argv = sealed_git_argv_v2([token, "--version"], trusted_repo_root=repo)
        except Exception as exc:
            print(f"token={token!r}: {type(exc).__name__}: {exc}")
        else:
            result = run(argv, cwd=repo, env=sealed_git_child_env_v2())
            print(f"token={token!r}: rc={result.returncode} out={result.stdout.strip()!r}")

    fake_bin = CASE / "fake-bin"
    fake_bin.mkdir()
    fake_marker = CASE / "fake-git-ran"
    make_executable(
        fake_bin / "git",
        f"#!/bin/sh\n: > {fake_marker}\nprintf 'fake git\\n'\n",
    )
    fake_env = sealed_git_child_env_v2()
    fake_env["PATH"] = str(fake_bin) + os.pathsep + fake_env["PATH"]
    fake_result = run(
        sealed_git_argv_v2(["git", "--version"], trusted_repo_root=repo),
        cwd=repo,
        env=fake_env,
    )
    print(f"literal-git-via-PATH: rc={fake_result.returncode} out={fake_result.stdout.strip()!r} marker={fake_marker.exists()}")


if __name__ == "__main__":
    main()
