"""`#200-D` correction: sealed Git execution boundary (issue #200).

Proves `GIT_SEMANTIC_EXECUTION_INVARIANT`'s environment-level mechanisms in
isolation, against real Git subprocesses -- never mocked -- so a future
change to `sealed_git_child_env_v2()` that silently drops one of these
protections is caught here, independent of any authority (diff acquisition,
toolrepo identity) that happens to use it today.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agent_review._sealed_git_execution_v2 import (
    has_executable_local_filter_config_v2,
    has_semantically_active_info_attributes_v2,
    sealed_git_argv_v2,
    sealed_git_child_env_v2,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_sealed_env_strips_every_neutralized_variable(monkeypatch):
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
        "GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS", "GIT_ATTR_SOURCE", "GIT_CONFIG_COUNT",
    ):
        monkeypatch.setenv(name, "ambient-value")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.pager")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "cat")

    env = sealed_git_child_env_v2()

    assert "GIT_DIR" not in env
    assert "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env
    assert "GIT_OBJECT_DIRECTORY" not in env
    assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in env
    assert "GIT_COMMON_DIR" not in env
    assert "GIT_NAMESPACE" not in env
    assert "GIT_EXTERNAL_DIFF" not in env
    assert "GIT_DIFF_OPTS" not in env
    assert "GIT_ATTR_SOURCE" not in env
    assert "GIT_CONFIG_COUNT" not in env
    assert "GIT_CONFIG_KEY_0" not in env
    assert "GIT_CONFIG_VALUE_0" not in env
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_SYSTEM"] == os.devnull
    assert "PATH" in env, "PATH must survive sealing -- Git needs it to execute at all"


def test_sealed_env_closes_blob_replacement_substitution(tmp_path: Path):
    """M1: a blob replacement must not change what a sealed `git cat-file`
    returns for the ORIGINAL declared object id."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("original content\n")
    _commit_all(repo, "init")
    blob = subprocess.run(
        ["git", "rev-parse", "HEAD:f"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    malicious_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input="MALICIOUS\n",
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "replace", blob, malicious_blob], cwd=repo, check=True)

    unsealed = subprocess.run(
        ["git", "cat-file", "-p", blob], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    assert unsealed == "MALICIOUS\n", "sanity: the vulnerability must be real before proving the fix"

    sealed = subprocess.run(
        ["git", "cat-file", "-p", blob], cwd=repo, env=sealed_git_child_env_v2(),
        capture_output=True, text=True, check=True,
    ).stdout
    assert sealed == "original content\n"


def test_sealed_env_closes_commit_replacement_for_diff(tmp_path: Path):
    """M2: a commit-level replacement of HEAD must not change what a sealed
    `git diff --name-only HEAD` reports."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("v1\n")
    _commit_all(repo, "init")
    real_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    fake_repo = tmp_path / "fake"
    _init_repo(fake_repo)
    (fake_repo / "f").write_text("MALICIOUS DIVERGENT CONTENT\n")
    fake_head = _commit_all(fake_repo, "fake")
    bundle = tmp_path / "fake.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "HEAD"], cwd=fake_repo, check=True)
    subprocess.run(["git", "fetch", "-q", str(bundle), "HEAD:refs/fake-import"], cwd=repo, check=True)

    subprocess.run(["git", "replace", real_head, fake_head], cwd=repo, check=True)

    unsealed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD", "--", "f"], cwd=repo, capture_output=True, text=True
    ).stdout
    assert unsealed, "sanity: the vulnerability must be real -- HEAD's replaced tree disagrees with the working copy"

    sealed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "HEAD", "--", "f"], cwd=repo,
        env=sealed_git_child_env_v2(), capture_output=True, text=True,
    ).stdout
    assert sealed == "", "sealed diff must compare against the REAL HEAD tree, not the replacement"


def test_sealed_env_resists_ambient_git_dir_redirection(tmp_path: Path, monkeypatch):
    """M3: an ambient GIT_DIR pointing at an unrelated repository must not
    redirect a sealed Git call away from the intended repository."""

    real_repo = tmp_path / "real"
    _init_repo(real_repo)
    (real_repo / "f").write_text("real\n")
    real_head = _commit_all(real_repo, "real")

    evil_repo = tmp_path / "evil"
    _init_repo(evil_repo)
    (evil_repo / "e").write_text("evil\n")
    _commit_all(evil_repo, "evil")

    unsealed_env = {**os.environ, "GIT_DIR": str(evil_repo / ".git")}
    unsealed = subprocess.run(
        ["git", "-C", str(real_repo), "log", "--format=%H", "-1"],
        env=unsealed_env, capture_output=True, text=True,
    ).stdout.strip()
    assert unsealed != real_head, "sanity: ambient GIT_DIR must actually redirect before sealing is proven to fix it"

    monkeypatch.setenv("GIT_DIR", str(evil_repo / ".git"))
    sealed_env = sealed_git_child_env_v2()
    sealed = subprocess.run(
        ["git", "-C", str(real_repo), "log", "--format=%H", "-1"],
        env=sealed_env, capture_output=True, text=True,
    ).stdout.strip()
    assert sealed == real_head


def test_sealed_env_resists_ambient_git_object_directory_redirection(tmp_path: Path, monkeypatch):
    """M3, GIT_OBJECT_DIRECTORY variant: an ambient override must not
    prevent a sealed call from resolving the repository's own real
    objects."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("real\n")
    head = _commit_all(repo, "real")

    empty_objects = tmp_path / "empty-objects"
    empty_objects.mkdir()

    unsealed_env = {**os.environ, "GIT_OBJECT_DIRECTORY": str(empty_objects)}
    unsealed = subprocess.run(
        ["git", "cat-file", "-p", head], cwd=repo, env=unsealed_env, capture_output=True, text=True,
    )
    assert unsealed.returncode != 0, "sanity: the injected object directory must actually break resolution first"

    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(empty_objects))
    sealed = subprocess.run(
        ["git", "cat-file", "-p", head], cwd=repo, env=sealed_git_child_env_v2(),
        capture_output=True, text=True,
    )
    assert sealed.returncode == 0, sealed.stderr


def test_info_attributes_absent_is_inactive(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("x\n")
    _commit_all(repo, "init")
    assert has_semantically_active_info_attributes_v2(repo, env=sealed_git_child_env_v2()) is False


def test_info_attributes_with_content_is_active(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("x\n")
    _commit_all(repo, "init")
    (repo / ".git" / "info" / "attributes").write_text("f -diff\n")
    assert has_semantically_active_info_attributes_v2(repo, env=sealed_git_child_env_v2()) is True


def test_info_attributes_comment_only_is_inactive(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("x\n")
    _commit_all(repo, "init")
    (repo / ".git" / "info" / "attributes").write_text("# nothing active\n\n   \n")
    assert has_semantically_active_info_attributes_v2(repo, env=sealed_git_child_env_v2()) is False


def test_info_attributes_leaks_into_a_linked_worktree(tmp_path: Path):
    """Documents WHY `info/attributes` needs its own explicit check rather
    than being closed by the disposable-worktree isolation
    `diff_acquisition_v2` uses for the working-tree `.gitattributes` vector:
    `info/attributes` is shared common-dir state, present for every
    worktree of a repository, including a freshly created one."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f").write_text("v1\n")
    head = _commit_all(repo, "init")
    (repo / ".git" / "info" / "attributes").write_text("f -diff\n")

    worktree_dir = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--quiet", "--detach", str(worktree_dir), head],
        cwd=repo, env=sealed_git_child_env_v2(), check=True,
    )
    try:
        assert has_semantically_active_info_attributes_v2(
            worktree_dir, env=sealed_git_child_env_v2()
        ) is True
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_dir)],
            cwd=repo, env=sealed_git_child_env_v2(), check=True,
        )


def test_sealed_argv_splices_hook_neutralization_after_the_executable():
    """`-c` options are only honoured between `git` and the subcommand, so
    placement is part of the contract, not a formatting detail."""

    argv = sealed_git_argv_v2(
        ["git", "diff", "--binary", "abc...def"], trusted_repo_root=Path("/srv/checkout")
    )

    assert argv == [
        "git",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "core.fsmonitor=false",
        "-c", "safe.directory=/srv/checkout",
        "diff", "--binary", "abc...def",
    ]


def test_sealed_argv_rejects_an_argv_that_is_not_git():
    """A call site handing this something other than `git` is a defect in
    this package, so it raises rather than becoming a subject refusal."""

    with pytest.raises(ValueError):
        sealed_git_argv_v2(["not-git", "diff"], trusted_repo_root=Path("/srv/checkout"))
    with pytest.raises(ValueError):
        sealed_git_argv_v2([], trusted_repo_root=Path("/srv/checkout"))


def test_sealed_argv_suppresses_a_planted_post_checkout_hook(tmp_path: Path):
    """`git worktree add` runs the TARGET's `post-checkout` hook. Reproduced
    directly: without the spliced `core.hooksPath`, this marker is created."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    head = _commit_all(repo, "base")

    marker = tmp_path / "hook-ran"
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    env = sealed_git_child_env_v2()
    worktree = tmp_path / "wt"
    subprocess.run(
        sealed_git_argv_v2(
            ["git", "worktree", "add", "--quiet", "--detach", str(worktree), head],
            trusted_repo_root=repo,
        ),
        cwd=repo, env=env, capture_output=True, check=True,
    )

    assert (worktree / "f.txt").is_file(), "the worktree must still materialize"
    assert not marker.exists(), "target-controlled post-checkout hook executed"


def test_sealed_argv_overrides_a_repository_local_hooks_path_redirect(tmp_path: Path):
    """Repository-local `.git/config` stays reachable by design, so a
    `core.hooksPath` redirect has to be beaten on the command line."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    head = _commit_all(repo, "base")

    marker = tmp_path / "redirected-hook-ran"
    evil_hooks = tmp_path / "evil-hooks"
    evil_hooks.mkdir()
    hook = evil_hooks / "post-checkout"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "config", "core.hooksPath", str(evil_hooks)], cwd=repo, check=True
    )

    env = sealed_git_child_env_v2()
    worktree = tmp_path / "wt"
    subprocess.run(
        sealed_git_argv_v2(
            ["git", "worktree", "add", "--quiet", "--detach", str(worktree), head],
            trusted_repo_root=repo,
        ),
        cwd=repo, env=env, capture_output=True, check=True,
    )

    assert (worktree / "f.txt").is_file(), "the worktree must still materialize"
    assert not marker.exists(), "repository-local core.hooksPath redirect executed"


def test_sealed_argv_suppresses_a_target_controlled_fsmonitor(tmp_path: Path):
    """`core.fsmonitor` holds a command Git executes to enumerate
    working-tree changes. Reproduced directly running during `git status`."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base")

    marker = tmp_path / "fsmonitor-ran"
    subprocess.run(
        ["git", "config", "core.fsmonitor", f"sh -c 'touch {marker}; echo'"],
        cwd=repo, check=True,
    )

    subprocess.run(
        sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=repo),
        cwd=repo, env=sealed_git_child_env_v2(), capture_output=True, check=True,
    )

    assert not marker.exists(), "target-controlled core.fsmonitor executed"


def test_sealed_argv_admits_a_foreign_owned_checkout(tmp_path: Path):
    """`GIT_CONFIG_GLOBAL=/dev/null` also discards the operator's
    `safe.directory`, and Git then refuses any checkout owned by another
    uid outright. That is the ordinary container/CI case, so the declared
    subject is named on the command line instead."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base")

    try:
        os.chown(repo, 65534, 65534)
        for path in repo.rglob("*"):
            os.chown(path, 65534, 65534)
    except (PermissionError, OSError):
        pytest.skip("cannot change ownership in this environment")
    if os.geteuid() == 65534:
        pytest.skip("process already runs as the owning uid")

    unsealed = subprocess.run(
        ["git", "status", "--short"], cwd=repo,
        env={**sealed_git_child_env_v2()}, capture_output=True, text=True, check=False,
    )
    assert unsealed.returncode != 0 and "dubious ownership" in unsealed.stderr, (
        "the precondition this guards against no longer reproduces"
    )

    sealed = subprocess.run(
        sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=repo),
        cwd=repo, env=sealed_git_child_env_v2(), capture_output=True, text=True, check=False,
    )
    assert sealed.returncode == 0, f"sealed git refused a declared subject: {sealed.stderr}"


def test_sealed_argv_safe_directory_does_not_admit_other_repositories(tmp_path: Path):
    """Naming the declared subject must not become a blanket grant."""

    subject = tmp_path / "subject"
    _init_repo(subject)
    (subject / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(subject, "base")

    other = tmp_path / "other"
    _init_repo(other)
    (other / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(other, "base")

    try:
        os.chown(other, 65534, 65534)
        for path in other.rglob("*"):
            os.chown(path, 65534, 65534)
    except (PermissionError, OSError):
        pytest.skip("cannot change ownership in this environment")
    if os.geteuid() == 65534:
        pytest.skip("process already runs as the owning uid")

    result = subprocess.run(
        sealed_git_argv_v2(["git", "status", "--short"], trusted_repo_root=subject),
        cwd=other, env=sealed_git_child_env_v2(), capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0, "safe.directory for one repo admitted another"
    assert "dubious ownership" in result.stderr


def test_executable_local_filter_config_is_detected(tmp_path: Path):
    """A repository-local filter driver executes on checkout and has no
    command-line closure, so it is detected and refused instead."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base")
    env = sealed_git_child_env_v2()

    assert has_executable_local_filter_config_v2(repo, env=env) is False

    subprocess.run(
        ["git", "config", "filter.evil.smudge", "sh -c 'touch /tmp/x; cat'"],
        cwd=repo, check=True,
    )
    assert has_executable_local_filter_config_v2(repo, env=env) is True


def test_non_executable_filter_config_is_not_treated_as_executable(tmp_path: Path):
    """`filter.<driver>.required` carries no command, so it must not by
    itself make a repository unreviewable."""

    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    _commit_all(repo, "base")

    subprocess.run(["git", "config", "filter.lfs.required", "false"], cwd=repo, check=True)

    assert has_executable_local_filter_config_v2(repo, env=sealed_git_child_env_v2()) is False
