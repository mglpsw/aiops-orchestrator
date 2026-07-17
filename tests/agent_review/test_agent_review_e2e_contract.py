from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import get_args

import pytest
import yaml

from app.agent_review.false_positive_signatures import signature_for_basis
from app.agent_review.schemas import FinalReviewVerdict, ReviewQualityGateStatus


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
FIXTURE_ROOT = ROOT / "agent_review" / "fixtures" / "agentescala_e2e"
SCRIPTS = REPO_ROOT / "scripts"
FIXTURE_SECRET = "AGENTESCALA_PHASE05_E2E_SECRET"
UNIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.~-])/(?:[A-Za-z0-9._@+=:-]+/)+[A-Za-z0-9._@+=:-]+")
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\\s]+\\)+[^\\\s]+")


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


def _write_fake_chunk_responses(chunk_payload_manifest: Path, payloads_dir: Path, responses_dir: Path) -> None:
    manifest = json.loads(chunk_payload_manifest.read_text(encoding="utf-8"))
    responses_dir.mkdir(parents=True, exist_ok=True)
    for chunk in manifest["chunks"]:
        payload_path = payloads_dir / str(chunk["payload_path"])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        files = [
            item.get("path")
            for item in payload.get("chunk_context", {}).get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
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
        if files:
            payload["confirmed_findings"].append(
                {
                    "severity": "P3",
                    "title": "Fixture docs severity marker",
                    "file_path": files[0],
                    "line_or_hunk": "L1-L2",
                    "evidence": "Changed fixture content has deterministic offline review evidence.",
                    "source_artifact": "file-diff-context",
                    "contract_id": "review.docs-severity",
                    "impact": "Human reviewers can audit suggested contract updates separately.",
                    "confidence": "high",
                    "dedupe_key": f"{chunk['chunk_id']}:{files[0]}",
                }
            )
        (responses_dir / f"{chunk['chunk_id']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _canonical_len(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


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


def _assert_no_absolute_paths(value: str) -> None:
    assert not UNIX_ABSOLUTE_PATH_RE.search(value), "unexpected unix absolute path leaked in output"
    assert not WINDOWS_ABSOLUTE_PATH_RE.search(value), "unexpected windows absolute path leaked in output"


def _prepare_intake_and_chunk_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    _install_offline_guards(monkeypatch)
    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "agent-stage"
    out_dir.mkdir()
    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"

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
    return target_repo, agent_dir, out_dir, intake, chunk_plan


def test_e2e_build_payloads_fails_closed_when_embedded_redaction_is_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, agent_dir, out_dir, intake, chunk_plan = _prepare_intake_and_chunk_plan(monkeypatch, tmp_path)
    redaction_report = out_dir / "redaction-report.json"
    pr_brief = out_dir / "pr-brief.json"
    manifest = out_dir / "chunk-payload-manifest.json"
    payloads_dir = out_dir / "chunk-payloads"

    intake_payload = json.loads(intake.read_text(encoding="utf-8"))
    intake_payload["redaction_summary"]["output_safe_for_llm"] = False
    intake.write_text(json.dumps(intake_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    build_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(payloads_dir),
            "--manifest-output",
            str(manifest),
        ],
    )
    assert build_result.returncode == 1
    payload = json.loads(build_result.stdout)
    assert payload["error_class"] == "redaction_report_unsafe"
    assert not pr_brief.exists()
    assert not manifest.exists()
    assert not payloads_dir.exists()


def test_e2e_build_payloads_fails_closed_when_redaction_reports_diverge(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, agent_dir, out_dir, intake, chunk_plan = _prepare_intake_and_chunk_plan(monkeypatch, tmp_path)
    redaction_report = out_dir / "redaction-report.json"
    pr_brief = out_dir / "pr-brief.json"
    manifest = out_dir / "chunk-payload-manifest.json"
    payloads_dir = out_dir / "chunk-payloads"

    redaction_payload = json.loads(redaction_report.read_text(encoding="utf-8"))
    redaction_payload["files_processed"] = int(redaction_payload.get("files_processed", 0)) + 1
    redaction_report.write_text(
        json.dumps(redaction_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    build_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(payloads_dir),
            "--manifest-output",
            str(manifest),
        ],
    )
    assert build_result.returncode == 1
    payload = json.loads(build_result.stdout)
    assert payload["error_class"] == "redaction_report_mismatch"
    assert not pr_brief.exists()
    assert not manifest.exists()
    assert not payloads_dir.exists()


def test_e2e_build_payloads_scopes_contracts_to_matching_chunks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, agent_dir, out_dir, intake, chunk_plan = _prepare_intake_and_chunk_plan(monkeypatch, tmp_path)
    redaction_report = out_dir / "redaction-report.json"
    pr_brief = out_dir / "pr-brief.json"
    manifest = out_dir / "chunk-payload-manifest.json"
    payloads_dir = out_dir / "chunk-payloads"

    plan_payload = json.loads(chunk_plan.read_text(encoding="utf-8"))
    scoped_file = ""
    for chunk in plan_payload.get("chunks", []):
        if isinstance(chunk, dict) and isinstance(chunk.get("files"), list) and chunk["files"]:
            candidate = chunk["files"][0]
            if isinstance(candidate, str):
                scoped_file = candidate
                break
    for chunk in plan_payload.get("chunks", []):
        if isinstance(chunk, dict):
            chunk["contracts"] = []
    chunk_plan.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert scoped_file

    intake_payload = json.loads(intake.read_text(encoding="utf-8"))
    intake_payload["target_profile"]["domain_contracts"]["rules"] = [
        {"id": "scoped-backend", "description": "backend", "file_path": scoped_file},
        {"id": "global-rule", "description": "global", "scope": "global"},
        {"id": "non-matching", "description": "none", "files": ["docs/never.md"]},
    ]
    intake.write_text(json.dumps(intake_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    build_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(payloads_dir),
            "--manifest-output",
            str(manifest),
        ],
    )
    assert build_result.returncode == 0, build_result.stderr + build_result.stdout

    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["payload_count"] > 0
    backend_seen = False
    global_seen = False
    unrelated_seen = False
    for entry in manifest_payload["chunks"]:
        payload_path = payloads_dir / str(entry["payload_path"])
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        contract_ids = {
            item.get("id")
            for item in payload.get("chunk_context", {}).get("contracts_context", {}).get("domain_contracts", [])
            if isinstance(item, dict)
        }
        chunk_files = {
            item.get("path")
            for item in payload.get("chunk_context", {}).get("files", [])
            if isinstance(item, dict)
        }
        if scoped_file in chunk_files and "scoped-backend" in contract_ids:
            backend_seen = True
        if "global-rule" in contract_ids:
            global_seen = True
        if "non-matching" in contract_ids:
            unrelated_seen = True
    assert backend_seen is True
    assert global_seen is True
    assert unrelated_seen is False


def test_agentescala_tool_repo_e2e_contract_runs_offline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_offline_guards(monkeypatch)
    working_tree_before = _git_status_snapshot()
    fixture_snapshot_before = _file_snapshot(FIXTURE_ROOT)
    target_repo, agent_dir = _copy_fixture(tmp_path)
    target_snapshot_before = _file_snapshot(target_repo)
    out_dir = tmp_path / "agent"
    responses_dir = out_dir / "chunk-responses"
    out_dir.mkdir()

    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    pr_brief = out_dir / "pr-brief.json"
    chunk_payload_manifest = out_dir / "chunk-payload-manifest.json"
    chunk_payloads_dir = out_dir / "chunk-payloads"
    chunk_results = out_dir / "chunk-results.json"
    final_review_json = out_dir / "final-review.json"
    final_review_md = out_dir / "final-review.md"
    quality_gate = out_dir / "review-quality-gate.json"
    telemetry = out_dir / "review-telemetry.json"
    false_positive_signatures = out_dir / "false-positive-signatures.json"
    false_positive_markers = out_dir / "false-positive-markers.json"
    false_positive_signatures_with_marker = out_dir / "false-positive-signatures-with-marker.json"
    suggested_contract_updates = out_dir / "suggested-contract-updates.yaml"

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

    build_payloads_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(chunk_payloads_dir),
            "--manifest-output",
            str(chunk_payload_manifest),
            "--brief-max-chars",
            "4000",
            "--payload-max-chars",
            "2200",
        ],
    )
    assert build_payloads_result.returncode == 0, build_payloads_result.stderr + build_payloads_result.stdout
    first_pr_brief_payload = pr_brief.read_text(encoding="utf-8")
    first_payload_manifest_payload = chunk_payload_manifest.read_text(encoding="utf-8")
    first_payload_files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(chunk_payloads_dir.glob("*.json"))
    }

    deterministic_dir = tmp_path / "agent-deterministic"
    deterministic_dir.mkdir()
    pr_brief_deterministic = deterministic_dir / "pr-brief.json"
    chunk_payload_manifest_deterministic = deterministic_dir / "chunk-payload-manifest.json"
    chunk_payloads_dir_deterministic = deterministic_dir / "chunk-payloads"

    deterministic_build_payloads_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief_deterministic),
            "--payloads-dir",
            str(chunk_payloads_dir_deterministic),
            "--manifest-output",
            str(chunk_payload_manifest_deterministic),
            "--brief-max-chars",
            "4000",
            "--payload-max-chars",
            "2200",
        ],
    )
    assert deterministic_build_payloads_result.returncode == 0, (
        deterministic_build_payloads_result.stderr + deterministic_build_payloads_result.stdout
    )
    assert pr_brief_deterministic.read_text(encoding="utf-8") == first_pr_brief_payload
    assert chunk_payload_manifest_deterministic.read_text(encoding="utf-8") == first_payload_manifest_payload
    second_payload_files = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(chunk_payloads_dir_deterministic.glob("*.json"))
    }
    assert second_payload_files == first_payload_files

    _write_fake_chunk_responses(chunk_payload_manifest, chunk_payloads_dir, responses_dir)

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

    telemetry_result = _run_cli(
        SCRIPTS / "aiops-review-telemetry.py",
        [
            "--final-review",
            str(final_review_json),
            "--quality-gate",
            str(quality_gate),
            "--chunk-results",
            str(chunk_results),
            "--chunk-plan",
            str(chunk_plan),
            "--intake",
            str(intake),
            "--redaction-report",
            str(redaction_report),
            "--output",
            str(telemetry),
        ],
    )
    assert telemetry_result.returncode == 0, telemetry_result.stderr + telemetry_result.stdout
    first_telemetry_payload = telemetry.read_text(encoding="utf-8")

    deterministic_telemetry_result = _run_cli(
        SCRIPTS / "aiops-review-telemetry.py",
        [
            "--final-review",
            str(final_review_json),
            "--quality-gate",
            str(quality_gate),
            "--chunk-results",
            str(chunk_results),
            "--chunk-plan",
            str(chunk_plan),
            "--intake",
            str(intake),
            "--redaction-report",
            str(redaction_report),
            "--output",
            str(telemetry),
        ],
    )
    assert deterministic_telemetry_result.returncode == 0, (
        deterministic_telemetry_result.stderr + deterministic_telemetry_result.stdout
    )
    assert telemetry.read_text(encoding="utf-8") == first_telemetry_payload

    false_positive_result = _run_cli(
        SCRIPTS / "aiops-review-false-positives.py",
        [
            "--review-telemetry",
            str(telemetry),
            "--quality-gate",
            str(quality_gate),
            "--final-review",
            str(final_review_json),
            "--chunk-results",
            str(chunk_results),
            "--output",
            str(false_positive_signatures),
        ],
    )
    assert false_positive_result.returncode == 0, false_positive_result.stderr + false_positive_result.stdout
    first_false_positive_payload = false_positive_signatures.read_text(encoding="utf-8")

    deterministic_false_positive_result = _run_cli(
        SCRIPTS / "aiops-review-false-positives.py",
        [
            "--review-telemetry",
            str(telemetry),
            "--quality-gate",
            str(quality_gate),
            "--final-review",
            str(final_review_json),
            "--chunk-results",
            str(chunk_results),
            "--output",
            str(false_positive_signatures),
        ],
    )
    assert deterministic_false_positive_result.returncode == 0, (
        deterministic_false_positive_result.stderr + deterministic_false_positive_result.stdout
    )
    assert false_positive_signatures.read_text(encoding="utf-8") == first_false_positive_payload

    for output in (
        intake,
        redaction_report,
        chunk_plan,
        pr_brief,
        chunk_payload_manifest,
        chunk_results,
        final_review_json,
        final_review_md,
        quality_gate,
        telemetry,
        false_positive_signatures,
    ):
        assert output.exists(), f"{output.name} was not generated"
        assert output.stat().st_size > 0, f"{output.name} is empty"
        _assert_under(output, out_dir)
        _assert_not_under(output, target_repo)
    for payload_file in sorted(chunk_payloads_dir.glob("*.json")):
        assert payload_file.exists(), f"{payload_file.name} was not generated"
        assert payload_file.stat().st_size > 0, f"{payload_file.name} is empty"
        _assert_under(payload_file, out_dir)
        _assert_not_under(payload_file, target_repo)

    final_markdown = final_review_md.read_text(encoding="utf-8")
    brief_payload = json.loads(pr_brief.read_text(encoding="utf-8"))
    payload_manifest_payload = json.loads(chunk_payload_manifest.read_text(encoding="utf-8"))
    final_payload = json.loads(final_review_json.read_text(encoding="utf-8"))
    results_payload = json.loads(chunk_results.read_text(encoding="utf-8"))
    gate_payload = json.loads(first_gate_payload)
    telemetry_payload = json.loads(first_telemetry_payload)
    false_positive_payload = json.loads(first_false_positive_payload)

    marker_signature = signature_for_basis(false_positive_payload["candidates"][0]["basis"])
    false_positive_markers.write_text(
        json.dumps(
            {
                "schema_id": "agent-review.false-positive-markers.v1",
                "schema_version": 1,
                "source": "manual",
                "markers": [
                    {
                        "finding_signature": marker_signature,
                        "reason": "docs_only_overseverity",
                        "suggested_rule": "Docs findings default to P3 unless deterministic high-impact evidence exists",
                        "contract_id": "review.docs-severity",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    marked_false_positive_result = _run_cli(
        SCRIPTS / "aiops-review-false-positives.py",
        [
            "--review-telemetry",
            str(telemetry),
            "--quality-gate",
            str(quality_gate),
            "--final-review",
            str(final_review_json),
            "--chunk-results",
            str(chunk_results),
            "--markers",
            str(false_positive_markers),
            "--output",
            str(false_positive_signatures_with_marker),
            "--suggestions-output",
            str(suggested_contract_updates),
        ],
    )
    assert marked_false_positive_result.returncode == 0, (
        marked_false_positive_result.stderr + marked_false_positive_result.stdout
    )
    marked_false_positive_payload = json.loads(false_positive_signatures_with_marker.read_text(encoding="utf-8"))
    suggestions_payload = yaml.safe_load(suggested_contract_updates.read_text(encoding="utf-8"))
    for output in (false_positive_markers, false_positive_signatures_with_marker, suggested_contract_updates):
        assert output.exists(), f"{output.name} was not generated"
        assert output.stat().st_size > 0, f"{output.name} is empty"
        _assert_under(output, out_dir)
        _assert_not_under(output, target_repo)

    assert final_payload["schema_id"] == "agent-review.final-review.v1"
    assert brief_payload["schema_id"] == "agent-review.pr-brief.v1"
    assert payload_manifest_payload["schema_id"] == "agent-review.chunk-payload-manifest.v1"
    assert payload_manifest_payload["payload_count"] == len(payload_manifest_payload["chunks"])
    assert payload_manifest_payload["payload_count"] == len(
        list(chunk_payloads_dir.glob("*.json"))
    )
    assert final_payload["target_repo"] == "mglpsw/AgentEscala"
    assert brief_payload["target"]["repository"] == "mglpsw/AgentEscala"
    assert payload_manifest_payload["target_repo"] == "mglpsw/AgentEscala"
    assert results_payload["schema_id"] == "agent-review.chunk-results.v1"
    assert results_payload["chunks_failed"] == []
    assert "# Agent Review" in final_markdown
    assert final_markdown.strip()

    brief_len = _canonical_len(brief_payload)
    assert brief_payload["truncation"]["emitted_chars"] == brief_len
    if brief_payload["truncation"]["truncation_reason"] != "max_chars_exceeded_minimum_required_sections":
        assert brief_len <= 4000

    chunk_plan_payload = json.loads(chunk_plan.read_text(encoding="utf-8"))
    files_by_chunk = {item["chunk_id"]: set(item["files"]) for item in chunk_plan_payload["chunks"]}
    for entry in payload_manifest_payload["chunks"]:
        payload_file = chunk_payloads_dir / entry["payload_path"]
        payload_data = json.loads(payload_file.read_text(encoding="utf-8"))
        canonical = json.dumps(payload_data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert entry["payload_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
        assert entry["truncation"] == payload_data["truncation"]
        payload_len = _canonical_len(payload_data)
        assert payload_data["truncation"]["emitted_chars"] == payload_len
        if payload_data["truncation"]["truncation_reason"] != "max_chars_exceeded_minimum_required_sections":
            assert payload_len <= 2200

        chunk_id = entry["chunk_id"]
        context_text = json.dumps(payload_data["chunk_context"], ensure_ascii=False, sort_keys=True)
        for other_chunk_id, other_files in files_by_chunk.items():
            if other_chunk_id == chunk_id:
                continue
            for other_file in other_files:
                assert other_file not in context_text

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
    assert gate_payload["status"] == "passed"
    assert gate_payload["normalized_verdict"] == "approve_with_minor_notes"
    assert gate_payload["manual_review_required"] is False
    assert gate_payload["second_opinion_requested"] is False
    assert gate_payload["second_opinion_status"] == "not_required"
    assert gate_payload["inputs"]["final_review"]["provided"] is True
    assert gate_payload["inputs"]["chunk_results"]["provided"] is True
    assert gate_payload["inputs"]["intake"]["provided"] is True
    assert gate_payload["inputs"]["chunk_plan"]["provided"] is True
    assert gate_payload["inputs"]["redaction_report"]["provided"] is True

    assert telemetry_payload["schema_id"] == "agent-review.telemetry.v1"
    assert telemetry_payload["schema_version"] == 1
    assert telemetry_payload["source"] == "aiops-review-telemetry"
    assert telemetry_payload["quality_gate"]["normalized_verdict"] == gate_payload["normalized_verdict"]
    assert telemetry_payload["quality_gate"]["status"] == gate_payload["status"]
    assert telemetry_payload["quality_gate"]["manual_review_required"] is gate_payload["manual_review_required"]
    assert telemetry_payload["status"] in {"complete", "partial", "degraded"}
    assert telemetry_payload["limitations"]
    assert all(isinstance(item, str) and item for item in telemetry_payload["limitations"])
    assert telemetry_payload["inputs"]["final_review"]["provided"] is True
    assert telemetry_payload["inputs"]["review_quality_gate"]["provided"] is True
    assert telemetry_payload["inputs"]["chunk_results"]["provided"] is True
    assert telemetry_payload["inputs"]["chunk_plan"]["provided"] is True
    assert telemetry_payload["inputs"]["intake"]["provided"] is True
    assert telemetry_payload["inputs"]["redaction_report"]["provided"] is True

    assert false_positive_payload["schema_id"] == "agent-review.false-positive-signatures.v1"
    assert false_positive_payload["schema_version"] == 1
    assert false_positive_payload["source"] == "aiops-review-false-positives"
    assert false_positive_payload["target"] == {"repository": "mglpsw/AgentEscala"}
    assert false_positive_payload["candidates"]
    assert false_positive_payload["markers"] == []
    assert marked_false_positive_payload["candidates"][0]["matched_markers"]
    assert suggestions_payload["schema_id"] == "agent-review.contract-suggestions.v1"
    assert suggestions_payload["apply_mode"] == "manual_only"
    assert suggestions_payload["applied"] is False
    assert suggestions_payload["target"] == {"repository": "mglpsw/AgentEscala"}
    assert len(suggestions_payload["suggestions"]) == 1
    assert suggestions_payload["suggestions"][0]["finding_signature"] == marker_signature

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
    telemetry_text = json.dumps(telemetry_payload, ensure_ascii=False, sort_keys=True)
    false_positive_text = json.dumps(marked_false_positive_payload, ensure_ascii=False, sort_keys=True)
    suggestions_text = json.dumps(suggestions_payload, ensure_ascii=False, sort_keys=True)
    brief_text = json.dumps(brief_payload, ensure_ascii=False, sort_keys=True)
    payload_manifest_text = json.dumps(payload_manifest_payload, ensure_ascii=False, sort_keys=True)
    for value in forbidden:
        assert value not in final_markdown
        assert value not in brief_text
        assert value not in payload_manifest_text
        assert value not in gate_text
        assert value not in telemetry_text
        assert value not in false_positive_text
        assert value not in suggestions_text

    _assert_no_absolute_paths(final_markdown)
    _assert_no_absolute_paths(brief_text)
    _assert_no_absolute_paths(payload_manifest_text)
    _assert_no_absolute_paths(gate_text)
    _assert_no_absolute_paths(telemetry_text)
    _assert_no_absolute_paths(false_positive_text)
    _assert_no_absolute_paths(suggestions_text)
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
        "pr-brief.json",
        "chunk-payload-manifest.json",
        "chunk-results.json",
        "final-review.json",
        "final-review.md",
        "review-quality-gate.json",
        "review-telemetry.json",
        "false-positive-signatures.json",
        "false-positive-markers.json",
        "false-positive-signatures-with-marker.json",
        "suggested-contract-updates.yaml",
    }
    generated_artifact_names.update(path.name for path in chunk_payloads_dir.glob("*.json"))
    assert not generated_artifact_names & {Path(path).name for path in target_files}
    assert _file_snapshot(FIXTURE_ROOT) == fixture_snapshot_before
    assert _git_status_snapshot() == working_tree_before


def test_agentescala_payload_builder_preserves_preexisting_output_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_offline_guards(monkeypatch)
    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "agent"
    out_dir.mkdir()
    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    pr_brief = out_dir / "pr-brief.json"
    chunk_payload_manifest = out_dir / "chunk-payload-manifest.json"
    chunk_payloads_dir = out_dir / "chunk-payloads"

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
        ["--intake", str(intake), "--output", str(chunk_plan), "--max-blocks", "6"],
    )
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout

    chunk_payloads_dir.mkdir(parents=True)
    sentinel = chunk_payloads_dir / "sentinel.txt"
    sentinel.write_text("keep-me", encoding="utf-8")

    build_payloads_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(chunk_payloads_dir),
            "--manifest-output",
            str(chunk_payload_manifest),
        ],
    )
    assert build_payloads_result.returncode == 1
    error_payload = json.loads(build_payloads_result.stdout)
    assert error_payload["error_class"] == "output_exists"
    assert sentinel.read_text(encoding="utf-8") == "keep-me"
    assert not pr_brief.exists()
    assert not chunk_payload_manifest.exists()


def test_agentescala_payload_builder_fails_closed_on_unsafe_redaction_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_offline_guards(monkeypatch)
    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "agent"
    out_dir.mkdir()
    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    pr_brief = out_dir / "pr-brief.json"
    chunk_payload_manifest = out_dir / "chunk-payload-manifest.json"
    chunk_payloads_dir = out_dir / "chunk-payloads"

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
        ["--intake", str(intake), "--output", str(chunk_plan), "--max-blocks", "6"],
    )
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout
    redaction_payload = json.loads(redaction_report.read_text(encoding="utf-8"))
    redaction_payload["output_safe_for_llm"] = False
    redaction_report.write_text(
        json.dumps(redaction_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    build_payloads_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(chunk_payloads_dir),
            "--manifest-output",
            str(chunk_payload_manifest),
        ],
    )
    assert build_payloads_result.returncode == 1
    error_payload = json.loads(build_payloads_result.stdout)
    assert error_payload["error_class"] == "redaction_report_unsafe"
    assert not pr_brief.exists()
    assert not chunk_payload_manifest.exists()
    assert not chunk_payloads_dir.exists()


def test_agentescala_payload_builder_handles_quoted_diff_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_offline_guards(monkeypatch)
    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "agent"
    out_dir.mkdir()
    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    chunk_plan = out_dir / "semantic-chunk-plan.json"
    pr_brief = out_dir / "pr-brief.json"
    chunk_payload_manifest = out_dir / "chunk-payload-manifest.json"
    chunk_payloads_dir = out_dir / "chunk-payloads"

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
        ["--intake", str(intake), "--output", str(chunk_plan), "--max-blocks", "6"],
    )
    assert plan_result.returncode == 0, plan_result.stderr + plan_result.stdout

    intake_payload = json.loads(intake.read_text(encoding="utf-8"))
    intake_payload["artifacts"]["file-diff-context"]["content"]["files"] = [
        {"path": "backend/my file.py", "status": "modified", "summary": "spaces"},
        {"path": "docs/ação clínica.md", "status": "modified", "summary": "unicode"},
        {"path": "new.py", "status": "renamed", "summary": "rename"},
        {"path": "obsolete.py", "status": "removed", "summary": "removed"},
    ]
    intake_payload["artifacts"]["full-diff"]["content"] = "\n".join(
        [
            'diff --git "a/backend/my file.py" "b/backend/my file.py"',
            '--- "a/backend/my file.py"',
            '+++ "b/backend/my file.py"',
            "@@ -1 +1 @@",
            "+print('ok')",
            'diff --git "a/docs/ação clínica.md" "b/docs/ação clínica.md"',
            '--- "a/docs/ação clínica.md"',
            '+++ "b/docs/ação clínica.md"',
            "@@ -1 +1 @@",
            "+conteúdo",
            'diff --git "a/old.py" "b/new.py"',
            "rename from old.py",
            "rename to new.py",
            "--- a/old.py",
            "+++ b/new.py",
            "@@ -1 +1 @@",
            "+renamed",
            "diff --git a/obsolete.py b/obsolete.py",
            "--- a/obsolete.py",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-old content",
        ]
    )
    intake.write_text(json.dumps(intake_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    plan_payload = json.loads(chunk_plan.read_text(encoding="utf-8"))
    plan_payload["chunks"][0]["files"] = [
        "backend/my file.py",
        "docs/ação clínica.md",
        "new.py",
        "obsolete.py",
    ]
    chunk_plan.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    build_payloads_result = _run_cli(
        SCRIPTS / "aiops-review-build-payloads.py",
        [
            "--intake",
            str(intake),
            "--chunk-plan",
            str(chunk_plan),
            "--redaction-report",
            str(redaction_report),
            "--checks",
            str(agent_dir / "checks.json"),
            "--validation-evidence",
            str(agent_dir / "validation-evidence" / "validation-evidence-result.json"),
            "--brief-output",
            str(pr_brief),
            "--payloads-dir",
            str(chunk_payloads_dir),
            "--manifest-output",
            str(chunk_payload_manifest),
            "--payload-max-chars",
            "20000",
        ],
    )
    assert build_payloads_result.returncode == 0, build_payloads_result.stderr + build_payloads_result.stdout
    manifest_payload = json.loads(chunk_payload_manifest.read_text(encoding="utf-8"))
    payload_path = chunk_payloads_dir / manifest_payload["chunks"][0]["payload_path"]
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    hunk_paths = {item["path"] for item in payload["chunk_context"]["chunk_hunks"]}
    assert {"backend/my file.py", "docs/ação clínica.md", "new.py", "obsolete.py"} <= hunk_paths


def test_agentescala_tool_repo_e2e_contract_fails_closed_on_production_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for key, value in _prod_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AIOPS_CT102_HOST", "ct102.internal")

    target_repo, agent_dir = _copy_fixture(tmp_path)
    out_dir = tmp_path / "agent"
    out_dir.mkdir()
    intake = out_dir / "aiops-intake.json"
    redaction_report = out_dir / "redaction-report.json"
    target_snapshot_before = _file_snapshot(target_repo)

    result = _run_cli(
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
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["error_class"] == "environment_blocked"
    assert "production runtime" in payload["message"].lower()
    assert not intake.exists()
    assert not redaction_report.exists()
    assert _file_snapshot(target_repo) == target_snapshot_before
