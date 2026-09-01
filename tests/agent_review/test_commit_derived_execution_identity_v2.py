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
from pathlib import Path

import pytest

from app.agent_review.bounded_git_v2 import BoundedGitError
from app.agent_review.commit_derived_execution_identity_v2 import (
    IDENTITY_CONTENT_MISMATCH_REASON_V2,
    IDENTITY_EXTRA_UNTRACKED_FILE_REASON_V2,
    IDENTITY_GITLINK_PRESENT_REASON_V2,
    IDENTITY_LOADED_CODE_OUTSIDE_SUBJECT_REASON_V2,
    IDENTITY_MISSING_TRACKED_FILE_REASON_V2,
    IDENTITY_SYMLINK_TARGET_MISMATCH_REASON_V2,
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
