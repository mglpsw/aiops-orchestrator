from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-agent-review-v2-evals.py"
REAL_CASES_DIR = ROOT / "evals" / "agent_review_v2" / "cases"
REAL_FIXTURES_ROOT = ROOT / "tests" / "agent_review" / "fixtures" / "v2"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def test_real_corpus_runs_clean(tmp_path: Path):
    json_output = tmp_path / "summary.json"
    markdown_output = tmp_path / "summary.md"
    result = _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    assert result.returncode == 0, result.stderr
    summary = json.loads(json_output.read_text(encoding="utf-8"))
    assert summary["summary"]["total_cases"] == len(list(REAL_CASES_DIR.glob("*.yaml")))
    assert summary["summary"]["false_approvals"] == []
    assert summary["summary"]["forbidden_findings_leaked_total"] == 0
    assert markdown_output.exists()


def test_check_mode_passes_against_freshly_written_report(tmp_path: Path):
    json_output = tmp_path / "summary.json"
    markdown_output = tmp_path / "summary.md"
    write_result = _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    assert write_result.returncode == 0

    check_result = _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--check",
        ]
    )
    assert check_result.returncode == 0, check_result.stderr
    assert "matches a fresh run" in check_result.stdout


def test_check_mode_fails_closed_on_drift(tmp_path: Path):
    json_output = tmp_path / "summary.json"
    markdown_output = tmp_path / "summary.md"
    _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ]
    )
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    payload["summary"]["total_cases"] = 999
    json_output.write_text(json.dumps(payload), encoding="utf-8")

    check_result = _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
            "--check",
        ]
    )
    assert check_result.returncode == 1
    assert "eval_summary_drift" in check_result.stderr


def test_check_mode_fails_closed_when_report_missing(tmp_path: Path):
    check_result = _run(
        [
            "--cases-dir",
            str(REAL_CASES_DIR),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(tmp_path / "does-not-exist.json"),
            "--markdown-output",
            str(tmp_path / "summary.md"),
            "--check",
        ]
    )
    assert check_result.returncode == 1
    assert "eval_summary_missing" in check_result.stderr


def _load_evals_script_module():
    """`scripts/*.py` filenames use hyphens and cannot be `import`ed
    normally -- load the real module directly so this test reuses the
    SCRIPT's own `_without_durations`, never a second, independently
    maintained copy of the same field-stripping logic that could silently
    drift from it."""

    import importlib.util

    spec = importlib.util.spec_from_file_location("run_agent_review_v2_evals", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_corpus_is_byte_reproducible(tmp_path: Path):
    without_durations = _load_evals_script_module()._without_durations
    outputs = []
    for i in range(2):
        json_output = tmp_path / f"summary_{i}.json"
        result = _run(
            [
                "--cases-dir",
                str(REAL_CASES_DIR),
                "--fixtures-root",
                str(REAL_FIXTURES_ROOT),
                "--json-output",
                str(json_output),
                "--markdown-output",
                str(tmp_path / f"summary_{i}.md"),
            ]
        )
        assert result.returncode == 0
        outputs.append(without_durations(json.loads(json_output.read_text(encoding="utf-8"))))
    assert outputs[0] == outputs[1]


def test_fails_closed_on_empty_cases_dir(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = _run(
        [
            "--cases-dir",
            str(empty_dir),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(tmp_path / "summary.json"),
            "--markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )
    assert result.returncode == 1
    assert "eval_case_load_failed" in result.stderr


def test_fails_closed_on_malformed_case_file(tmp_path: Path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "broken.yaml").write_text("case_id: broken\ncategory: not-a-real-category\n", encoding="utf-8")
    result = _run(
        [
            "--cases-dir",
            str(cases_dir),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(tmp_path / "summary.json"),
            "--markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )
    assert result.returncode == 1
    assert "eval_case_load_failed" in result.stderr


def test_fails_closed_on_duplicate_case_id(tmp_path: Path):
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    case_body = """
case_id: duplicate-case
category: contract
target: agent_escala
files:
  - path: backend/scheduling/shift_rules.py
    hunks:
      - {old_start: 10, old_lines: 6, new_start: 10, new_lines: 8, seed: dup}
expected_readiness: ready
rationale: duplicate-id test fixture
"""
    (cases_dir / "a.yaml").write_text(case_body, encoding="utf-8")
    (cases_dir / "b.yaml").write_text(case_body, encoding="utf-8")
    result = _run(
        [
            "--cases-dir",
            str(cases_dir),
            "--fixtures-root",
            str(REAL_FIXTURES_ROOT),
            "--json-output",
            str(tmp_path / "summary.json"),
            "--markdown-output",
            str(tmp_path / "summary.md"),
        ]
    )
    assert result.returncode == 1
    assert "duplicate case_id" in result.stderr
