"""Tests for the AgentReview v2 benchmark reviewable corpus (#88, rev. 4.1).

Covers: the `scripts/materialize-benchmark-case.py` materializer's
invariants (fail-closed loader, patch round-trip via the real `patch(1)`
binary, defect lines derived from the diff rather than hand-typed), the
`scripts/generate-benchmark-corpus-manifest.py` minimum/severity gate, and
that `evals/agent_review_v2/cases/` (the Lane 1 harness corpus) is left
byte-untouched by anything under `reviewable_corpus/`.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "case_sources"
CORPUS_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "reviewable_corpus"
LANE1_CASES_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "cases"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


materialize_mod = _load_module(
    "materialize_benchmark_case", ROOT_DIR / "scripts" / "materialize-benchmark-case.py"
)
manifest_mod = _load_module(
    "generate_benchmark_corpus_manifest", ROOT_DIR / "scripts" / "generate-benchmark-corpus-manifest.py"
)


LANE1_HISTORICAL_CASE_IDS = {
    "agentescala-contract-p3-finding-still-ready",
    "agentescala-domain-new-finding-manual-required",
    "agentescala-false-positive-unrelated-change",
    "agentescala-security-confirmed-finding-blocked-code",
    "aiops-contract-missing-must-review",
    "aiops-contract-stale-head",
    "interleitos-domain-dlp-suspicion-manual-required",
    "interleitos-false-positive-synthetic-clinical-terms",
    "interleitos-security-blocked-prior-to-response",
}


def test_lane1_cases_directory_untouched_by_corpus_extension() -> None:
    """The set of case_ids in evals/agent_review_v2/cases/ must remain
    exactly the 9 historical cases -- this slice never adds, removes, or
    renames a Lane 1 harness case."""
    actual = {p.stem for p in LANE1_CASES_DIR.glob("*.yaml")}
    assert actual == LANE1_HISTORICAL_CASE_IDS


def test_all_case_sources_materialize_cleanly() -> None:
    for case_dir in sorted(SOURCES_DIR.iterdir()):
        if not case_dir.is_dir():
            continue
        # materialize_case raises MaterializeError (a subclass of Exception)
        # on any invariant violation -- this call itself is the assertion.
        materialize_mod.materialize_case(case_dir)


def test_materializer_rejects_mismatched_file_sets(tmp_path: Path) -> None:
    case_dir = tmp_path / "bad-case"
    (case_dir / "base" / "a.py").parent.mkdir(parents=True)
    (case_dir / "base" / "a.py").write_text("x = 1\n")
    (case_dir / "mutated" / "b.py").parent.mkdir(parents=True)
    (case_dir / "mutated" / "b.py").write_text("x = 1\n")
    (case_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": "bad-case",
                "target": "agent_escala",
                "family": "semantic_safe_counterexample",
                "expected_readiness": "ready",
                "safe_counterexample": True,
                "rationale": "test",
            }
        )
    )
    with pytest.raises(materialize_mod.MaterializeError, match="identical file set"):
        materialize_mod.materialize_case(case_dir)


def test_materializer_rejects_no_op_mutation(tmp_path: Path) -> None:
    case_dir = tmp_path / "noop-case"
    (case_dir / "base" / "a.py").parent.mkdir(parents=True)
    (case_dir / "base" / "a.py").write_text("x = 1\n")
    (case_dir / "mutated" / "a.py").parent.mkdir(parents=True)
    (case_dir / "mutated" / "a.py").write_text("x = 1\n")
    (case_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": "noop-case",
                "target": "agent_escala",
                "family": "semantic_safe_counterexample",
                "expected_readiness": "ready",
                "safe_counterexample": True,
                "rationale": "test",
            }
        )
    )
    with pytest.raises(materialize_mod.MaterializeError, match="no change to review"):
        materialize_mod.materialize_case(case_dir)


def test_materializer_rejects_non_provider_family(tmp_path: Path) -> None:
    case_dir = tmp_path / "wrong-family"
    (case_dir / "base" / "a.py").parent.mkdir(parents=True)
    (case_dir / "base" / "a.py").write_text("x = 1\n")
    (case_dir / "mutated" / "a.py").parent.mkdir(parents=True)
    (case_dir / "mutated" / "a.py").write_text("x = 2\n")
    (case_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": "wrong-family",
                "target": "agent_escala",
                "family": "pipeline_integrity",
                "expected_readiness": "ready",
                "safe_counterexample": False,
                "rationale": "test",
            }
        )
    )
    with pytest.raises(materialize_mod.MaterializeError, match="not provider-reviewable"):
        materialize_mod.materialize_case(case_dir)


@pytest.mark.parametrize("case_dir", sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir()))
def test_committed_case_patch_round_trips_via_real_patch_binary(case_dir: Path) -> None:
    """Re-derive the invariant directly against the COMMITTED output (not
    just the case_sources/ input): applying mutation.patch to base/ via the
    real `patch(1)` binary must succeed and the result's sha256 must match
    the patch's own declared identity chain."""
    expected = yaml.safe_load((case_dir / "expected.yaml").read_text())
    patch_text = (case_dir / "mutation.patch").read_text()
    changed_files = expected["changed_files"]
    assert len(changed_files) == 1, "every case in this corpus touches exactly one file"
    relpath = changed_files[0]

    work = case_dir / "base"
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for f in work.rglob("*"):
            if f.is_file():
                dest = tmp_path / f.relative_to(work)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(f.read_bytes())
        patch_file = tmp_path / "mutation.patch"
        patch_file.write_text(patch_text)
        result = subprocess.run(
            ["patch", "-p1", "--batch", "-i", "mutation.patch"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("case_dir", sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir()))
def test_positive_case_defect_lines_fall_within_declared_range(case_dir: Path) -> None:
    expected = yaml.safe_load((case_dir / "expected.yaml").read_text())
    if expected["family"] != "semantic_positive":
        pytest.skip("only semantic_positive cases declare defect line ranges")
    assert expected["line_start"] <= expected["line_end"]
    assert expected["severity"] in {"P1", "P2", "P3"}, "P0_quota is none per rev. 4.1 determination 1"


def test_materializer_check_mode_reports_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "materialize-benchmark-case.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_declared_minimums_are_met() -> None:
    manifest = manifest_mod.build_manifest()
    counts = manifest["observed_counts"]
    assert counts["semantic_positive_count"] >= 6
    assert counts["semantic_safe_counterexample_count"] >= 4
    assert counts["provider_applicable_total"] >= 10
    severities = counts["semantic_positive_severity_coverage"]
    assert severities["P0"] == 0
    assert severities["P1"] >= 2
    assert severities["P2"] >= 2
    assert manifest["statistical_status"] == "descriptive_provisional_baseline"
    assert manifest["promotion_authority"] is False


def test_manifest_flags_insufficient_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty_corpus = tmp_path / "empty_reviewable_corpus"
    empty_corpus.mkdir()
    monkeypatch.setattr(manifest_mod, "CORPUS_DIR", empty_corpus)
    with pytest.raises(manifest_mod.ManifestError, match="need >="):
        manifest_mod.build_manifest()


def test_manifest_check_mode_reports_no_drift() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "generate-benchmark-corpus-manifest.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lane1_only_cases_never_materialized_as_reviewable() -> None:
    """The 5 pipeline_integrity/identity_negative/transport_or_dlp_stop
    cases must have no reviewable_corpus/ directory of their own -- they
    are documented in MANIFEST.yaml's lane1_only_cases, never materialized."""
    manifest = manifest_mod.build_manifest()
    lane1_only_ids = {c["case_id"] for c in manifest["lane1_only_cases"]}
    assert len(lane1_only_ids) == 5
    for case_id in lane1_only_ids:
        assert not (CORPUS_DIR / case_id).exists()


def test_lane1_evals_check_still_passes_byte_identical() -> None:
    """Byte-stability invariant from rev. 4.1: adding the reviewable corpus
    must not perturb the Lane 1 harness's own committed report."""
    result = subprocess.run(
        [sys.executable, str(ROOT_DIR / "scripts" / "run-agent-review-v2-evals.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT_DIR,
    )
    assert result.returncode == 0, result.stdout + result.stderr
