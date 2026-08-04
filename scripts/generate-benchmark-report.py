#!/usr/bin/env python3
"""Generate/verify the definitive AgentReview v2 benchmark report (#88, PR-2).

Reads ONLY already-committed, already-acquired artifacts:

    evals/agent_review_v2/reviewable_corpus/*/expected.yaml   (ground truth)
    evals/agent_review_v2/observations/aiops_pipeline/*.json  (OP-BENCH 2A)
    evals/agent_review_v2/observations/codex_local/*.json     (Lane 2)
    evals/agent_review_v2/observations/codex_github/*.json    (Lane 3)
    evals/agent_review_v2/benchmark_identity.final.json       (identity)

Never calls Codex or any provider -- this is the deterministic half of
"provider acquisition is external and registered once; report generation
from those observations is deterministic and reproducible" (rev. 4.1).

Validates every observation against the real, strict ExternalObservationV2
model before using it (fail-closed on a malformed committed artifact), and
uses the real, unmodified `correlate_observation_v2` for canonical
correlation -- this script adds no correlation logic of its own.

Separates PIPELINE CORRECTNESS (aiops_pipeline: the deterministic engine's
own behavior given an alleged finding) from DETECTION (codex_local_detection
/codex_github_detection: whether a real, independent observer found the
right defect) -- never emits aiops_precision/aiops_recall, per rev. 4.1's
explicit prohibition.

Usage:
    python scripts/generate-benchmark-report.py           # write report
    python scripts/generate-benchmark-report.py --check   # verify byte-stability
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from evals.agent_review_v2.observation import (  # noqa: E402
    AiopsFindingReferenceV2,
    ExternalObservationV2,
    correlate_observation_v2,
)

CORPUS_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "reviewable_corpus"
OBS_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "observations"
IDENTITY_FINAL_PATH = ROOT_DIR / "evals" / "agent_review_v2" / "benchmark_identity.final.json"
REPORT_JSON_PATH = ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.json"
REPORT_MD_PATH = ROOT_DIR / "reports" / "agent-review-v2-benchmark-summary.md"


class ReportError(Exception):
    pass


def _load_expected() -> dict[str, dict]:
    expected = {}
    for case_dir in sorted(CORPUS_DIR.iterdir()):
        if not case_dir.is_dir():
            continue
        exp_path = case_dir / "expected.yaml"
        if not exp_path.is_file():
            continue
        expected[case_dir.name] = yaml.safe_load(exp_path.read_text(encoding="utf-8"))
    return expected


def _load_aiops_pipeline() -> dict[str, dict]:
    out = {}
    for f in sorted((OBS_DIR / "aiops_pipeline").glob("*.json")):
        out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return out


def _load_lane_observations(lane: str) -> dict[str, list[ExternalObservationV2]]:
    """Load and STRICTLY VALIDATE every committed observation file for one
    lane. Fail-closed: a malformed file aborts report generation rather
    than being silently skipped."""
    out: dict[str, list[ExternalObservationV2]] = {}
    lane_dir = OBS_DIR / lane
    if not lane_dir.is_dir():
        raise ReportError(f"missing observations directory: {lane_dir}")
    for f in sorted(lane_dir.glob("*.json")):
        raw = json.loads(f.read_text(encoding="utf-8"))
        try:
            out[f.stem] = [ExternalObservationV2(**o) for o in raw]
        except ValidationError as exc:
            raise ReportError(f"{lane}/{f.name}: invalid ExternalObservationV2 record(s): {exc}") from exc
    return out


def _aiops_finding_refs_for_case(pipeline_entry: dict) -> list[AiopsFindingReferenceV2]:
    return [AiopsFindingReferenceV2(**fr) for fr in pipeline_entry["finding_references"]]


def _compute_aiops_pipeline_metrics(expected: dict, pipeline: dict) -> dict:
    total = len(expected)
    readiness_matches = sum(1 for c in pipeline.values() if c["readiness_matches"])
    positives = {cid: e for cid, e in expected.items() if e["family"] == "semantic_positive"}
    preserved = sum(1 for cid in positives if pipeline[cid]["expected_finding_preserved"])
    false_approvals = sum(1 for c in pipeline.values() if c["false_approval"])
    complete_assemblies = sum(1 for c in pipeline.values() if c["manifest_hash"])
    return {
        "readiness_accuracy": f"{readiness_matches}/{total}",
        "finding_preservation": f"{preserved}/{len(positives)}",
        "stale_rejection": "not_applicable -- no identity_negative case is provider_review_applicable "
        "(stale is a Lane 1/2A-only pipeline_integrity question, see MANIFEST.yaml lane1_only_cases)",
        "coverage_behavior": f"complete_manifest_assembly={complete_assemblies}/{total} (no blocked_pipeline outcome)",
        "false_approval_count": false_approvals,
    }


def _compute_detection_metrics(
    lane: str,
    expected: dict,
    pipeline: dict,
    observations: dict[str, list[ExternalObservationV2]],
) -> dict:
    positives = {cid: e for cid, e in expected.items() if e["family"] == "semantic_positive"}
    counterexamples = {cid: e for cid, e in expected.items() if e["family"] == "semantic_safe_counterexample"}

    location_tp = 0
    severity_exact = 0
    per_case = {}
    canonical_matched = canonical_rejected = canonical_inconclusive = 0

    for case_id, exp in positives.items():
        obs_list = observations.get(case_id, [])
        found_here = [o for o in obs_list if o.file_path == exp["file_path"]]
        case_result = {
            "location_match": bool(found_here),
            "severity_claimed": found_here[0].severity_claimed if found_here else None,
            "severity_expected": exp["severity"],
        }
        if found_here:
            location_tp += 1
            if found_here[0].severity_claimed == exp["severity"]:
                severity_exact += 1
        per_case[case_id] = case_result

        aiops_refs = _aiops_finding_refs_for_case(pipeline[case_id])
        for obs in obs_list:
            corr = correlate_observation_v2(obs, aiops_findings=aiops_refs)
            if corr.disposition == "matched":
                canonical_matched += 1
            elif corr.disposition == "inconclusive":
                canonical_inconclusive += 1
            else:
                canonical_rejected += 1

    false_positive_count = 0
    for case_id in counterexamples:
        false_positive_count += len(observations.get(case_id, []))

    location_recall_denominator = len(positives)
    return {
        "location_recall": f"{location_tp}/{location_recall_denominator}",
        "severity_exact_match_rate": f"{severity_exact}/{location_tp}" if location_tp else "0/0",
        "false_positive_count": false_positive_count,
        "false_positive_denominator": len(counterexamples),
        "canonical_correlation_via_correlate_observation_v2": {
            "matched": canonical_matched,
            "rejected": canonical_rejected,
            "inconclusive": canonical_inconclusive,
            "note": (
                "correlate_observation_v2 requires EXACT severity match (never just "
                "file_path) -- a location-correct finding whose claimed severity "
                "differs from the case's ground truth severity is 'rejected' by this "
                "canonical tool, not silently loosened here. See location_recall above "
                "for the file-level detection signal independent of severity calibration."
            ),
        },
        "denominator": location_recall_denominator,
        "sample_size_caveat": "statistical_status: descriptive_provisional_baseline -- "
        f"n={location_recall_denominator} positives, n={len(counterexamples)} counterexamples; "
        "no significance or generalization claim.",
        "per_case": per_case,
    }


def _compute_cross_source_overlap(expected: dict, codex_local: dict, codex_github: dict) -> dict:
    positives = [cid for cid, e in expected.items() if e["family"] == "semantic_positive"]
    both = 0
    local_only = 0
    github_only = 0
    neither = 0
    for case_id in positives:
        exp = expected[case_id]
        local_hit = any(o.file_path == exp["file_path"] for o in codex_local.get(case_id, []))
        github_hit = any(o.file_path == exp["file_path"] for o in codex_github.get(case_id, []))
        if local_hit and github_hit:
            both += 1
        elif local_hit:
            local_only += 1
        elif github_hit:
            github_only += 1
        else:
            neither += 1
    return {
        "canonical_expected_vs_codex": {
            "both_lanes_location_match": f"{both}/{len(positives)}",
            "codex_local_only": local_only,
            "codex_github_only": github_only,
            "neither_lane": neither,
        }
    }


def build_report() -> dict:
    expected = _load_expected()
    pipeline = _load_aiops_pipeline()
    codex_local = _load_lane_observations("codex_local")
    codex_github = _load_lane_observations("codex_github")

    if not IDENTITY_FINAL_PATH.is_file():
        raise ReportError(f"missing {IDENTITY_FINAL_PATH}")
    identity_final = json.loads(IDENTITY_FINAL_PATH.read_text(encoding="utf-8"))

    aiops_pipeline_metrics = _compute_aiops_pipeline_metrics(expected, pipeline)
    codex_local_detection = _compute_detection_metrics("codex_local", expected, pipeline, codex_local)
    codex_github_detection = _compute_detection_metrics("codex_github", expected, pipeline, codex_github)
    cross_source_overlap = _compute_cross_source_overlap(expected, codex_local, codex_github)

    return {
        "schema_id": "agent-review.benchmark-report.v1",
        "benchmark_run_id": identity_final["benchmark_run_id"],
        "source_master_sha": identity_final["source_master_sha"],
        "aiops_pipeline": aiops_pipeline_metrics,
        "codex_local_detection": codex_local_detection,
        "codex_github_detection": codex_github_detection,
        "cross_source_overlap": cross_source_overlap,
        "human_lane": identity_final["human_lane"],
        "codex_operational_eligibility": "eligible",
        "allowed_role": "shadow",
        "advisory_eligibility": "deferred_to_target_observation",
        "required_check_eligible": False,
        "readiness_authority": False,
        "statistical_status": "descriptive_provisional_baseline",
        "promotion_authority": False,
        "limitations": [
            "sample size is 6 semantic_positive / 4 semantic_safe_counterexample cases -- "
            "a descriptive baseline, not a statistically powered study",
            "correlate_observation_v2 requires exact severity match; codex_local's severity "
            "calibration diverged from ground truth on 3/6 positive cases in this run "
            "(all location-correct) -- see codex_local_detection.canonical_correlation... "
            "vs .location_recall for the split",
            "stale/pipeline_integrity/transport_or_dlp_stop cases are Lane 1/2A-only by "
            "design (see MANIFEST.yaml) and are excluded from these detection denominators",
            "Lane 4 (human) is deferred to RI-C/RI-D per the H2 disposition; no human "
            "precision/recall is reported",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# AgentReview v2 benchmark report (#88)",
        "",
        f"- **benchmark_run_id:** `{report['benchmark_run_id']}`",
        f"- **source_master_sha:** `{report['source_master_sha']}`",
        f"- **statistical_status:** {report['statistical_status']} (promotion_authority: {report['promotion_authority']})",
        "",
        "## aiops_pipeline (pipeline correctness, not detection)",
        "",
        f"- readiness_accuracy: {report['aiops_pipeline']['readiness_accuracy']}",
        f"- finding_preservation: {report['aiops_pipeline']['finding_preservation']}",
        f"- false_approval_count: {report['aiops_pipeline']['false_approval_count']}",
        f"- coverage_behavior: {report['aiops_pipeline']['coverage_behavior']}",
        f"- stale_rejection: {report['aiops_pipeline']['stale_rejection']}",
        "",
        "## codex_local_detection",
        "",
        f"- location_recall: {report['codex_local_detection']['location_recall']}",
        f"- severity_exact_match_rate: {report['codex_local_detection']['severity_exact_match_rate']}",
        f"- false_positive_count: {report['codex_local_detection']['false_positive_count']}"
        f" / {report['codex_local_detection']['false_positive_denominator']} counterexamples",
        f"- canonical correlation (correlate_observation_v2): "
        f"{report['codex_local_detection']['canonical_correlation_via_correlate_observation_v2']}",
        "",
        "## codex_github_detection",
        "",
        f"- location_recall: {report['codex_github_detection']['location_recall']}",
        f"- severity_exact_match_rate: {report['codex_github_detection']['severity_exact_match_rate']}",
        f"- false_positive_count: {report['codex_github_detection']['false_positive_count']}"
        f" / {report['codex_github_detection']['false_positive_denominator']} counterexamples",
        f"- canonical correlation (correlate_observation_v2): "
        f"{report['codex_github_detection']['canonical_correlation_via_correlate_observation_v2']}",
        "",
        "## cross_source_overlap",
        "",
        f"{report['cross_source_overlap']}",
        "",
        "## Disposition",
        "",
        f"- codex_operational_eligibility: {report['codex_operational_eligibility']}",
        f"- allowed_role: {report['allowed_role']}",
        f"- advisory_eligibility: {report['advisory_eligibility']}",
        f"- required_check_eligible: {report['required_check_eligible']}",
        f"- readiness_authority: {report['readiness_authority']}",
        "",
        "## human_lane",
        "",
        f"{report['human_lane']}",
        "",
        "## Limitations",
        "",
        *[f"- {item}" for item in report["limitations"]],
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify committed report is byte-stable")
    args = parser.parse_args()

    try:
        report = build_report()
    except ReportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    rendered_json = json.dumps(report, indent=2, sort_keys=False) + "\n"
    rendered_md = render_markdown(report)

    if args.check:
        errors = 0
        if not REPORT_JSON_PATH.is_file() or REPORT_JSON_PATH.read_text(encoding="utf-8") != rendered_json:
            print("DRIFT: agent-review-v2-benchmark-summary.json does not match a fresh generation", file=sys.stderr)
            errors += 1
        if not REPORT_MD_PATH.is_file() or REPORT_MD_PATH.read_text(encoding="utf-8") != rendered_md:
            print("DRIFT: agent-review-v2-benchmark-summary.md does not match a fresh generation", file=sys.stderr)
            errors += 1
        if errors:
            return 1
        print("ok: benchmark report is byte-identical to a fresh, deterministic recomputation from committed observations")
        return 0

    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(rendered_json, encoding="utf-8")
    REPORT_MD_PATH.write_text(rendered_md, encoding="utf-8")
    print(f"wrote {REPORT_JSON_PATH.relative_to(ROOT_DIR)} and {REPORT_MD_PATH.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
