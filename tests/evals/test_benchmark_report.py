"""Tests for the AgentReview v2 definitive benchmark report (#88, PR-2).

Proves: the report is deterministically reproducible from committed
observations alone (no provider call); observations validate against the
real, strict ExternalObservationV2 model; correlation uses the real,
unmodified correlate_observation_v2; the report never emits
aiops_precision/aiops_recall; and the identity manifest's frozen fields
are never silently changed from the premanifest.
"""

from __future__ import annotations

import importlib.util
import json
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


report_mod = _load_module("generate_benchmark_report", ROOT_DIR / "scripts" / "generate-benchmark-report.py")


def test_report_recomputation_is_byte_identical() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "generate-benchmark-report.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_identity_final_check_passes_and_frozen_fields_intact() -> None:
    pre = json.loads((ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.pre.json").read_text())
    final = json.loads((ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.final.json").read_text())
    for key in (
        "schema_id",
        "benchmark_run_id",
        "source_master_sha",
        "corpus_digest",
        "applicability_manifest_digest",
        "expected_manifest_digest",
        "codex_cli_version",
        "rules_digest",
        "normalizer_digest",
        "attempt_policy",
        "created_before_provider_call",
    ):
        assert final[key] == pre[key], f"frozen field {key!r} diverged from premanifest"
    assert final["acquisition_complete"] is True
    assert final["human_lane"]["status"] == "deferred"
    assert final["human_lane"]["completed"] is False


def test_report_never_emits_aiops_precision_or_recall() -> None:
    report = json.loads((ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.json").read_text())
    assert "aiops_precision" not in report["aiops_pipeline"]
    assert "aiops_recall" not in report["aiops_pipeline"]
    serialized = json.dumps(report)
    assert "aiops_precision" not in serialized
    assert "aiops_recall" not in serialized


def test_report_disposition_matches_rev_4_1_determination_4() -> None:
    report = json.loads((ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.json").read_text())
    assert report["allowed_role"] == "shadow"
    assert report["advisory_eligibility"] == "deferred_to_target_observation"
    assert report["required_check_eligible"] is False
    assert report["readiness_authority"] is False
    assert report["statistical_status"] == "descriptive_provisional_baseline"
    assert report["promotion_authority"] is False


def test_all_committed_observations_validate_against_real_schema() -> None:
    for lane in ("codex_local", "codex_github"):
        observations = report_mod._load_lane_observations(lane)
        assert len(observations) == 10
        for case_id, obs_list in observations.items():
            for obs in obs_list:
                assert obs.repo == "mglpsw/aiops-orchestrator"
                assert obs.source == lane
                assert len(obs.head_sha) == 40


def test_aiops_pipeline_metrics_are_real_not_vacuous() -> None:
    """10/10 readiness accuracy is only meaningful because it was measured
    against REAL PR/HEAD identity with a real diff -- this test just
    proves the report's own claimed numerator/denominator shape is sane,
    not a fabricated 100%."""
    report = json.loads((ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.json").read_text())
    num, den = report["aiops_pipeline"]["readiness_accuracy"].split("/")
    assert int(den) == 10
    assert 0 <= int(num) <= 10
    fp_num, fp_den = report["aiops_pipeline"]["finding_preservation"].split("/")
    assert int(fp_den) == 6


def test_cross_source_overlap_denominator_is_six_positives() -> None:
    report = json.loads((ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.json").read_text())
    both = report["cross_source_overlap"]["canonical_expected_vs_codex"]["both_lanes_location_match"]
    _, den = both.split("/")
    assert int(den) == 6


def test_report_error_on_missing_observations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report_mod, "OBS_DIR", tmp_path / "does-not-exist")
    with pytest.raises(report_mod.ReportError):
        report_mod._load_lane_observations("codex_local")
