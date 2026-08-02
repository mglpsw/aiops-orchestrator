#!/usr/bin/env python3
"""Emit a real AgentReview v2 ReviewReadinessV2 artifact (issue #130).

Reads an already-computed C1 readiness decision (state/reason_codes/
blockers/coverage/pipeline -- see
app.agent_review.readiness_decision_v2.compute_readiness_decision_v2),
identity/evaluated-identity, findings, pr_state, and checks, and emits the
resulting ReviewReadinessV2 -- fail-closed if the combination does not
satisfy ReviewReadinessV2.validate_state_invariants (the sole authority;
this CLI never re-implements it, see
app.agent_review.review_readiness_emission_v2).

--contract-version v2 is required and explicit, per the CLI naming
decision registered in #102: a NEW v2 CLI script, using the "-v2" suffix
convention to avoid colliding with the existing v1
scripts/aiops-review-quality-gate.py (untouched by this issue).

If --payload and --response are both supplied, they are checked with
app.agent_review.versioning.select_contract_version BEFORE anything else
runs -- closing the "sem call site em produção" gap that function's own
module docstring names. A v1-shaped payload/response fed into a
--contract-version v2 invocation is refused as mixed_contract_versions,
never silently converted.

Acquiring pr_state/checks live from GitHub (e.g. via `gh pr view`/`gh pr
checks`) is explicitly out of scope here -- see
review_readiness_emission_v2.py's module docstring for why. This CLI
accepts them as already-acquired input, offline, no network.
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

from app.agent_review.contracts_v2 import (  # noqa: E402
    ChunkCoverageV2,
    FindingLifecycleRecordV2,
    PipelineAssessmentV2,
    PullRequestStateV2,
    ReadinessBlockerV2,
    ReadinessReasonV2,
    ReadinessStateV2,
    RequiredCheckResultV2,
    ResponseBindingError,
    RunIdentityV2,
)
from app.agent_review.readiness_decision_v2 import ReadinessDecisionV2  # noqa: E402
from app.agent_review.review_readiness_emission_v2 import emit_review_readiness_v2  # noqa: E402
from app.agent_review.versioning import select_contract_version  # noqa: E402

CONTRACT_VERSION_INVALID_REASON_V2 = "contract_version_required"
INPUT_INVALID_REASON_V2 = "gate_input_invalid"


class QualityGateCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-version", required=True, help="must be exactly 'v2'")
    parser.add_argument("--decision", required=True, help="JSON: state/reason_codes/blockers/coverage/pipeline")
    parser.add_argument("--identity", required=True, help="JSON: RunIdentityV2 fields")
    parser.add_argument("--evaluated-identity", required=True, help="JSON: RunIdentityV2 fields")
    parser.add_argument("--findings", required=True, help="JSON array of FindingLifecycleRecordV2")
    parser.add_argument("--pr-state", required=True, choices=["open", "closed", "merged"])
    parser.add_argument("--checks", required=True, help="JSON array of RequiredCheckResultV2")
    parser.add_argument("--payload", help="optional: JSON ChunkPayload for the mixed-contract-version gate")
    parser.add_argument("--response", help="optional: JSON ChunkResponseEnvelope for the same gate")
    parser.add_argument("--output", required=True, help="path to write the emitted ReviewReadinessV2 JSON")
    return parser.parse_args(argv)


def _read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_decision(path: str) -> ReadinessDecisionV2:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        return ReadinessDecisionV2(
            state=ReadinessStateV2(raw["state"]),
            reason_codes=tuple(ReadinessReasonV2(code) for code in raw["reason_codes"]),
            blockers=tuple(ReadinessBlockerV2.model_validate(item) for item in raw["blockers"]),
            coverage=ChunkCoverageV2.model_validate(raw["coverage"]),
            pipeline=PipelineAssessmentV2.model_validate(raw["pipeline"]),
        )
    except (KeyError, ValueError, ValidationError) as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_identity(path: str) -> RunIdentityV2:
    try:
        return RunIdentityV2.model_validate(_read_json(path))
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_findings(path: str) -> list[FindingLifecycleRecordV2]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        return [FindingLifecycleRecordV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_checks(path: str) -> list[RequiredCheckResultV2]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        return [RequiredCheckResultV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.contract_version != "v2":
        print(f"error: {CONTRACT_VERSION_INVALID_REASON_V2}", file=sys.stderr)
        return 1

    try:
        if args.payload is not None and args.response is not None:
            payload_raw = _read_json(args.payload)
            response_raw = _read_json(args.response)
            if not isinstance(payload_raw, dict) or not isinstance(response_raw, dict):
                raise QualityGateCliError(INPUT_INVALID_REASON_V2)
            select_contract_version(requested="v2", payload_raw=payload_raw, response_raw=response_raw)

        decision = _load_decision(args.decision)
        identity = _load_identity(args.identity)
        evaluated_identity = _load_identity(args.evaluated_identity)
        findings = _load_findings(args.findings)
        checks = _load_checks(args.checks)

        readiness = emit_review_readiness_v2(
            decision=decision,
            findings=findings,
            identity=identity,
            evaluated_identity=evaluated_identity,
            pr_state=PullRequestStateV2(args.pr_state),
            checks=checks,
        )
    except QualityGateCliError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except ResponseBindingError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"error: readiness_invariant_violation\n{exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(readiness.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
