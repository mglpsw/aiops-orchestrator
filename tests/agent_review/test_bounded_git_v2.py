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
    BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2,
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


# -- `#200-G1-PM` finding 5: arbitrary (non-`origin`) promisor remotes -----------


def test_partial_clone_with_non_origin_promisor_remote_is_refused(tmp_path: Path) -> None:
    """`remote.origin.promisor=false`, hardcoded, only disables lazy fetching
    for a remote literally named `origin`. `git remote rename origin evil`
    (ordinary git, real plumbing below, no mocking) preserves the
    `promisor` flag under the new name, and this primitive is meant to be
    fully offline -- nothing it does may pull bytes from outside the
    repository's own local object store, regardless of what a repository's
    own remote happens to be named.

    Also proves the severity is broader than "only the non-`origin` case is
    unhandled": empirically, the *pre-fix* `-c remote.origin.promisor=false`
    override did not block the fetch even when the remote was still named
    `origin` on this git build (verified separately, not asserted here) --
    the fix in this module refuses on ANY promisor remote rather than
    trying to suppress the fetch by name or by protocol/env switch, none of
    which proved reliable.
    """
    upstream = tmp_path / "upstream"
    _init_repo(upstream)
    (upstream / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=upstream, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=upstream, check=True)
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=upstream, check=True)
    subprocess.run(
        ["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=upstream, check=True
    )
    blob_sha = subprocess.run(
        ["git", "rev-parse", "HEAD:f.txt"],
        cwd=upstream,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject = tmp_path / "subject"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            f"file://{upstream}",
            str(subject),
        ],
        check=True,
    )
    subprocess.run(["git", "remote", "rename", "origin", "evil"], cwd=subject, check=True)

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2


def test_repo_without_any_promisor_remote_is_unaffected(tmp_path: Path) -> None:
    """Sanity check: the finding-5 fix must not refuse an ordinary,
    non-partial-clone repository with no remotes at all -- the overwhelming
    majority of calls this primitive ever makes."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=repo, check=True)

    completed = run_bounded_git_v2(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo)
    assert completed.returncode == 0


# -- post-review correction: promisor value spelled other than literal `true` --


def test_partial_clone_with_non_true_spelled_promisor_value_is_refused(tmp_path: Path) -> None:
    """External Codex review of the finding-5 fix itself (round 1 on this
    PR): comparing the raw config value against the literal bytes
    `b"true"` misses every other legal git-boolean spelling of the same
    value -- `yes`, `on`, `1`, and bare presence with no value, none of
    which `git config --set` normalises on write. Reproduced with a real
    partial clone below, then `git config remote.origin.promisor yes`
    (still the same remote, deliberately isolating this one variable from
    the already-covered non-`origin`-name case): before this fix, the raw
    `git config --get-regexp` (no `--type`) echoed the literal text `yes`
    unchanged, which the literal-`b"true"` comparison never matched,
    leaving lazy fetch fully enabled."""
    upstream = tmp_path / "upstream"
    _init_repo(upstream)
    (upstream / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "f.txt"], cwd=upstream, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=upstream, check=True)
    subprocess.run(["git", "config", "uploadpack.allowFilter", "true"], cwd=upstream, check=True)
    subprocess.run(
        ["git", "config", "uploadpack.allowAnySHA1InWant", "true"], cwd=upstream, check=True
    )
    blob_sha = subprocess.run(
        ["git", "rev-parse", "HEAD:f.txt"],
        cwd=upstream,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject = tmp_path / "subject"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            f"file://{upstream}",
            str(subject),
        ],
        check=True,
    )
    # Same remote name (`origin`) as the already-covered case -- isolating
    # the boolean-spelling variable specifically.
    subprocess.run(
        ["git", "config", "remote.origin.promisor", "yes"], cwd=subject, check=True
    )

    with pytest.raises(BoundedGitError) as excinfo:
        run_bounded_git_v2(
            ["cat-file", "--batch"], cwd=subject, input_bytes=(blob_sha + "\n").encode()
        )
    assert excinfo.value.reason_code == BOUNDED_GIT_PROMISOR_REMOTE_PRESENT_REASON_V2
