from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-quality-gate-v2.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def _identity_dict(**overrides: object) -> dict[str, object]:
    raw: dict[str, object] = {
        "repo": "mglpsw/aiops-orchestrator",
        "pr_number": 130,
        "base_sha": "1" * 40,
        "head_sha": "2" * 40,
        "tested_merge_sha": "3" * 40,
        "toolrepo_sha": "4" * 40,
        "profile_hash": "a" * 64,
        "policy_hash": "b" * 64,
        "manifest_hash": "c" * 64,
        "evidence_hash": "d" * 64,
    }
    raw.update(overrides)
    return raw


def _ready_decision_dict(head_sha: str) -> dict[str, object]:
    return {
        "state": "ready",
        "reason_codes": [],
        "blockers": [],
        "coverage": {
            "status": "complete",
            "expected_files": ["app/a.py"],
            "reviewed_files": ["app/a.py"],
            "partially_reviewed_files": [],
            "missing_files": [],
            "must_review_files": ["app/a.py"],
            "missing_must_review_files": [],
            "degradation_causes": [],
        },
        "pipeline": {"degraded": False, "causes": []},
    }


def _write_fixtures(tmp_path: Path) -> dict[str, Path]:
    head_sha = "2" * 40
    identity = _identity_dict()

    paths = {}
    paths["identity"] = tmp_path / "identity.json"
    paths["identity"].write_text(json.dumps(identity), encoding="utf-8")
    paths["evaluated_identity"] = tmp_path / "evaluated_identity.json"
    paths["evaluated_identity"].write_text(json.dumps(identity), encoding="utf-8")
    paths["decision"] = tmp_path / "decision.json"
    paths["decision"].write_text(json.dumps(_ready_decision_dict(head_sha)), encoding="utf-8")
    paths["findings"] = tmp_path / "findings.json"
    paths["findings"].write_text(json.dumps([]), encoding="utf-8")
    paths["checks"] = tmp_path / "checks.json"
    paths["checks"].write_text(
        json.dumps(
            [{"check_name": "pytest", "required": True, "deterministic": True, "conclusion": "success", "head_sha": head_sha}]
        ),
        encoding="utf-8",
    )
    return paths


def test_cli_emits_a_valid_ready_readiness_artifact(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--decision", str(paths["decision"]),
            "--identity", str(paths["identity"]),
            "--evaluated-identity", str(paths["evaluated_identity"]),
            "--findings", str(paths["findings"]),
            "--pr-state", "open",
            "--checks", str(paths["checks"]),
            "--output", str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    doc = json.loads(output_path.read_text(encoding="utf-8"))
    assert doc["state"] == "ready"


def test_cli_refuses_without_contract_version_v2(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(
        [
            "--contract-version", "v1",
            "--decision", str(paths["decision"]),
            "--identity", str(paths["identity"]),
            "--evaluated-identity", str(paths["evaluated_identity"]),
            "--findings", str(paths["findings"]),
            "--pr-state", "open",
            "--checks", str(paths["checks"]),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()


def test_cli_fails_closed_on_a_readiness_invariant_violation(tmp_path: Path) -> None:
    """ready requires an open PR -- a merged PR with a ready decision must
    be refused by ReviewReadinessV2's own validator, never silently
    accepted."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    result = _run(
        [
            "--contract-version", "v2",
            "--decision", str(paths["decision"]),
            "--identity", str(paths["identity"]),
            "--evaluated-identity", str(paths["evaluated_identity"]),
            "--findings", str(paths["findings"]),
            "--pr-state", "merged",
            "--checks", str(paths["checks"]),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()
    assert "readiness_invariant_violation" in result.stderr


def test_cli_rejects_a_v1_payload_mixed_into_a_v2_gate_run(tmp_path: Path) -> None:
    """Closes the 'select_contract_version sem call site em produção' gap:
    a v1-shaped payload/response fed alongside --contract-version v2 is
    refused as mixed_contract_versions, zero conversion."""

    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v1", "schema_version": 1}), encoding="utf-8"
    )
    response_path = tmp_path / "response.json"
    response_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    result = _run(
        [
            "--contract-version", "v2",
            "--decision", str(paths["decision"]),
            "--identity", str(paths["identity"]),
            "--evaluated-identity", str(paths["evaluated_identity"]),
            "--findings", str(paths["findings"]),
            "--pr-state", "open",
            "--checks", str(paths["checks"]),
            "--payload", str(payload_path),
            "--response", str(response_path),
            "--output", str(output_path),
        ]
    )

    assert result.returncode != 0
    assert not output_path.exists()
    assert "mixed_contract_versions" in result.stderr


def test_cli_accepts_a_matching_v2_payload_and_response(tmp_path: Path) -> None:
    paths = _write_fixtures(tmp_path)
    output_path = tmp_path / "out" / "readiness.json"

    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-payload.v2", "schema_version": 2}), encoding="utf-8"
    )
    response_path = tmp_path / "response.json"
    response_path.write_text(
        json.dumps({"schema_id": "agent-review.chunk-response-envelope.v2", "schema_version": 2}), encoding="utf-8"
    )

    result = _run(
        [
            "--contract-version", "v2",
            "--decision", str(paths["decision"]),
            "--identity", str(paths["identity"]),
            "--evaluated-identity", str(paths["evaluated_identity"]),
            "--findings", str(paths["findings"]),
            "--pr-state", "open",
            "--checks", str(paths["checks"]),
            "--payload", str(payload_path),
            "--response", str(response_path),
            "--output", str(output_path),
        ]
    )

    assert result.returncode == 0, result.stderr
    assert output_path.exists()
