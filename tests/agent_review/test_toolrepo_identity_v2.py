"""`#200-D` successor: toolrepo SOURCE checkout identity (issue #200).

Proves `TOOLREPO_SOURCE_IDENTITY_INVARIANT`'s six clauses against a real,
isolated fixture toolrepo -- never against this test run's own checkout,
so a genuinely dirty development tree never makes this suite flaky and a
genuinely clean tree never makes it vacuous.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import app.agent_review as agent_review_package
from app.agent_review.toolrepo_identity_v2 import (
    TOOLREPO_IDENTITY_MISMATCH_REASON_V2,
    TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2,
    TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2,
    TOOLREPO_WORKTREE_DIRTY_REASON_V2,
    ToolrepoIdentityError,
    establish_toolrepo_source_identity_v2,
    resolve_toolrepo_root_v2,
)


def _init_fixture_toolrepo(root: Path) -> str:
    (root / "app" / "agent_review").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "app" / "agent_review" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent_review" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "scripts" / "aiops-review-run-v2.py").write_text("# cli\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "init"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def fixture_toolrepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "fixture-toolrepo"
    head_sha = _init_fixture_toolrepo(root)
    monkeypatch.setattr(agent_review_package, "__file__", str(root / "app" / "agent_review" / "__init__.py"))
    return root, head_sha


def test_clean_matching_sha_succeeds(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    identity = establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
    assert identity.toolrepo_sha == head_sha
    assert identity.toolrepo_root == root.resolve()


def test_wrong_sha_is_a_mismatch(fixture_toolrepo):
    _, head_sha = fixture_toolrepo
    with pytest.raises(ToolrepoIdentityError) as excinfo:
        establish_toolrepo_source_identity_v2(declared_toolrepo_sha="a" * 40)
    assert excinfo.value.reason_code == TOOLREPO_IDENTITY_MISMATCH_REASON_V2


@pytest.mark.parametrize("malformed", ["short", "g" * 40, "", "A" * 40, "not-a-sha-at-all"])
def test_malformed_sha_shape_is_a_mismatch(fixture_toolrepo, malformed):
    with pytest.raises(ToolrepoIdentityError) as excinfo:
        establish_toolrepo_source_identity_v2(declared_toolrepo_sha=malformed)
    assert excinfo.value.reason_code == TOOLREPO_IDENTITY_MISMATCH_REASON_V2


def test_executing_script_outside_root_is_a_mismatch(fixture_toolrepo, tmp_path):
    _, head_sha = fixture_toolrepo
    outside = tmp_path / "elsewhere" / "cli.py"
    with pytest.raises(ToolrepoIdentityError) as excinfo:
        establish_toolrepo_source_identity_v2(
            declared_toolrepo_sha=head_sha, executing_script=outside
        )
    assert excinfo.value.reason_code == TOOLREPO_IDENTITY_MISMATCH_REASON_V2


def test_executing_script_inside_root_succeeds(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    identity = establish_toolrepo_source_identity_v2(
        declared_toolrepo_sha=head_sha, executing_script=root / "scripts" / "aiops-review-run-v2.py"
    )
    assert identity.toolrepo_sha == head_sha


def test_dirty_tracked_bounded_file_is_refused(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    target = root / "app" / "agent_review" / "mod.py"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "# dirty\n", encoding="utf-8")
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        target.write_text(original, encoding="utf-8")


def test_staged_but_uncommitted_bounded_change_is_refused(fixture_toolrepo):
    """A change staged with `git add` but not yet committed must be
    detected -- `git diff --name-only HEAD` (not `git diff` alone) covers
    both staged and unstaged tracked changes relative to HEAD."""

    root, head_sha = fixture_toolrepo
    target = root / "app" / "agent_review" / "mod.py"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "# staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/agent_review/mod.py"], cwd=root, check=True)
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        subprocess.run(["git", "reset", "--quiet", "HEAD", "--", "app/agent_review/mod.py"], cwd=root, check=True)
        target.write_text(original, encoding="utf-8")


def test_untracked_importable_source_is_unverifiable(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    stray = root / "app" / "agent_review" / "_stray_v2.py"
    stray.write_text("# stray\n", encoding="utf-8")
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2
    finally:
        stray.unlink()


def test_dirty_file_outside_bounded_source_set_does_not_block(fixture_toolrepo):
    """A dirty file elsewhere in the toolrepo (a doc, a fixture) must not
    refuse a run -- only the structurally bounded source set matters."""

    root, head_sha = fixture_toolrepo
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "readme"], cwd=root, check=True)
    head_sha_2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    (root / "README.md").write_text("hello dirty\n", encoding="utf-8")

    identity = establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha_2)
    assert identity.toolrepo_sha == head_sha_2


def test_gitless_toolrepo_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "gitless-toolrepo"
    (root / "app" / "agent_review").mkdir(parents=True)
    (root / "app" / "agent_review" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(agent_review_package, "__file__", str(root / "app" / "agent_review" / "__init__.py"))

    with pytest.raises(ToolrepoIdentityError) as excinfo:
        establish_toolrepo_source_identity_v2(declared_toolrepo_sha="a" * 40)
    assert excinfo.value.reason_code == TOOLREPO_IDENTITY_UNAVAILABLE_REASON_V2


def test_second_order_honesty_is_documented():
    """This module cannot claim zero unverified code execution -- it is
    itself imported before it verifies anything. The docstring must state
    the honest, narrower claim."""

    from app.agent_review import toolrepo_identity_v2

    doc = " ".join((toolrepo_identity_v2.__doc__ or "").split())
    assert "review execution was blocked before semantic review/transport" in doc
    assert "zero unverified code" in doc
