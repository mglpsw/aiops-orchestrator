"""`#200-E` -- production tests for `controlled_subject_v2.py`.

Every hostile fixture here ports a specific `#274` forensic witness (see
`docs/checkpoints/AGENT_REVIEW_V2_200E_CONTROLLED_SUBJECT.md`'s
counterexample ledger for the `id` -> witness mapping) as a proposition
against the NEW authority, not a copy of `#274`'s implementation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.agent_review.controlled_subject_v2 import (
    CONTROLLED_SUBJECT_ALTERNATES_PRESENT_REASON_V2,
    CONTROLLED_SUBJECT_INVALID_REF_REASON_V2,
    CONTROLLED_SUBJECT_OBJECT_CLOSURE_INCOMPLETE_REASON_V2,
    CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2,
    CONTROLLED_SUBJECT_SOURCE_LAYOUT_UNSUPPORTED_REASON_V2,
    CONTROLLED_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2,
    ControlledSubjectError,
    checkout_head_into_subject_v2,
    materialize_controlled_reference_root_v2,
    materialize_controlled_target_subject_v2,
    run_semantic_git_in_subject_v2,
)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "."], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _two_commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")
    return repo, base, head


def test_materialize_and_diff_happy_path(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "diff", f"{base}...{head}"])
    assert result.returncode == 0
    assert b"+world" in result.stdout


def test_scratch_root_removed_on_exit(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        root = subj.root
        assert root.is_dir()
    assert not root.exists()


def test_scratch_root_removed_on_exception_inside_with(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)

    class Boom(Exception):
        pass

    root_holder: list[Path] = []
    with pytest.raises(Boom):
        with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
            root_holder.append(subj.root)
            raise Boom
    assert not root_holder[0].exists()


def test_invalid_ref_shape_is_refused(tmp_path: Path):
    repo, base, _head = _two_commit_repo(tmp_path)
    with pytest.raises(ControlledSubjectError) as excinfo:
        with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha="not-a-sha"):
            pass
    assert excinfo.value.reason_code == CONTROLLED_SUBJECT_INVALID_REF_REASON_V2


def test_source_missing_git_dir_is_refused(tmp_path: Path):
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    with pytest.raises(ControlledSubjectError) as excinfo:
        with materialize_controlled_target_subject_v2(
            not_a_repo, base_sha="0" * 40, head_sha="1" * 40
        ):
            pass
    assert excinfo.value.reason_code == CONTROLLED_SUBJECT_SOURCE_LAYOUT_UNSUPPORTED_REASON_V2


def test_source_alternates_present_is_refused(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    alt_dir = tmp_path / "alt"
    alt_dir.mkdir()
    (repo / ".git" / "objects" / "info" / "alternates").write_text(str(alt_dir), encoding="utf-8")
    with pytest.raises(ControlledSubjectError) as excinfo:
        with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head):
            pass
    assert excinfo.value.reason_code == CONTROLLED_SUBJECT_ALTERNATES_PRESENT_REASON_V2


def test_genuinely_missing_object_fails_closed_ce16(tmp_path: Path):
    """CE-16: a genuinely missing loose object must produce a typed
    refusal at the object-closure step, never a lazy fetch."""
    repo, base, head = _two_commit_repo(tmp_path)
    base_blob = subprocess.run(
        ["git", "rev-parse", f"{base}:f.txt"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    loose_object = repo / ".git" / "objects" / base_blob[:2] / base_blob[2:]
    assert loose_object.is_file()
    loose_object.unlink()

    with pytest.raises(ControlledSubjectError) as excinfo:
        with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head):
            pass
    assert excinfo.value.reason_code == CONTROLLED_SUBJECT_OBJECT_CLOSURE_INCOMPLETE_REASON_V2


def test_lazy_fetch_helper_never_executes_ce16(tmp_path: Path):
    """The sharper form of CE-16: not just a refusal, but proof the
    hostile ext:: transport helper never ran."""
    repo, base, head = _two_commit_repo(tmp_path)
    base_blob = subprocess.run(
        ["git", "rev-parse", f"{base}:f.txt"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    (repo / ".git" / "objects" / base_blob[:2] / base_blob[2:]).unlink()

    marker = tmp_path / "helper-ran"
    helper = tmp_path / "helper.sh"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\nexec git upload-pack {repo}\n", encoding="utf-8")
    helper.chmod(0o755)
    subprocess.run(["git", "config", "protocol.ext.allow", "always"], cwd=repo, check=True)
    subprocess.run(["git", "config", "remote.origin.url", f"ext::{helper}"], cwd=repo, check=True)
    subprocess.run(["git", "config", "remote.origin.promisor", "true"], cwd=repo, check=True)

    with pytest.raises(ControlledSubjectError):
        with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head):
            pass
    assert not marker.exists()


def test_replacement_object_ignored_ce01(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    real_blob = subprocess.run(
        ["git", "rev-parse", f"{head}:f.txt"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    malicious = tmp_path / "malicious.txt"
    malicious.write_text("MALICIOUS\n", encoding="utf-8")
    fake_blob = subprocess.run(
        ["git", "hash-object", "-w", str(malicious)], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "replace", real_blob, fake_blob], cwd=repo, check=True)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "cat-file", "-p", f"{head}:f.txt"])
    assert result.returncode == 0
    assert result.stdout == b"hello\nworld\n"
    assert b"MALICIOUS" not in result.stdout


def test_hostile_hook_never_executes_ce08(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "checkout", "-q", head, "--", "."])
    assert not marker.exists()


def test_hostile_smudge_filter_never_executes_ce10(tmp_path: Path):
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("f.txt filter=evil\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    marker = tmp_path / "smudge-ran"
    subprocess.run(["git", "config", "filter.evil.smudge", f"touch {marker}"], cwd=repo, check=True)
    subprocess.run(["git", "config", "filter.evil.required", "false"], cwd=repo, check=True)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "checkout", "-q", head, "--", "."])
    assert not marker.exists()


def test_includeif_worktree_pattern_never_matches_ce13(tmp_path: Path):
    """CE-13: includeIf.gitdir patterns matching a worktree admin path
    must never fire, because the scratch subject's .git was never derived
    from -- and is never nested under -- the source's .git at all."""
    repo, base, head = _two_commit_repo(tmp_path)
    (repo / ".gitattributes").write_text("f.txt filter=evil2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    head2 = _commit_all(repo, "attrs")

    marker = tmp_path / "includeif-ran"
    inc = tmp_path / "inc.cfg"
    inc.write_text(f'[filter "evil2"]\n\tsmudge = touch {marker}\n', encoding="utf-8")
    subprocess.run(
        ["git", "config", f"includeIf.gitdir:{repo}/.git/worktrees/**.path", str(inc)],
        cwd=repo, check=True,
    )

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head2) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "checkout", "-q", head2, "--", "."])
    assert not marker.exists()


def test_ambient_env_has_no_effect_ce02_ce03(tmp_path: Path, monkeypatch):
    repo, base, head = _two_commit_repo(tmp_path)
    monkeypatch.setenv("GIT_DIR", "/nonexistent/attacker/repo")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/nonexistent/attacker/objects")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=/tmp/should-not-matter'")

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "cat-file", "-p", f"{head}:f.txt"])
    assert result.returncode == 0
    assert result.stdout == b"hello\nworld\n"


def test_severance_semantic_ops_survive_source_unavailable(tmp_path: Path):
    """The decisive TARGET_SUBJECT_MATERIALIZATION_INVARIANT test: once
    SEALED, semantic operations must remain valid even if the source
    checkout becomes unavailable."""
    repo, base, head = _two_commit_repo(tmp_path)
    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        repo.rename(tmp_path / "source.SEVERED")
        try:
            result = run_semantic_git_in_subject_v2(subj, ["git", "diff", f"{base}...{head}"])
            assert result.returncode == 0
            assert b"+world" in result.stdout
        finally:
            (tmp_path / "source.SEVERED").rename(repo)


def _recursive_snapshot(root: Path) -> dict[str, bytes]:
    """Recursive before/after observation of worktree files, .git files,
    ignored files, and untracked files -- not merely `git status
    --porcelain`, per §16."""
    snapshot: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = path.read_bytes()
    return snapshot


def test_target_nonmutation_oracle_catches_a_git_admin_write(tmp_path: Path):
    """§16's known mutant: a write into <target>/.git must be caught by a
    recursive before/after observation, unlike `git status --porcelain`
    (the exact #274 gap -- CE-17)."""
    repo, base, head = _two_commit_repo(tmp_path)
    before = _recursive_snapshot(repo)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "diff", f"{base}...{head}"])
        # MUTATION INJECTION POINT: simulate a defect that writes into the
        # target during materialization, to prove the oracle would catch it.
        (repo / ".git" / "agent-review-mutant-marker").write_text("x", encoding="utf-8")

    after = _recursive_snapshot(repo)
    (repo / ".git" / "agent-review-mutant-marker").unlink()  # cleanup for a clean comparison below
    after_cleaned = _recursive_snapshot(repo)

    assert before != after, "oracle must be ABLE to detect the injected mutation"
    assert before == after_cleaned, "oracle must be silent on a genuinely unmutated target"


def test_target_nonmutation_normal_materialization_leaves_no_trace(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    before = _recursive_snapshot(repo)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "diff", f"{base}...{head}"])
        run_semantic_git_in_subject_v2(subj, ["git", "checkout", "-q", head, "--", "."])

    after = _recursive_snapshot(repo)
    assert before == after


def test_source_untracked_gitattributes_has_no_effect_ce04(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    (repo / ".gitattributes").write_text("f.txt -diff\n", encoding="utf-8")  # untracked

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "diff", "--binary", f"{base}...{head}"])
    assert b"GIT binary patch" not in result.stdout
    assert b"+world" in result.stdout


def test_source_core_attributesfile_redirect_has_no_effect_ce05(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    redirect = tmp_path / "outside.attributes"
    redirect.write_text("f.txt -diff\n", encoding="utf-8")
    subprocess.run(["git", "config", "core.attributesFile", str(redirect)], cwd=repo, check=True)

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "diff", "--binary", f"{base}...{head}"])
    assert b"GIT binary patch" not in result.stdout
    assert b"+world" in result.stdout


def test_source_info_attributes_has_no_effect_ce06_ce07(tmp_path: Path):
    """CE-06/CE-07: an active $GIT_DIR/info/attributes on the source (with
    an NBSP prefix or as a FIFO, per #274's specific bypasses) must have no
    effect -- the scratch's own info/attributes is never populated from the
    source, so there is no detector to fool in the first place."""
    repo, base, head = _two_commit_repo(tmp_path)
    (repo / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("f.txt -diff\n", encoding="utf-8")

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        result = run_semantic_git_in_subject_v2(subj, ["git", "diff", "--binary", f"{base}...{head}"])
    assert b"GIT binary patch" not in result.stdout
    assert b"+world" in result.stdout


def test_source_fsmonitor_never_executes_ce12(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    subprocess.run(
        ["git", "config", "core.fsmonitor", f"sh -c 'touch {marker}; echo'"], cwd=repo, check=True
    )

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        run_semantic_git_in_subject_v2(subj, ["git", "status", "--short"])
    assert not marker.exists()


def test_checkout_head_materializes_working_tree(tmp_path: Path):
    repo, base, head = _two_commit_repo(tmp_path)
    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        checkout_head_into_subject_v2(subj)
        assert (subj.root / "f.txt").read_text() == "hello\nworld\n"


def test_checkout_head_triggers_no_hostile_execution(tmp_path: Path):
    """The checkout helper reuses the exact mechanism Phase 1 proved safe
    against hooks/filters/fsmonitor/includeIf -- this is a smoke check that
    the wiring didn't regress that, not a re-derivation of Phase 1."""
    repo, base, head = _two_commit_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n", encoding="utf-8")
    hook.chmod(0o755)
    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        checkout_head_into_subject_v2(subj)
    assert not marker.exists()


def test_reference_root_materializes_declared_regular_files(tmp_path: Path):
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "artifact.yaml").write_text("artifact bytes\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / ".aiops" / "artifact.yaml").write_text("artifact bytes\nmore\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        checkout_head_into_subject_v2(subj)
        ref_root = materialize_controlled_reference_root_v2(
            subj, declared_paths=(".aiops/artifact.yaml", ".aiops/does-not-exist.yaml")
        )
        assert (ref_root / ".aiops" / "artifact.yaml").read_text() == "artifact bytes\nmore\n"
        assert not (ref_root / ".aiops" / "does-not-exist.yaml").exists()


def test_reference_root_refuses_symlink_declared_path(tmp_path: Path):
    """The reference-material adapter's OWN independent symlink refusal --
    reachable without a prior checkout, since it reads via ls-tree/cat-file
    against the object database directly, not the working tree. (A
    checkout would ALSO refuse first now, per
    test_checkout_refuses_a_committed_symlink_anywhere_in_the_tree below --
    this test proves the reference adapter doesn't rely on that.)"""
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / ".aiops").mkdir()
    (repo / "outside.yaml").write_text("MALICIOUS\n", encoding="utf-8")
    (repo / ".aiops" / "evil_link.yaml").symlink_to(repo / "outside.yaml")
    base = _commit_all(repo, "with symlink")
    head = base

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        with pytest.raises(ControlledSubjectError) as excinfo:
            materialize_controlled_reference_root_v2(subj, declared_paths=(".aiops/evil_link.yaml",))
        assert (
            excinfo.value.reason_code == CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2
        )


def test_checkout_refuses_a_committed_symlink_anywhere_in_the_tree(tmp_path: Path):
    """§ independent review lane A's finding: a committed symlink escaping
    the subject must be refused by checkout itself, not merely by the
    reference-material adapter for paths a profile happens to declare."""
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / "backend").mkdir()
    outside = tmp_path / "host_secret.txt"
    outside.write_text("SECRET_HOST_CONTENT\n", encoding="utf-8")
    (repo / "backend" / "evil_link.py").symlink_to(outside)
    base = _commit_all(repo, "with symlink")
    head = base

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        with pytest.raises(ControlledSubjectError) as excinfo:
            checkout_head_into_subject_v2(subj)
        assert (
            excinfo.value.reason_code == CONTROLLED_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2
        )
        assert not (subj.root / "backend" / "evil_link.py").exists()


def test_reference_root_refuses_directory_declared_path(tmp_path: Path):
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / ".aiops" / "nested").mkdir(parents=True)
    (repo / ".aiops" / "nested" / "f.yaml").write_text("x\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    head = base

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        checkout_head_into_subject_v2(subj)
        with pytest.raises(ControlledSubjectError) as excinfo:
            materialize_controlled_reference_root_v2(subj, declared_paths=(".aiops/nested",))
        assert (
            excinfo.value.reason_code == CONTROLLED_SUBJECT_REFERENCE_PATH_UNSUPPORTED_REASON_V2
        )


def test_reference_root_ignores_source_working_tree_toctou(tmp_path: Path):
    """Decisive proposition for §7: identical (base_sha, head_sha) inputs
    must bind identical reference bytes regardless of what the SOURCE's own
    mutable working tree contains at read time -- the whole TOCTOU class
    #274's reference_source_v2.py existed to close."""
    repo = tmp_path / "source"
    _init_repo(repo)
    (repo / ".aiops").mkdir()
    (repo / ".aiops" / "artifact.yaml").write_text("committed bytes\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    head = base

    # Mutate the SOURCE's own working tree after commit, without a new commit.
    (repo / ".aiops" / "artifact.yaml").write_text("DIRTY_UNCOMMITTED_BYTES\n", encoding="utf-8")

    with materialize_controlled_target_subject_v2(repo, base_sha=base, head_sha=head) as subj:
        checkout_head_into_subject_v2(subj)
        ref_root = materialize_controlled_reference_root_v2(
            subj, declared_paths=(".aiops/artifact.yaml",)
        )
        assert (ref_root / ".aiops" / "artifact.yaml").read_text() == "committed bytes\n"
