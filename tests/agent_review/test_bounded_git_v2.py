"""`#200-G1` -- new tests for the ported bounded-git child environment.

Ported with revalidation from the frozen-forensic `#200-F` reconstruction
(commit `5703e5b`); no qualification transfer -- these tests are new.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agent_review.bounded_git_v2 import (
    BOUNDED_GIT_COMMAND_FAILED_REASON_V2,
    BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2,
    BoundedGitError,
    bounded_git_environment_v2,
    resolve_trusted_git_absolute_path_v2,
    run_bounded_git_v2,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def test_bounded_environment_key_set_is_exact() -> None:
    """The child's environment mapping is asserted on its exact key set so
    it cannot silently drift into `dict(os.environ)` with deletions."""
    env = bounded_git_environment_v2()
    assert set(env) == {
        "PATH",
        "LC_ALL",
        "LANG",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS",
        "GIT_ASKPASS",
        "GIT_SSH_COMMAND",
        "HOME",
    }


def test_bounded_environment_path_is_defpath_not_caller_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/attacker/controlled/bin")
    env = bounded_git_environment_v2()
    assert env["PATH"] == os.defpath
    assert "/attacker/controlled/bin" not in env["PATH"]


def test_bounded_environment_home_defaults_to_devnull_not_callers_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/attacker/controlled/home")
    env = bounded_git_environment_v2()
    assert env["HOME"] == os.devnull


def test_resolve_trusted_git_ignores_caller_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text("#!/bin/sh\necho fake\n")
    (fake_bin / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    resolved = resolve_trusted_git_absolute_path_v2()
    assert resolved != str(fake_bin / "git")
    assert Path(resolved).is_absolute()


def test_run_bounded_git_refuses_nonexistent_cwd(tmp_path: Path) -> None:
    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(["status"], cwd=tmp_path / "does-not-exist")
    assert excinfo.value.reason_code == BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2


def test_run_bounded_git_raises_on_command_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "refs/heads/does-not-exist"], cwd=repo)
    assert excinfo.value.reason_code == BOUNDED_GIT_COMMAND_FAILED_REASON_V2


def test_run_bounded_git_check_false_does_not_raise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    completed = run_bounded_git_v2(
        ["rev-parse", "--verify", "--quiet", "refs/heads/does-not-exist"],
        cwd=repo,
        check=False,
    )
    assert completed.returncode != 0


def test_run_bounded_git_ignores_ambient_git_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)

    monkeypatch.setenv("GIT_DIR", str(tmp_path / "totally-unrelated.git"))
    completed = run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo)
    assert completed.returncode == 0
