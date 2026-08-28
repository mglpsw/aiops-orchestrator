"""`#200-E` -- production tests for `toolrepo_execution_subject_v2.py`."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.agent_review.toolrepo_execution_subject_v2 import (
    TOOLREPO_EXECUTION_SUBJECT_BYTE_IDENTITY_MISMATCH_REASON_V2,
    TOOLREPO_EXECUTION_SUBJECT_INVALID_SHA_REASON_V2,
    TOOLREPO_EXECUTION_SUBJECT_ROOT_UNUSABLE_REASON_V2,
    TOOLREPO_EXECUTION_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2,
    ToolrepoExecutionSubjectError,
    materialize_toolrepo_execution_subject_v2,
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


def _package_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "devrepo"
    _init_repo(repo)
    (repo / "app").mkdir()
    (repo / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "agent_review").mkdir()
    (repo / "app" / "agent_review" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "app" / "agent_review" / "probe_target.py").write_text(
        'MARKER = "LEGITIMATE"\n', encoding="utf-8"
    )
    (repo / "scripts").mkdir()
    (repo / "scripts" / "probe_entry.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import argparse\n"
        "import app.agent_review.probe_target as pt\n"
        "print('MARKER', pt.MARKER)\n"
        "print('ARGPARSE_FILE', argparse.__file__)\n"
        "print('PROBE_FILE', pt.__file__)\n"
        "try:\n"
        "    import pythonpath_canary\n"
        "    print('PYTHONPATH_CANARY_IMPORTED')\n"
        "except ImportError:\n"
        "    print('PYTHONPATH_CANARY_ABSENT')\n",
        encoding="utf-8",
    )
    sha = _commit_all(repo, "base")
    return repo, sha


def test_materialize_happy_path(tmp_path: Path):
    repo, sha = _package_repo(tmp_path)
    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app", "scripts")
    ) as subj:
        assert (subj.root / "app" / "agent_review" / "probe_target.py").read_text() == (
            'MARKER = "LEGITIMATE"\n'
        )
        assert {e.path for e in subj.entries} >= {
            "app/agent_review/probe_target.py",
            "scripts/probe_entry.py",
        }


def test_scratch_root_removed_on_exit(tmp_path: Path):
    repo, sha = _package_repo(tmp_path)
    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app",)
    ) as subj:
        root = subj.root
    assert not root.exists()


def test_invalid_sha_shape_is_refused(tmp_path: Path):
    repo, _sha = _package_repo(tmp_path)
    with pytest.raises(ToolrepoExecutionSubjectError) as excinfo:
        with materialize_toolrepo_execution_subject_v2(
            repo, declared_toolrepo_sha="not-a-sha", bounded_paths=("app",)
        ):
            pass
    assert excinfo.value.reason_code == TOOLREPO_EXECUTION_SUBJECT_INVALID_SHA_REASON_V2


def test_non_repo_root_is_refused(tmp_path: Path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(ToolrepoExecutionSubjectError) as excinfo:
        with materialize_toolrepo_execution_subject_v2(
            not_a_repo, declared_toolrepo_sha="0" * 40, bounded_paths=("app",)
        ):
            pass
    assert excinfo.value.reason_code == TOOLREPO_EXECUTION_SUBJECT_ROOT_UNUSABLE_REASON_V2


def test_committed_symlink_is_refused_ce22(tmp_path: Path):
    repo = tmp_path / "devrepo"
    _init_repo(repo)
    (repo / "app").mkdir()
    (repo / "app" / "real.py").write_text("REAL=1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "real.py").write_text("MALICIOUS_EXTERNAL=1\n", encoding="utf-8")
    (repo / "app" / "evil_link.py").symlink_to(outside / "real.py")
    sha = _commit_all(repo, "with symlink")

    with pytest.raises(ToolrepoExecutionSubjectError) as excinfo:
        with materialize_toolrepo_execution_subject_v2(
            repo, declared_toolrepo_sha=sha, bounded_paths=("app",)
        ):
            pass
    assert excinfo.value.reason_code == TOOLREPO_EXECUTION_SUBJECT_SYMLINK_OR_GITLINK_PRESENT_REASON_V2


def test_index_flags_have_no_effect_ce14_ce15(tmp_path: Path):
    """CE-14/CE-15: assume-unchanged and skip-worktree defeat a
    `git diff HEAD`-based cleanliness proof; `git archive` reads the
    commit tree directly, so they must have zero effect here."""
    repo, sha = _package_repo(tmp_path)
    (repo / "app" / "agent_review" / "probe_target.py").write_text(
        'MARKER = "TAMPERED"\n', encoding="utf-8"
    )
    subprocess.run(
        ["git", "update-index", "--assume-unchanged", "app/agent_review/probe_target.py"],
        cwd=repo, check=True,
    )
    subprocess.run(
        ["git", "update-index", "--skip-worktree", "scripts/probe_entry.py"], cwd=repo, check=True
    )

    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app", "scripts")
    ) as subj:
        assert (subj.root / "app" / "agent_review" / "probe_target.py").read_text() == (
            'MARKER = "LEGITIMATE"\n'
        )


def test_filter_clean_has_no_effect_ce09(tmp_path: Path):
    repo = tmp_path / "devrepo"
    _init_repo(repo)
    (repo / "app").mkdir()
    (repo / "app" / "victim.py") .write_text('MARKER = "LEGITIMATE"\n', encoding="utf-8")
    (repo / "app" / ".gitattributes").write_text("victim.py filter=evil\n", encoding="utf-8")
    sha = _commit_all(repo, "base")

    marker = tmp_path / "clean-ran"
    subprocess.run(
        ["git", "config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'"], cwd=repo, check=True
    )

    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app",)
    ) as subj:
        assert (subj.root / "app" / "victim.py").read_text() == 'MARKER = "LEGITIMATE"\n'
    assert not marker.exists()


def test_untracked_root_and_scripts_shadow_absent_ce18_ce19_ce20(tmp_path: Path):
    repo, sha = _package_repo(tmp_path)
    (repo / "pydantic.py").write_text("print('ROOT_SHADOW_EXECUTED')\n", encoding="utf-8")
    (repo / "scripts" / "argparse.py").write_text(
        "print('SCRIPTS_SHADOW_EXECUTED')\n", encoding="utf-8"
    )
    (repo / "app" / "agent_review" / "_stray_evil.py").write_text(
        "print('UNTRACKED_STRAY_EXECUTED')\n", encoding="utf-8"
    )

    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app", "scripts")
    ) as subj:
        assert not (subj.root / "pydantic.py").exists()
        assert not (subj.root / "scripts" / "argparse.py").exists()
        assert not (subj.root / "app" / "agent_review" / "_stray_evil.py").exists()


def test_pyc_shadow_absent_ce21(tmp_path: Path):
    repo, sha = _package_repo(tmp_path)
    pycache = repo / "app" / "agent_review" / "__pycache__"
    pycache.mkdir()
    (pycache / "probe_target.cpython-311.pyc").write_bytes(b"not-a-real-pyc-but-present")

    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app",)
    ) as subj:
        assert not (subj.root / "app" / "agent_review" / "__pycache__").exists()


def test_real_subprocess_isolated_mode_imports_from_subject_not_devrepo(tmp_path: Path):
    """End-to-end: a REAL subprocess, `python -I`, executing from the
    materialized subject, must import the committed bytes and must not
    resolve the hostile scripts/argparse.py shadow -- proving
    TOOLREPO_EXECUTION_SUBJECT_INVARIANT for a real semantic child, not
    just a filesystem-content assertion."""
    repo, sha = _package_repo(tmp_path)
    (repo / "app" / "agent_review" / "probe_target.py").write_text(
        'MARKER = "TAMPERED"\n', encoding="utf-8"
    )
    (repo / "scripts" / "argparse.py").write_text(
        "raise SystemExit('SCRIPTS_ARGPARSE_SHADOW_EXECUTED')\n", encoding="utf-8"
    )

    canary_dir = tmp_path / "pythonpath-injected"
    canary_dir.mkdir()
    (canary_dir / "pythonpath_canary.py").write_text(
        "raise SystemExit('PYTHONPATH_INJECTED_MODULE_EXECUTED')\n", encoding="utf-8"
    )

    with materialize_toolrepo_execution_subject_v2(
        repo, declared_toolrepo_sha=sha, bounded_paths=("app", "scripts")
    ) as subj:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "scripts/probe_entry.py"],
            cwd=subj.root, capture_output=True, text=True,
            env={"PYTHONPATH": str(canary_dir), "PATH": "/usr/bin:/bin"},
        )
    assert result.returncode == 0, result.stderr
    assert "MARKER LEGITIMATE" in result.stdout
    assert str(subj.root) in result.stdout  # PROBE_FILE line shows the subject root
    assert "/usr/lib/python" in result.stdout or "argparse.py" in result.stdout
    assert "SHADOW_EXECUTED" not in result.stdout
    assert "SHADOW_EXECUTED" not in result.stderr
    assert "PYTHONPATH_CANARY_ABSENT" in result.stdout, (
        "a REAL hostile module on PYTHONPATH must not become importable under -I"
    )


def test_tampered_bounded_source_refused_by_byte_identity_oracle(tmp_path: Path, monkeypatch):
    """Direct falsifier for §8's byte-identity oracle: force a mismatch
    between the committed blob and the materialized filesystem bytes, and
    confirm the authority refuses rather than silently proceeding. This is
    the mutation-non-vacuity check for 'skip raw blob/filesystem equality'
    (grant §20) -- monkeypatches the extraction step to write different
    bytes than the archive actually produced.
    """
    import app.agent_review.toolrepo_execution_subject_v2 as mod

    repo, sha = _package_repo(tmp_path)

    original_extract = mod._extract_tar_bytes_v2

    def _tampering_extract(raw: bytes, *, into):
        original_extract(raw, into=into)
        (into / "app" / "agent_review" / "probe_target.py").write_text(
            'MARKER = "MUTATED_AFTER_EXTRACTION"\n', encoding="utf-8"
        )

    monkeypatch.setattr(mod, "_extract_tar_bytes_v2", _tampering_extract)

    with pytest.raises(ToolrepoExecutionSubjectError) as excinfo:
        with materialize_toolrepo_execution_subject_v2(
            repo, declared_toolrepo_sha=sha, bounded_paths=("app",)
        ):
            pass
    assert excinfo.value.reason_code == TOOLREPO_EXECUTION_SUBJECT_BYTE_IDENTITY_MISMATCH_REASON_V2
