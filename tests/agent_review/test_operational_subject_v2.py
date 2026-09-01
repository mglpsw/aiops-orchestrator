"""`#200-F` §11/§15 -- controlled subjects and the git adversarial corpus.

Ported from `#274`/`#276` as RED tests, not as inherited qualification.

Several entries are proved *structurally unreachable* rather than defended.
Materialisation enumerates a commit's tree with ``ls-tree -r`` and reads bytes
with ``cat-file``, so index bits (``assume-unchanged``, ``skip-worktree``) and
``.gitattributes`` export rules have nothing to act on. A check can regress
when someone edits it; a mechanism that is never invoked cannot.
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from app.agent_review.operational_bounded_git_v2 import (
    BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2,
    BoundedGitError,
    bounded_git_environment_v2,
    resolve_trusted_git_absolute_path_v2,
    run_bounded_git_v2,
)
from app.agent_review.operational_inner_control_v2 import compute_subject_digest_v2
from app.agent_review.operational_refusal_v2 import ExpectedOperationalRefusalV2
from app.agent_review.operational_subject_v2 import (
    SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2,
    SUBJECT_UNKNOWN_COMMIT_REASON_V2,
    SubjectMaterialisationError,
    materialise_controlled_target_subject_v2,
    materialise_toolrepo_execution_subject_v2,
)

_GIT_V2 = resolve_trusted_git_absolute_path_v2()


def _git_v2(repository: pathlib.Path, *arguments: str) -> str:
    """Drive a fixture repository directly, outside the bounded contract."""
    completed = subprocess.run(
        [_GIT_V2, *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return completed.stdout.strip()


@pytest.fixture
def target_repository_v2(tmp_path: pathlib.Path) -> pathlib.Path:
    repository = tmp_path / "target"
    repository.mkdir()
    _git_v2(repository, "init", "-q", "-b", "main")
    (repository / "src").mkdir()
    (repository / "src" / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repository / "README.md").write_text("readme\n", encoding="utf-8")
    _git_v2(repository, "add", "-A")
    _git_v2(repository, "commit", "-q", "-m", "initial")
    return repository


def _head_v2(repository: pathlib.Path) -> str:
    return _git_v2(repository, "rev-parse", "HEAD")


def test_a_subject_is_materialised_from_committed_bytes(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Non-vacuity control for the whole file."""
    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert subject.file_count == 2
    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"
    assert subject.head_sha == _head_v2(target_repository_v2)


def test_an_uncommitted_worktree_modification_is_invisible(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The committed tree is the subject, not the working directory.

    This is also the methodology trap that repeatedly cost the predecessor
    time: a mutation left uncommitted is invisible to black-box tests, so a
    mutant must be *committed* before it proves anything.
    """
    (target_repository_v2 / "src" / "service.py").write_text(
        "VALUE = 999  # uncommitted\n", encoding="utf-8"
    )

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"


def test_source_severance_the_subject_outlives_its_checkout(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Deleting the original checkout cannot change what the run reviews."""
    import shutil

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )
    before = compute_subject_digest_v2(subject.root)

    shutil.rmtree(target_repository_v2)

    assert not target_repository_v2.exists()
    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"
    assert compute_subject_digest_v2(subject.root) == before


def test_the_target_repository_is_not_mutated(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Reading a repository must leave no trace in it.

    HEAD and the full porcelain status are compared before and after, so an
    added file, a staged change or a moved ref would all surface.
    """
    head_before = _head_v2(target_repository_v2)
    status_before = _git_v2(target_repository_v2, "status", "--porcelain=v1")

    materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=head_before,
        destination=tmp_path / "subject",
    )

    assert _git_v2(target_repository_v2, "rev-parse", "HEAD") == head_before
    assert _git_v2(target_repository_v2, "status", "--porcelain=v1") == status_before


def test_a_planted_fake_git_on_path_is_never_executed(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The `#276` round-2 P0.

    A ``git`` earlier on ``PATH`` must not be what runs. Resolution goes
    through ``os.defpath``, which the caller cannot influence, and the child
    is exec'd with the resulting absolute path.
    """
    fake_directory = tmp_path / "fakebin"
    fake_directory.mkdir()
    marker = tmp_path / "fake-git-was-executed"
    fake_git = fake_directory / "git"
    fake_git.write_text(
        f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8"
    )
    fake_git.chmod(0o755)

    original_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{fake_directory}{os.pathsep}{original_path}"
    try:
        assert resolve_trusted_git_absolute_path_v2() != str(fake_git)
        subject = materialise_controlled_target_subject_v2(
            target_root=target_repository_v2,
            head_sha=_head_v2(target_repository_v2),
            destination=tmp_path / "subject",
        )
    finally:
        os.environ["PATH"] = original_path

    assert not marker.exists(), "the planted git was executed"
    assert subject.file_count == 2


@pytest.mark.parametrize(
    "variable_name, value",
    [
        ("GIT_DIR", "/nonexistent/elsewhere/.git"),
        ("GIT_WORK_TREE", "/nonexistent/elsewhere"),
        ("GIT_INDEX_FILE", "/nonexistent/index"),
        ("GIT_OBJECT_DIRECTORY", "/nonexistent/objects"),
        ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "/nonexistent/alt"),
        ("GIT_CONFIG", "/nonexistent/config"),
        ("GIT_CONFIG_GLOBAL", "/nonexistent/gitconfig"),
        ("GIT_EXTERNAL_DIFF", "/bin/false"),
        ("GIT_CEILING_DIRECTORIES", "/"),
    ],
)
def test_ambient_git_environment_variables_cannot_reach_the_child(
    target_repository_v2: pathlib.Path,
    tmp_path: pathlib.Path,
    variable_name: str,
    value: str,
) -> None:
    """The allowlist means these are absent, not overridden.

    Each of these would redirect or break the read if it were inherited. The
    child's environment is built from nothing, so this module never needs to
    know the full list of dangerous names -- which is the point, because git
    has more of them than anyone reliably remembers.
    """
    # Read HEAD *before* poisoning: this helper deliberately runs git with the
    # ambient environment, so a poisoned GIT_DIR would break the fixture
    # rather than test the product.
    head_sha = _head_v2(target_repository_v2)

    original = os.environ.get(variable_name)
    os.environ[variable_name] = value
    try:
        subject = materialise_controlled_target_subject_v2(
            target_root=target_repository_v2,
            head_sha=head_sha,
            destination=tmp_path / "subject",
        )
    finally:
        if original is None:
            os.environ.pop(variable_name, None)
        else:
            os.environ[variable_name] = original

    assert subject.file_count == 2
    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"


def test_the_bounded_environment_is_an_allowlist_not_a_filtered_copy() -> None:
    """Asserted on the mapping itself, so it cannot drift into a copy.

    A ``dict(os.environ)`` with deletions would pass most behavioural tests
    while still leaking everything nobody thought to delete.
    """
    os.environ["AGENT_REVIEW_CANARY_V2"] = "must-not-propagate"
    try:
        environment = bounded_git_environment_v2()
    finally:
        os.environ.pop("AGENT_REVIEW_CANARY_V2", None)

    assert "AGENT_REVIEW_CANARY_V2" not in environment
    assert set(environment) == {
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
    assert environment["PATH"] == os.defpath


def test_a_repository_gitattributes_cannot_omit_files_from_the_subject(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``export-ignore`` is why ``git archive`` was abandoned.

    A target repository controls its own ``.gitattributes``. Under ``archive``
    it could delete its own files from the subject a reviewer sees, invisibly.
    ``ls-tree``/``cat-file`` never consult attributes, so the file is present.
    """
    (target_repository_v2 / ".gitattributes").write_text(
        "src/service.py export-ignore\n", encoding="utf-8"
    )
    _git_v2(target_repository_v2, "add", "-A")
    _git_v2(target_repository_v2, "commit", "-q", "-m", "hide the interesting file")

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert (subject.root / "src" / "service.py").is_file(), (
        "the repository excluded its own file from review"
    )
    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("index_flag", ["--assume-unchanged", "--skip-worktree"])
def test_index_bits_cannot_hide_a_file_from_the_subject(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path, index_flag: str
) -> None:
    """Structurally unreachable rather than defended.

    These are index bits. A commit's tree has no index, so enumerating the
    tree cannot consult them.
    """
    _git_v2(target_repository_v2, "update-index", index_flag, "src/service.py")
    (target_repository_v2 / "src" / "service.py").write_text(
        "VALUE = 666\n", encoding="utf-8"
    )

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n"


def test_a_git_replace_ref_cannot_rewrite_what_is_materialised(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``--no-replace-objects`` on every invocation.

    A replace ref silently substitutes one object for another, so a subject
    could be built from a commit the recorded sha does not describe.
    """
    original_head = _head_v2(target_repository_v2)
    (target_repository_v2 / "src" / "service.py").write_text(
        "VALUE = 'replaced'\n", encoding="utf-8"
    )
    _git_v2(target_repository_v2, "add", "-A")
    _git_v2(target_repository_v2, "commit", "-q", "-m", "decoy")
    decoy_head = _head_v2(target_repository_v2)
    _git_v2(target_repository_v2, "replace", original_head, decoy_head)

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=original_head,
        destination=tmp_path / "subject",
    )

    assert (subject.root / "src" / "service.py").read_text() == "VALUE = 1\n", (
        "a replace ref substituted the materialised commit"
    )


def test_a_repository_hook_is_never_executed(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Reading a repository must not run its code."""
    marker = tmp_path / "hook-was-executed"
    hooks = target_repository_v2 / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for hook_name in ("post-checkout", "pre-command", "post-index-change"):
        hook = hooks / hook_name
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
        hook.chmod(0o755)

    materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert not marker.exists()


def test_an_unknown_commit_is_a_typed_refusal(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    with pytest.raises(SubjectMaterialisationError) as caught:
        materialise_controlled_target_subject_v2(
            target_root=target_repository_v2,
            head_sha="0" * 40,
            destination=tmp_path / "subject",
        )

    assert caught.value.reason_code == SUBJECT_UNKNOWN_COMMIT_REASON_V2
    assert isinstance(caught.value, ExpectedOperationalRefusalV2)


def test_a_tree_sha_is_refused_because_it_names_no_revision(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """``^{commit}`` is required, not merely "some object exists"."""
    tree_sha = _git_v2(target_repository_v2, "rev-parse", "HEAD^{tree}")

    with pytest.raises(SubjectMaterialisationError) as caught:
        materialise_controlled_target_subject_v2(
            target_root=target_repository_v2,
            head_sha=tree_sha,
            destination=tmp_path / "subject",
        )

    assert caught.value.reason_code == SUBJECT_UNKNOWN_COMMIT_REASON_V2


def test_a_non_empty_destination_is_refused(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Materialising over existing bytes would blend two subjects."""
    destination = tmp_path / "subject"
    destination.mkdir()
    (destination / "leftover.py").write_text("stale\n", encoding="utf-8")

    with pytest.raises(SubjectMaterialisationError) as caught:
        materialise_controlled_target_subject_v2(
            target_root=target_repository_v2,
            head_sha=_head_v2(target_repository_v2),
            destination=destination,
        )

    assert caught.value.reason_code == SUBJECT_DESTINATION_NOT_EMPTY_REASON_V2


def test_an_unusable_worktree_is_a_typed_refusal(tmp_path: pathlib.Path) -> None:
    with pytest.raises(BoundedGitError) as caught:
        run_bounded_git_v2(["status"], cwd=tmp_path / "does-not-exist")

    assert caught.value.reason_code == BOUNDED_GIT_WORKTREE_UNUSABLE_REASON_V2


def test_executable_bits_and_symlinks_survive_materialisation(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Mode and link structure are part of the bytes under review."""
    script = target_repository_v2 / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o755)
    (target_repository_v2 / "link.py").symlink_to("src/service.py")
    _git_v2(target_repository_v2, "add", "-A")
    _git_v2(target_repository_v2, "commit", "-q", "-m", "modes and links")

    subject = materialise_controlled_target_subject_v2(
        target_root=target_repository_v2,
        head_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "subject",
    )

    assert os.access(subject.root / "run.sh", os.X_OK)
    assert (subject.root / "link.py").is_symlink()
    assert os.readlink(subject.root / "link.py") == "src/service.py"


def test_a_toolrepo_subject_digest_describes_the_bytes_that_will_run(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The digest is computed after writing, from disk.

    Computing it from the request rather than the result would make it a
    restatement of the input instead of a description of the subject.
    """
    subject = materialise_toolrepo_execution_subject_v2(
        toolrepo_root=target_repository_v2,
        toolrepo_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "toolrepo",
    )

    assert subject.subject_digest == compute_subject_digest_v2(subject.root)

    (subject.root / "src" / "service.py").write_text("tampered\n", encoding="utf-8")
    assert subject.subject_digest != compute_subject_digest_v2(subject.root)


def test_an_untracked_root_module_cannot_reach_the_inner_epoch(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """`#200-F` §6 -- the bootstrap shadowing witness, for the inner.

    The `#276` witness planted an untracked ``dataclasses.py`` beside the
    entry point, where it executed before any subject was sealed while HEAD
    and the worktree status stayed unchanged.

    For the **inner** this is closed structurally rather than by a check: the
    inner executes from a subject materialised out of committed bytes, and an
    untracked file is by definition not in them. There is no import-order
    guard to get wrong, and nothing to regress.

    The scope of the claim is exactly that. The **outer** still executes from
    the ordinary checkout before anything is sealed and remains exposed;
    closing that needs an attested launcher this slice does not build, and the
    checkpoint records ``bootstrap.remotely_attested: false`` rather than
    implying otherwise.
    """
    shadow = target_repository_v2 / "dataclasses.py"
    shadow.write_text(
        "raise SystemExit('shadow module executed')\n", encoding="utf-8"
    )
    also_shadowed = target_repository_v2 / "src" / "os.py"
    also_shadowed.write_text("BAD = True\n", encoding="utf-8")

    # Untracked, and deliberately left that way: this is the whole point.
    assert "dataclasses.py" in _git_v2(target_repository_v2, "status", "--porcelain=v1")

    subject = materialise_toolrepo_execution_subject_v2(
        toolrepo_root=target_repository_v2,
        toolrepo_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "toolrepo",
    )

    assert not (subject.root / "dataclasses.py").exists()
    assert not (subject.root / "src" / "os.py").exists()
    assert (subject.root / "src" / "service.py").is_file(), (
        "non-vacuity: committed files must still be present"
    )


def test_a_stale_bytecode_file_cannot_reach_the_inner_epoch(
    target_repository_v2: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The ``.pyc`` entry of the red corpus, closed the same way.

    A committed tree carries no ``__pycache__``, and the inner runs under
    ``python -B`` so it writes none either.
    """
    cache = target_repository_v2 / "src" / "__pycache__"
    cache.mkdir()
    (cache / "service.cpython-311.pyc").write_bytes(b"\x00stale bytecode")

    subject = materialise_toolrepo_execution_subject_v2(
        toolrepo_root=target_repository_v2,
        toolrepo_sha=_head_v2(target_repository_v2),
        destination=tmp_path / "toolrepo",
    )

    assert not (subject.root / "src" / "__pycache__").exists()
    assert list(subject.root.rglob("*.pyc")) == []
