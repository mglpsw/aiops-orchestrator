from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-quality-gate.py"
QUALITY_GATE_MODULE = ROOT / "app" / "agent_review" / "quality_gate.py"
FIXTURE_SECRET = "AGENTESCALA_PHASE5A_CLI_SECRET"


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


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-primary_backend_logic",
        "semantic_group": "primary_backend_logic",
        "severity": "P2",
        "title": "Schedule validation needs follow-up",
        "file_path": "backend/services/schedule.py",
        "line_or_hunk": "L42-L48",
        "evidence": f"token={FIXTURE_SECRET} appears in fixture evidence.",
        "source_artifact": "artifact:file-diff-context",
        "impact": "Inactive doctors could be scheduled.",
        "confidence": "high",
        "source_chunks": ["chunk-01-primary_backend_logic"],
        "semantic_groups": ["primary_backend_logic"],
    }
    payload.update(overrides)
    return payload


def _final_review(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.final-review.v1",
        "source": "aiops-review-synthesize",
        "target_repo": "mglpsw/AgentEscala",
        "status": "complete",
        "verdict": "approved",
        "summary": "Synthetic final review fixture.",
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "rejected_summary": {"total": 0, "by_reason": {}, "sample_titles": []},
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
            "expected_files": [],
            "missing_expected_files": [],
            "extra_reported_files": [],
            "comparison_available": False,
        },
        "counts": {
            "confirmed_findings_total": 0,
            "findings_by_severity": {},
            "risks_total": 0,
            "risks_by_source": {},
            "rejected_findings_total": 0,
            "rejected_findings_by_reason": {},
            "limitations_total": 0,
            "chunks_parsed": 1,
            "chunks_failed": 0,
        },
        "inputs": {},
        "created_at": "2026-06-02T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _chunk_results(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.chunk-results.v1",
        "source": "aiops-review-parse-chunks",
        "target_repo": "mglpsw/AgentEscala",
        "chunk_plan_ref": {"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
        "chunks_parsed": ["chunk-01-primary_backend_logic"],
        "chunks_failed": [],
        "confirmed_findings": [],
        "risks": [],
        "limitations": [],
        "rejected_findings": [],
        "coverage": {
            "files_reviewed": ["backend/services/schedule.py"],
            "files_partial": [],
            "files_not_reviewed": [],
        },
        "status": "complete",
        "created_at": "2026-06-02T00:00:00Z",
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
        "created_at": "2026-06-02T00:00:00Z",
    }


def _write_json(tmp_path: Path, name: str, payload: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


def _run_cli(
    final_review: Path,
    chunk_results: Path,
    output: Path,
    *,
    env: dict[str, str],
    intake: Path | None = None,
    checks: Path | None = None,
    critical_pr: bool = False,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--final-review",
        str(final_review),
        "--chunk-results",
        str(chunk_results),
        "--output",
        str(output),
    ]
    if intake:
        args.extend(["--intake", str(intake)])
    if checks:
        args.extend(["--checks", str(checks)])
    if critical_pr:
        args.append("--critical-pr")
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def test_quality_gate_cli_generates_output_in_dev_toolrepo(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review(confirmed_findings=[_finding()]))
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_dev_env())

    assert result.returncode == 0
    stdout = json.loads(result.stdout)
    assert stdout == {
        "ok": True,
        "manual_review_required": False,
        "normalized_verdict": "approve_with_required_followup",
        "output_written": True,
        "status": "passed",
    }
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "agent-review.quality-gate.v1"
    assert payload["inputs"]["intake"]["provided"] is False
    assert payload["inputs"]["checks"]["provided"] is False
    assert FIXTURE_SECRET not in output.read_text(encoding="utf-8")


def test_quality_gate_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output.exists()


def test_quality_gate_cli_fails_closed_on_invalid_required_input(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", {"schema_id": "wrong", "schema_version": 1})
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "final_review_invalid"
    assert not output.exists()


def test_quality_gate_cli_fails_closed_on_invalid_provided_optional_input(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    checks = tmp_path / "checks.json"
    checks.write_text("[", encoding="utf-8")
    output = tmp_path / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_dev_env(), checks=checks)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "checks_invalid"
    assert not output.exists()


def test_quality_gate_cli_rejects_output_inside_known_target_repo(tmp_path: Path) -> None:
    target_repo = tmp_path / "AgentEscala"
    target_repo.mkdir()
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    intake = _write_json(tmp_path, "aiops-intake.json", _intake(target_root=target_repo))
    output = target_repo / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_dev_env(), intake=intake)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "target_repo_write_blocked"
    assert "target repo must not be modified" in payload["message"]
    assert not output.exists()


def test_quality_gate_cli_rejects_output_equal_to_any_input(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())

    result = _run_cli(final_review, chunk_results, final_review, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "output_overwrites_input"


def test_quality_gate_cli_unknown_verdict_writes_failed_gate(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review(verdict="unexpected"))
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"

    result = _run_cli(final_review, chunk_results, output, env=_dev_env())

    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is False
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["normalized_verdict"] == "review_unavailable"
    assert payload["manual_review_required"] is True
    assert "final_review_verdict_unknown" in payload["limitations"]


def test_quality_gate_cli_output_is_deterministic(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"

    first = _run_cli(final_review, chunk_results, output, env=_dev_env())
    first_payload = output.read_text(encoding="utf-8")
    second = _run_cli(final_review, chunk_results, output, env=_dev_env())
    second_payload = output.read_text(encoding="utf-8")

    assert first.returncode == 0
    assert second.returncode == 0
    assert first_payload == second_payload


def test_quality_gate_cli_does_not_call_network_or_provider(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setenv("AIOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("AIOPS_NODE_ROLE", "toolrepo")
    monkeypatch.setenv("AIOPS_REPO_MODE", "agent_review_tooling")
    monkeypatch.setenv("AIOPS_PRODUCTION_RUNTIME", "false")
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    chunk_results = _write_json(tmp_path, "chunk-results.json", _chunk_results())
    output = tmp_path / "review-quality-gate.json"
    module = _load_script_module()

    assert module.main(
        [
            "--final-review",
            str(final_review),
            "--chunk-results",
            str(chunk_results),
            "--output",
            str(output),
        ]
    ) == 0
    assert output.exists()


def test_new_active_paths_do_not_contain_prohibited_router_or_provider_calls() -> None:
    active_text = SCRIPT.read_text(encoding="utf-8") + "\n" + QUALITY_GATE_MODULE.read_text(encoding="utf-8")

    forbidden = [
        "/v1/chat/ingest",
        "/v1/chat/completions",
        "Agent Router",
        "provider direct",
        "requests.",
        "urllib.request",
    ]
    for value in forbidden:
        assert value not in active_text


def _load_script_module():
    spec = importlib.util.spec_from_file_location("aiops_review_quality_gate", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

