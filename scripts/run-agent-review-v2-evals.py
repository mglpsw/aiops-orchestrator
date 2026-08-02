#!/usr/bin/env python3
"""Run the AgentReview v2 benchmark corpus, Lane 1 only (issue #88).

Lane 1 -- "AIOps v2 deterministic/offline" -- is the only lane this script
executes. It loads every `evals/agent_review_v2/cases/*.yaml` case, runs it
through the real, already-merged v2 pipeline (`evals.agent_review_v2
.harness.run_eval_case_v2`), and writes a deterministic JSON + Markdown
summary. No network, no real provider, no GitHub write, no CT102.

Lanes 2 (Codex local/CLI) and 3 (Codex GitHub shadow) are NOT executed by
this script -- invoking a real Codex CLI or the GitHub shadow-review
surface is a real provider call, which is a protected action outside this
offline slice's own grant (see docs/AGENT_REVIEW_V2_BENCHMARK.md's
"deliberately not executed" section). `scripts/compare-review-observations
.py` accepts already-produced observation records from those lanes,
whenever a separate, properly authorized run has produced them, and
correlates them against this script's own AIOps-lane findings -- it never
runs Codex itself either.

Lane 4 (human review) is inherently manual; this script does not, and
cannot, fabricate one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.agent_review_v2.harness import (  # noqa: E402
    EvalCaseError,
    EvalCaseResultV2,
    EvalCaseV2,
    compute_eval_summary_v2,
    run_eval_case_v2,
)

DEFAULT_CASES_DIR = REPO_ROOT / "evals" / "agent_review_v2" / "cases"
DEFAULT_FIXTURES_ROOT = REPO_ROOT / "tests" / "agent_review" / "fixtures" / "v2"
_EVAL_HEAD_SHA = "2" * 40

CASE_LOAD_FAILED_REASON_V2 = "eval_case_load_failed"


class EvalRunnerError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--fixtures-root", default=str(DEFAULT_FIXTURES_ROOT))
    parser.add_argument("--json-output", default=str(REPO_ROOT / "reports" / "agent-review-v2-eval-summary.json"))
    parser.add_argument("--markdown-output", default=str(REPO_ROOT / "reports" / "agent-review-v2-eval-summary.md"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the fresh run against the existing --json-output file (ignoring duration_ms/"
        "total_duration_ms, which legitimately vary run to run) instead of overwriting it",
    )
    return parser.parse_args(argv)


def _load_cases(cases_dir: Path) -> list[EvalCaseV2]:
    case_files = sorted(cases_dir.glob("*.yaml"))
    if not case_files:
        raise EvalRunnerError(CASE_LOAD_FAILED_REASON_V2, f"no case files found under {cases_dir}")
    cases: list[EvalCaseV2] = []
    seen_ids: set[str] = set()
    for path in case_files:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise EvalRunnerError(CASE_LOAD_FAILED_REASON_V2, f"{path}: {exc}") from exc
        try:
            case = EvalCaseV2.model_validate(raw)
        except Exception as exc:  # pydantic.ValidationError, kept broad only for a clear CLI message
            raise EvalRunnerError(CASE_LOAD_FAILED_REASON_V2, f"{path}: {exc}") from exc
        if case.case_id in seen_ids:
            raise EvalRunnerError(CASE_LOAD_FAILED_REASON_V2, f"duplicate case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    return cases


def _result_to_dict(result: EvalCaseResultV2) -> dict:
    return {
        "case_id": result.case_id,
        "category": result.category,
        "actual_readiness": result.actual_readiness,
        "expected_readiness": result.expected_readiness,
        "readiness_matches": result.readiness_matches,
        "expected_findings_recovered": result.expected_findings_recovered,
        "expected_findings_total": result.expected_findings_total,
        "forbidden_findings_leaked": result.forbidden_findings_leaked,
        "manifest_hash": result.manifest_hash,
        "payload_hashes": list(result.payload_hashes),
        "duration_ms": round(result.duration_ms, 3),
        "chunk_count": result.chunk_count,
        "fragment_count": result.fragment_count,
        "blocked_at_assembly": result.blocked_at_assembly,
        "blocked_reason_code": result.blocked_reason_code,
        "error": result.error,
    }


def _summary_dict(results: list[EvalCaseResultV2], summary) -> dict:
    return {
        "schema_id": "agent-review.v2-eval-summary",
        "lane": "aiops_v2_deterministic_offline",
        "cases": [_result_to_dict(r) for r in results],
        "summary": {
            "total_cases": summary.total_cases,
            "readiness_matches": summary.readiness_matches,
            "readiness_mismatches": list(summary.readiness_mismatches),
            "false_approvals": list(summary.false_approvals),
            "stale_cases_total": summary.stale_cases_total,
            "stale_cases_correct": summary.stale_cases_correct,
            "expected_findings_total": summary.expected_findings_total,
            "expected_findings_recovered": summary.expected_findings_recovered,
            "forbidden_findings_leaked_total": summary.forbidden_findings_leaked_total,
            "total_duration_ms": round(summary.total_duration_ms, 3),
        },
    }


def _without_durations(payload: dict) -> dict:
    stripped = json.loads(json.dumps(payload))
    for case in stripped["cases"]:
        case.pop("duration_ms", None)
    stripped["summary"].pop("total_duration_ms", None)
    return stripped


def _render_markdown(results: list[EvalCaseResultV2], summary) -> str:
    lines = [
        "# AgentReview v2 benchmark summary (Lane 1 -- AIOps deterministic/offline)",
        "",
        f"- total cases: {summary.total_cases}",
        f"- readiness matches: {summary.readiness_matches}/{summary.total_cases}",
        f"- false approvals (critical KPI): {len(summary.false_approvals)}",
        f"- stale cases correctly rejected: {summary.stale_cases_correct}/{summary.stale_cases_total}",
        f"- expected findings recovered: {summary.expected_findings_recovered}/{summary.expected_findings_total}",
        f"- forbidden findings leaked: {summary.forbidden_findings_leaked_total}",
        f"- total duration: {summary.total_duration_ms:.1f} ms",
        "",
        "| case_id | category | expected | actual | match | findings | forbidden leaked |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.case_id} | {r.category} | {r.expected_readiness} | {r.actual_readiness} | "
            f"{'yes' if r.readiness_matches else 'NO'} | {r.expected_findings_recovered}/{r.expected_findings_total} | "
            f"{r.forbidden_findings_leaked} |"
        )
    if summary.readiness_mismatches:
        lines += ["", "## Readiness mismatches", ""] + [f"- {c}" for c in summary.readiness_mismatches]
    if summary.false_approvals:
        lines += ["", "## False approvals", ""] + [f"- {c}" for c in summary.false_approvals]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = _load_cases(Path(args.cases_dir))
        results = [
            run_eval_case_v2(case, fixtures_root=Path(args.fixtures_root), head_sha=_EVAL_HEAD_SHA)
            for case in cases
        ]
    except EvalRunnerError as exc:
        detail = f" ({exc.detail})" if exc.detail else ""
        print(f"error: {exc.reason_code}{detail}", file=sys.stderr)
        return 1
    except EvalCaseError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1

    summary = compute_eval_summary_v2(results)
    fresh_payload = _summary_dict(results, summary)
    json_output = Path(args.json_output)

    if args.check:
        if not json_output.is_file():
            print(f"error: eval_summary_missing ({json_output} does not exist)", file=sys.stderr)
            return 1
        try:
            existing_payload = json.loads(json_output.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"error: eval_summary_invalid ({exc})", file=sys.stderr)
            return 1
        if _without_durations(existing_payload) != _without_durations(fresh_payload):
            print("error: eval_summary_drift (committed report no longer matches a fresh run)", file=sys.stderr)
            return 1
        print(f"ok: {json_output} matches a fresh run (durations excluded)")
        return 0

    json_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(fresh_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    markdown_output = Path(args.markdown_output)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(_render_markdown(results, summary), encoding="utf-8")

    print(f"ok: {summary.total_cases} cases, {summary.readiness_matches} readiness matches")
    if summary.false_approvals or summary.readiness_mismatches or summary.forbidden_findings_leaked_total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
