"""Tests for the AgentReview v2 benchmark corpus safety gate (#88, rev. 4.1 §9).

Proves the gate FAILS (not silently sanitizes) on planted secrets, local
paths, and real-world personal identifiers, passes clean fabricated
technical vocabulary, and that the real, committed corpus is clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from evals.agent_review_v2.corpus_safety import (
    SafetyReport,
    detect_identifiers,
    validate_text,
    validate_tree,
)

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_secret_assignment_is_flagged_not_sanitized() -> None:
    report = SafetyReport()
    validate_text("f.py", "OPENAI_KEY = 'sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890abcd'", report)
    assert not report.ok
    assert report.findings[0].category == "secret_or_path"


def test_local_absolute_path_is_flagged() -> None:
    report = SafetyReport()
    validate_text("f.py", "loaded from /home/miguel/aiops-orchestrator/secrets.env", report)
    assert not report.ok
    assert report.findings[0].category == "secret_or_path"


def test_valid_cpf_is_flagged() -> None:
    # 529.982.247-25 has valid CPF check digits (a well-known synthetic
    # test CPF, never a real person's document).
    report = SafetyReport()
    validate_text("f.py", "cpf 529.982.247-25", report)
    assert not report.ok
    assert report.findings[0].category == "cpf"


def test_bare_11_digit_number_without_valid_check_digits_is_not_flagged_as_cpf() -> None:
    # An arbitrary 11-digit number that does NOT satisfy the CPF check-digit
    # algorithm must not be flagged -- avoids drowning real findings in
    # false positives from unrelated numeric literals.
    findings = detect_identifiers("some_id = 12345678901")
    assert not any(cat == "cpf" for cat, _ in findings)


def test_cns_shaped_number_is_flagged() -> None:
    report = SafetyReport()
    validate_text("f.py", "cns 700123456789012", report)
    assert not report.ok
    assert report.findings[0].category == "cns"


def test_phone_number_is_flagged() -> None:
    report = SafetyReport()
    validate_text("f.py", "call (11) 91234-5678", report)
    assert not report.ok
    assert any(f.category == "phone" for f in report.findings)


def test_email_is_flagged() -> None:
    report = SafetyReport()
    validate_text("f.py", "contact test.user@example.com", report)
    assert not report.ok
    assert any(f.category == "email" for f in report.findings)


def test_fabricated_technical_vocabulary_is_not_flagged() -> None:
    """The gate must NOT ban clinical-sounding or domain-sounding
    fabricated vocabulary -- only identifier shapes and unredacted
    secrets/paths. Banning "anything clinical-sounding" would destroy the
    safe counterexample cases that exist specifically to prove this."""
    report = SafetyReport()
    validate_text(
        "f.py",
        "condition_code_x, care_team_ref, encounter_id, clinical, regulatory, "
        "logistics, communication axes",
        report,
    )
    assert report.ok


def test_committed_corpus_is_clean() -> None:
    for subdir in ("case_sources", "reviewable_corpus"):
        target = ROOT_DIR / "evals" / "agent_review_v2" / subdir
        if not target.is_dir():
            continue
        report = validate_tree(target)
        assert report.ok, [f"{f.path} [{f.category}]: {f.detail}" for f in report.findings]


def test_cli_exits_zero_on_clean_corpus() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "validate-benchmark-corpus-safety.py")],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cli_exits_nonzero_on_planted_violation(tmp_path: Path, monkeypatch) -> None:
    bad_dir = tmp_path / "case_sources"
    bad_dir.mkdir()
    (bad_dir / "leak.py").write_text("EMAIL = 'real.person@example.com'\n")

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "validate_benchmark_corpus_safety", ROOT_DIR / "scripts" / "validate-benchmark-corpus-safety.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "TARGET_DIRS", (bad_dir,))
    assert mod.main() == 1
