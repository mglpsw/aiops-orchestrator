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
    # Mirrors the real toolrepo's shape: app/ contains MULTIPLE packages
    # (agent_review, common, ...), not just agent_review -- the bounded
    # source set covers the whole app/ tree precisely because the composed
    # review path imports across that boundary (e.g. app.common.strict_json
    # from review_transport_v2.py and several sibling modules).
    (root / "app" / "agent_review").mkdir(parents=True)
    (root / "app" / "common").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent_review" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "agent_review" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (root / "app" / "common" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "common" / "strict_json.py").write_text("y = 2\n", encoding="utf-8")
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


def test_dirty_tracked_file_outside_agent_review_but_inside_app_is_refused(fixture_toolrepo):
    """The bounded set covers the WHOLE `app/` package, not just
    `app/agent_review` -- the composed review path imports across that
    boundary (`app.common.strict_json`, reached from `review_transport_v2.py`
    and several sibling modules). A dirty file under `app/common/` must
    refuse a run exactly like one under `app/agent_review/`."""

    root, head_sha = fixture_toolrepo
    target = root / "app" / "common" / "strict_json.py"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "# dirty\n", encoding="utf-8")
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        target.write_text(original, encoding="utf-8")


def test_staged_dirty_file_outside_agent_review_but_inside_app_is_refused(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    target = root / "app" / "common" / "strict_json.py"
    original = target.read_text(encoding="utf-8")
    target.write_text(original + "# staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/common/strict_json.py"], cwd=root, check=True)
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        subprocess.run(["git", "reset", "--quiet", "HEAD", "--", "app/common/strict_json.py"], cwd=root, check=True)
        target.write_text(original, encoding="utf-8")


def test_untracked_file_outside_agent_review_but_inside_app_is_unverifiable(fixture_toolrepo):
    root, head_sha = fixture_toolrepo
    stray = root / "app" / "common" / "_stray_v2.py"
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


def test_gitignore_evasion_of_untracked_check_is_closed(fixture_toolrepo):
    """M6: `#200-D` correction. A stray importable source file matched by a
    `.gitignore` entry must still be refused -- `--exclude-standard`
    (removed from the untracked-source check) would have made it
    completely invisible here, reproduced directly before the fix
    existed."""

    root, head_sha = fixture_toolrepo
    (root / ".gitignore").write_text("app/agent_review/_stray*.py\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "add gitignore"], cwd=root, check=True)
    head_sha_2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()

    stray = root / "app" / "agent_review" / "_stray_evil.py"
    stray.write_text("malicious = True\n", encoding="utf-8")
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha_2)
        assert excinfo.value.reason_code == TOOLREPO_IDENTITY_UNVERIFIABLE_REASON_V2
    finally:
        stray.unlink()


def test_deleted_cli_script_is_refused_not_silently_dropped(fixture_toolrepo):
    """M7: `#200-D` correction. Deleting the exact runner CLI path must be
    detected -- a `.exists()` filesystem prefilter (removed) would have
    silently excluded the deleted path from the pathspec `git diff` was
    even asked about, reproduced directly before the fix existed."""

    root, head_sha = fixture_toolrepo
    cli = root / "scripts" / "aiops-review-run-v2.py"
    cli.unlink()
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        subprocess.run(["git", "checkout", "--", "scripts/aiops-review-run-v2.py"], cwd=root, check=True)


def test_deleted_tracked_app_source_is_refused(fixture_toolrepo):
    """M8: the same deletion-visibility property for a tracked file under
    the bounded `app/` tree, not just the CLI script."""

    root, head_sha = fixture_toolrepo
    mod = root / "app" / "agent_review" / "mod.py"
    mod.unlink()
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        subprocess.run(["git", "checkout", "--", "app/agent_review/mod.py"], cwd=root, check=True)


def test_replace_deleted_tracked_file_with_untracked_same_spelling_is_refused(fixture_toolrepo):
    """A deleted tracked file replaced by an untracked file of the identical
    path must still be refused: the deletion itself is dirty (staged or
    not), independent of whatever untracked content now occupies that
    path."""

    root, head_sha = fixture_toolrepo
    mod = root / "app" / "agent_review" / "mod.py"
    mod.unlink()
    mod.write_text("# untracked replacement, same path\n", encoding="utf-8")
    try:
        with pytest.raises(ToolrepoIdentityError) as excinfo:
            establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert excinfo.value.reason_code == TOOLREPO_WORKTREE_DIRTY_REASON_V2
    finally:
        subprocess.run(["git", "checkout", "--", "app/agent_review/mod.py"], cwd=root, check=True)


def test_commit_replacement_of_toolrepo_head_does_not_fool_identity(fixture_toolrepo, tmp_path):
    """M2, end-to-end through the real authority: a `git replace` of the
    toolrepo's own HEAD commit with one pointing at different bounded-path
    content must not be able to make identity describe the replacement, and
    must not spuriously report dirtiness (or spuriously report cleanliness)
    based on the replacement's tree instead of the real one."""

    root, head_sha = fixture_toolrepo

    fake_root = tmp_path / "fake-source"
    fake_root.mkdir()
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=fake_root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=fake_root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=fake_root, check=True)
    (fake_root / "app").mkdir()
    (fake_root / "agent_review_placeholder.txt").write_text("unrelated\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=fake_root, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fake"], cwd=fake_root, check=True)
    fake_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=fake_root, capture_output=True, text=True, check=True
    ).stdout.strip()

    bundle = tmp_path / "fake.bundle"
    subprocess.run(["git", "bundle", "create", str(bundle), "HEAD"], cwd=fake_root, check=True)
    subprocess.run(["git", "fetch", "--quiet", str(bundle), "HEAD:refs/fake-import"], cwd=root, check=True)
    subprocess.run(["git", "replace", head_sha, fake_head], cwd=root, check=True)

    try:
        identity = establish_toolrepo_source_identity_v2(declared_toolrepo_sha=head_sha)
        assert identity.toolrepo_sha == head_sha
    finally:
        subprocess.run(["git", "replace", "-d", head_sha], cwd=root, check=True)


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
