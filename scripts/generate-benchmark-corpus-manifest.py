#!/usr/bin/env python3
"""Generate/verify the AgentReview v2 benchmark corpus manifest (#88, rev. 4.1).

Aggregates lane-applicability for every case relevant to the benchmark:

- the 10 materialized, provider-reviewable cases under
  `evals/agent_review_v2/reviewable_corpus/<case_id>/` (read from each
  case's own `expected.yaml` + `applicability.yaml` -- never re-derived by
  hand here, to avoid two sources of truth drifting apart);
- the 5 historical Lane-1-only cases from
  `evals/agent_review_v2/cases/*.yaml` that are NOT provider-reviewable
  (`pipeline_integrity`, `identity_negative`, `transport_or_dlp_stop`
  families) -- declared statically below, since those cases carry no
  reviewable code and are never materialized.

Verifies, fail-closed, the rev. 4.1 determination-1 minimums:

    declared_minimum_semantic_positive: 6
    declared_minimum_semantic_safe_counterexample: 4
    declared_minimum_provider_applicable_total: 10
    severity_coverage: P1 >= 2, P2 >= 2, P0 quota none

Never invents a threshold: every number in the manifest is either a fixed
plan determination (documented as such) or counted directly from the
committed corpus.

Usage:
    python scripts/generate-benchmark-corpus-manifest.py            # write manifest
    python scripts/generate-benchmark-corpus-manifest.py --check    # verify byte-stability
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT_DIR / "evals" / "agent_review_v2" / "reviewable_corpus"
MANIFEST_PATH = CORPUS_DIR / "MANIFEST.yaml"

DECLARED_MINIMUM_SEMANTIC_POSITIVE = 6
DECLARED_MINIMUM_SEMANTIC_SAFE_COUNTEREXAMPLE = 4
DECLARED_MINIMUM_PROVIDER_APPLICABLE_TOTAL = 10

# The 5 historical Lane 1 cases that are NOT provider-reviewable. Declared
# statically because they carry no reviewable code (they exercise pipeline
# behavior directly via evals/agent_review_v2/cases/*.yaml, per the
# `cases/` vs `reviewable_corpus/` separation in rev. 4.1 determination 2).
# `not_applicable` here NEVER counts as a false negative in the benchmark
# report -- these cases simply do not ask a detection question.
LANE1_ONLY_CASES = {
    "aiops-contract-missing-must-review": {
        "family": "pipeline_integrity",
        "source": "evals/agent_review_v2/cases/aiops-contract-missing-must-review.yaml",
        "reason": (
            "coverage-without-promotion resolving to blocked_pipeline is a "
            "pipeline-binding condition, not a code-review finding"
        ),
    },
    "agentescala-contract-p3-finding-still-ready": {
        "family": "pipeline_integrity",
        "source": "evals/agent_review_v2/cases/agentescala-contract-p3-finding-still-ready.yaml",
        "reason": (
            "readiness precedence for a low-severity finding resolving to "
            "ready is pipeline synthesis behavior, not a code-review finding"
        ),
    },
    "aiops-contract-stale-head": {
        "family": "identity_negative",
        "source": "evals/agent_review_v2/cases/aiops-contract-stale-head.yaml",
        "reason": "stale identity is a pipeline-binding condition, not a code-review finding",
    },
    "interleitos-security-blocked-prior-to-response": {
        "family": "transport_or_dlp_stop",
        "source": "evals/agent_review_v2/cases/interleitos-security-blocked-prior-to-response.yaml",
        "reason": (
            "this case measures blocking BEFORE any provider call "
            "(provider_invocations_expected: 0), not detection recall"
        ),
    },
    "interleitos-domain-dlp-suspicion-manual-required": {
        "family": "transport_or_dlp_stop",
        "source": "evals/agent_review_v2/cases/interleitos-domain-dlp-suspicion-manual-required.yaml",
        "reason": (
            "model_uncertainty forcing manual_required is a pipeline "
            "limitation-handling condition, not a code-review finding "
            "(provider_invocations_expected: 0)"
        ),
    },
}

NOT_APPLICABLE_LANE_APPLICABILITY = {
    "lane1_pipeline": "applicable",
    "aiops_subject_projection": "applicable",
    "codex_local_detection": "not_applicable",
    "codex_github_detection": "not_applicable",
    "human_detection": "not_applicable",
}


class ManifestError(Exception):
    pass


def _load_materialized_cases() -> list[dict]:
    if not CORPUS_DIR.is_dir():
        raise ManifestError(f"no reviewable_corpus directory at {CORPUS_DIR}")
    cases = []
    for case_dir in sorted(CORPUS_DIR.iterdir()):
        if not case_dir.is_dir():
            continue
        expected_path = case_dir / "expected.yaml"
        applicability_path = case_dir / "applicability.yaml"
        if not expected_path.is_file() or not applicability_path.is_file():
            raise ManifestError(f"{case_dir.name}: missing expected.yaml or applicability.yaml")
        expected = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
        applicability = yaml.safe_load(applicability_path.read_text(encoding="utf-8"))
        cases.append({"expected": expected, "applicability": applicability})
    return cases


def _verify_minimums(materialized: list[dict]) -> dict:
    positives = [c for c in materialized if c["expected"]["family"] == "semantic_positive"]
    counterexamples = [c for c in materialized if c["expected"]["family"] == "semantic_safe_counterexample"]

    if len(positives) < DECLARED_MINIMUM_SEMANTIC_POSITIVE:
        raise ManifestError(
            f"only {len(positives)} semantic_positive cases materialized, "
            f"need >= {DECLARED_MINIMUM_SEMANTIC_POSITIVE}"
        )
    if len(counterexamples) < DECLARED_MINIMUM_SEMANTIC_SAFE_COUNTEREXAMPLE:
        raise ManifestError(
            f"only {len(counterexamples)} semantic_safe_counterexample cases materialized, "
            f"need >= {DECLARED_MINIMUM_SEMANTIC_SAFE_COUNTEREXAMPLE}"
        )
    if len(materialized) < DECLARED_MINIMUM_PROVIDER_APPLICABLE_TOTAL:
        raise ManifestError(
            f"only {len(materialized)} provider-applicable cases total, "
            f"need >= {DECLARED_MINIMUM_PROVIDER_APPLICABLE_TOTAL}"
        )

    severities = [c["expected"]["severity"] for c in positives]
    p0 = severities.count("P0")
    p1 = severities.count("P1")
    p2 = severities.count("P2")
    p3 = severities.count("P3")
    if p0 != 0:
        raise ManifestError(f"P0_quota is none, but {p0} P0 positive case(s) found")
    if p1 < 2:
        raise ManifestError(f"severity_coverage requires P1 >= 2, found {p1}")
    if p2 < 2:
        raise ManifestError(f"severity_coverage requires P2 >= 2, found {p2}")

    positive_targets = [c["expected"]["target"] for c in positives]
    counterexample_targets = [c["expected"]["target"] for c in counterexamples]

    return {
        "semantic_positive_count": len(positives),
        "semantic_positive_by_target": {
            "agent_escala": positive_targets.count("agent_escala"),
            "interleitos": positive_targets.count("interleitos"),
        },
        "semantic_positive_severity_coverage": {"P0": p0, "P1": p1, "P2": p2, "P3": p3},
        "semantic_safe_counterexample_count": len(counterexamples),
        "semantic_safe_counterexample_by_target": {
            "agent_escala": counterexample_targets.count("agent_escala"),
            "interleitos": counterexample_targets.count("interleitos"),
        },
        "provider_applicable_total": len(materialized),
    }


def build_manifest() -> dict:
    materialized = _load_materialized_cases()
    counts = _verify_minimums(materialized)

    provider_applicable_cases = []
    for c in sorted(materialized, key=lambda c: c["expected"]["case_id"]):
        exp = c["expected"]
        entry = {
            "case_id": exp["case_id"],
            "target": exp["target"],
            "family": exp["family"],
            "expected_readiness": exp["expected_readiness"],
            "lane_applicability": c["applicability"]["lane_applicability"],
        }
        if exp["family"] == "semantic_positive":
            entry["severity"] = exp["severity"]
        provider_applicable_cases.append(entry)

    lane1_only_cases = [
        {
            "case_id": case_id,
            "family": info["family"],
            "source": info["source"],
            "lane_applicability": NOT_APPLICABLE_LANE_APPLICABILITY,
            "reason": info["reason"],
        }
        for case_id, info in sorted(LANE1_ONLY_CASES.items())
    ]

    return {
        "schema_id": "agent-review.benchmark-corpus-manifest.v1",
        "declared_minimums": {
            "declared_minimum_semantic_positive": DECLARED_MINIMUM_SEMANTIC_POSITIVE,
            "declared_minimum_semantic_safe_counterexample": DECLARED_MINIMUM_SEMANTIC_SAFE_COUNTEREXAMPLE,
            "declared_minimum_provider_applicable_total": DECLARED_MINIMUM_PROVIDER_APPLICABLE_TOTAL,
            "severity_coverage_requirement": {"P1": "at_least_2", "P2": "at_least_2", "P0_quota": "none"},
        },
        "observed_counts": counts,
        "statistical_status": "descriptive_provisional_baseline",
        "promotion_authority": False,
        "provider_applicable_cases": provider_applicable_cases,
        "lane1_only_cases": lane1_only_cases,
        "not_applicable_never_counts_as_false_negative": True,
        "note": (
            "This manifest governs OP-BENCH (issue #88). "
            "evals/agent_review_v2/cases/ (the Lane 1 harness corpus) is "
            "never modified by this manifest or by anything under "
            "reviewable_corpus/ -- see rev. 4.1 determination 2."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify MANIFEST.yaml is byte-stable")
    args = parser.parse_args()

    try:
        manifest = build_manifest()
    except ManifestError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    rendered = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)

    if args.check:
        if not MANIFEST_PATH.is_file():
            print(f"FAIL: {MANIFEST_PATH} does not exist", file=sys.stderr)
            return 1
        committed = MANIFEST_PATH.read_text(encoding="utf-8")
        if committed != rendered:
            print("DRIFT: MANIFEST.yaml does not match a fresh generation", file=sys.stderr)
            return 1
        print(
            f"ok: MANIFEST.yaml is byte-identical "
            f"({manifest['observed_counts']['provider_applicable_total']} provider-applicable cases, "
            f"{len(manifest['lane1_only_cases'])} Lane-1-only cases)"
        )
        return 0

    MANIFEST_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {MANIFEST_PATH.relative_to(ROOT_DIR)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
