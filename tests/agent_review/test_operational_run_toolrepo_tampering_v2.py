"""`#200-E` Phase 3, §18 -- product-level toolrepo-tampering corpus.

Runs the REAL public outer CLI from a hostile DEVELOPMENT CHECKOUT (a
disposable clone of this toolrepo, tampered), not merely a hostile
environment (that is `test_operational_run_blackbox_e2e_v2.py`'s job).
This is the decisive product proof that Phase 2's `ToolrepoExecutionSubjectV2`
is actually wired into the CLI, not merely implemented beside it: every
witness here is a `#274` forensic class the toolrepo spike already closed
at the unit level (see `test_toolrepo_execution_subject_v2.py`) -- run
again here through the real product entrypoint.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLREPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "v2" / "agent_escala"
_TOOLCHAIN_DIGEST = "e" * 64
_TESTED_MERGE_SHA = "3" * 40


def _write_grouping_policy(path: Path) -> None:
    doc = {
        "schema_id": "agent-review.semantic-grouping-policy.v2",
        "schema_version": 2,
        "source": "repo-semantic-grouping-policy",
        "rules": [
            {
                "rule_id": "backend",
                "semantic_group": "primary_backend_logic",
                "path_patterns": ["backend/scheduling/*.py"],
                "contract_ids": [],
                "artifact_ids": [],
                "priority": 0,
            }
        ],
        "fallback_group": None,
        "policy_sha256": "0" * 64,
    }
    sys.path.insert(0, str(TOOLREPO_ROOT))
    from app.agent_review.semantic_grouping_policy_v2 import compute_semantic_grouping_policy_sha256_v2

    doc["policy_sha256"] = compute_semantic_grouping_policy_sha256_v2(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")


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


def _build_real_target(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "target_repo"
    _init_repo(repo)
    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 1\n", encoding="utf-8"
    )
    (repo / "artifacts").mkdir()
    shutil.copy(FIXTURES_ROOT / "artifacts" / "full.diff", repo / "artifacts" / "full.diff")
    (repo / "contracts").mkdir()
    shutil.copy(
        FIXTURES_ROOT / "contracts" / "domain-contracts.yaml", repo / "contracts" / "domain-contracts.yaml"
    )
    base_sha = _commit_all(repo, "base")
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        "def compute_shift():\n    return 2\n", encoding="utf-8"
    )
    head_sha = _commit_all(repo, "head")
    return repo, base_sha, head_sha


def _clone_toolrepo(dest: Path) -> None:
    subprocess.run(["git", "clone", "--quiet", str(TOOLREPO_ROOT), str(dest)], check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=dest, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=dest, check=True)


def _run_cli_from(toolrepo: Path, *, target_repo: Path, base_sha: str, head_sha: str,
                   responses_dir: Path, policy_path: Path) -> subprocess.CompletedProcess:
    cli_script = toolrepo / "scripts" / "aiops-review-run-v2.py"
    return subprocess.run(
        [
            sys.executable, str(cli_script),
            "--target-root", str(target_repo),
            "--base-sha", base_sha,
            "--head-sha", head_sha,
            "--tested-merge-sha", _TESTED_MERGE_SHA,
            "--toolchain-digest", _TOOLCHAIN_DIGEST,
            "--repo", "mglpsw/AgentEscala",
            "--pr-number", "101",
            "--trusted-profile-root", str(FIXTURES_ROOT),
            "--grouping-policy", str(policy_path),
            "--responses-dir", str(responses_dir),
            "--pr-state", "open",
            "--event-type", "manual",
            "--event-action", "manual",
            "--delivery-id", "tampering-1",
        ],
        capture_output=True, text=True,
    )


@pytest.fixture(scope="module")
def _clean_readiness(tmp_path_factory):
    """One clean baseline run, computed once and reused as the comparison
    point for every tampering scenario below -- each scenario tampers a
    FRESH clone, never this one."""
    tmp_path = tmp_path_factory.mktemp("clean")
    toolrepo = tmp_path / "clean_toolrepo"
    _clone_toolrepo(toolrepo)
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    result = _run_cli_from(
        toolrepo, target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
        responses_dir=responses_dir, policy_path=policy_path,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _tampering_scenario(tmp_path: Path, _clean_readiness: dict, *, tamper) -> None:
    toolrepo = tmp_path / "hostile_toolrepo"
    _clone_toolrepo(toolrepo)
    tamper(toolrepo)

    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    result = _run_cli_from(
        toolrepo, target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
        responses_dir=responses_dir, policy_path=policy_path,
    )
    assert result.returncode == 0, result.stderr
    hostile_readiness = json.loads(result.stdout)

    assert hostile_readiness["identity"]["manifest_hash"] == _clean_readiness["identity"]["manifest_hash"]
    assert hostile_readiness["state"] == _clean_readiness["state"]
    assert hostile_readiness["identity"]["toolrepo_sha"] == _clean_readiness["identity"]["toolrepo_sha"], (
        "toolrepo_sha must reflect this clone's own committed HEAD (identical commit history to "
        "the clean clone), never a value derived from the tampering below"
    )


def test_tampered_tracked_file_with_assume_unchanged(tmp_path: Path, _clean_readiness):
    def tamper(toolrepo: Path) -> None:
        target = toolrepo / "app" / "agent_review" / "operational_run_v2.py"
        original = target.read_text(encoding="utf-8")
        target.write_text(original + "\nraise SystemExit('TAMPERED_MODULE_EXECUTED')\n", encoding="utf-8")
        subprocess.run(
            ["git", "update-index", "--assume-unchanged", "app/agent_review/operational_run_v2.py"],
            cwd=toolrepo, check=True,
        )
    _tampering_scenario(tmp_path, _clean_readiness, tamper=tamper)


def test_root_level_shadow_module(tmp_path: Path, _clean_readiness):
    def tamper(toolrepo: Path) -> None:
        (toolrepo / "pydantic.py").write_text(
            "raise SystemExit('ROOT_SHADOW_EXECUTED')\n", encoding="utf-8"
        )
    _tampering_scenario(tmp_path, _clean_readiness, tamper=tamper)


def test_scripts_directory_shadow_module(tmp_path: Path, _clean_readiness):
    def tamper(toolrepo: Path) -> None:
        (toolrepo / "scripts" / "argparse.py").write_text(
            "raise SystemExit('SCRIPTS_SHADOW_EXECUTED')\n", encoding="utf-8"
        )
    _tampering_scenario(tmp_path, _clean_readiness, tamper=tamper)


def test_untracked_project_python(tmp_path: Path, _clean_readiness):
    def tamper(toolrepo: Path) -> None:
        (toolrepo / "app" / "agent_review" / "_stray_evil.py").write_text(
            "raise SystemExit('UNTRACKED_STRAY_EXECUTED')\n", encoding="utf-8"
        )
    _tampering_scenario(tmp_path, _clean_readiness, tamper=tamper)


def test_malicious_pyc_shadow(tmp_path: Path, _clean_readiness):
    def tamper(toolrepo: Path) -> None:
        pycache = toolrepo / "app" / "agent_review" / "__pycache__"
        pycache.mkdir(exist_ok=True)
        (pycache / "operational_run_v2.cpython-311.pyc").write_bytes(b"not-a-real-pyc-but-present")
    _tampering_scenario(tmp_path, _clean_readiness, tamper=tamper)


def test_hostile_pythonpath_in_calling_shell(tmp_path: Path, _clean_readiness):
    """The tampering vector here is the CALLER's own PYTHONPATH, not the
    toolrepo checkout content -- the clean toolrepo clone is used, but run
    with a hostile PYTHONPATH env, complementing (not duplicating)
    test_product_level_hostile_outer_environment_has_zero_semantic_effect
    by planting a REAL module at the PYTHONPATH location, not merely an
    unresolvable path."""
    import os

    toolrepo = tmp_path / "clean_toolrepo_2"
    _clone_toolrepo(toolrepo)
    canary_dir = tmp_path / "pythonpath-injected"
    canary_dir.mkdir()
    (canary_dir / "operational_run_v2.py").write_text(
        "raise SystemExit('PYTHONPATH_SHADOW_EXECUTED')\n", encoding="utf-8"
    )

    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    hostile_env = dict(os.environ)
    hostile_env["PYTHONPATH"] = str(canary_dir)
    cli_script = toolrepo / "scripts" / "aiops-review-run-v2.py"
    result = subprocess.run(
        [
            sys.executable, str(cli_script),
            "--target-root", str(target_repo), "--base-sha", base_sha, "--head-sha", head_sha,
            "--tested-merge-sha", _TESTED_MERGE_SHA, "--toolchain-digest", _TOOLCHAIN_DIGEST,
            "--repo", "mglpsw/AgentEscala", "--pr-number", "101",
            "--trusted-profile-root", str(FIXTURES_ROOT),
            "--grouping-policy", str(policy_path), "--responses-dir", str(responses_dir),
            "--pr-state", "open", "--event-type", "manual", "--event-action", "manual",
            "--delivery-id", "tampering-pythonpath",
        ],
        capture_output=True, text=True, env=hostile_env,
    )
    assert result.returncode == 0, result.stderr
    hostile_readiness = json.loads(result.stdout)
    assert hostile_readiness["identity"]["manifest_hash"] == _clean_readiness["identity"]["manifest_hash"]
