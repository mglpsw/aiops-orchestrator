#!/usr/bin/env python3
"""CLI for the AgentReview v2 benchmark corpus safety gate (#88, rev. 4.1 §9).

Runs `evals.agent_review_v2.corpus_safety.validate_tree` against
`evals/agent_review_v2/case_sources/` and
`evals/agent_review_v2/reviewable_corpus/`, and exits non-zero on any
finding. This gate protects the SYNTHETIC benchmark corpus of issue #88
only -- it is NOT the DLP/PHI gate for InterLeitos production traffic and
NOT the RI-B0 #160 gate.

Usage:
    python scripts/validate-benchmark-corpus-safety.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from evals.agent_review_v2.corpus_safety import validate_tree  # noqa: E402

TARGET_DIRS = (
    ROOT_DIR / "evals" / "agent_review_v2" / "case_sources",
    ROOT_DIR / "evals" / "agent_review_v2" / "reviewable_corpus",
    ROOT_DIR / "evals" / "agent_review_v2" / "observations",
)


def _display_path(target: Path) -> str:
    try:
        return str(target.relative_to(ROOT_DIR))
    except ValueError:
        return str(target)


def main() -> int:
    errors = 0
    for target in TARGET_DIRS:
        if not target.is_dir():
            print(f"SKIP: {target} does not exist", file=sys.stderr)
            continue
        report = validate_tree(target)
        label = _display_path(target)
        if report.ok:
            print(f"ok: {label} is clean (no secrets/paths/identifiers)")
            continue
        errors += len(report.findings)
        for finding in report.findings:
            print(
                f"FAIL {label}/{finding.path} [{finding.category}]: {finding.detail}",
                file=sys.stderr,
            )
    if errors:
        print(f"\nbenchmark corpus safety gate: {errors} finding(s) -- FAILED", file=sys.stderr)
        return 1
    print("\nbenchmark corpus safety gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
