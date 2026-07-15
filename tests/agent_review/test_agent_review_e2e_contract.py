from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import get_args

import pytest

from app.agent_review.schemas import FinalReviewVerdict, ReviewQualityGateStatus


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


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str
    stderr: str


def _run_cli(script: Path, args: list[str]) -> CliResult:
    module_name = f"agent_review_e2e_{script.stem.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            returncode = module.main(args)
        except SystemExit as exc:
            returncode = int(exc.code or 0)
    return CliResult(returncode=returncode, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def _git_status_snapshot() -> str:
    result = subprocess.run(
        ["git", "--no-pager", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def _copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    target_repo = tmp_path / "AgentEscala"
    shutil.copytree(FIXTURE_ROOT, target_repo)
    agent_dir = target_repo / "artifacts"
    return target_repo, agent_dir


def _file_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


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


def _assert_under(path: Path, root: Path) -> None:
    assert path.resolve().is_relative_to(root.resolve()), f"{path} must be written under {root}"


def _assert_not_under(path: Path, root: Path) -> None:
    assert not path.resolve().is_relative_to(root.resolve()), f"{path} must not be written under {root}"


def _install_offline_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline E2E contract attempted network access")

    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket, "getaddrinfo", fail_network)
    monkeypatch.setattr(socket, "gethostbyname", fail_network)
    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    for key in (
        "AGENT_ROUTER_URL",
        "AGENT_ROUTER_TOKEN",
        "OPENAI_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OLLAMA_HOST",
        "AIOPS_CT102_HOST",
    ):
        monkeypatch.delenv(key, raising=False)


def test_agentescala_tool_repo_e2e_contract_runs_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_offline_guards(monkeypatch)
    working_tree_before = _git_status_snapshot()
    target_repo, agent_dir = _copy_fixture(tmp_path)
    target_snapshot_before = _file_snapshot(target_repo)
    out_dir = tmp_path / "agent"
    responses_dir = out_dir / "chunk-responses"
    out_dir.mkdir()

    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    chunk_results = out_dir / "chunk-results.json"
    final_review_json = out_dir / "final-review.json"
    final_review_md = out_dir / "final-review.md"
    quality_gate = out_dir / "review-quality-gate.json"

    intake_result = _run_cli(
        SCRIPTS / "aiops-review-intake.py",
        [
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
    )
    assert intake_result.returncode == 0, intake_result.stderr + intake_result.stdout

    plan_result = _run_cli(
        SCRIPTS / "aiops-review-plan-chunks.py",
        [
            "--intake",
            str(intake),
            "--output",
            str(chunk_plan),
            "--max-blocks",
            "6",
        ],
    )
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout

    _write_fake_chunk_responses(chunk_plan, responses_dir)

    parse_result = _run_cli(
        SCRIPTS / "aiops-review-parse-chunks.py",
        [
            "--chunk-plan",
            str(chunk_plan),
            "--responses-dir",
            str(responses_dir),
            "--intake",
            str(intake),
            "--output",
            str(chunk_results),
        ],
    )
    assert parse_result.returncode == 0, parse_result.stderr + parse_result.stdout

    synthesize_result = _run_cli(
        SCRIPTS / "aiops-review-synthesize.py",
        [
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
    )
    assert synthesize_result.returncode == 0, synthesize_result.stderr + synthesize_result.stdout

    gate_result = _run_cli(
        SCRIPTS / "aiops-review-quality-gate.py",
        [
            "--final-review",
            str(final_review_json),
            "--chunk-results",
            str(chunk_results),
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--output",
            str(quality_gate),
        ],
    )
    assert gate_result.returncode == 0, gate_result.stderr + gate_result.stdout
    first_gate_payload = quality_gate.read_text(encoding="utf-8")

    deterministic_gate_result = _run_cli(
        SCRIPTS / "aiops-review-quality-gate.py",
        [
            "--final-review",
            str(final_review_json),
            "--chunk-results",
            str(chunk_results),
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--output",
            str(quality_gate),
        ],
    )
    assert deterministic_gate_result.returncode == 0, (
        deterministic_gate_result.stderr + deterministic_gate_result.stdout
    )
    assert quality_gate.read_text(encoding="utf-8") == first_gate_payload

    for output in (
        intake,
        redaction_report,
        chunk_plan,
        chunk_results,
        final_review_json,
        final_review_md,
        quality_gate,
    ):
        assert output.exists(), f"{output.name} was not generated"
        assert output.stat().st_size > 0, f"{output.name} is empty"
        _assert_under(output, out_dir)
        _assert_not_under(output, target_repo)

    final_markdown = final_review_md.read_text(encoding="utf-8")
    final_payload = json.loads(final_review_json.read_text(encoding="utf-8"))
    results_payload = json.loads(chunk_results.read_text(encoding="utf-8"))
    gate_payload = json.loads(first_gate_payload)

    assert final_payload["schema_id"] == "agent-review.final-review.v1"
    assert final_payload["target_repo"] == "mglpsw/AgentEscala"
    assert results_payload["schema_id"] == "agent-review.chunk-results.v1"
    assert results_payload["chunks_failed"] == []
    assert "# Agent Review" in final_markdown
    assert final_markdown.strip()

    expected_gate_keys = {
        "schema_version",
        "schema_id",
        "source",
        "status",
        "normalized_verdict",
        "quality_score",
        "manual_review_required",
        "second_opinion_requested",
        "second_opinion_status",
        "blocked_reasons",
        "warnings",
        "limitations",
        "inputs",
        "created_at",
    }
    assert expected_gate_keys <= gate_payload.keys()
    assert gate_payload["schema_id"] == "agent-review.quality-gate.v1"
    assert gate_payload["schema_version"] == 1
    assert gate_payload["source"] == "aiops-review-quality-gate"
    assert gate_payload["status"] in set(get_args(ReviewQualityGateStatus))
    assert gate_payload["normalized_verdict"] in set(get_args(FinalReviewVerdict))
    assert gate_payload["second_opinion_requested"] is False
    assert gate_payload["second_opinion_status"] == "not_required"
    assert gate_payload["inputs"]["final_review"]["provided"] is True
    assert gate_payload["inputs"]["chunk_results"]["provided"] is True
    assert gate_payload["inputs"]["intake"]["provided"] is True
    assert gate_payload["inputs"]["chunk_plan"]["provided"] is True
    assert gate_payload["inputs"]["redaction_report"]["provided"] is True

    forbidden = [
        FIXTURE_SECRET,
        str(REPO_ROOT),
        str(tmp_path),
        str(target_repo),
        str(out_dir),
        "Authorization:",
        "Bearer ",
        "Cookie:",
        "raw prompt",
        "raw payload",
        "AGENT_ROUTER_TOKEN",
        "/v1/chat/ingest",
        "/v1/chat/completions",
        "OLLAMA_HOST",
        "AIOPS_CT102_HOST",
    ]
    gate_text = json.dumps(gate_payload, ensure_ascii=False, sort_keys=True)
    for value in forbidden:
        assert value not in final_markdown
        assert value not in gate_text

    assert "prompt bruto" not in final_markdown.lower()
    assert "payload bruto" not in final_markdown.lower()
    assert "secret" not in final_markdown.lower()

    target_snapshot_after = _file_snapshot(target_repo)
    assert target_snapshot_after == target_snapshot_before
    target_files = set(target_snapshot_after)
    generated_artifact_names = {
        "aiops-intake.json",
        "redaction-report.json",
        "semantic-chunk-plan.json",
        "chunk-results.json",
        "final-review.json",
        "final-review.md",
        "review-quality-gate.json",
    }
    assert not generated_artifact_names & {Path(path).name for path in target_files}
    assert _git_status_snapshot() == working_tree_before
