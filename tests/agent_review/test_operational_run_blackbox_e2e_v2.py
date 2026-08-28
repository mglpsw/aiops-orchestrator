"""`#200-E` Phase 3 -- product-level black-box tests, running the REAL
`scripts/aiops-review-run-v2.py` as a real subprocess (outer bootstrap ->
real inner semantic child), not merely calling `run_operational_review_v2`
in-process. This is what distinguishes these from
`test_operational_run_v2.py`/`test_operational_run_router_v2.py`: those
prove the composer is correct; these prove the composer is actually WIRED
into the product entrypoint the way the architecture claims.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLREPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SCRIPT = TOOLREPO_ROOT / "scripts" / "aiops-review-run-v2.py"
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


def _build_real_target(tmp_path: Path, *, source_line: str = "return 1") -> tuple[Path, str, str]:
    repo = tmp_path / "target_repo"
    _init_repo(repo)
    (repo / "backend" / "scheduling").mkdir(parents=True)
    (repo / "backend" / "scheduling" / "shift_rules.py").write_text(
        f"def compute_shift():\n    {source_line}\n", encoding="utf-8"
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


def _cli_argv(*, target_repo: Path, base_sha: str, head_sha: str, responses_dir: Path, policy_path: Path) -> list[str]:
    return [
        sys.executable, str(CLI_SCRIPT),
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
        "--delivery-id", "blackbox-1",
    ]


def _recursive_snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_offline_product_e2e_via_real_outer_and_inner_process(tmp_path: Path):
    """§15: real outer subprocess -> real inner subprocess -> real
    controlled target subject -> real diff/manifest/payload/content ->
    offline transport -> stdout JSON, exit 0. No authoritative checks
    submitted, so the honest terminal readiness is non-ready -- never
    fabricated to `ready`."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    readiness = json.loads(result.stdout)
    assert readiness["schema_id"] == "agent-review.review-readiness.v2"
    assert readiness["evaluated_head_sha"] == head_sha
    assert readiness["state"] != "ready"
    assert readiness["identity"]["toolrepo_sha"] != "0" * 40  # a real toolrepo HEAD, not a placeholder


def test_source_origin_diagnostic_proves_subject_not_devrepo(tmp_path: Path):
    """§5: the inner semantic child's own loaded modules must report
    __file__ origins inside the materialized subject, never this
    toolrepo's own mutable checkout."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    argv = _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                      responses_dir=responses_dir, policy_path=policy_path)
    # Same outer-bootstrap path, but ask the OUTER process to forward a
    # diagnostic-only inner invocation instead of a real review. Simplest
    # correct way: invoke the outer CLI normally; it always forwards
    # unrecognized-by-outer flags straight through to inner, and
    # --_diagnose-source-origin is inner-only, so outer parses it too
    # (SUPPRESS help, but it's a real argparse flag) without it changing
    # outer's own bootstrap behavior.
    result = subprocess.run(
        [*argv, "--_diagnose-source-origin"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    diagnostic = json.loads(result.stderr.strip().splitlines()[-1])
    for key in ("operational_run_v2_file", "controlled_subject_v2_file", "review_transport_v2_file", "this_script_file"):
        assert str(TOOLREPO_ROOT) not in diagnostic[key], (
            f"{key} loaded from the mutable development checkout, not the materialized subject: "
            f"{diagnostic[key]}"
        )
        assert "agent-review-toolrepo-subject-v2-" in diagnostic[key]


def test_product_level_hostile_outer_environment_has_zero_semantic_effect(tmp_path: Path, monkeypatch):
    """§7's product-level pin of the already-proven Phase-3 finding: run
    the REAL outer CLI, with the REAL inner semantic child, exercising the
    REAL unmodified acquire_diff_v2, from a shell carrying hostile ambient
    GIT_* and PYTHONPATH state -- must have zero effect on the result."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    clean_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert clean_result.returncode == 0, clean_result.stderr
    clean_readiness = json.loads(clean_result.stdout)

    hostile_env = dict(os.environ)
    hostile_env["GIT_DIR"] = "/nonexistent/attacker/repo"
    hostile_env["GIT_WORK_TREE"] = "/nonexistent/attacker/worktree"
    hostile_env["GIT_OBJECT_DIRECTORY"] = "/nonexistent/attacker/objects"
    hostile_env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = "/nonexistent/attacker/alt"
    hostile_env["GIT_CONFIG_PARAMETERS"] = "'core.hooksPath=/tmp/should-not-matter'"
    hostile_env["PYTHONPATH"] = "/nonexistent/attacker/pypath"

    hostile_result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True, env=hostile_env,
    )
    assert hostile_result.returncode == 0, hostile_result.stderr
    hostile_readiness = json.loads(hostile_result.stdout)

    assert hostile_readiness["identity"]["manifest_hash"] == clean_readiness["identity"]["manifest_hash"]
    assert hostile_readiness["state"] == clean_readiness["state"]


def test_product_level_target_nonmutation_recursive_oracle(tmp_path: Path):
    """§17: recursive before/after snapshot (worktree + .git + ignored +
    untracked) around the REAL product subprocess."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    before = _recursive_snapshot(target_repo)
    result = subprocess.run(
        _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                   responses_dir=responses_dir, policy_path=policy_path),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    after = _recursive_snapshot(target_repo)
    assert before == after


def test_product_level_target_readonly_fixture_still_succeeds(tmp_path: Path):
    """§17: a successful product run must not REQUIRE a target write --
    proven by making the target worktree and .git read-only (where the
    host permits) and confirming the CLI still succeeds."""
    target_repo, base_sha, head_sha = _build_real_target(tmp_path)
    responses_dir = tmp_path / "responses"
    responses_dir.mkdir()
    policy_path = tmp_path / "policy.json"
    _write_grouping_policy(policy_path)

    def _chmod_tree_readonly(root: Path) -> None:
        for p in root.rglob("*"):
            if p.is_file():
                p.chmod(0o444)
            elif p.is_dir():
                p.chmod(0o555)
        root.chmod(0o555)

    try:
        _chmod_tree_readonly(target_repo)
        result = subprocess.run(
            _cli_argv(target_repo=target_repo, base_sha=base_sha, head_sha=head_sha,
                       responses_dir=responses_dir, policy_path=policy_path),
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        # restore permissions so tmp_path cleanup can remove the tree
        for p in target_repo.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        target_repo.chmod(0o755)
