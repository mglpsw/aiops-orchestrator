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


# -- #200-G1-S / S3: Git path-byte faithful digest -------------------------------


def test_digest_survives_and_is_byte_faithful_for_non_utf8_git_filenames(
    tmp_path: Path,
) -> None:
    """Salvaged from forensic PR #302 (finding #6, already CLOSED_AND_
    VERIFIED there -- unrelated to that PR's terminal STOP). Git tree entry
    paths are raw bytes; git never requires them to be valid UTF-8. A
    legitimately non-UTF-8 filename (constructed here with real `git mktree`
    plumbing, not a synthetic mock) is decoded by this codebase's own
    `list_commit_tree_entries_v2` with `errors="surrogateescape"` -- on
    purpose, per that function's own docstring, so a real property of the
    commit's history is never refused as "our" error -- and
    `materialise_commit_subject_v2` round-trips it back onto disk exactly,
    because `pathlib`/`os` encode a `str` path back to bytes via
    `os.fsencode`, which uses the same `surrogateescape` error handler by
    default on POSIX. `compute_subject_digest_v2`'s FINAL step broke that
    chain: it joined every entry's path into one big string and called
    `.encode("utf-8")` in *strict* mode -- a lone surrogate codepoint
    produced by decoding non-UTF-8 bytes with `surrogateescape` cannot be
    strictly UTF-8-encoded, so digesting a subject materialised from a
    perfectly legitimate commit crashed with `UnicodeEncodeError`, on the
    very same bytes the rest of this module's pipeline already handled
    correctly.

    Two DIFFERENT non-UTF-8 names (differing only in the invalid byte) are
    digested to prove byte fidelity, not just crash-avoidance: a fix that
    papered over the crash by discarding or replacing the unrepresentable
    byte (e.g. `errors="replace"`) would make both names collapse to the
    same replacement character and digest identically, silently losing the
    very information a content-addressed digest exists to preserve."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "seed.py").write_text("SEED = 1\n")
    _commit_all(repo, "seed")

    blob_sha = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=repo,
        input=b"non-utf8-filename fixture content\n",
        check=True,
        capture_output=True,
    ).stdout.decode("ascii").strip()

    def _commit_with_raw_named_blob(raw_name: bytes, message: str) -> str:
        mktree_input = f"100644 blob {blob_sha}\t".encode("ascii") + raw_name + b"\n"
        tree_sha = subprocess.run(
            ["git", "mktree", "--missing"],
            cwd=repo,
            input=mktree_input,
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()
        return subprocess.run(
            ["git", "commit-tree", tree_sha, "-m", message],
            cwd=repo,
            check=True,
            capture_output=True,
        ).stdout.decode("ascii").strip()

    # Two names differing only in one invalid byte -- 0xff vs 0xfe are each
    # invalid as a UTF-8 continuation/lead byte on their own, so both
    # legitimately round-trip through `surrogateescape`, not through any
    # valid multi-byte UTF-8 sequence.
    commit_a = _commit_with_raw_named_blob(b"bad-\xff-name.py", "non-utf8 a")
    commit_b = _commit_with_raw_named_blob(b"bad-\xfe-name.py", "non-utf8 b")

    destination_a = tmp_path / "subject_a"
    destination_b = tmp_path / "subject_b"
    materialise_commit_subject_v2(repo_root=repo, ref=commit_a, destination=destination_a)
    materialise_commit_subject_v2(repo_root=repo, ref=commit_b, destination=destination_b)

    digest_a = compute_subject_digest_v2(destination_a)  # must not raise UnicodeEncodeError
    digest_b = compute_subject_digest_v2(destination_b)
    assert digest_a and len(digest_a) == 64
    assert digest_b and len(digest_b) == 64
    assert digest_a != digest_b, "distinct raw byte names must not digest identically"


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
