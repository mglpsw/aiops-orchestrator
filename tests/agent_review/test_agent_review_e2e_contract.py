from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
FIXTURE_ROOT = ROOT / "agent_review" / "fixtures" / "agentescala_e2e"
SCRIPTS = REPO_ROOT / "scripts"
FIXTURE_SECRET = "AGENTESCALA_PHASE05_E2E_SECRET"


def _dev_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "dev",
            "AIOPS_NODE_ROLE": "toolrepo",
            "AIOPS_REPO_MODE": "agent_review_tooling",
            "AIOPS_PRODUCTION_RUNTIME": "false",
        }
    )
    return env


def _run_cli(args: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def _copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    target_repo = tmp_path / "AgentEscala"
    shutil.copytree(FIXTURE_ROOT, target_repo)
    agent_dir = target_repo / "artifacts"
    return target_repo, agent_dir


def _write_fake_chunk_responses(chunk_plan: Path, responses_dir: Path) -> None:
    plan = json.loads(chunk_plan.read_text(encoding="utf-8"))
    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in plan["chunks"]:
        files = list(chunk.get("files", []))
        payload: dict[str, Any] = {
            "schema_version": 1,
            "chunk_id": chunk["chunk_id"],
            "semantic_group": chunk["semantic_group"],
            "confirmed_findings": [],
            "risks": [],
            "limitations": [
                {
                    "type": "offline_contract_fixture",
                    "detail": "fake_chunk_response",
                }
            ],
            "coverage_notes": {
                "files_reviewed": files,
                "files_partial": [],
                "files_not_reviewed": [],
            },
        }
        (responses_dir / f"{chunk['chunk_id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def test_agentescala_tool_repo_e2e_contract_runs_offline(tmp_path: Path) -> None:
    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "out"
    responses_dir = out_dir / "chunk-responses"
    out_dir.mkdir()
    env = _dev_env()

    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    chunk_results = out_dir / "chunk-results.json"
    final_review_json = out_dir / "final-review.json"
    final_review_md = out_dir / "final-review.md"

    intake_result = _run_cli(
        [
            str(SCRIPTS / "aiops-review-intake.py"),
            "--target-repo",
            "mglpsw/AgentEscala",
            "--repo-root",
            str(target_repo),
            "--agent-dir",
            str(agent_dir),
            "--output",
            str(intake),
            "--redaction-report",
            str(redaction_report),
        ],
        env=env,
    )
    assert intake_result.returncode == 0, intake_result.stderr + intake_result.stdout

    plan_result = _run_cli(
        [
            str(SCRIPTS / "aiops-review-plan-chunks.py"),
            "--intake",
            str(intake),
            "--output",
            str(chunk_plan),
            "--max-blocks",
            "6",
        ],
        env=env,
    )
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout

    _write_fake_chunk_responses(chunk_plan, responses_dir)

    parse_result = _run_cli(
        [
            str(SCRIPTS / "aiops-review-parse-chunks.py"),
            "--chunk-plan",
            str(chunk_plan),
            "--responses-dir",
            str(responses_dir),
            "--intake",
            str(intake),
            "--output",
            str(chunk_results),
        ],
        env=env,
    )
    assert parse_result.returncode == 0, parse_result.stderr + parse_result.stdout

    synthesize_result = _run_cli(
        [
            str(SCRIPTS / "aiops-review-synthesize.py"),
            "--chunk-results",
            str(chunk_results),
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--output-json",
            str(final_review_json),
            "--output-md",
            str(final_review_md),
        ],
        env=env,
    )
    assert synthesize_result.returncode == 0, synthesize_result.stderr + synthesize_result.stdout

    for output in (
        intake,
        redaction_report,
        chunk_plan,
        chunk_results,
        final_review_json,
        final_review_md,
    ):
        assert output.exists(), f"{output.name} was not generated"
        assert output.stat().st_size > 0, f"{output.name} is empty"

    final_markdown = final_review_md.read_text(encoding="utf-8")
    final_payload = json.loads(final_review_json.read_text(encoding="utf-8"))
    results_payload = json.loads(chunk_results.read_text(encoding="utf-8"))

    assert final_payload["schema_id"] == "agent-review.final-review.v1"
    assert final_payload["target_repo"] == "mglpsw/AgentEscala"
    assert results_payload["schema_id"] == "agent-review.chunk-results.v1"
    assert results_payload["chunks_failed"] == []
    assert "# Agent Review" in final_markdown

    forbidden = [
        FIXTURE_SECRET,
        str(tmp_path),
        str(target_repo),
        str(out_dir),
        "Authorization:",
        "Bearer ",
        "Cookie:",
        "raw prompt",
        "raw payload",
        "AGENT_ROUTER_TOKEN",
    ]
    for value in forbidden:
        assert value not in final_markdown

    assert "prompt bruto" not in final_markdown.lower()
    assert "payload bruto" not in final_markdown.lower()
