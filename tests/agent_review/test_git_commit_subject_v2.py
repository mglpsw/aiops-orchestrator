"""`#200-G1` -- new tests for ls-tree+cat-file commit subject materialisation.

Ported with revalidation from the frozen-forensic `#200-F` reconstruction
(commit `5703e5b`); no qualification transfer -- these tests are new.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.agent_review.git_commit_subject_v2 import (
    SUBJECT_DESTINATION_IS_SYMLINK_REASON_V2,
    SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2,
    SUBJECT_DUPLICATE_TREE_PATH_REASON_V2,
    SUBJECT_UNKNOWN_COMMIT_REASON_V2,
    SubjectMaterialisationError,
    compute_subject_digest_v2,
    list_commit_tree_entries_v2,
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


def test_materialise_refuses_blob_subtree_name_collision_instead_of_crashing(
    tmp_path: Path,
) -> None:
    """Independent-review P1 (lane B, correction round): a git tree object
    can legally contain a blob and a tree with the *same one-byte name*
    (git's own sort comparator treats a subdirectory entry as if it had a
    trailing "/", so "collide" (blob) and "collide" (tree) do not collide
    from git's point of view, and `git mktree` accepts it). `ls-tree -r`'s
    canonical sort then emits the blob entry before the tree's flattened
    "collide/bar.py" entry. Materialisation wrote "collide" as a regular
    file first, then tried `mkdir(parents=True, exist_ok=True)` for
    "collide" as a directory for the second entry -- `exist_ok=True` only
    suppresses the error when the existing path is already a directory, so
    this raised a raw, untyped `FileExistsError` and left the "collide"
    file behind as a partial write. Proven with real `git mktree` plumbing,
    not a synthetic mock."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.py").write_text("SEED = 1\n")
    _commit_all(repo, "seed")

    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="blob content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    nested_blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="nested content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    inner_tree_sha = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=f"100644 blob {nested_blob_sha}\tbar.py\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    # A blob "collide" and a tree "collide" as siblings in the SAME tree --
    # git's sort treats the directory as "collide/" for comparison, so this
    # is not a duplicate name from git's point of view.
    outer_tree_sha = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=(
            f"100644 blob {blob_sha}\tcollide\n"
            f"040000 tree {inner_tree_sha}\tcollide\n"
        ),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    collision_commit = subprocess.run(
        ["git", "commit-tree", outer_tree_sha, "-m", "collision"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    destination = tmp_path / "dest"
    with pytest.raises(SubjectMaterialisationError):
        materialise_commit_subject_v2(repo_root=repo, ref=collision_commit, destination=destination)
    # No partial write left behind for a caller to mistake for a valid
    # subject.
    assert not destination.exists() or not any(destination.iterdir())


# -- `#200-G1-PM` finding 2: symlink destination ---------------------------------


def test_materialise_refuses_a_symlink_destination(tmp_path: Path) -> None:
    """`destination` itself must never be a symlink. If it were, the
    exists()+iterdir() emptiness check and `mkdir(..., exist_ok=True)` both
    transparently follow it, and every write in
    `materialise_commit_subject_v2` would land wherever the symlink points
    -- not inside the advertised, severed-from-source destination."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "a.py").write_text("A = 1\n")
    head = _commit_all(repo, "init")

    real_target = tmp_path / "real_target"
    real_target.mkdir()
    destination = tmp_path / "dest_symlink"
    destination.symlink_to(real_target)

    with pytest.raises(SubjectMaterialisationError) as excinfo:
        materialise_commit_subject_v2(repo_root=repo, ref=head, destination=destination)
    assert excinfo.value.reason_code == SUBJECT_DESTINATION_IS_SYMLINK_REASON_V2
    # Decisive: nothing was written through the symlink into the real
    # target directory it points at.
    assert not any(real_target.iterdir())


# -- `#200-G1-PM` finding 3: duplicate tree paths --------------------------------


def test_duplicate_tree_path_is_refused_instead_of_silently_overwritten(tmp_path: Path) -> None:
    """`git commit-tree` accepts, and `git ls-tree -r` emits, two blob
    entries sharing the exact same literal path in a single tree object --
    proven with real `git mktree` plumbing below, not a hypothetical.
    Without this check, `list_commit_tree_entries_v2`'s own caller-facing
    contract silently drops one committed object: any path-keyed structure
    built from its output (a dict, a materialised file) can only ever hold
    one of the two."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.py").write_text("SEED = 1\n")
    _commit_all(repo, "seed")

    first_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="first content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    second_blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input="second content",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    duplicate_tree = subprocess.run(
        ["git", "mktree", "--missing"],
        cwd=repo,
        input=(f"100644 blob {first_blob}\tsame.py\n" f"100644 blob {second_blob}\tsame.py\n"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    duplicate_commit = subprocess.run(
        ["git", "commit-tree", duplicate_tree, "-m", "duplicate path"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(SubjectMaterialisationError) as excinfo:
        list_commit_tree_entries_v2(repo_root=repo, commit_sha=duplicate_commit)
    assert excinfo.value.reason_code == SUBJECT_DUPLICATE_TREE_PATH_REASON_V2

    destination = tmp_path / "dest"
    with pytest.raises(SubjectMaterialisationError) as excinfo:
        materialise_commit_subject_v2(repo_root=repo, ref=duplicate_commit, destination=destination)
    assert excinfo.value.reason_code == SUBJECT_DUPLICATE_TREE_PATH_REASON_V2
    assert not destination.exists() or not any(destination.iterdir())


# -- `#200-G1-PM` finding 6: non-UTF-8 path digest encoding ----------------------


def test_digest_handles_non_utf8_filename_via_surrogateescape(tmp_path: Path) -> None:
    """A materialised subject can legitimately contain a non-UTF-8 path --
    `list_commit_tree_entries_v2` and `materialise_commit_subject_v2` both
    already decode/write such paths with `surrogateescape`. Before this
    fix, `compute_subject_digest_v2`'s plain `.encode("utf-8")` (strict
    errors) raised `UnicodeEncodeError` on the resulting lone surrogate
    characters -- unable to digest exactly the subjects the adjacent APIs
    explicitly support."""
    root = tmp_path / "root"
    root.mkdir()
    weird_name = os.fsdecode(b"weird-\xff-name.py")
    (root / weird_name).write_bytes(b"content\n")

    digest = compute_subject_digest_v2(root)
    assert digest and len(digest) == 64
