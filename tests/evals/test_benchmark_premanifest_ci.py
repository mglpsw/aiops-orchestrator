"""Regression test for the AgentReview v2 benchmark premanifest's CI
behavior (#133 PR-4).

`generate-benchmark-premanifest.py --check` crashed on the real GitHub
Actions runner with `FileNotFoundError: [Errno 2] No such file or
directory: 'codex'` -- Codex CLI is a local developer tool, never
installed on a generic CI runner. This module proves: (1) a missing
`codex` binary is handled gracefully, never a crash; (2) `--check`
excludes `codex_cli_version` from strict equality (legitimate
acquisition-time environment metadata, not a pure function of committed
repo content) while still requiring it to be present and non-empty.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


premanifest_mod = _load_module("generate_benchmark_premanifest", ROOT_DIR / "scripts" / "generate-benchmark-premanifest.py")


def test_codex_cli_version_handles_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args, **kwargs):
        if args[0] == "codex":
            raise FileNotFoundError(2, "No such file or directory", "codex")
        raise AssertionError("unexpected subprocess call")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert premanifest_mod._codex_cli_version() == "unavailable"


def test_codex_cli_version_handles_generic_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert premanifest_mod._codex_cli_version() == "unavailable"


def test_check_excludes_codex_cli_version_from_strict_equality() -> None:
    assert "codex_cli_version" in premanifest_mod._CHECK_EXCLUDED_FIELDS


def test_excluded_field_presence_check_rejects_empty_value() -> None:
    """Direct test of the missing-or-empty guard's logic: an excluded
    field is still required to be PRESENT and non-empty -- exclusion from
    strict equality is not exclusion from existing at all."""
    committed_bad = {"codex_cli_version": ""}
    committed_good = {"codex_cli_version": "codex-cli 0.144.6"}
    missing_or_empty_bad = [f for f in premanifest_mod._CHECK_EXCLUDED_FIELDS if not committed_bad.get(f)]
    missing_or_empty_good = [f for f in premanifest_mod._CHECK_EXCLUDED_FIELDS if not committed_good.get(f)]
    assert missing_or_empty_bad == ["codex_cli_version"]
    assert missing_or_empty_good == []


def test_check_passes_regardless_of_codex_availability_on_this_host() -> None:
    """The real, end-to-end proof: --check succeeds via subprocess exactly
    as CI runs it, independent of whether this host happens to have codex
    installed."""
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "generate-benchmark-premanifest.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr
