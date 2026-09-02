"""`#200-G1` -- executed source identity bound to git commit authority.

The first two tests in this file (`test_round1_narrow_root_attack_is_refused`
and `test_round2_tampered_code_honest_digest_honest_sha_fabrication_is_
refused`) are the two independently-reproduced falsifiers that refuted
`#277`'s `operational_inner_control_v2.py`, ported forward as RED tests
against this replacement per the `#200-G1` process contract. They were
written and run against a stub (`NotImplementedError`) before any real
verification logic existed, and are not thrown away afterwards -- they stay
in the permanent corpus below.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

import app.agent_review.commit_derived_execution_identity_v2 as commit_derived_execution_identity_module
from app.agent_review.bounded_git_v2 import BoundedGitError
from app.agent_review.commit_derived_execution_identity_v2 import (
    IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2,
    IDENTITY_BLOB_MISSING_REASON_V2,
    IDENTITY_CONTENT_MISMATCH_REASON_V2,
    IDENTITY_DUPLICATE_TREE_PATH_REASON_V2,
    IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2,
    IDENTITY_GITLINK_PRESENT_REASON_V2,
    IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2,
    IDENTITY_MISSING_TRACKED_FILE_REASON_V2,
    IDENTITY_MODE_MISMATCH_REASON_V2,
    IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2,
    IDENTITY_SYMLINKED_DIRECTORY_REASON_V2,
    IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2,
    IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2,
    IDENTITY_UNKNOWN_COMMIT_REASON_V2,
    ExecutedSourceIdentityError,
    authorize_commit_for_execution_v2,
    loaded_module_files_v2,
    verify_executed_source_identity_v2,
)
from app.agent_review.git_commit_subject_v2 import (
    SubjectMaterialisationError,
    compute_subject_digest_v2,
    materialise_commit_subject_v2,
    resolve_commit_v2,
)


# -- fixtures ------------------------------------------------------------------


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return _rev_parse(repo, "HEAD")


def _rev_parse(repo: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _toolrepo_fixture(tmp_path: Path) -> tuple[Path, str]:
    """A minimal repo shaped like the real toolrepo: an entry point plus a
    'semantic package' directory, so narrow-root and content-mismatch
    scenarios can be expressed the same way the real attack was."""
    repo = tmp_path / "toolrepo"
    _init_repo(repo)
    (repo / "scripts").mkdir()
    (repo / "scripts" / "entry.py").write_text("import app_agent_review.core\n")
    (repo / "app_agent_review").mkdir()
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\n")
    head_sha = _commit_all(repo, "init")
    return repo, head_sha


# -- round 1: narrow-root attack (RED-first) ------------------------------------


def test_round1_narrow_root_attack_is_refused(tmp_path: Path) -> None:
    """`#277` round 1: a caller declares a subject root narrowed to a
    subdirectory that genuinely contains the entry script but excludes the
    real semantic package. The narrowed root's own digest is entirely
    honest -- the forgery is in what was excluded, not in any hash."""
    repo, head_sha = _toolrepo_fixture(tmp_path)

    narrow_root = tmp_path / "narrow_subject"
    narrow_root.mkdir()
    shutil.copy(repo / "scripts" / "entry.py", narrow_root / "entry.py")

    # The semantic module actually "running" lives outside the narrowed
    # root -- this is the load-bearing fact a correct verifier must catch.
    loaded_semantic_module = repo / "app_agent_review" / "core.py"

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=head_sha,
            subject_root=narrow_root,
            loaded_module_paths=(loaded_semantic_module,),
        )
    assert excinfo.value.reason_code in (
        IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2,
        IDENTITY_MISSING_TRACKED_FILE_REASON_V2,
    )


# -- round 2: fabricated digest over a tampered-but-correctly-rooted tree (RED-first) --


def test_round2_tampered_code_honest_digest_honest_sha_fabrication_is_refused(
    tmp_path: Path,
) -> None:
    """`#277` round 2: the root is declared correctly and materialised in
    full. A module inside it is tampered *after* materialisation. The
    attacker recomputes a digest honestly, with this codebase's own public
    digest helper, over the tampered tree, and declares the real, honest
    HEAD sha. Nothing about the declared document is internally
    inconsistent -- the fabrication only shows up by comparing against git
    itself, which is exactly what a caller-trusting verifier never does."""
    repo, head_sha = _toolrepo_fixture(tmp_path)

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialised = materialise_commit_subject_v2(
        repo_root=repo, ref=head_sha, destination=subject_root
    )
    assert materialised.commit_sha == head_sha

    tampered_module = subject_root / "app_agent_review" / "core.py"
    tampered_module.write_text("SEMANTIC = True\nBACKDOOR = True\n")

    # The fabrication: an "honest" digest of the tampered tree, computed
    # with the same public helper this module ships. A verifier that
    # merely compared two self-reported values would accept this.
    fabricated_digest = compute_subject_digest_v2(subject_root)
    assert fabricated_digest and len(fabricated_digest) == 64

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=head_sha,  # the real, honestly-committed HEAD sha
            subject_root=subject_root,
            loaded_module_paths=(tampered_module,),
        )
    assert excinfo.value.reason_code == IDENTITY_CONTENT_MISMATCH_REASON_V2


# -- happy path -----------------------------------------------------------------


def test_happy_path_verifies_successfully_for_honest_complete_subject(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    loaded = (subject_root / "app_agent_review" / "core.py",)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=loaded
    )
    assert identity.commit_sha == head_sha
    assert identity.subject_root == subject_root.resolve()


def test_executable_bit_is_materialised_and_verified(tmp_path: Path) -> None:
    repo, _ = _toolrepo_fixture(tmp_path)
    script = repo / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    head_sha = _commit_all(repo, "add executable")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert os.access(subject_root / "run.sh", os.X_OK)


def test_mode_mismatch_is_refused(tmp_path: Path) -> None:
    """A file materialised as non-executable but then chmod +x'd on disk
    (without touching content) must be caught -- content-only comparison
    would miss this."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    (subject_root / "scripts" / "entry.py").chmod(0o755)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_MODE_MISMATCH_REASON_V2


def test_symlink_content_materialised_and_verified(tmp_path: Path) -> None:
    repo, _ = _toolrepo_fixture(tmp_path)
    (repo / "link.py").symlink_to("app_agent_review/core.py")
    head_sha = _commit_all(repo, "add symlink")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert (subject_root / "link.py").is_symlink()


def test_tampered_symlink_target_is_refused(tmp_path: Path) -> None:
    repo, _ = _toolrepo_fixture(tmp_path)
    (repo / "link.py").symlink_to("app_agent_review/core.py")
    head_sha = _commit_all(repo, "add symlink")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    (subject_root / "link.py").unlink()
    (subject_root / "link.py").symlink_to("/etc/passwd")

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2


# -- required negative corpus (#200-G1 issue text) ------------------------------


def test_tampered_dev_worktree_is_invisible_to_identity(tmp_path: Path) -> None:
    """`mutable_dev_checkout must_not_define_executed_identity`: editing the
    tracked file directly in the source repo's *worktree*, without
    committing, must not change what materialises or verifies -- identity
    comes from git objects, never from worktree state."""
    repo, head_sha = _toolrepo_fixture(tmp_path)

    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nTAMPERED = True\n")
    # deliberately NOT committed

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    # The materialised bytes reflect the COMMITTED content, not the tamper.
    assert "TAMPERED" not in (subject_root / "app_agent_review" / "core.py").read_text()


def test_untracked_shadow_file_in_subject_is_refused(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    # An extra file planted directly into the subject after materialisation
    # -- e.g. a TOCTOU write, or a naive design that just points subject_root
    # at an arbitrary mutable directory.
    (subject_root / "app_agent_review" / "shadow.py").write_text("EVIL = True\n")

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2


def test_tracked_working_tree_edit_is_invisible_to_identity(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    (repo / "scripts" / "entry.py").write_text("import app_agent_review.core\n# edited\n")
    # not committed

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert "edited" not in (subject_root / "scripts" / "entry.py").read_text()


def test_assume_unchanged_does_not_hide_materialised_identity(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    target = repo / "app_agent_review" / "core.py"
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "app_agent_review/core.py"],
        cwd=repo,
        check=True,
    )
    target.write_text("SEMANTIC = True\nASSUME_UNCHANGED_TAMPER = True\n")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert "ASSUME_UNCHANGED_TAMPER" not in (
        subject_root / "app_agent_review" / "core.py"
    ).read_text()


def test_skip_worktree_does_not_hide_materialised_identity(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    target = repo / "app_agent_review" / "core.py"
    subprocess.run(
        ["git", "update-index", "--skip-worktree", "app_agent_review/core.py"],
        cwd=repo,
        check=True,
    )
    target.write_text("SEMANTIC = True\nSKIP_WORKTREE_TAMPER = True\n")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert "SKIP_WORKTREE_TAMPER" not in (
        subject_root / "app_agent_review" / "core.py"
    ).read_text()


def test_git_replace_ref_cannot_substitute_a_different_tree(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nOTHER = True\n")
    other_sha = _commit_all(repo, "other commit with different tree")

    subprocess.run(["git", "replace", head_sha, other_sha], cwd=repo, check=True)
    try:
        subject_root = tmp_path / "subject"
        subject_root.mkdir()
        materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
        identity = verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
        assert identity.commit_sha == head_sha
        # Content must be the ORIGINAL head_sha tree, not the replacement's.
        assert "OTHER" not in (subject_root / "app_agent_review" / "core.py").read_text()
    finally:
        subprocess.run(["git", "replace", "-d", head_sha], cwd=repo, check=True)


def test_hostile_git_env_vars_have_no_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)

    decoy_dir = tmp_path / "decoy_objects"
    decoy_dir.mkdir()
    decoy_index = tmp_path / "decoy_index"

    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(decoy_dir))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(decoy_dir))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy_index))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "nonexistent.git"))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=/tmp/hostile-hooks'")
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", "echo hostile-diff-ran > /tmp/hostile-diff-marker")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha


def test_fake_git_earlier_in_path_is_never_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)

    marker = tmp_path / "fake-git-ran"
    fake_git_dir = tmp_path / "fake-bin"
    fake_git_dir.mkdir()
    fake_git = fake_git_dir / "git"
    fake_git.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    fake_git.chmod(0o755)

    monkeypatch.setenv("PATH", f"{fake_git_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == head_sha
    assert not marker.exists()


def test_module_outside_the_executed_closure_is_refused(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    outside_module = tmp_path / "elsewhere.py"
    outside_module.write_text("SNEAKY = True\n")

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=head_sha,
            subject_root=subject_root,
            loaded_module_paths=(outside_module,),
        )
    assert excinfo.value.reason_code == IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2


def test_commit_absent_from_object_store_is_refused(tmp_path: Path) -> None:
    repo, _head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()

    plausible_but_absent_sha = "a" * 40
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=plausible_but_absent_sha,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_UNKNOWN_COMMIT_REASON_V2


def test_blob_absent_from_object_store_is_refused(tmp_path: Path) -> None:
    """A tree object honestly references a blob sha the object store does
    not actually have -- constructed directly with plumbing rather than
    corrupting a real commit, since that is the only reliable, git-version-
    independent way to produce this state."""
    repo, _head_sha = _toolrepo_fixture(tmp_path)

    nonexistent_blob_sha = "b" * 40
    mktree_input = f"100644 blob {nonexistent_blob_sha}\tmissing.py\n"
    tree_sha = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=mktree_input,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    commit_sha = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "tree with missing blob"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=commit_sha,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_BLOB_MISSING_REASON_V2


def test_gitlink_in_tree_is_refused(tmp_path: Path) -> None:
    """A gitlink (submodule reference, mode 160000) names a commit in
    another repository -- there are no bytes here for this primitive to
    verify, so it must refuse rather than silently skip the entry."""
    repo, _head_sha = _toolrepo_fixture(tmp_path)
    fake_submodule_sha = "c" * 40
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{fake_submodule_sha},vendored",
        ],
        cwd=repo,
        check=True,
    )
    # Deliberately NOT `_commit_all` (which runs `git add -A` first): the
    # gitlink path has no corresponding working-tree directory, so `-A`
    # would stage its removal again and cancel the `update-index` above.
    # Commit exactly what is already staged.
    subprocess.run(["git", "commit", "--quiet", "-m", "add gitlink"], cwd=repo, check=True)
    commit_sha = _rev_parse(repo, "HEAD")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=commit_sha,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_GITLINK_PRESENT_REASON_V2


def test_pure_gitlink_only_tree_is_refused(tmp_path: Path) -> None:
    """Independent review (round 2, lane D) noted the existing gitlink test
    always mixes the gitlink with other tracked files from
    `_toolrepo_fixture`, so the gitlink-present code path is never isolated
    -- a regression that broke the check only when a gitlink is the SOLE
    tree entry could pass the existing test undetected. Committed here as
    the minimal case: a tree containing nothing but one gitlink."""
    repo = tmp_path / "toolrepo"
    _init_repo(repo)
    fake_submodule_sha = "e" * 40
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{fake_submodule_sha},only_submodule",
        ],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "commit", "--quiet", "-m", "only a gitlink"], cwd=repo, check=True)
    commit_sha = _rev_parse(repo, "HEAD")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=commit_sha,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_GITLINK_PRESENT_REASON_V2


def test_missing_tracked_file_is_refused(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    (subject_root / "app_agent_review" / "core.py").unlink()

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_MISSING_TRACKED_FILE_REASON_V2


def test_path_traversal_tree_entry_cannot_escape_subject_root(tmp_path: Path) -> None:
    """`git mktree` accepts a subtree literally named `..` (it only refuses
    a literal `/` inside one path segment); `git ls-tree -r` then flattens
    that into an entry path like `../evil.py`. Proven exploitable against a
    naive `subject_root / entry.path` join (the OS resolves the `..` on
    access, escaping `subject_root`) before this test was written; must be
    refused instead."""
    repo = tmp_path / "toolrepo"
    _init_repo(repo)
    (repo / "legit.py").write_text("LEGIT = True\n")
    _commit_all(repo, "init")

    outside_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="evil content\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    inner_tree = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=f"100644 blob {outside_blob}\tevil.py\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # A subtree literally named ".." -- ls-tree -r flattens this to
    # "../evil.py" for anything materialised/verified relative to a subject
    # root one level down.
    outer_tree = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=f"040000 tree {inner_tree}\t..\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    malicious_commit = subprocess.run(
        ["git", "commit-tree", outer_tree, "-m", "path traversal attempt"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject_root = tmp_path / "nested" / "subject"
    subject_root.mkdir(parents=True)
    # Plant a file at the escape target whose content matches the malicious
    # blob byte-for-byte -- without a containment check, an unresolved
    # `subject_root / "../evil.py"` join would read *this* file and the
    # content comparison would pass, falsely validating identity using
    # bytes that were never inside subject_root. The refusal below must
    # fire regardless of what happens to be sitting at the escaped path.
    (tmp_path / "nested" / "evil.py").write_text("evil content\n")

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=malicious_commit,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2


def test_symlinked_directory_cannot_hide_an_untracked_file(tmp_path: Path) -> None:
    """Independent-review P0 (lane A, correction round): `Path.rglob("*")`
    does not descend into a symlinked directory (it reports the symlink
    entry itself and stops), but ordinary path joining used by the
    per-tracked-path comparison transparently follows a symlinked directory
    in an intermediate path component. If `pkg` in `subject_root` is
    replaced with a symlink to an attacker directory containing a
    byte-identical `pkg/util.py` (satisfies the tracked-file check) plus an
    untracked `pkg/evil.py`, the two checks disagree about what is "under"
    subject_root: the completeness scan never sees `evil.py` (it never
    descends past the symlink), while Python's own import machinery would
    happily read it. `materialise_commit_subject_v2` never creates a
    symlinked directory itself (only real directories via `mkdir`, and
    symlinks only as leaf blob entries) -- so any symlinked directory found
    under `subject_root` is definitionally something the primitive itself
    did not put there, and must be refused outright rather than silently
    traversed one way and not the other."""
    repo = tmp_path / "toolrepo"
    _init_repo(repo)
    (repo / "main.py").write_text("MAIN = 1\n")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "util.py").write_text("UTIL = 1\n")
    head_sha = _commit_all(repo, "init")

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    attacker_dir = tmp_path / "attacker_pkg"
    attacker_dir.mkdir()
    (attacker_dir / "util.py").write_text("UTIL = 1\n")  # byte-identical to the tracked blob
    (attacker_dir / "evil.py").write_text("EVIL = True\n")  # untracked, never in the commit

    shutil.rmtree(subject_root / "pkg")
    (subject_root / "pkg").symlink_to(attacker_dir)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    # Independent review (round 2, lane D) noted this assertion was
    # previously tautological (`is not None` is true for every reason
    # code). Pinned to the specific code so a future change that swaps in
    # some other -- still non-None -- refusal cannot pass this test
    # unnoticed.
    assert excinfo.value.reason_code == IDENTITY_SYMLINKED_DIRECTORY_REASON_V2
    # The decisive assertion: verification must NOT report success while
    # evil.py sits reachable under subject_root, uncompared against git.
    assert (subject_root / "pkg" / "evil.py").exists()


def test_completeness_is_reverified_close_to_return_not_only_at_call_start(
    tmp_path: Path,
) -> None:
    """Independent-review P1 (round 2, lane C): the completeness scan ran
    exactly ONCE, at the very start of `verify_executed_source_identity_v2`,
    and everything after (commit resolution, tree listing, blob reads, the
    per-entry comparison loop) reused that single frozen snapshot rather
    than re-scanning. A concurrent writer with access to `subject_root`
    during the call itself could add a new file inside that window and
    verification would return SUCCESS while the new file existed on disk,
    uncompared against git -- a different mechanism from the round-1 static
    symlink-directory bypass (this one requires an attacker who can write
    into `subject_root` *during* the call, not just before it), but the
    same underlying failure: a completeness guarantee that was not actually
    true at return time.

    Deterministic repro: delay `resolve_commit_v2` (called early in
    verification, well before any check that reads `subject_root`'s
    content) and have a background thread write a new file into
    `subject_root` partway through that delay. If completeness is checked
    fresh, close to return, the new file must be caught regardless of when
    within the call it appeared."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    evil_path = subject_root / "app_agent_review" / "toctou_evil.py"

    real_resolve_commit_v2 = commit_derived_execution_identity_module.resolve_commit_v2

    def delayed_resolve_commit_v2(*, repo_root, ref):
        time.sleep(0.2)
        return real_resolve_commit_v2(repo_root=repo_root, ref=ref)

    def write_evil_file_partway_through_the_delay() -> None:
        time.sleep(0.05)
        evil_path.write_text("EVIL = True\n")

    writer = threading.Thread(target=write_evil_file_partway_through_the_delay)
    commit_derived_execution_identity_module.resolve_commit_v2 = delayed_resolve_commit_v2
    try:
        writer.start()
        with pytest.raises(ExecutedSourceIdentityError) as excinfo:
            verify_executed_source_identity_v2(
                repo_root=repo,
                commit_sha=head_sha,
                subject_root=subject_root,
                loaded_module_paths=(),
            )
    finally:
        commit_derived_execution_identity_module.resolve_commit_v2 = real_resolve_commit_v2
        writer.join()

    assert excinfo.value.reason_code == IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2
    assert evil_path.exists()


def test_nonexistent_subject_root_is_refused(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError):
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=head_sha,
            subject_root=tmp_path / "does-not-exist",
            loaded_module_paths=(),
        )


# -- loaded_module_files_v2 default introspection -------------------------------


def test_loaded_module_files_v2_reads_real_interpreter_state() -> None:
    import sys as _sys
    import types

    fake_name = "app.agent_review._g1_test_fixture_module"
    fake_module = types.ModuleType(fake_name)
    fake_module.__file__ = "/tmp/fake_module_for_test.py"

    _sys.modules[fake_name] = fake_module
    try:
        discovered = loaded_module_files_v2(package_prefix="app.agent_review")
        assert Path("/tmp/fake_module_for_test.py") in discovered
    finally:
        del _sys.modules[fake_name]


# -- authorization: distinct from identity ---------------------------------------


def test_authorization_true_when_commit_is_ancestor_of_trusted_ref(tmp_path: Path) -> None:
    repo, first_sha = _toolrepo_fixture(tmp_path)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nMORE = True\n")
    second_sha = _commit_all(repo, "second commit")

    result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=first_sha, trusted_ref=second_sha
    )
    assert result.authorized is True
    assert result.commit_sha == first_sha
    assert result.trusted_ref_sha == second_sha


def test_authorization_false_when_commit_is_not_an_ancestor(tmp_path: Path) -> None:
    """Identity can be perfectly valid for a commit that is simply not
    authorized for this invocation -- e.g. an un-merged branch tip. This
    primitive must not conflate 'this sha is real' with 'this sha is
    permitted': a diverged branch commit is a real, identity-verifiable
    commit that is nonetheless unauthorized against a different branch."""
    repo, base_sha = _toolrepo_fixture(tmp_path)

    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nFEATURE = True\n")
    feature_sha = _commit_all(repo, "feature work")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    # Identity verification succeeds for the feature commit -- it is a real,
    # honestly-materialisable commit.
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=feature_sha, destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=feature_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == feature_sha

    # But it is NOT authorized against `main` (base_sha), because it is not
    # an ancestor of it.
    result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=feature_sha, trusted_ref="main"
    )
    assert result.authorized is False
    assert result.commit_sha == feature_sha


def test_authorization_rejects_an_unresolvable_commit(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha="d" * 40, trusted_ref=head_sha
        )
    assert excinfo.value.reason_code == IDENTITY_UNKNOWN_COMMIT_REASON_V2


def test_unauthorized_result_is_falsy(tmp_path: Path) -> None:
    """Independent-review P1 (lane B, correction round):
    `ExecutedSourceAuthorizationV2` had no `__bool__`, so `bool(instance)`
    was always `True` regardless of `.authorized` -- a footgun for any
    future caller who writes `if authorize_commit_for_execution_v2(...):`
    instead of `.authorized`. Not live today (zero call sites outside this
    module/its tests), but G5 is expected to wire this primitive in next,
    so the dataclass's own truthiness must agree with its `.authorized`
    field rather than silently always being truthy."""
    repo, base_sha = _toolrepo_fixture(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nFEATURE = True\n")
    feature_sha = _commit_all(repo, "feature work")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    unauthorized = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=feature_sha, trusted_ref="main"
    )
    assert unauthorized.authorized is False
    assert not unauthorized, "bool(result) must track .authorized, not always be True"

    authorized = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=base_sha, trusted_ref=feature_sha
    )
    assert authorized.authorized is True
    assert authorized


# -- `#200-G1-PM` finding 4: completeness traversal error swallowing ------------


def test_completeness_traversal_error_is_refused_not_silently_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.walk` suppresses any `OSError` `scandir()` raises unless given an
    `onerror` callback -- with none, a directory the process cannot
    traverse is simply treated as empty, and any untracked file hidden
    inside it is invisible to the completeness scan while still reachable
    by anything that actually opens files (e.g. Python's own import
    machinery). Tests in this suite commonly run as root, where chmod-based
    permission denial is not enforced, so the unreadable condition is
    simulated deterministically by monkeypatching `os.scandir` for the
    specific directory rather than depending on real filesystem
    permissions -- the same category of technique this file already uses
    for the TOCTOU completeness test above (module-level monkeypatching of
    a specific call, not a black-box permission fixture)."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    unreadable_dir = (subject_root / "app_agent_review").resolve()
    real_scandir = os.scandir

    def fake_scandir(path="."):
        if Path(path).resolve() == unreadable_dir:
            raise PermissionError(13, "Permission denied", str(unreadable_dir))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2


# -- `#200-G1-PM` finding 7: merge-base operational failure vs. clean negative --


def test_merge_base_operational_failure_is_distinguished_from_clean_non_ancestor(
    tmp_path: Path,
) -> None:
    """`merge-base --is-ancestor` exits 1 -- git's own documented "not an
    ancestor" code -- both for a genuine negative AND for an aborted
    traversal caused by a missing/corrupt object in the ancestry it needed
    to walk (proven with real git plumbing: deleting a repo's root commit's
    own object, with two branches diverging from it, makes `merge-base
    --is-ancestor <tip-1> <tip-2>` print `error: Could not read ...` to
    stderr and still exit 1 -- identical to a clean negative). Collapsing
    both into `authorized=False` tells a caller "this commit is not
    permitted" when the true state is "this cannot be determined" -- an
    availability failure of the identity primitive itself, not a policy
    decision about the commit."""
    repo, root_sha = _toolrepo_fixture(tmp_path)

    subprocess.run(["git", "checkout", "-q", "-b", "branch-b"], cwd=repo, check=True)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nB = True\n")
    b_sha = _commit_all(repo, "b")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    subprocess.run(["git", "checkout", "-q", "-b", "branch-d"], cwd=repo, check=True)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nD = True\n")
    d_sha = _commit_all(repo, "d")
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    # Sanity check first: without corruption, this is a clean, determined
    # negative (b and d diverge at root_sha, neither is an ancestor of the
    # other).
    clean_result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=b_sha, trusted_ref=d_sha
    )
    assert clean_result.authorized is False

    object_path = repo / ".git" / "objects" / root_sha[:2] / root_sha[2:]
    assert object_path.exists()
    object_path.unlink()

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha=b_sha, trusted_ref=d_sha)
    assert excinfo.value.reason_code == IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2


# -- post-review correction: shallow-repository false negative ------------------


def test_shallow_repository_makes_a_true_ancestor_undetermined_not_false(
    tmp_path: Path,
) -> None:
    """External Codex review of the finding-7 fix itself (round 1 on this
    PR): `_merge_base_history_is_fully_readable_v2`'s "rev-list exits zero
    means fully readable" reasoning is false for a shallow repository,
    which deliberately truncates history WITHOUT ever producing a read
    error. Reproduced with real git plumbing below, exactly as found: a
    linear chain `C -> B -> T`, shallow-cloned with `--depth 2` from `T`'s
    branch (so `B` becomes the shallow boundary and `C` is initially
    absent), then `C`'s own commit object fetched separately by a tag
    pointing at it (`git fetch --depth=1 origin tag c-tag`, mirroring a
    realistic multi-ref partial fetch) so it is present locally but NOT
    linked as `B`'s parent from git's structural point of view.
    `merge-base --is-ancestor C T` exits 1 (traversal from `T` stops at the
    shallow boundary `B`, never reaching `C`) even though `C` genuinely is
    `T`'s ancestor in true project history -- and, before this fix, `git
    rev-list C T` exited 0 (no read error, since git treats a shallow
    boundary as a legitimate root), which the previous fix's "fully
    readable" check wrongly took as proof the exit-1 negative was clean.
    `authorize_commit_for_execution_v2` must raise
    `IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2` here, not silently
    return `authorized=False` for a commit that truly is an ancestor."""
    full = tmp_path / "full"
    _init_repo(full)
    (full / "f.txt").write_text("c\n")
    c_sha = _commit_all(full, "C")
    subprocess.run(["git", "tag", "c-tag", "HEAD"], cwd=full, check=True)
    (full / "f.txt").write_text("b\n")
    b_sha = _commit_all(full, "B")
    (full / "f.txt").write_text("t\n")
    t_sha = _commit_all(full, "T")

    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "--quiet", "--depth", "2", f"file://{full}", str(shallow)],
        check=True,
    )
    shallow_marker = shallow / ".git" / "shallow"
    assert shallow_marker.exists()
    assert b_sha in shallow_marker.read_text()

    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", "tag", "c-tag"],
        cwd=shallow,
        check=True,
    )
    # C's object is present locally now, just not linked as B's parent.
    present = subprocess.run(
        ["git", "cat-file", "-t", c_sha], cwd=shallow, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert present == "commit"

    # Sanity check first: confirm the false negative this fix must not
    # silently trust actually reproduces on this git build.
    merge_base = subprocess.run(
        ["git", "merge-base", "--is-ancestor", c_sha, t_sha], cwd=shallow, capture_output=True
    )
    assert merge_base.returncode == 1

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=shallow, commit_sha=c_sha, trusted_ref=t_sha)
    assert excinfo.value.reason_code == IDENTITY_AUTHORIZATION_UNDETERMINED_REASON_V2


# -- `#200-G1-PM` finding 1: architectural, reproduced and NOT fixed here -------


def test_finding1_verification_cannot_see_tampering_that_was_restored_before_it_ran(
    tmp_path: Path,
) -> None:
    """`#200-G1-PM` finding 1 (Codex, PR #284 review of `18dc9e4f`):
    ``verify_executed_source_identity_v2`` binds loaded module bytes only by
    ``module.__file__``'s PATH, never by content actually read at import
    time. Reproduced here exactly as specified in `#200-G1-PM`: tamper a
    module under ``subject_root``, simulate it having been imported while
    tampered (real interpreter execution/import is not what this finding
    is about -- the finding is that verification cannot see the tamper
    window at all, regardless of what a real import statement would have
    done with the tampered bytes during it), then restore it to match the
    commit exactly before verification ever runs.

    This SUCCEEDS below -- proving the finding is real. It is deliberately
    NOT fixed here: closing it would require an ordering this module alone
    cannot enforce (verify the materialised subject, THEN start a fresh
    process, THEN import only after verification succeeds), which is a
    different primitive composing process/import lifecycle this module has
    no say over. Scoped separately as `#200-G1B` (fresh-process execution
    provenance from a verified commit subject). This test exists to keep
    the true, narrowed boundary of what `ExecutedSourceIdentityV2` proves
    regression-pinned -- see the module docstring's "What this module does
    NOT prove" section, narrowed in this same corrective change."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    module_path = subject_root / "app_agent_review" / "core.py"
    committed_bytes = module_path.read_bytes()

    # Tamper (simulating an import of the tampered bytes happening here),
    # then restore to match the commit exactly before verification runs.
    module_path.write_bytes(committed_bytes + b"\nBACKDOOR = True\n")
    module_path.write_bytes(committed_bytes)

    # `module.__file__` for a real import that happened during the tamper
    # window would report exactly this path.
    loaded = (module_path,)

    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=loaded
    )
    # Verification cannot see that different bytes were on disk -- and
    # potentially executed/imported -- at an earlier moment than this call.
    assert identity.commit_sha == head_sha


# -- post-review correction round 2: resolved-path collision, sibling of materialise --


def test_dot_named_subtree_alias_colliding_with_a_root_blob_is_refused_at_verification(
    tmp_path: Path,
) -> None:
    """External Codex review (`#200-G1-PM` round 2 on this PR), sibling of
    the same fix in `git_commit_subject_v2.materialise_commit_subject_v2`:
    a subtree literally named `.` containing `a`, alongside a root-level
    blob also named `a`, produces the distinct raw strings `./a` and `a`
    -- no duplicate by `list_commit_tree_entries_v2`'s own tree-object-level
    check -- but both resolve through `_safe_subject_path_v2` (verification's
    own resolution authority) to the identical `subject_root`-relative
    path. `_reject_resolved_actual_path_collisions_v2` must refuse this
    before any content comparison, using that same resolution function,
    not a second independently-derived string check."""
    repo, _head_sha = _toolrepo_fixture(tmp_path)

    root_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="root content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dot_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="dot content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    inner_tree = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=f"100644 blob {dot_blob}\ta\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    outer_tree = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=(f"040000 tree {inner_tree}\t.\n" f"100644 blob {root_blob}\ta\n"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    alias_commit = subprocess.run(
        ["git", "commit-tree", outer_tree, "-m", "dot alias"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo,
            commit_sha=alias_commit,
            subject_root=subject_root,
            loaded_module_paths=(),
        )
    assert excinfo.value.reason_code == IDENTITY_DUPLICATE_TREE_PATH_REASON_V2
