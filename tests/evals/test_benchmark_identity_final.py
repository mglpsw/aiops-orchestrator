"""Regression tests for generate-benchmark-identity-final.py's --check
(#133 PR-4), proving two real findings from a Codex GitHub review of that
exact PR are genuinely caught, not merely documented as fixed:

1. Dropping a planned case from real_acquisition (while leaving the
   remaining structure valid) must fail --check, not silently pass because
   "real_acquisition is non-empty."
2. A corrupted field VALUE (wrong repo, non-positive pr_number, malformed
   patch_digest, empty attempts_used/retries) must fail --check, not
   silently pass because the key merely exists.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


identity_mod = _load_module("generate_benchmark_identity_final", ROOT_DIR / "scripts" / "generate-benchmark-identity-final.py")


@pytest.fixture()
def real_pre_and_final() -> tuple[dict, dict]:
    pre = json.loads((ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.pre.json").read_text())
    final = json.loads((ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.final.json").read_text())
    return pre, final


def test_real_committed_file_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_pre_and_final: tuple[dict, dict]) -> None:
    assert identity_mod.run_check() == 0


def test_dropped_planned_case_is_caught(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_pre_and_final: tuple[dict, dict], capsys: pytest.CaptureFixture) -> None:
    pre, final = real_pre_and_final
    corrupted = dict(final)
    corrupted["real_acquisition"] = dict(final["real_acquisition"])
    dropped_case = next(iter(corrupted["real_acquisition"]))
    del corrupted["real_acquisition"][dropped_case]

    fake_final = tmp_path / "final.json"
    fake_final.write_text(json.dumps(corrupted))
    monkeypatch.setattr(identity_mod, "FINAL_PATH", fake_final)

    assert identity_mod.run_check() == 1
    captured = capsys.readouterr()
    assert "missing planned case" in captured.err
    assert dropped_case in captured.err


@pytest.mark.parametrize(
    "field,bad_value,expected_error_fragment",
    [
        ("repo", "wrong/repo", "repo is"),
        ("pr_number", None, "pr_number is not a positive int"),
        ("pr_number", -5, "pr_number is not a positive int"),
        ("patch_digest", "not-a-digest", "patch_digest does not match"),
        ("base_ref", "", "base_ref is empty"),
        ("head_ref", "", "head_ref is empty"),
    ],
)
def test_corrupted_field_value_is_caught(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    real_pre_and_final: tuple[dict, dict],
    capsys: pytest.CaptureFixture,
    field: str,
    bad_value,
    expected_error_fragment: str,
) -> None:
    pre, final = real_pre_and_final
    corrupted = dict(final)
    corrupted["real_acquisition"] = {k: dict(v) for k, v in final["real_acquisition"].items()}
    target_case = next(iter(corrupted["real_acquisition"]))
    corrupted["real_acquisition"][target_case][field] = bad_value

    fake_final = tmp_path / "final.json"
    fake_final.write_text(json.dumps(corrupted))
    monkeypatch.setattr(identity_mod, "FINAL_PATH", fake_final)

    assert identity_mod.run_check() == 1
    captured = capsys.readouterr()
    assert expected_error_fragment in captured.err


def test_empty_attempts_used_is_caught(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_pre_and_final: tuple[dict, dict], capsys: pytest.CaptureFixture) -> None:
    pre, final = real_pre_and_final
    corrupted = dict(final)
    corrupted["real_acquisition"] = {k: dict(v) for k, v in final["real_acquisition"].items()}
    target_case = next(iter(corrupted["real_acquisition"]))
    corrupted["real_acquisition"][target_case]["attempts_used"] = {}

    fake_final = tmp_path / "final.json"
    fake_final.write_text(json.dumps(corrupted))
    monkeypatch.setattr(identity_mod, "FINAL_PATH", fake_final)

    assert identity_mod.run_check() == 1
    captured = capsys.readouterr()
    assert "attempts_used missing" in captured.err
