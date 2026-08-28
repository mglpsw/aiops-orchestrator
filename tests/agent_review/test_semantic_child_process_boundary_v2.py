"""`#200-E` Phase 3 -- the decisive process-boundary proposition (grant §2,
§4, §5).

The architectural claim: launching the SEMANTIC CHILD process itself with
`env=bounded_child_env_v2(...)` as its OS process environment automatically
seals every EXISTING owner's un-parameterized `subprocess.run(argv,
cwd=repo_root)` call (no explicit `env=`) -- because such a call inherits
the calling PYTHON PROCESS's own environment by default, and that
environment IS already bounded. No existing owner (`diff_acquisition_v2`,
etc.) needs to be modified for this to hold.

This is proven here against `acquire_diff_v2`, completely unmodified, as a
REAL subprocess launched from a shell carrying hostile ambient state -- not
merely asserted from reading the source.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.agent_review._bounded_git_child_env_v2 import bounded_child_env_v2

_PROBE_SCRIPT = (Path(__file__).parent / "fixtures" / "semantic_child_probe_v2.py")


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


def test_real_existing_owner_ignores_hostile_ambient_outer_environment(tmp_path: Path, monkeypatch):
    """M3-04's positive control: a real semantic child, launched bounded,
    running the real (unmodified) acquire_diff_v2, produces the correct
    diff while the OUTER caller's shell carries hostile GIT_DIR,
    GIT_OBJECT_DIRECTORY, GIT_CONFIG_PARAMETERS, and PYTHONPATH."""
    repo = tmp_path / "target_source"
    _init_repo(repo)
    (repo / "f.txt").write_text("hello\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("hello\nworld\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    monkeypatch.setenv("GIT_DIR", "/nonexistent/attacker/repo")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "/nonexistent/attacker/objects")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath=/tmp/should-not-matter'")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent/attacker/pypath")

    home = tmp_path / "child-home"
    home.mkdir()
    env = bounded_child_env_v2(isolated_home=home)
    assert "GIT_DIR" not in env
    assert "PYTHONPATH" not in env
    assert "GIT_CONFIG_PARAMETERS" not in env

    result = subprocess.run(
        [sys.executable, "-I", "-B", str(_PROBE_SCRIPT), str(repo), base, head],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "B_SAW_GIT_DIR=None" in result.stderr
    assert "B_SAW_PYTHONPATH=None" in result.stderr
    assert "+world" in result.stdout


def test_real_existing_owner_reports_correct_diff_content(tmp_path: Path):
    """Non-vacuity companion: the bounded child must still produce the
    CORRECT diff, not merely fail safely -- a child that always errored
    would trivially "ignore" any hostile state too."""
    repo = tmp_path / "target_source"
    _init_repo(repo)
    (repo / "f.txt").write_text("alpha\n", encoding="utf-8")
    base = _commit_all(repo, "base")
    (repo / "f.txt").write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    head = _commit_all(repo, "head")

    home = tmp_path / "child-home"
    home.mkdir()
    env = bounded_child_env_v2(isolated_home=home)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(_PROBE_SCRIPT), str(repo), base, head],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "+beta" in result.stdout
    assert "+gamma" in result.stdout
