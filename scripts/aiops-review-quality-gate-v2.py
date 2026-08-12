#!/usr/bin/env python3
"""Emit a real AgentReview v2 ReviewReadinessV2 artifact (issue #130,
required-check wiring by `#201-C`).

Reads an already-computed C1 readiness decision (state/reason_codes/
blockers/coverage/pipeline/run_id/manifest_hash -- see
app.agent_review.readiness_decision_v2.compute_readiness_decision_v2),
identity/evaluated-identity, findings, pr_state, and required-check CLAIMS
(``--checks``/``--checks-provenance``), and emits the resulting
``ReviewReadinessV2`` via ``review_readiness_emission_v2.produce_review_
readiness_v2`` -- THE single production entry point, which re-verifies the
required-check claims against `#201-C0`'s real boundary
(``reassemble_and_verify_required_checks_v2``) before any of them can
reach the artifact, and fails closed via
``ReviewReadinessV2.validate_state_invariants`` (the sole authority; this
CLI never re-implements it) if the assembled combination is not
well-formed.

--contract-version v2 is required and explicit, per the CLI naming
decision registered in #102: a NEW v2 CLI script, using the "-v2" suffix
convention to avoid colliding with the existing v1
scripts/aiops-review-quality-gate.py (untouched by this issue).

## `#201-C`: this CLI no longer validates required-check completeness or
## provenance itself

Both concerns -- "is the required set complete?" and "may each submitted
check be here at all?" -- moved into ``produce_review_readiness_v2`` (via
``required_check_readiness_v2._verify_and_assess_required_checks_v2``),
which derives the required-check set EXCLUSIVELY from ``--target-profile``
(a trusted base/default checkout) bound to
``evaluated_identity.profile_hash`` -- never from anything this CLI itself
inspects. This CLI is now purely: parse args, load inputs, call
``produce_review_readiness_v2``, write the result. See that module's own
docstring for the full architecture.

## `CLI_EXIT_SUCCESS != READINESS_READY`

```text
exit 0  =>  a ReviewReadinessV2 artifact was written to --output.
            Does NOT mean ready -- the state is INSIDE the artifact.
exit !=0 => NO artifact was written. Never read as ready, never as
            not-ready: it is the absence of a decision.
```

A required check that is missing or unestablished no longer fails the CLI
outright (`#217`/`#145`'s own fix is preserved -- it still never reaches
`ready`) -- it now emits a real `manual_required` artifact with
`policy_failure` in `reason_codes`, and the CLI exits 0. A submission the
`#201-C0` boundary refuses (forged, invalid, cross-run, non-allowlisted
producer, etc.) still writes NOTHING and exits 1. The decision consumible
by any caller of this CLI is always `readiness.state`, never the exit code
alone -- see `docs/AGENT_REVIEW_V2_REVIEW_READINESS_EMISSION.md`.

## Fixes from an independent Codex review of PR #145 (unchanged by `#201-C`)

1. **Decision-run binding.** The decision file carries its own
   ``run_id``/``manifest_hash``, and ``produce_review_readiness_v2``
   verifies them against ``evaluated_identity`` before emission -- a
   decision computed for one run can no longer be replayed against an
   unrelated run's identity/findings/checks.
2. **One-sided version-gate inputs.** ``--payload`` alone is validated
   immediately as v2 (``select_contract_version`` already supports a
   missing response); ``--response`` without ``--payload`` is rejected
   outright, never silently ignored.
3. **Output/input collision.** ``--output`` is rejected if it resolves to
   the same file as any supplied input, checked by path BEFORE any input
   is read or the output is written.

Acquiring pr_state/checks/the target profile live from GitHub (e.g. via
`gh pr view`/`gh pr checks`) is explicitly out of scope here -- see
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
    RunOriginV2,
)
from app.agent_review.authoritative_check_policy_v2 import (  # noqa: E402
    DEFAULT_AUTHORITATIVE_CHECK_POLICY_RELATIVE_PATH,
    AuthoritativeCheckPolicyErrorV2,
)
from app.agent_review.profile_loader_v2 import (  # noqa: E402
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TargetProfileLoadErrorV2,
)
from app.agent_review.authoritative_ci_snapshot_v2 import (  # noqa: E402
    parse_authoritative_ci_snapshot_v2,
)
from app.agent_review.required_check_provenance_v2 import (  # noqa: E402
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
)
from app.agent_review.required_check_readiness_v2 import (  # noqa: E402
    RequiredCheckReadinessErrorV2,
)
from app.agent_review.readiness_decision_v2 import ReadinessDecisionV2  # noqa: E402
from app.agent_review.review_readiness_emission_v2 import (  # noqa: E402
    ReadinessEmissionError,
    produce_review_readiness_v2,
)
from app.agent_review.versioning import select_contract_version  # noqa: E402
from app.common.strict_json import strict_json_loads  # noqa: E402

CONTRACT_VERSION_INVALID_REASON_V2 = "contract_version_required"
INPUT_INVALID_REASON_V2 = "gate_input_invalid"
RESPONSE_WITHOUT_PAYLOAD_REASON_V2 = "gate_response_without_payload"
OUTPUT_OVERWRITES_INPUT_REASON_V2 = "gate_output_overwrites_input"


class QualityGateCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-version", required=True, help="must be exactly 'v2'")
    parser.add_argument("--decision", required=True, help="JSON: state/reason_codes/blockers/coverage/pipeline/run_id/manifest_hash")
    parser.add_argument("--identity", required=True, help="JSON: RunIdentityV2 fields")
    parser.add_argument("--evaluated-identity", required=True, help="JSON: RunIdentityV2 fields")
    parser.add_argument("--findings", required=True, help="JSON array of FindingLifecycleRecordV2")
    parser.add_argument("--pr-state", required=True, choices=["open", "closed", "merged"])
    parser.add_argument("--checks", required=True, help="JSON array of RequiredCheckResultV2")
    parser.add_argument(
        "--checks-provenance",
        required=True,
        help="JSON array of RequiredCheckProvenanceV2, one per --checks entry",
    )
    parser.add_argument(
        "--checks-snapshot",
        required=True,
        help="AuthoritativeCheckSnapshotV2 JSON the checks must be re-derivable from",
    )
    parser.add_argument(
        "--run-origin",
        required=True,
        help="JSON: RunOriginV2 fields; selects the tested-tree rule",
    )
    parser.add_argument(
        "--toolchain-digest",
        required=True,
        help="digest of the host toolchain that performed acquisition",
    )
    parser.add_argument(
        "--target-profile",
        required=True,
        help=(
            "path to a TRUSTED BASE/DEFAULT repo-root checkout containing "
            ".aiops/target-profile.v2.yaml and .aiops/authoritative-checks.v2.yaml"
        ),
    )
    parser.add_argument("--payload", help="optional: JSON ChunkPayload for the mixed-contract-version gate")
    parser.add_argument("--response", help="optional: JSON ChunkResponseEnvelope for the same gate")
    parser.add_argument("--output", required=True, help="path to write the emitted ReviewReadinessV2 JSON")
    return parser.parse_args(argv)


def _check_no_output_input_collision(args: argparse.Namespace) -> None:
    """Reject ``--output`` colliding with any supplied input, by RESOLVED
    PATH, before any input is read or the output is written -- a Codex
    review of #145 found that a mistyped pipeline argument pointing
    ``--output`` at one of the inputs would read every input first and
    then silently overwrite that source artifact with the readiness JSON,
    returning success. Mirrors the same pattern the v1 quality-gate CLI
    already uses.

    ``target_profile`` is a repository root, not a file: ``load_target_
    profile_v2`` actually reads ``<target_profile>/DEFAULT_TARGET_PROFILE_
    RELATIVE_PATH`` underneath it. Comparing ``--output`` only against the
    bare root would miss a collision with that nested file -- a Codex
    review of #156 found exactly this: ``--target-profile /repo``,
    ``--output /repo/.aiops/target-profile.v2.yaml`` passed the check,
    then the final write silently corrupted the real profile source.
    """

    output_resolved = Path(args.output).resolve()

    file_input_args = (
        "decision",
        "identity",
        "evaluated_identity",
        "findings",
        "checks",
        "checks_provenance",
        "checks_snapshot",
        "run_origin",
        "payload",
        "response",
    )
    for name in file_input_args:
        value = getattr(args, name)
        if value is None:
            continue
        if Path(value).resolve() == output_resolved:
            raise QualityGateCliError(OUTPUT_OVERWRITES_INPUT_REASON_V2)

    if args.target_profile is not None:
        target_profile_resolved = Path(args.target_profile).resolve()
        # The root now carries TWO nested inputs. Comparing only against the
        # bare root, or against the profile alone, would miss a collision with
        # the authoritative-check policy and silently corrupt it -- the same
        # class of bug a Codex review of #156 found for the profile itself.
        nested = [
            (target_profile_resolved / DEFAULT_TARGET_PROFILE_RELATIVE_PATH).resolve(),
            (target_profile_resolved / DEFAULT_AUTHORITATIVE_CHECK_POLICY_RELATIVE_PATH).resolve(),
        ]
        if output_resolved in (target_profile_resolved, *nested):
            raise QualityGateCliError(OUTPUT_OVERWRITES_INPUT_REASON_V2)


def _read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _read_json_strict(path: str) -> object:
    """Like `_read_json`, but through the same duplicate-key-rejecting parser
    `--checks-snapshot` already goes through.

    `--checks-provenance` and `--run-origin` are authority-bearing documents:
    a duplicate `source_kind` or `event_type` key would let plain `json.loads`
    silently pick the last value while another parser or auditor reading the
    same bytes could reasonably see a different one."""

    try:
        return strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_decision(path: str) -> ReadinessDecisionV2:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        # run_id/manifest_hash are REQUIRED, not defaulted: a decision file
        # missing its own run provenance is a real gap a Codex review of
        # #145 found (see readiness_decision_v2.ReadinessDecisionV2's own
        # docstring) -- never accept a legacy decision file without it as
        # if it were conclusive.
        return ReadinessDecisionV2(
            state=ReadinessStateV2(raw["state"]),
            reason_codes=tuple(ReadinessReasonV2(code) for code in raw["reason_codes"]),
            blockers=tuple(ReadinessBlockerV2.model_validate(item) for item in raw["blockers"]),
            # model_validate_json, not model_validate(dict): raw["coverage"]
            # is an already-parsed dict from json.loads, so ChunkCoverageV2's
            # tuple-typed fields (expected_files etc., Codex review of #97)
            # would otherwise be rejected as "should be tuple, got list" --
            # a JSON array is exactly as valid a source for a tuple field as
            # for a list field, but model_validate on an already-parsed
            # Python dict enforces strict=True's Python-level distinction
            # directly, unlike parsing raw JSON text.
            coverage=ChunkCoverageV2.model_validate_json(json.dumps(raw["coverage"])),
            pipeline=PipelineAssessmentV2.model_validate(raw["pipeline"]),
            run_id=raw["run_id"],
            manifest_hash=raw["manifest_hash"],
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


def _load_checks_provenance(path: str) -> list[RequiredCheckProvenanceV2]:
    raw = _read_json_strict(path)
    if not isinstance(raw, list):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        return [RequiredCheckProvenanceV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_run_origin(path: str) -> RunOriginV2:
    try:
        return RunOriginV2.model_validate(_read_json_strict(path))
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_checks_snapshot(path: str):
    """Parse through the same strict parser the assembler uses, so the gate
    cannot accept a snapshot the offline pipeline would reject."""

    try:
        return parse_authoritative_ci_snapshot_v2(Path(path).read_bytes())
    except OSError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc
    except RequiredCheckProvenanceErrorV2 as exc:
        raise QualityGateCliError(exc.reason_code) from exc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.contract_version != "v2":
        print(f"error: {CONTRACT_VERSION_INVALID_REASON_V2}", file=sys.stderr)
        return 1

    try:
        _check_no_output_input_collision(args)

        if args.response is not None and args.payload is None:
            raise QualityGateCliError(RESPONSE_WITHOUT_PAYLOAD_REASON_V2)

        if args.payload is not None:
            payload_raw = _read_json(args.payload)
            if not isinstance(payload_raw, dict):
                raise QualityGateCliError(INPUT_INVALID_REASON_V2)
            response_raw = None
            if args.response is not None:
                response_raw = _read_json(args.response)
                if not isinstance(response_raw, dict):
                    raise QualityGateCliError(INPUT_INVALID_REASON_V2)
            select_contract_version(requested="v2", payload_raw=payload_raw, response_raw=response_raw)

        decision = _load_decision(args.decision)
        identity = _load_identity(args.identity)
        evaluated_identity = _load_identity(args.evaluated_identity)
        findings = _load_findings(args.findings)
        checks = _load_checks(args.checks)
        checks_provenance = _load_checks_provenance(args.checks_provenance)
        run_origin = _load_run_origin(args.run_origin)
        checks_snapshot = _load_checks_snapshot(args.checks_snapshot)

        # `#201-C`: neither completeness nor entitlement is validated here
        # any more -- both are `produce_review_readiness_v2`'s job now (via
        # `required_check_readiness_v2._verify_and_assess_required_checks_v2`,
        # which derives the required set from `--target-profile` itself,
        # bound to `evaluated_identity.profile_hash`, and re-verifies
        # `checks`/`checks_provenance` against the real `#201-C0` boundary).
        # `checks`/`checks_provenance` reaching this call are CLAIMS, never
        # evidence.
        readiness = produce_review_readiness_v2(
            decision=decision,
            findings=findings,
            identity=identity,
            evaluated_identity=evaluated_identity,
            pr_state=PullRequestStateV2(args.pr_state),
            checks=checks,
            provenance=checks_provenance,
            origin=run_origin,
            snapshot=checks_snapshot,
            toolchain_digest=args.toolchain_digest,
            target_profile_root=args.target_profile,
        )
    except QualityGateCliError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except ReadinessEmissionError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except ResponseBindingError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except (
        RequiredCheckProvenanceErrorV2,
        RequiredCheckReadinessErrorV2,
        TargetProfileLoadErrorV2,
        AuthoritativeCheckPolicyErrorV2,
    ) as exc:
        # Surfaced verbatim: every one of these reason codes is already
        # stable, typed and log-safe -- translating them into a second
        # gate-local vocabulary would only create synonyms. This is the
        # boundary refusal case (forged/invalid/cross-run/non-allowlisted
        # submission, or a broken --target-profile checkout): no artifact is
        # ever written for it, matching CLI_EXIT_SUCCESS != READINESS_READY
        # (module docstring) -- an artifact is written only past this point.
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
