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
    IDENTITY_BLOB_MISSING_REASON_V2,
    IDENTITY_CONTENT_MISMATCH_REASON_V2,
    IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2,
    IDENTITY_GITLINK_PRESENT_REASON_V2,
    IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2,
    IDENTITY_MISSING_TRACKED_FILE_REASON_V2,
    IDENTITY_MISSING_TREE_NODE_REASON_V2,
    IDENTITY_MODE_MISMATCH_REASON_V2,
    IDENTITY_PATH_ESCAPES_SUBJECT_REASON_V2,
    IDENTITY_SYMLINKED_DIRECTORY_REASON_V2,
    IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2,
    IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2,
    IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2,
    IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2,
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
from app.agent_review.trusted_object_authority_v2 import open_trusted_object_authority_v2


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
        # `#333`: a narrowed root is missing a whole tree node, and the
        # structural verifier names that more precisely than "missing file".
        IDENTITY_MISSING_TREE_NODE_REASON_V2,
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


# -- #200-G1-S / S2: completeness traversal must fail closed --------------------


@pytest.mark.parametrize("failure_point", ["open", "mid-iteration"])
def test_completeness_traversal_error_is_refused_not_silently_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    """`#200-G1-S` (issue #305): "cannot enumerate a directory" is not the
    same fact as "directory is empty". `#333` re-expresses the injection
    against the descriptor-relative walk: the enumeration of one
    subdirectory (``os.scandir`` on its descriptor, streamed since `#352`
    F3) fails -- when opened, or while entries are being read -- and the
    verifier must answer with a typed refusal, never with a clean pass that
    treats the unreadable subtree as empty (or as partially listed)."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    real_scandir = os.scandir
    unreadable_dir = (subject_root / "app_agent_review").resolve()

    class _FailingIterator:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __enter__(self):
            return self

        def __exit__(self, *exc_info) -> None:
            self._inner.close()

        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("simulated read failure while streaming directory entries")

        def close(self) -> None:
            self._inner.close()

    def fake_scandir(target="."):
        if isinstance(target, int):
            try:
                located = Path(os.readlink(f"/proc/self/fd/{target}")).resolve()
            except OSError:
                located = None
            if located == unreadable_dir:
                if failure_point == "open":
                    raise PermissionError(f"simulated unreadable directory: {unreadable_dir}")
                return _FailingIterator(real_scandir(target))
        return real_scandir(target)

    monkeypatch.setattr(os, "scandir", fake_scandir)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2


def test_completeness_traversal_classification_error_is_refused_not_silently_treated_as_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node whose no-follow classification (``stat(name, dir_fd=...,
    follow_symlinks=False)``) fails must be a typed refusal, not sorted into
    some default kind (the CPython ``os.walk`` fallback this suite
    originally closed, PR #306). Only one entry's classification fails;
    every other stat call is real."""
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    real_stat = os.stat

    def fake_stat(target, *args, **kwargs):
        if kwargs.get("dir_fd") is not None and os.fsdecode(target) == "app_agent_review":
            raise OSError("simulated entry classification failure")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fake_stat)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.reason_code == IDENTITY_TRAVERSAL_UNREADABLE_REASON_V2


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
        repo_root=repo, commit_sha=first_sha, trusted_ref_sha=second_sha
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
    # an ancestor of it. `trusted_ref_sha` is the resolved sha `main`
    # currently names, supplied directly -- never the ref name `"main"`
    # itself (see `#313`: a ref name is refused outright, exercised
    # separately below).
    result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=feature_sha, trusted_ref_sha=base_sha
    )
    assert result.authorized is False
    assert result.commit_sha == feature_sha


def test_authorization_rejects_an_unresolvable_commit(tmp_path: Path) -> None:
    repo, head_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha="d" * 40, trusted_ref_sha=head_sha
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
        repo_root=repo, commit_sha=feature_sha, trusted_ref_sha=base_sha
    )
    assert unauthorized.authorized is False
    assert not unauthorized, "bool(result) must track .authorized, not always be True"

    authorized = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=base_sha, trusted_ref_sha=feature_sha
    )
    assert authorized.authorized is True
    assert authorized


# -- #313 (#200-G1C2-F2): trusted_ref_sha must be an out-of-band anchor ----------
#
# `authorize_commit_for_execution_v2` used to accept a ref NAME (e.g.
# `"refs/heads/master"`) as `trusted_ref` and resolve it against the trusted
# object authority built from the (potentially hostile) checkout at
# `repo_root`. Object *content* in that authority is genuinely
# hash-verified, but ref *values* are copied verbatim from that same
# checkout -- so a hostile checkout that points a branch at an attacker
# commit got that commit accepted as the trust anchor, `authorized=True`,
# using exactly the calling convention the old docstring demonstrated. The
# tests below are the falsifier corpus for the fix: the positive control
# (an out-of-band sha still works even when the branch tip is hostile), the
# exploit reproduction (a ref-name-shaped anchor is refused, not resolved),
# and the malformed-input corpus (anything not exactly sha-shaped is
# refused with a typed reason code).


def test_positive_control_out_of_band_sha_still_authorizes_when_branch_tip_is_hostile(
    tmp_path: Path,
) -> None:
    """The fix must not break the legitimate case: a caller who has
    independently, out-of-band verified a real commit sha as its trust
    anchor still gets a correct `authorized=True` -- even while the live
    checkout's own branch tip (`main`) has been moved somewhere completely
    unrelated, simulating a hostile checkout. The anchor here is `trusted_
    commit_sha` itself, supplied directly by the caller -- never resolved
    from what `main` currently, possibly-hostilely, points at."""
    repo, base_sha = _toolrepo_fixture(tmp_path)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nMORE = True\n")
    trusted_commit_sha = _commit_all(repo, "the real, out-of-band verified release commit")

    # Simulate a hostile checkout: move `main`'s tip away from the trusted
    # commit entirely, onto an unrelated attacker-controlled commit.
    subprocess.run(
        ["git", "checkout", "--orphan", "attacker-branch"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "rm", "-rf", "--quiet", "."], cwd=repo, check=True, capture_output=True)
    (repo / "evil.py").write_text("ATTACKER = True\n")
    attacker_sha = _commit_all(repo, "attacker commit, unrelated history")
    subprocess.run(
        ["git", "branch", "-f", "main", attacker_sha], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    assert _rev_parse(repo, "refs/heads/main") == attacker_sha

    # The caller supplies `trusted_commit_sha` directly -- the real sha it
    # verified out-of-band before this checkout was ever hostile -- never
    # the ref name `"main"`, which now names the attacker's commit.
    result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=base_sha, trusted_ref_sha=trusted_commit_sha
    )
    assert result.authorized is True
    assert result.commit_sha == base_sha
    assert result.trusted_ref_sha == trusted_commit_sha


def test_ref_name_shaped_trusted_ref_sha_is_refused_not_resolved(tmp_path: Path) -> None:
    """The exploit reproduction (#313): a ref NAME must never be accepted as
    `trusted_ref_sha`, regardless of what it currently resolves to in the
    (potentially hostile) checkout. `main` here genuinely exists and
    genuinely is an ancestor-inclusive anchor for `base_sha` -- if this
    module still resolved ref names, this call would have quietly
    succeeded with `authorized=True`, which is exactly the false-positive
    #313 reports. It must instead be refused outright, before any
    resolution attempt."""
    repo, base_sha = _toolrepo_fixture(tmp_path)

    for ref_name in ("refs/heads/main", "HEAD", "main"):
        with pytest.raises(ExecutedSourceIdentityError) as excinfo:
            authorize_commit_for_execution_v2(
                repo_root=repo, commit_sha=base_sha, trusted_ref_sha=ref_name
            )
        assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2, ref_name


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "a" * 39,  # one short of a sha1
        "a" * 41,  # one long of a sha1
        "g" * 40,  # right length, not hex
        "A" * 40,  # right length and hex alphabet, wrong case
        "a" * 63,  # one short of a sha256 (still wrong even though 64 itself is also refused)
        "a" * 64,  # sha256 length -- deliberately dropped, see the P0 test above
        "a" * 65,  # one long of a sha256
        "deadbeef",  # a real, but abbreviated, sha
        "not-a-sha-at-all",
    ],
)
def test_malformed_trusted_ref_sha_is_refused(tmp_path: Path, malformed: str) -> None:
    """Anything not exactly 40 lowercase hex characters is refused with a
    typed reason code -- never silently truncated, case-folded, or
    partially matched against a real commit. 64 (sha256) is included here
    as a plain shape-refusal case; `test_64_hex_trusted_ref_sha_is_refused_
    not_shadow_resolved_as_a_ref` above additionally exercises the full
    reproduced attack path for that specific length."""
    repo, base_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha=malformed
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


def test_malformed_trusted_ref_sha_is_refused_before_opening_the_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shape check must run BEFORE this module ever opens the trusted
    object authority for a malformed value -- not merely reject it
    eventually via some downstream git failure. Patches `open_trusted_
    object_authority_v2` to raise if called at all; a malformed
    `trusted_ref_sha` must never reach it."""
    repo, base_sha = _toolrepo_fixture(tmp_path)

    def _must_not_be_called(*args: object, **kwargs: object) -> object:
        raise AssertionError("open_trusted_object_authority_v2 must not be called for malformed input")

    monkeypatch.setattr(
        commit_derived_execution_identity_module,
        "open_trusted_object_authority_v2",
        _must_not_be_called,
    )
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha="refs/heads/main"
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


def test_laundering_hostile_ref_through_resolve_commit_v2_reproduces_the_original_attack(
    tmp_path: Path,
) -> None:
    """KNOWN, ACCEPTED RESIDUAL RISK -- independent adversarial review of
    #313's fix (`#200-G1C2-F3`), reproduced here deliberately and left
    GREEN (the bypass succeeds) rather than made to fail, because there is
    no validation this module could add that would catch it -- see both
    `authorize_commit_for_execution_v2`'s docstring ("Residual risk the
    shape check does NOT and cannot close") and `resolve_commit_v2`'s own
    docstring for the full explanation. This test exists so the risk stays
    visible and re-verified on every run, not silently forgotten.

    The shape check (`_is_full_commit_sha_shape_v2`) has no bypass of its
    OWN: a ref name, an abbreviated sha, and every other non-sha shape are
    all correctly refused (see the tests above). But a caller does not need
    to bypass the shape check to reconstruct the pre-#313 attack -- they
    only need to produce a STRING that happens to already be a real,
    shape-valid commit sha before calling `authorize_commit_for_execution_v2`
    at all. `resolve_commit_v2`, called directly against the trusted object
    authority's own `trusted_repo_root` (exactly the composition a caller
    reaching for "how do I turn a ref name into a sha" would plausibly
    write), does exactly that: it resolves a ref NAME through the same
    hostile-derived authority this module refuses to consult internally,
    and hands back a genuine 40-hex sha -- which, because it IS a real
    commit sha, sails through the shape check unchanged.

    Closing this for real requires a caller-side provenance/attestation
    channel that never touches this module family's read path at all --
    out of scope while there are no live callers of
    `authorize_commit_for_execution_v2` to design that channel against
    (tracked as `#200-G1C2-F3`, deliberately not designed speculatively
    here, matching this session's own discipline of not building
    architecture ahead of a real consumer -- see `#200-G1B`'s identical
    scoping)."""
    repo, base_sha = _toolrepo_fixture(tmp_path)

    # Simulate a hostile checkout: move `main`'s tip to an attacker commit,
    # unrelated to `base_sha`'s history.
    subprocess.run(
        ["git", "checkout", "--orphan", "attacker-branch"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "rm", "-rf", "--quiet", "."], cwd=repo, check=True, capture_output=True)
    (repo / "evil.py").write_text("EVIL = True\n")
    evil_sha = _commit_all(repo, "attacker commit, unrelated history")
    subprocess.run(["git", "branch", "-f", "main", evil_sha], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
    assert _rev_parse(repo, "refs/heads/main") == evil_sha

    # The caller resolves the ref THEMSELVES, against the same
    # hostile-derived trusted object authority `authorize_commit_for_
    # execution_v2` would have opened internally -- this is the laundering
    # step this fix cannot see or prevent.
    with open_trusted_object_authority_v2(repo) as authority:
        laundered_sha = resolve_commit_v2(repo_root=authority.trusted_repo_root, ref="refs/heads/main")
    assert laundered_sha == evil_sha, "sanity: the laundered sha is exactly the hostile branch tip"

    # `laundered_sha` is shape-valid (it is a real commit sha), so it passes
    # the #313 fix's guard. The result reconstructs the original false
    # positive: an attacker commit accepted as the trust anchor.
    result = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=evil_sha, trusted_ref_sha=laundered_sha
    )
    assert result.authorized is True, (
        "this assertion documents the KNOWN residual risk, not a desired "
        "outcome -- see this test's docstring and #200-G1C2-F3"
    )


# -- #313 correction round 2 (independent adversarial review, P0/P1/P2) ---------
#
# Fallback-quorum review of the #313 fix found a real P0: the shape check's
# accepted lengths were (40, 64) -- 40 for sha1, 64 anticipating sha256 --
# but the trusted object authority is hardcoded sha1-format ALWAYS, so a
# 64-hex string can never be a real object id there. Git falls through,
# silently, to ordinary REF-NAME resolution for a hex string whose length
# doesn't match the repo's hash algorithm -- and ref values are exactly what
# this module already treats as hostile. A caller's genuine, public,
# out-of-band 64-hex anchor could therefore be "shadowed" by an attacker who
# plants a same-named ref pointing at their own commit. Independently
# reproduced (by two adversarial review lanes and directly by a human
# maintainer) before fixing. Fixed two ways: (1) 64 dropped from the
# accepted shape entirely (zero legitimate use today -- `resolve_commit_v2`
# independently hard-rejects any resolved value with `len != 40` regardless);
# (2) a general `resolved_trusted == trusted_ref_sha` equality invariant
# added, closing the class rather than just the one exploitable length.


def test_64_hex_trusted_ref_sha_is_refused_not_shadow_resolved_as_a_ref(
    tmp_path: Path,
) -> None:
    """The reproduced P0: a 64-hex string that LOOKS like a plausible
    out-of-band sha256 anchor must be refused outright by the shape check,
    not silently resolved as a ref name -- because the trusted object
    authority is sha1-only, a 64-hex string can never be a real object id
    there, and letting it fall through to ref-name resolution would let an
    attacker who plants `refs/heads/<the caller's own public 64-hex pin>`
    get their own commit accepted as the trust anchor."""
    repo, base_sha = _toolrepo_fixture(tmp_path)

    subprocess.run(
        ["git", "checkout", "--orphan", "attacker-branch"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "rm", "-rf", "--quiet", "."], cwd=repo, check=True, capture_output=True)
    (repo / "evil.py").write_text("EVIL = True\n")
    evil_sha = _commit_all(repo, "attacker commit")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    fake_pin = "a" * 64  # the caller's genuine, public, out-of-band sha256-shaped anchor
    subprocess.run(
        ["git", "update-ref", f"refs/heads/{fake_pin}", evil_sha], cwd=repo, check=True, capture_output=True
    )
    assert _rev_parse(repo, f"refs/heads/{fake_pin}") == evil_sha

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(repo_root=repo, commit_sha=evil_sha, trusted_ref_sha=fake_pin)
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


def test_resolved_trusted_ref_sha_must_equal_the_supplied_value(tmp_path: Path) -> None:
    """The general invariant behind the P0 fix: `trusted_ref_sha` must
    resolve back to EXACTLY itself, not merely to *some* real commit.
    Reproduced with an annotated tag: its own object sha is a genuine,
    shape-valid 40-hex object id in the store (so the shape check and the
    plain existence check both pass), but `resolve_commit_v2`'s `^{commit}`
    peels an annotated tag to the commit it points AT -- a different sha
    than the tag object's own. Supplying the tag's sha as `trusted_ref_sha`
    must be refused, not silently treated as if the peeled commit sha had
    been supplied."""
    repo, base_sha = _toolrepo_fixture(tmp_path)
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nMORE = True\n")
    second_sha = _commit_all(repo, "second commit")

    subprocess.run(
        ["git", "tag", "-a", "-m", "annotated", "v-annotated", second_sha],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tag_object_sha = _rev_parse(repo, "refs/tags/v-annotated")
    assert tag_object_sha != second_sha, "sanity: the tag object's own sha differs from the commit it peels to"

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha=tag_object_sha
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_SHA_MISMATCH_REASON_V2


@pytest.mark.parametrize("non_str", [None, 12345, ["a"] * 40, ("a",) * 40, b"a" * 40])
def test_non_string_trusted_ref_sha_is_refused_with_a_typed_reason_not_a_raw_typeerror(
    tmp_path: Path, non_str: object
) -> None:
    """Independent-review P2: a non-`str` value -- including a list of
    single hex-digit characters, which would each individually satisfy a
    naive per-element check -- must be refused via the typed reason code,
    never allowed to reach `len()`/iteration and either coincidentally
    "pass" or raise an untyped `TypeError`."""
    repo, base_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha=non_str  # type: ignore[arg-type]
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


# -- round 3: adversarial `str` subclass at the trust boundary (RED-first) ------
#
# `isinstance(value, str)` accepts SUBCLASSES, and a subclass gets to define
# the very operators the anchor's guards are written in terms of. Which
# override is load-bearing was established empirically, not assumed --
# see `test_only_a_ne_override_defeats_the_invariant_eq_alone_does_not`.


class _NeOnlyStr(str):
    """A `str` subclass whose ONLY override is `__ne__`.

    This is the MINIMAL witness for the round-3 bypass: one dunder, and the
    instance's real content is an honest, genuinely-resolvable 40-lowercase-hex
    object id, so every content-based check passes truthfully.
    """

    def __ne__(self, other: object) -> bool:
        return False


class _EqAndNeStr(str):
    """Overrides both halves of the equality protocol."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return str.__hash__(self)


class _EqOnlyStr(str):
    """Overrides ONLY `__eq__` -- deliberately NOT sufficient to bypass;
    kept as a discriminating control, see the mechanism test below."""

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return str.__hash__(self)


class _ShapeLyingStr(str):
    """Lies about `__len__` and `__iter__` so the shape gate reads a
    40-hex value while the real content is an attacker-planted REF NAME,
    and lies about `__ne__` so the resolved==supplied invariant cannot
    catch the substitution either."""

    _FAKE = "0" * 40

    def __len__(self) -> int:
        return 40

    def __iter__(self) -> object:
        return iter(self._FAKE)

    def __ne__(self, other: object) -> bool:
        return False


class _ClassLyingNonSubclass:
    """NOT a `str` subclass at all -- defeats `isinstance(value, str)` via
    the `__class__` property rather than via inheritance. #322: every
    existing witness above is a genuine `str` subclass, so the corpus pins
    "not a subclass" rather than the actually-required property "exact
    built-in `str`". `type(value) is str` (the shipped gate) is immune to
    this; `isinstance(value, str)` and the semantically-plausible refactor
    `value.__class__ is str` are both defeated by it -- see
    `test_class_lying_non_subclass_would_defeat_isinstance_or___class___but_not_the_shipped_gate`
    for the reproduced mutation proof."""

    def __init__(self, v: str) -> None:
        self._v = v

    @property
    def __class__(self) -> type:  # type: ignore[override]
        return str

    def __len__(self) -> int:
        return len(self._v)

    def __iter__(self) -> object:
        return iter(self._v)

    def __ne__(self, other: object) -> bool:
        return False

    def __str__(self) -> str:
        # Load-bearing for the full end-to-end reproduction, not decoration:
        # `resolve_commit_v2` builds its git argv via an f-string
        # (`f"{ref}^{{commit}}"`), which calls `format(ref, "")` ->
        # `object.__format__` -> `str(ref)`. Without this override that
        # falls through to the default `object.__str__` repr, git receives
        # a garbled non-hex argument, and the reproduction fails for an
        # UNRELATED reason (rev-parse can't resolve garbage) rather than
        # demonstrating the actual vulnerability. Returning the honest
        # content here is what makes this a faithful adversarial object,
        # not a weaker one.
        return self._v


def test_only_a_ne_override_defeats_the_invariant_eq_alone_does_not() -> None:
    """Pins the EXACT dispatch mechanism the fix is aimed at, because the
    obvious guess is wrong and a wrong finding description would invite a
    wrong (equality-hardening) fix.

    `resolved_trusted != trusted_ref_sha` puts the caller's object on the
    RIGHT. Python gives the reflected operation priority when the right
    operand's type is a proper subclass of the left's, so the subclass is
    consulted first -- but the name it looks up is `__ne__`, and `str`
    DEFINES its own `__ne__`. A subclass overriding only `__eq__` therefore
    inherits `str.__ne__` (it never reaches `object.__ne__`'s
    delegate-and-negate behaviour) and is compared by real content.

    Consequence: `__ne__` is the load-bearing override, and no amount of
    equality hardening is the right fix -- excluding subclasses at the type
    gate is.
    """
    honest = "a" * 40
    other = "b" * 40
    assert (other != _EqOnlyStr(honest)) is True, "an __eq__-only subclass must NOT defeat `!=`"
    assert (other != _NeOnlyStr(honest)) is False, "a __ne__ override DOES defeat `!=`"
    assert (other != _EqAndNeStr(honest)) is False
    assert type(_NeOnlyStr(honest)) is not str and isinstance(_NeOnlyStr(honest), str)


@pytest.mark.parametrize(
    "adversarial_type",
    [_NeOnlyStr, _EqAndNeStr, _EqOnlyStr, _ShapeLyingStr, _ClassLyingNonSubclass],
    ids=["ne_only", "eq_and_ne", "eq_only", "shape_lying", "class_lying_non_subclass"],
)
def test_str_subclass_trusted_ref_sha_is_refused_the_anchor_must_be_an_exact_str(
    tmp_path: Path, adversarial_type: type
) -> None:
    """The trust anchor must be an EXACT built-in `str`, never a subclass
    and never a non-subclass object that lies about its type via
    `__class__` (#322).

    A `str` subclass passes `isinstance(value, str)` while redefining
    `__len__`, `__iter__` and `__ne__` -- i.e. every operator the shape
    gate and the resolved==supplied invariant are expressed in.
    `_ClassLyingNonSubclass` is not even a subclass -- it defeats
    `isinstance` purely via a `__class__` property override, proving the
    gate must check the object's real type, not merely "not a str
    subclass". The object being validated must not be allowed to define
    what validation means. Refused at the type gate, BEFORE resolution,
    with the typed reason code.
    """
    repo, base_sha = _toolrepo_fixture(tmp_path)
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo,
            commit_sha=base_sha,
            trusted_ref_sha=adversarial_type(base_sha),  # type: ignore[arg-type]
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


def test_class_lying_non_subclass_would_defeat_isinstance_or___class___but_not_the_shipped_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation-discrimination proof for #322: reproduces the exact
    reviewer-invisible refactor this witness exists to catch.

    `_ClassLyingNonSubclass` defeats `isinstance(value, str)` -- the
    round-2 gate -- and would equally defeat the semantically-plausible
    refactor `value.__class__ is str`, because both consult `__class__`
    rather than the object's real type. Only `type(value) is str` (the
    shipped gate) is immune. Proven here by temporarily monkeypatching
    `_is_full_commit_sha_shape_v2` to each of the two vulnerable variants
    and observing the exploit succeed, then confirming the shipped
    function refuses the identical input.
    """
    repo, base_sha = _toolrepo_fixture(tmp_path)
    liar = _ClassLyingNonSubclass(base_sha)

    assert isinstance(liar, str) is True, "the exploit precondition: isinstance is fooled"
    assert type(liar) is not str, "but the real type is not str"
    assert liar.__class__ is str, "and __class__ is fooled too -- the refactor trap"

    def shape_check_via_isinstance(value: object) -> bool:
        if not isinstance(value, str):
            return False
        return len(value) == 40 and all(c in "0123456789abcdef" for c in value)

    def shape_check_via_dunder_class(value: object) -> bool:
        if value.__class__ is not str:
            return False
        return len(value) == 40 and all(c in "0123456789abcdef" for c in value)

    for vulnerable_shape_check in (shape_check_via_isinstance, shape_check_via_dunder_class):
        monkeypatch.setattr(
            commit_derived_execution_identity_module,
            "_is_full_commit_sha_shape_v2",
            vulnerable_shape_check,
        )
        result = authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha=liar  # type: ignore[arg-type]
        )
        assert result.authorized is True, (
            f"{vulnerable_shape_check.__name__} was expected to be defeated by the "
            "class-lying object -- if this assertion fails, the mutation stopped "
            "reproducing the vulnerability this test exists to guard against"
        )

    monkeypatch.undo()
    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo, commit_sha=base_sha, trusted_ref_sha=liar  # type: ignore[arg-type]
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2


def test_str_subclass_anchor_cannot_authorize_an_unrelated_attacker_commit(
    tmp_path: Path,
) -> None:
    """The full round-3 exploit, end to end.

    An annotated tag's own object sha is honest 40-lowercase-hex and really
    resolves, but `^{commit}` peels it to a DIFFERENT commit -- here the
    attacker's orphan commit, an ancestor of nothing trusted. On the plain
    `str` that is caught by the resolved==supplied invariant
    (`test_resolved_trusted_ref_sha_must_equal_the_supplied_value`). Wrapped
    in a `str` subclass that overrides `__ne__`, the invariant asks the
    attacker's own object whether it mismatches and is told no --
    previously yielding `authorized=True` for the attacker's commit,
    anchored on the attacker's own commit.
    """
    repo, base_sha = _toolrepo_fixture(tmp_path)

    subprocess.run(
        ["git", "checkout", "--orphan", "attacker-branch"], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(["git", "rm", "-rf", "--quiet", "."], cwd=repo, check=True, capture_output=True)
    (repo / "evil.py").write_text("EVIL = True\n")
    evil_sha = _commit_all(repo, "attacker commit")
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    subprocess.run(
        ["git", "tag", "-a", "-m", "annotated", "v-evil", evil_sha],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    tag_object_sha = _rev_parse(repo, "refs/tags/v-evil")
    assert tag_object_sha != evil_sha, "sanity: the tag object's own sha differs from the commit it peels to"
    assert len(tag_object_sha) == 40 and tag_object_sha.islower()

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        authorize_commit_for_execution_v2(
            repo_root=repo,
            commit_sha=evil_sha,
            trusted_ref_sha=_NeOnlyStr(tag_object_sha),  # type: ignore[arg-type]
        )
    assert excinfo.value.reason_code == IDENTITY_TRUSTED_REF_NOT_A_SHA_REASON_V2

    # and the honest plain-`str` control still works
    assert authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha=base_sha, trusted_ref_sha=base_sha
    ).authorized


def test_composing_identity_and_authorization_from_the_same_input_can_resolve_different_commits(
    tmp_path: Path,
) -> None:
    """KNOWN, DOCUMENTED COMPOSITION HAZARD (independent adversarial review,
    #313 correction round 2, P1) -- checked in deliberately GREEN (the
    hazard reproduces) rather than made to fail, because neither function is
    wrong on its own; see the module docstring's "Composing verify_executed_
    source_identity_v2 + authorize_commit_for_execution_v2" section for the
    full explanation and the required caller-side mitigation (compare the
    resolved shas).

    `commit_sha` is deliberately NOT shape-checked in either function (it
    may legitimately be a ref name) -- but `verify_executed_source_identity_
    v2` and `authorize_commit_for_execution_v2` each open their OWN fresh
    trusted object authority and resolve `commit_sha` independently. If the
    checkout mutates a ref between the two calls, a caller supplying the
    SAME literal string to both can get IDENTITY proven about one real
    commit and AUTHORIZATION granted about a genuinely DIFFERENT real
    commit."""
    repo, first_sha = _toolrepo_fixture(tmp_path)

    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref="main", destination=subject_root)
    identity = verify_executed_source_identity_v2(
        repo_root=repo, commit_sha="main", subject_root=subject_root, loaded_module_paths=()
    )
    assert identity.commit_sha == first_sha

    # The checkout mutates between the two calls -- `main` now names a
    # completely different, real commit.
    (repo / "app_agent_review" / "core.py").write_text("SEMANTIC = True\nMORE = True\n")
    second_sha = _commit_all(repo, "second commit")
    assert second_sha != first_sha

    authorization = authorize_commit_for_execution_v2(
        repo_root=repo, commit_sha="main", trusted_ref_sha=second_sha
    )

    # Both calls were given the identical literal string `"main"`. Each
    # function did exactly what it promises, independently -- and yet:
    assert identity.commit_sha == first_sha
    assert authorization.commit_sha == second_sha
    assert identity.commit_sha != authorization.commit_sha, (
        "this assertion documents the KNOWN composition hazard, not a "
        "desired outcome -- a caller MUST compare identity.commit_sha == "
        "authorization.commit_sha before treating the pair as describing "
        "the same commit; see this test's docstring"
    )


# --- C3 boundary compatibility: the spool-owning carrier is closed deterministically ---


def _record_carriers(monkeypatch):
    from app.agent_review import commit_derived_execution_identity_v2 as ident

    carriers: list = []
    real = ident.read_commit_blobs_v2

    def recording(*args, **kwargs):
        carrier = real(*args, **kwargs)
        carriers.append(carrier)
        return carrier

    monkeypatch.setattr(ident, "read_commit_blobs_v2", recording)
    return carriers


def test_carrier_is_closed_after_successful_verification(tmp_path: Path, monkeypatch) -> None:
    carriers = _record_carriers(monkeypatch)
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    verify_executed_source_identity_v2(
        repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
    )
    assert len(carriers) == 1 and carriers[0]._closed
    assert carriers[0]._spool.closed


def test_carrier_is_closed_on_refusal_even_when_the_traceback_is_retained(tmp_path: Path, monkeypatch) -> None:
    carriers = _record_carriers(monkeypatch)
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)
    (subject_root / "scripts" / "entry.py").chmod(0o755)

    with pytest.raises(ExecutedSourceIdentityError) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    # `excinfo` still holds the traceback, and with it the consumer frame:
    # closure must not depend on that frame (or the carrier's __del__) going away.
    assert excinfo.value.__traceback__ is not None
    assert len(carriers) == 1 and carriers[0]._closed
    assert carriers[0]._spool.closed


@pytest.mark.parametrize("exc_type", [RuntimeError, KeyboardInterrupt])
def test_carrier_is_closed_on_unexpected_and_process_control_exceptions(
    tmp_path: Path, monkeypatch, exc_type
) -> None:
    from app.agent_review import commit_derived_execution_identity_v2 as ident

    carriers = _record_carriers(monkeypatch)
    repo, head_sha = _toolrepo_fixture(tmp_path)
    subject_root = tmp_path / "subject"
    subject_root.mkdir()
    materialise_commit_subject_v2(repo_root=repo, ref=head_sha, destination=subject_root)

    def boom(*_a, **_k):
        raise exc_type("injected")

    # `#333`: inject after the carrier exists (the observation step), so the
    # property under test -- deterministic carrier release -- is exercised.
    monkeypatch.setattr(ident, "_observe_subject_graph_v2", boom)
    with pytest.raises(exc_type) as excinfo:
        verify_executed_source_identity_v2(
            repo_root=repo, commit_sha=head_sha, subject_root=subject_root, loaded_module_paths=()
        )
    assert excinfo.value.__traceback__ is not None
    assert len(carriers) == 1 and carriers[0]._closed
