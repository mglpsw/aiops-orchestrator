#!/usr/bin/env python3
"""Correlate external review observations against AIOps-canonical findings
(issue #88, "Correlação de findings").

Reads already-produced `ExternalObservationV2` JSON records (from Codex
local/CLI, Codex GitHub shadow review, or a human reviewer -- whichever
lanes a SEPARATE, properly authorized process already ran) and a JSON array
of AIOps-canonical finding references for the same repo/PR/HEAD, and
reports a location-based correlation (matched/rejected/inconclusive) for
benchmark/telemetry purposes only.

This script never runs Codex, never calls a provider, never touches
GitHub, and never writes back into any `FindingLifecycleRecordV2` or
`ReviewReadinessV2`. Per the issue's own rule, a `matched` correlation here
NEVER means "confirmed" in the AIOps lifecycle sense -- it is a reporting
signal, nothing more.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from evals.agent_review_v2.observation import (  # noqa: E402
    AiopsFindingReferenceV2,
    CorrelationResultV2,
    ExternalObservationV2,
    correlate_observation_v2,
)

INPUT_INVALID_REASON_V2 = "observation_input_invalid"


class CompareObservationsError(ValueError):
    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--observations", required=True, help="path to a JSON array of ExternalObservationV2 records"
    )
    parser.add_argument(
        "--aiops-findings", required=True, help="path to a JSON array of AiopsFindingReferenceV2 records"
    )
    parser.add_argument("--output", required=True, help="path to write the correlation report JSON")
    return parser.parse_args(argv)


def _read_json_array(path: str) -> list:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompareObservationsError(INPUT_INVALID_REASON_V2, str(exc)) from exc
    if not isinstance(raw, list):
        raise CompareObservationsError(INPUT_INVALID_REASON_V2, f"{path} must contain a JSON array")
    return raw


def _load_observations(path: str) -> list[ExternalObservationV2]:
    raw = _read_json_array(path)
    try:
        return [ExternalObservationV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise CompareObservationsError(INPUT_INVALID_REASON_V2, str(exc)) from exc


def _load_aiops_findings(path: str) -> list[AiopsFindingReferenceV2]:
    raw = _read_json_array(path)
    try:
        return [AiopsFindingReferenceV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise CompareObservationsError(INPUT_INVALID_REASON_V2, str(exc)) from exc


def _result_to_dict(result: CorrelationResultV2) -> dict:
    return {
        "source": result.source,
        "file_path": result.file_path,
        "severity_claimed": result.severity_claimed,
        "disposition": result.disposition,
        "matched_finding_id": result.matched_finding_id,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        observations = _load_observations(args.observations)
        aiops_findings = _load_aiops_findings(args.aiops_findings)
    except CompareObservationsError as exc:
        detail = f" ({exc.detail})" if exc.detail else ""
        print(f"error: {exc.reason_code}{detail}", file=sys.stderr)
        return 1

    results = [correlate_observation_v2(obs, aiops_findings=aiops_findings) for obs in observations]

    by_source: dict[str, dict[str, int]] = {}
    for result in results:
        counts = by_source.setdefault(result.source, {"matched": 0, "rejected": 0, "inconclusive": 0})
        counts[result.disposition] += 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "schema_id": "agent-review.v2-observation-correlation",
                "total_observations": len(results),
                "by_source": by_source,
                "results": [_result_to_dict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"ok: {len(results)} observations correlated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
