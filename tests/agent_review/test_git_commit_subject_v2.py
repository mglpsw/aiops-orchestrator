"""`#200-G1` -- new tests for ls-tree+cat-file commit subject materialisation.

Ported with revalidation from the frozen-forensic `#200-F` reconstruction
(commit `5703e5b`); no qualification transfer -- these tests are new.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_review.git_commit_subject_v2 import (
    SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2,
    SUBJECT_UNKNOWN_COMMIT_REASON_V2,
    SubjectMaterialisationError,
    compute_subject_digest_v2,
    materialise_commit_subject_v2,
    resolve_commit_v2,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_resolve_commit_accepts_head_and_returns_full_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    head = _commit_all(repo, "init")
    resolved = resolve_commit_v2(repo_root=repo, ref="HEAD")
    assert resolved == head
    assert len(resolved) == 40


def test_resolve_commit_refuses_a_tree_sha(tmp_path: Path) -> None:
    """A tree sha is not a revision -- accepting it would let materialised
    identity name something no commit history contains."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    _commit_all(repo, "init")
    tree_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(SubjectMaterialisationError) as excinfo:
        resolve_commit_v2(repo_root=repo, ref=tree_sha)
    assert excinfo.value.reason_code == SUBJECT_UNKNOWN_COMMIT_REASON_V2


def test_resolve_commit_refuses_unknown_ref(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    _commit_all(repo, "init")

    with pytest.raises(SubjectMaterialisationError) as excinfo:
        resolve_commit_v2(repo_root=repo, ref="e" * 40)
    assert excinfo.value.reason_code == SUBJECT_UNKNOWN_COMMIT_REASON_V2


def test_materialise_refuses_nonempty_destination(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    head = _commit_all(repo, "init")

    destination = tmp_path / "dest"
    destination.mkdir()
    (destination / "preexisting.txt").write_text("already here\n")

    with pytest.raises(SubjectMaterialisationError) as excinfo:
        materialise_commit_subject_v2(repo_root=repo, ref=head, destination=destination)
    assert excinfo.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2


def test_materialise_writes_nested_directories_and_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "pkg").mkdir()
    (repo / "pkg" / "mod.py").write_text("VALUE = 42\n")
    head = _commit_all(repo, "init")

    destination = tmp_path / "dest"
    result = materialise_commit_subject_v2(repo_root=repo, ref=head, destination=destination)
    assert result.commit_sha == head
    assert result.file_count == 1
    assert (destination / "pkg" / "mod.py").read_text() == "VALUE = 42\n"


def test_digest_is_stable_across_directory_iteration_order(tmp_path: Path) -> None:
    """The digest sorts entries explicitly, so it must not depend on
    filesystem iteration order."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "zzz.py").write_text("1\n")
    (a / "aaa.py").write_text("2\n")

    b = tmp_path / "b"
    b.mkdir()
    (b / "aaa.py").write_text("2\n")
    (b / "zzz.py").write_text("1\n")

    assert compute_subject_digest_v2(a) == compute_subject_digest_v2(b)


def test_digest_changes_when_content_changes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "f.py").write_text("A = 1\n")
    before = compute_subject_digest_v2(root)
    (root / "f.py").write_text("A = 2\n")
    after = compute_subject_digest_v2(root)
    assert before != after


def test_digest_hashes_symlink_target_text_not_followed_content(tmp_path: Path) -> None:
    """Following a symlink during digesting would let a link planted inside
    the subject pull in bytes from outside it and still digest as
    unchanged when the link's target moves; hashing the target text closes
    that."""
    outside = tmp_path / "outside.py"
    outside.write_text("OUTSIDE = True\n")

    root = tmp_path / "root"
    root.mkdir()
    (root / "link.py").symlink_to(outside)
    before = compute_subject_digest_v2(root)

    outside.write_text("OUTSIDE = False\nCHANGED = True\n")
    after = compute_subject_digest_v2(root)
    # The symlink's target *text* did not change, only the pointed-at file's
    # content -- which this digest never reads.
    assert before == after
