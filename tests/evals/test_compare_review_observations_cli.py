from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "compare-review-observations.py"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def test_correlates_observations_against_aiops_findings(tmp_path: Path):
    observations = [
        {
            "source": "codex_local",
            "repo": "mglpsw/aiops-orchestrator",
            "pr_number": 1,
            "head_sha": "2" * 40,
            "file_path": "app/agent_review/foo.py",
            "severity_claimed": "P2",
            "normalized_cause": "synthetic cause",
            "evidence_summary": "synthetic evidence",
        },
        {
            "source": "human",
            "repo": "mglpsw/aiops-orchestrator",
            "pr_number": 1,
            "head_sha": "2" * 40,
            "file_path": "app/agent_review/nowhere.py",
            "severity_claimed": "P1",
            "normalized_cause": "synthetic cause",
            "evidence_summary": "synthetic evidence",
        },
    ]
    aiops_findings = [{"finding_id": "f1", "file_path": "app/agent_review/foo.py", "severity": "P2"}]

    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    findings_path = tmp_path / "aiops_findings.json"
    findings_path.write_text(json.dumps(aiops_findings), encoding="utf-8")
    output_path = tmp_path / "correlation.json"

    result = _run(
        [
            "--observations",
            str(observations_path),
            "--aiops-findings",
            str(findings_path),
            "--output",
            str(output_path),
        ]
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["total_observations"] == 2
    assert report["by_source"]["codex_local"]["matched"] == 1
    assert report["by_source"]["human"]["rejected"] == 1


def test_fails_closed_on_invalid_observation(tmp_path: Path):
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps([{"source": "not_a_real_source"}]), encoding="utf-8")
    findings_path = tmp_path / "aiops_findings.json"
    findings_path.write_text(json.dumps([]), encoding="utf-8")
    output_path = tmp_path / "correlation.json"

    result = _run(
        [
            "--observations",
            str(observations_path),
            "--aiops-findings",
            str(findings_path),
            "--output",
            str(output_path),
        ]
    )
    assert result.returncode == 1
    assert "observation_input_invalid" in result.stderr
    assert not output_path.exists()


def test_fails_closed_on_non_array_input(tmp_path: Path):
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps({"not": "an array"}), encoding="utf-8")
    findings_path = tmp_path / "aiops_findings.json"
    findings_path.write_text(json.dumps([]), encoding="utf-8")
    output_path = tmp_path / "correlation.json"

    result = _run(
        [
            "--observations",
            str(observations_path),
            "--aiops-findings",
            str(findings_path),
            "--output",
            str(output_path),
        ]
    )
    assert result.returncode == 1
    assert "observation_input_invalid" in result.stderr
