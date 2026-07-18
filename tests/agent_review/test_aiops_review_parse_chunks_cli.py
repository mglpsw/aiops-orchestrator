from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-parse-chunks.py"
FIXTURE_SECRET = "AGENTESCALA_PHASE3_CLI_FIXTURE_SECRET"


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


def _chunk(
    *,
    chunk_id: str = "chunk-01-primary_backend_logic",
    semantic_group: str = "primary_backend_logic",
    files: list[str] | None = None,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "semantic_group": semantic_group,
        "order_index": 0,
        "files": files if files is not None else ["backend/services/schedule.py"],
        "artifacts": ["artifact:file-diff-context", "artifact:checks"],
        "contracts": ["target_profile:domain_contracts"],
        "depends_on": [],
        "coverage": "complete",
        "prompt_budget_chars": 24_000,
        "estimated_chars": 512,
        "limitations": [],
    }


def _plan(chunks: list[dict[str, object]] | None = None, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.semantic-chunk-plan.v1",
        "source": "aiops-semantic-chunk-planner",
        "target_repo": "mglpsw/AgentEscala",
        "max_parallel_blocks": 6,
        "chunks": chunks if chunks is not None else [_chunk()],
        "files_covered": ["backend/services/schedule.py"],
        "files_partially_covered": [],
        "files_not_covered": [],
        "limitations": [],
        "status": "complete",
        "created_at": "2026-05-29T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _response(chunk: dict[str, object], *, file_path: str = "backend/services/schedule.py") -> dict[str, object]:
    return {
        "schema_version": 1,
        "chunk_id": chunk["chunk_id"],
        "semantic_group": chunk["semantic_group"],
        "confirmed_findings": [
            {
                "severity": "P2",
                "title": "Schedule validation skips inactive doctor guard",
                "file_path": file_path,
                "line_or_hunk": "L42-L48",
                "evidence": f"token={FIXTURE_SECRET} appears in sanitized fixture evidence.",
                "source_artifact": "artifact:file-diff-context",
                "contract_id": "doctor-schedule-active",
                "impact": "Inactive doctors could be scheduled.",
                "confidence": "high",
            }
        ],
        "risks": [],
        "limitations": [],
        "coverage_notes": {"files_reviewed": [file_path], "files_partial": [], "files_not_reviewed": []},
    }


def _write_plan(tmp_path: Path, plan: dict[str, object] | None = None) -> Path:
    path = tmp_path / "semantic-chunk-plan.json"
    path.write_text(json.dumps(plan if plan is not None else _plan()), encoding="utf-8")
    return path


def _write_response(responses_dir: Path, chunk: dict[str, object], response: dict[str, object] | None = None) -> Path:
    path = responses_dir / f"{chunk['chunk_id']}.json"
    path.write_text(json.dumps(response if response is not None else _response(chunk)), encoding="utf-8")
    return path


def _write_intake(tmp_path: Path, *, target_root: Path | None = None) -> Path:
    payload: dict[str, object] = {
        "schema_version": "agent-review.intake.v1",
        "target_repo": "mglpsw/AgentEscala",
        "target_profile": {},
        "artifacts": {},
        "artifact_status": [],
        "status": "complete",
        "limitations": [],
    }
    if target_root:
        payload["target_profile"] = {"repo_root": str(target_root)}
    path = tmp_path / "aiops-intake.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_cli(
    chunk_plan: Path,
    responses_dir: Path,
    output: Path,
    *,
    env: dict[str, str],
    intake: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--chunk-plan",
        str(chunk_plan),
        "--responses-dir",
        str(responses_dir),
        "--output",
        str(output),
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


def test_parse_chunks_cli_generates_chunk_results_in_dev_toolrepo(tmp_path: Path) -> None:
    chunk = _chunk()
    chunk_plan = _write_plan(tmp_path, _plan([chunk]))
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    _write_response(responses_dir, chunk)
    output = tmp_path / "chunk-results.json"

    result = _run_cli(chunk_plan, responses_dir, output, env=_dev_env())

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"ok": True, "output_written": True, "status": "complete"}
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "agent-review.chunk-results.v1"
    assert payload["status"] == "complete"
    assert payload["chunk_plan_ref"]["chunk_count"] == 1
    assert "semantic-chunk-plan.json" not in json.dumps(payload)
    assert FIXTURE_SECRET not in output.read_text(encoding="utf-8")


def test_parse_chunks_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    chunk = _chunk()
    chunk_plan = _write_plan(tmp_path, _plan([chunk]))
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    _write_response(responses_dir, chunk)
    output = tmp_path / "chunk-results.json"

    result = _run_cli(chunk_plan, responses_dir, output, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output.exists()


def test_parse_chunks_cli_rejects_output_inside_known_target_repo(tmp_path: Path) -> None:
    target_repo = tmp_path / "AgentEscala"
    target_repo.mkdir()
    chunk = _chunk()
    chunk_plan = _write_plan(tmp_path, _plan([chunk]))
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    _write_response(responses_dir, chunk)
    intake = _write_intake(tmp_path, target_root=target_repo)
    output = target_repo / "chunk-results.json"

    result = _run_cli(chunk_plan, responses_dir, output, env=_dev_env(), intake=intake)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "target_repo_write_blocked"
    assert "target repo must not be modified" in payload["message"]
    assert not output.exists()


def test_parse_chunks_cli_rejects_response_path_escape(tmp_path: Path) -> None:
    chunk = _chunk(chunk_id="../escape")
    chunk_plan = _write_plan(tmp_path, _plan([chunk]))
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    (tmp_path / "escape.json").write_text(json.dumps(_response(chunk)), encoding="utf-8")
    output = tmp_path / "chunk-results.json"

    result = _run_cli(chunk_plan, responses_dir, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "chunk_plan_chunk_id_invalid"
    assert "../escape" not in result.stdout
    assert not output.exists()
    assert (tmp_path / "escape.json").exists()


def test_parse_chunks_cli_does_not_call_network_or_provider(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setenv("AIOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("AIOPS_NODE_ROLE", "toolrepo")
    monkeypatch.setenv("AIOPS_REPO_MODE", "agent_review_tooling")
    monkeypatch.setenv("AIOPS_PRODUCTION_RUNTIME", "false")
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    chunk = _chunk()
    chunk_plan = _write_plan(tmp_path, _plan([chunk]))
    responses_dir = tmp_path / "chunk-responses"
    responses_dir.mkdir()
    _write_response(responses_dir, chunk)
    output = tmp_path / "chunk-results.json"
    module = _load_script_module()

    assert module.main(["--chunk-plan", str(chunk_plan), "--responses-dir", str(responses_dir), "--output", str(output)]) == 0
    assert output.exists()


def _load_script_module():
    spec = importlib.util.spec_from_file_location("aiops_review_parse_chunks", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
