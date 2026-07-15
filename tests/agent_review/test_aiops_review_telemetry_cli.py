from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-telemetry.py"
TELEMETRY_MODULE = ROOT / "app" / "agent_review" / "telemetry.py"


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
            "expected_files": ["backend/services/schedule.py"],
            "missing_expected_files": [],
            "extra_reported_files": [],
            "comparison_available": True,
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


def _quality_gate(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "schema_id": "agent-review.quality-gate.v1",
        "source": "aiops-review-quality-gate",
        "status": "passed",
        "normalized_verdict": "approved",
        "quality_score": 1.0,
        "manual_review_required": False,
        "second_opinion_requested": False,
        "second_opinion_status": "not_required",
        "blocked_reasons": [],
        "warnings": [],
        "limitations": [],
        "inputs": {},
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
    quality_gate: Path,
    output: Path,
    *,
    env: dict[str, str],
    intake: Path | None = None,
    chunk_results: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--final-review",
        str(final_review),
        "--quality-gate",
        str(quality_gate),
        "--output",
        str(output),
        "--pr-number",
        "61",
        "--commit-sha",
        "abc123",
    ]
    if intake:
        args.extend(["--intake", str(intake)])
    if chunk_results:
        args.extend(["--chunk-results", str(chunk_results)])
    return subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        shell=False,
        check=False,
    )


def test_telemetry_cli_generates_output_in_dev_toolrepo(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env())

    assert result.returncode == 0
    stdout = json.loads(result.stdout)
    assert stdout == {
        "ok": True,
        "manual_review_required": False,
        "normalized_verdict": "approved",
        "output_written": True,
        "status": "partial",
    }
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "agent-review.telemetry.v1"
    assert payload["quality_gate"]["normalized_verdict"] == "approved"
    assert payload["target"]["pr_number"] == 61
    assert payload["target"]["commit_sha"] == "abc123"
    assert "optional_artifact_missing:chunk_results" in payload["limitations"]


def test_telemetry_cli_fails_closed_on_prod_runtime_env(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_prod_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"]
    assert not output.exists()


def test_telemetry_cli_fails_closed_on_invalid_required_input(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", {"schema_id": "wrong", "schema_version": 1})
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "final_review_invalid"
    assert not output.exists()


def test_telemetry_cli_fails_closed_on_unknown_final_review_verdict(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review(verdict="not_a_valid_verdict"))
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "final_review_invalid"
    assert not output.exists()


def test_telemetry_cli_fails_closed_on_invalid_required_json(tmp_path: Path) -> None:
    final_review = tmp_path / "final-review.json"
    final_review.write_text("{not-json", encoding="utf-8")
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env())

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "final_review_invalid"
    assert "invalid" in payload["message"]
    assert not output.exists()


def test_telemetry_cli_rejects_output_inside_known_target_repo(tmp_path: Path) -> None:
    target_repo = tmp_path / "AgentEscala"
    target_repo.mkdir()
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    intake = _write_json(tmp_path, "aiops-intake.json", _intake(target_root=target_repo))
    output = target_repo / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env(), intake=intake)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "target_repo_write_blocked"
    assert "target repo must not be modified" in payload["message"]
    assert not output.exists()


def test_telemetry_cli_ignores_optional_artifact_with_incompatible_schema_version(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    chunk_results = _write_json(
        tmp_path,
        "chunk-results.json",
        {"schema_id": "agent-review.chunk-results.v1", "schema_version": 2, "chunks_parsed": ["chunk-01"]},
    )
    output = tmp_path / "review-telemetry.json"

    result = _run_cli(final_review, quality_gate, output, env=_dev_env(), chunk_results=chunk_results)

    assert result.returncode == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "artifact_schema_version_mismatch:chunk_results" in payload["limitations"]
    assert payload["pipeline"]["chunk_results_status"] is None


def test_telemetry_cli_output_is_deterministic(tmp_path: Path) -> None:
    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"

    first = _run_cli(final_review, quality_gate, output, env=_dev_env())
    first_payload = output.read_text(encoding="utf-8")
    second = _run_cli(final_review, quality_gate, output, env=_dev_env())
    second_payload = output.read_text(encoding="utf-8")

    assert first.returncode == 0
    assert second.returncode == 0
    assert first_payload == second_payload


def test_telemetry_cli_does_not_call_network_or_provider(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    monkeypatch.setenv("AIOPS_ENVIRONMENT", "dev")
    monkeypatch.setenv("AIOPS_NODE_ROLE", "toolrepo")
    monkeypatch.setenv("AIOPS_REPO_MODE", "agent_review_tooling")
    monkeypatch.setenv("AIOPS_PRODUCTION_RUNTIME", "false")
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)

    final_review = _write_json(tmp_path, "final-review.json", _final_review())
    quality_gate = _write_json(tmp_path, "review-quality-gate.json", _quality_gate())
    output = tmp_path / "review-telemetry.json"
    module = _load_script_module()

    assert module.main(
        [
            "--final-review",
            str(final_review),
            "--quality-gate",
            str(quality_gate),
            "--output",
            str(output),
        ]
    ) == 0
    assert output.exists()


def test_new_active_paths_do_not_contain_prohibited_router_or_provider_calls() -> None:
    active_text = SCRIPT.read_text(encoding="utf-8") + "\n" + TELEMETRY_MODULE.read_text(encoding="utf-8")

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
    spec = importlib.util.spec_from_file_location("aiops_review_telemetry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
