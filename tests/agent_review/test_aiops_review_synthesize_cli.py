from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-synthesize.py"
FIXTURE_SECRET = "AGENTESCALA_PHASE4_CLI_FIXTURE_SECRET"


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


def _prod_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "prod",
            "AIOPS_NODE_ROLE": "runtime",
            "AIOPS_REPO_MODE": "aiops_runtime",
            "AIOPS_PRODUCTION_RUNTIME": "true",
        }
    )
    return env


def _chunk_results(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.chunk-results.v1",
        "source": "aiops-review-parse-chunks",
        "target_repo": "mglpsw/AgentEscala",
        "chunk_plan_ref": {"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        "chunks_parsed": ["chunk-01-primary_backend_logic"],
        "chunks_failed": [],
        "confirmed_findings": [
            {
                "chunk_id": "chunk-01-primary_backend_logic",
                "semantic_group": "primary_backend_logic",
                "severity": "P2",
                "title": "Schedule validation skips inactive doctor guard",
                "file_path": "backend/services/schedule.py",
                "line_or_hunk": "L42-L48",
                "evidence": f"token={FIXTURE_SECRET} appears in sanitized fixture evidence.",
                "source_artifact": "artifact:file-diff-context",
                "contract_id": "doctor-schedule-active",
                "impact": "Inactive doctors could be scheduled.",
                "confidence": "high",
                "dedupe_key": "schedule-active-doctor",
            }
        ],
        "risks": [],
        "limitations": [],
        "rejected_findings": [],
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
        "status": "complete",
        "created_at": "2026-05-30T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _intake(*, target_root: Path | None = None) -> dict[str, object]:
    target_profile: dict[str, object] = {}
    if target_root is not None:
        target_profile["repo_root"] = str(target_root)
    return {
        "schema_version": "agent-review.intake.v1",
        "source": "aiops-review-intake",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": target_profile,
        "artifacts": {},
        "artifact_status": [],
        "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
        "limitations": [],
        "completeness": {},
        "status": "complete",
        "created_at": "2026-05-30T00:00:00Z",
    }


def _write_json(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_cli(
    chunk_results: Path,
    output_json: Path,
    output_md: Path,
    *,
    env: dict[str, str],
    intake: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--chunk-results",
        str(chunk_results),
        "--output-json",
        str(output_json),
        "--output-md",
        str(output_md),
    ]
    if intake:
        args.extend(["--intake", str(intake)])
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def test_synthesize_cli_generates_final_review_outputs_in_dev_toolrepo(tmp_path: Path) -> None:
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output_json = tmp_path / "final-review.json"
    output_md = tmp_path / "final-review.md"

    result = _run_cli(chunk_results, output_json, output_md, env=_dev_env())

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "outputs_written": True,
        "status": "complete",
        "verdict": "approve_with_required_followup",
    }
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    markdown = output_md.read_text(encoding="utf-8")
    assert payload["schema_id"] == "agent-review.final-review.v1"
    assert payload["verdict"] == "approve_with_required_followup"
    assert "# Agent Review" in markdown
    assert FIXTURE_SECRET not in output_json.read_text(encoding="utf-8") + markdown


def test_synthesize_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output_json = tmp_path / "final-review.json"
    output_md = tmp_path / "final-review.md"

    result = _run_cli(chunk_results, output_json, output_md, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output_json.exists()
    assert not output_md.exists()


def test_synthesize_cli_rejects_output_inside_known_target_repo(tmp_path: Path) -> None:
    target_repo = tmp_path / "AgentEscala"
    target_repo.mkdir()
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    intake = _write_json(tmp_path, "aiops-intake.json", _intake(target_root=target_repo))
    output_json = target_repo / "final-review.json"
    output_md = tmp_path / "final-review.md"

    result = _run_cli(chunk_results, output_json, output_md, env=_dev_env(), intake=intake)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "target_repo_write_blocked"
    assert "target repo must not be modified" in payload["message"]
    assert not output_json.exists()
    assert not output_md.exists()


def test_synthesize_cli_rejects_equal_outputs(tmp_path: Path) -> None:
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "final-review"

    result = _run_cli(chunk_results, output, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "outputs_not_distinct"
    assert not output.exists()


def test_synthesize_cli_rejects_invalid_chunk_results_without_outputs(tmp_path: Path) -> None:
    chunk_results = _write_json(tmp_path, "chunk-results.json", {"schema_id": "wrong", "schema_version": 1})
    output_json = tmp_path / "final-review.json"
    output_md = tmp_path / "final-review.md"

    result = _run_cli(chunk_results, output_json, output_md, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "chunk_results_invalid"
    assert not output_json.exists()
    assert not output_md.exists()


def test_synthesize_cli_does_not_call_network_or_provider(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setenv("AIOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("AIOPS_NODE_ROLE", "toolrepo")
    monkeypatch.setenv("AIOPS_REPO_MODE", "agent_review_tooling")
    monkeypatch.setenv("AIOPS_PRODUCTION_RUNTIME", "false")
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output_json = tmp_path / "final-review.json"
    output_md = tmp_path / "final-review.md"
    module = _load_script_module()

    assert module.main(
        [
            "--chunk-results",
            str(chunk_results),
            "--output-json",
            str(output_json),
            "--output-md",
            str(output_md),
        ]
    ) == 0
    assert output_json.exists()
    assert output_md.exists()


def _load_script_module():
    spec = importlib.util.spec_from_file_location("aiops_review_synthesize", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
