from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from typing import Any

import yaml

from app.agent_review.false_positive_signatures import signature_for_basis

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
SCRIPT = REPO_ROOT / "scripts" / "aiops-review-false-positives.py"


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


def _run_cli(args: list[str]) -> tuple[int, str, str]:
    spec = importlib.util.spec_from_file_location("aiops_review_false_positives_cli_test", SCRIPT)
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
    return returncode, stdout.getvalue(), stderr.getvalue()


def _finding(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "chunk_id": "chunk-01-docs_changelog",
        "semantic_group": "docs_changelog",
        "severity": "P1",
        "title": "Docs finding",
        "file_path": "docs/release.md",
        "evidence": "docs only",
        "contract_id": "review.docs-severity",
        "impact": "overseverity",
        "confidence": "high",
        "source_chunks": ["chunk-01-docs_changelog"],
        "semantic_groups": ["docs_changelog"],
    }
    payload.update(overrides)
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _artifacts(tmp_path: Path) -> dict[str, Path]:
    final_review = tmp_path / "final-review.json"
    quality_gate = tmp_path / "review-quality-gate.json"
    telemetry = tmp_path / "review-telemetry.json"
    chunk_results = tmp_path / "chunk-results.json"
    _write_json(
        final_review,
        {
            "schema_version": 1,
            "schema_id": "agent-review.final-review.v1",
            "source": "aiops-review-synthesize",
            "target_repo": "mglpsw/AgentEscala",
            "status": "complete",
            "verdict": "changes_requested",
            "summary": "Synthetic final review fixture.",
            "confirmed_findings": [_finding()],
            "risks": [],
            "limitations": [],
            "rejected_summary": {"total": 0, "by_reason": {}, "sample_titles": []},
            "coverage": {},
            "counts": {"confirmed_findings_total": 1},
            "inputs": {},
            "created_at": "2026-06-02T00:00:00Z",
        },
    )
    _write_json(
        quality_gate,
        {
            "schema_version": 1,
            "schema_id": "agent-review.quality-gate.v1",
            "source": "aiops-review-quality-gate",
            "status": "passed",
            "normalized_verdict": "changes_requested",
            "quality_score": 0.95,
            "manual_review_required": False,
            "created_at": "2026-06-02T00:00:00Z",
        },
    )
    _write_json(
        telemetry,
        {
            "schema_version": 1,
            "schema_id": "agent-review.telemetry.v1",
            "source": "aiops-review-telemetry",
            "status": "complete",
            "target": {"repository": "mglpsw/AgentEscala"},
        },
    )
    _write_json(
        chunk_results,
        {
            "schema_version": 1,
            "schema_id": "agent-review.chunk-results.v1",
            "source": "aiops-review-parse-chunks",
            "target_repo": "mglpsw/AgentEscala",
            "chunk_plan_ref": {"schema_id": "agent-review.semantic-chunk-plan.v1", "schema_version": 1},
            "chunks_parsed": ["chunk-01-docs_changelog"],
            "chunks_failed": [],
            "confirmed_findings": [],
            "risks": [],
            "limitations": [],
            "rejected_findings": [],
            "coverage": {},
            "status": "complete",
            "created_at": "2026-06-02T00:00:00Z",
        },
    )
    return {"final_review": final_review, "quality_gate": quality_gate, "telemetry": telemetry, "chunk_results": chunk_results}


def _base_args(paths: dict[str, Path], output: Path, suggestions: Path | None = None, markers: Path | None = None) -> list[str]:
    args = [
        "--review-telemetry",
        str(paths["telemetry"]),
        "--quality-gate",
        str(paths["quality_gate"]),
        "--final-review",
        str(paths["final_review"]),
        "--chunk-results",
        str(paths["chunk_results"]),
        "--output",
        str(output),
    ]
    if suggestions is not None:
        args.extend(["--suggestions-output", str(suggestions)])
    if markers is not None:
        args.extend(["--markers", str(markers)])
    return args


def test_cli_generates_signatures_without_inventing_suggestions(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out" / "false-positive-signatures.json"
    suggestions = tmp_path / "out" / "suggested-contract-updates.yaml"

    first = _run_cli(_base_args(paths, output, suggestions))
    assert first[0] == 0, first[2] + first[1]
    first_payload = output.read_bytes()
    first_suggestions = suggestions.read_bytes()
    second = _run_cli(_base_args(paths, output, suggestions))
    assert second[0] == 0, second[2] + second[1]
    assert output.read_bytes() == first_payload
    assert suggestions.read_bytes() == first_suggestions

    payload = json.loads(output.read_text(encoding="utf-8"))
    result_payload = json.loads(first[1])
    suggestion_payload = yaml.safe_load(suggestions.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "agent-review.false-positive-signatures.v1"
    assert result_payload["status"] == "complete"
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["signature"] == signature_for_basis(payload["candidates"][0]["basis"])
    assert suggestion_payload["schema_id"] == "agent-review.contract-suggestions.v1"
    assert suggestion_payload["suggestions"] == []


def test_cli_generates_manual_suggestion(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out" / "false-positive-signatures.json"
    suggestions = tmp_path / "out" / "suggested-contract-updates.yaml"
    first = _run_cli(_base_args(paths, output, suggestions))
    assert first[0] == 0, first[2] + first[1]
    payload = json.loads(output.read_text(encoding="utf-8"))
    signature = signature_for_basis(payload["candidates"][0]["basis"])
    markers = tmp_path / "false-positive-markers.json"
    _write_json(
        markers,
        {
            "schema_id": "agent-review.false-positive-markers.v1",
            "schema_version": 1,
            "source": "manual",
            "markers": [
                {
                    "finding_signature": signature,
                    "reason": "docs_only_overseverity",
                    "suggested_rule": "Docs findings default to P3",
                    "contract_id": "review.docs-severity",
                }
            ],
        },
    )

    result = _run_cli(_base_args(paths, output, suggestions, markers))

    assert result[0] == 0, result[2] + result[1]
    suggestion_payload = yaml.safe_load(suggestions.read_text(encoding="utf-8"))
    assert len(suggestion_payload["suggestions"]) == 1
    assert suggestion_payload["suggestions"][0]["finding_signature"] == signature
    assert suggestion_payload["apply_mode"] == "manual_only"
    assert suggestion_payload["applied"] is False


def test_cli_missing_markers_path_is_complete(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out.json"
    missing_markers = tmp_path / "missing" / "false-positive-markers.json"

    result = _run_cli(_base_args(paths, output, markers=missing_markers))

    assert result[0] == 0, result[2] + result[1]
    payload = json.loads(output.read_text(encoding="utf-8"))
    result_payload = json.loads(result[1])
    assert result_payload["status"] == "complete"
    assert payload["limitations"] == []


def test_cli_invalid_markers_json_is_partial_with_limitation(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out.json"
    markers = tmp_path / "false-positive-markers.json"
    markers.write_text("{", encoding="utf-8")

    result = _run_cli(_base_args(paths, output, markers=markers))

    assert result[0] == 0, result[2] + result[1]
    payload = json.loads(output.read_text(encoding="utf-8"))
    result_payload = json.loads(result[1])
    assert result_payload["status"] == "partial"
    assert "false_positive_markers_invalid" in payload["limitations"]


def test_cli_invalid_markers_schema_is_partial_with_limitation(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out.json"
    markers = tmp_path / "false-positive-markers.json"
    _write_json(
        markers,
        {
            "schema_id": "wrong",
            "schema_version": 1,
            "source": "manual",
            "markers": [],
        },
    )

    result = _run_cli(_base_args(paths, output, markers=markers))

    assert result[0] == 0, result[2] + result[1]
    payload = json.loads(output.read_text(encoding="utf-8"))
    result_payload = json.loads(result[1])
    assert result_payload["status"] == "partial"
    assert "false_positive_markers_schema_invalid" in payload["limitations"]


def test_cli_empty_markers_file_is_complete(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    output = tmp_path / "out.json"
    markers = tmp_path / "false-positive-markers.json"
    _write_json(
        markers,
        {
            "schema_id": "agent-review.false-positive-markers.v1",
            "schema_version": 1,
            "source": "manual",
            "markers": [],
        },
    )

    first = _run_cli(_base_args(paths, output, markers=markers))
    assert first[0] == 0, first[2] + first[1]
    first_payload = output.read_bytes()
    second = _run_cli(_base_args(paths, output, markers=markers))
    assert second[0] == 0, second[2] + second[1]
    assert output.read_bytes() == first_payload
    payload = json.loads(output.read_text(encoding="utf-8"))
    result_payload = json.loads(first[1])
    assert result_payload["status"] == "complete"
    assert payload["limitations"] == []


def test_cli_missing_or_invalid_chunk_results_is_limitation(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    paths["chunk_results"].write_text("not-json", encoding="utf-8")
    output = tmp_path / "out.json"

    result = _run_cli(_base_args(paths, output))

    assert result[0] == 0, result[2] + result[1]
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "chunk_results_invalid" in payload["limitations"]


def test_cli_invalid_required_input_fails_without_writing(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    paths["final_review"].write_text("{}", encoding="utf-8")
    output = tmp_path / "out.json"

    result = _run_cli(_base_args(paths, output))

    assert result[0] == 1
    assert not output.exists()
    assert json.loads(result[1])["error_class"] == "final_review_invalid"


def test_cli_blocks_overwrite_worktree_and_symlink_outputs(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)

    overwrite = _run_cli(_base_args(paths, paths["final_review"]))
    assert overwrite[0] == 1
    assert json.loads(overwrite[1])["error_class"] == "output_overwrites_input"

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    blocked = _run_cli(_base_args(paths, worktree / "out.json"))
    assert blocked[0] == 1
    assert json.loads(blocked[1])["error_class"] == "target_repo_write_blocked"

    target = worktree / "linked-output.json"
    symlink = tmp_path / "linked-output.json"
    symlink.symlink_to(target)
    symlink_blocked = _run_cli(_base_args(paths, symlink))
    assert symlink_blocked[0] == 1
    assert json.loads(symlink_blocked[1])["error_class"] == "target_repo_write_blocked"


def test_cli_blocks_declared_target_repo_output(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _artifacts(tmp_path)
    target_repo = tmp_path / "AgentEscala"
    target_repo.mkdir()
    payload = json.loads(paths["final_review"].read_text(encoding="utf-8"))
    payload["target_repo_root"] = str(target_repo)
    _write_json(paths["final_review"], payload)

    result = _run_cli(_base_args(paths, target_repo / "false-positive-signatures.json"))

    assert result[0] == 1
    assert json.loads(result[1])["error_class"] == "target_repo_write_blocked"
    assert not (target_repo / "false-positive-signatures.json").exists()
