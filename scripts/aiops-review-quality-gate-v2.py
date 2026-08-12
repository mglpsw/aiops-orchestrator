#!/usr/bin/env python3
"""Emit a real AgentReview v2 ReviewReadinessV2 artifact (issue #130).

Reads an already-computed C1 readiness decision (state/reason_codes/
blockers/coverage/pipeline/run_id/manifest_hash -- see
app.agent_review.readiness_decision_v2.compute_readiness_decision_v2),
identity/evaluated-identity, findings, pr_state, checks, and the target
profile, and emits the resulting ReviewReadinessV2 -- fail-closed if the
combination does not satisfy ReviewReadinessV2.validate_state_invariants
(the sole authority; this CLI never re-implements it, see
app.agent_review.review_readiness_emission_v2).

--contract-version v2 is required and explicit, per the CLI naming
decision registered in #102: a NEW v2 CLI script, using the "-v2" suffix
convention to avoid colliding with the existing v1
scripts/aiops-review-quality-gate.py (untouched by this issue).

## Fixes from an independent Codex review of PR #145

1. **Decision-run binding.** The decision file now carries its own
   ``run_id``/``manifest_hash`` (see ``ReadinessDecisionV2``'s own
   docstring), and ``emit_review_readiness_v2`` verifies them against
   ``evaluated_identity`` before emission -- a decision computed for one
   run can no longer be replayed against an unrelated run's identity/
   findings/checks.
2. **Required-checks completeness.** ``--target-profile`` (a trusted
   repo-root checkout, loaded via ``profile_loader_v2.load_target_
   profile_v2``, the same strict canonical loader every other v2 module
   uses) is now required. Its recomputed ``profile_hash`` must match
   ``evaluated_identity.profile_hash``, and every name in
   ``profile.policies.required_checks`` must be present in ``--checks``
   -- a required check silently missing from the submitted list (while
   every SUBMITTED check is green) no longer passes.
3. **One-sided version-gate inputs.** ``--payload`` alone is validated
   immediately as v2 (``select_contract_version`` already supports a
   missing response); ``--response`` without ``--payload`` is rejected
   outright, never silently ignored.
4. **Output/input collision.** ``--output`` is rejected if it resolves to
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
    load_authoritative_check_policy_v2,
    validate_policy_against_profile_v2,
)
from app.agent_review.profile_loader_v2 import (  # noqa: E402
    DEFAULT_TARGET_PROFILE_RELATIVE_PATH,
    TargetProfileLoadErrorV2,
    compute_profile_hash_v2,
    load_target_profile_v2,
)
from app.agent_review.authoritative_ci_snapshot_v2 import (  # noqa: E402
    parse_authoritative_ci_snapshot_v2,
)
from app.agent_review.required_check_assembly_v2 import (  # noqa: E402
    reassemble_and_verify_required_checks_v2,
)
from app.agent_review.required_check_provenance_v2 import (  # noqa: E402
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
)
from app.agent_review.readiness_decision_v2 import ReadinessDecisionV2  # noqa: E402
from app.agent_review.review_readiness_emission_v2 import (  # noqa: E402
    ReadinessEmissionError,
    emit_review_readiness_v2,
)
from app.agent_review.versioning import select_contract_version  # noqa: E402

CONTRACT_VERSION_INVALID_REASON_V2 = "contract_version_required"
INPUT_INVALID_REASON_V2 = "gate_input_invalid"
RESPONSE_WITHOUT_PAYLOAD_REASON_V2 = "gate_response_without_payload"
OUTPUT_OVERWRITES_INPUT_REASON_V2 = "gate_output_overwrites_input"
PROFILE_IDENTITY_MISMATCH_REASON_V2 = "gate_profile_identity_mismatch"
REQUIRED_CHECK_MISSING_REASON_V2 = "gate_required_check_missing"


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
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise QualityGateCliError(INPUT_INVALID_REASON_V2)
    try:
        return [RequiredCheckProvenanceV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise QualityGateCliError(INPUT_INVALID_REASON_V2) from exc


def _load_run_origin(path: str) -> RunOriginV2:
    try:
        return RunOriginV2.model_validate(_read_json(path))
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


def _validate_required_check_provenance(
    *,
    target_profile_path: str,
    evaluated_identity: RunIdentityV2,
    checks: list[RequiredCheckResultV2],
    provenance: list[RequiredCheckProvenanceV2],
    origin: RunOriginV2,
    snapshot: object,
    toolchain_digest: str,
) -> None:
    """Re-derive every submitted `RequiredCheckResultV2` from the acquired
    evidence and refuse anything that does not follow from it.

    This closes the bypass `#217` describes. `_validate_required_checks_complete`
    below matches required checks BY NAME, so any object called `pytest` with
    `conclusion=success` satisfied the gate regardless of who built it.

    A first version of this function checked the sidecar's structure instead --
    1:1 digest binding, run identity, policy conformance. A Codex review of
    this PR showed that was not enough: every field it inspected is derivable
    from public inputs, so a caller able to write both `--checks` and
    `--checks-provenance` could hand-build a fabricated green whose sidecar was
    perfectly consistent. Matching allowlisted strings proves consistency,
    never that a check ran. So the gate no longer trusts the submission at all:
    it re-runs the assembler over `--checks-snapshot` and accepts the pair only
    if it is exactly what the assembler independently produces.

    Hardening the gate is deliberately NOT readiness wiring. Nothing here
    touches `review_readiness_emission_v2` or `readiness_decision_v2`, and
    nothing here decides a readiness state -- connecting a legitimated check
    set to `ReviewReadinessV2` remains `#201-C`. This function only answers
    "may this object be here at all?".

    The authoritative-check policy is read from the SAME `--target-profile`
    root, which is documented as a trusted base/default checkout. That is the
    security property: read from a PR working tree, the policy would let the
    pull request nominate its own producer.
    """

    try:
        loaded_policy = load_authoritative_check_policy_v2(target_profile_path)
        profile = load_target_profile_v2(target_profile_path)
        validate_policy_against_profile_v2(policy=loaded_policy.policy, profile=profile)
    except (AuthoritativeCheckPolicyErrorV2, TargetProfileLoadErrorV2) as exc:
        raise QualityGateCliError(exc.reason_code) from exc

    try:
        reassemble_and_verify_required_checks_v2(
            checks=checks,
            provenance=provenance,
            identity=evaluated_identity,
            origin=origin,
            loaded_policy=loaded_policy,
            snapshot=snapshot,
            toolchain_digest=toolchain_digest,
        )
    except RequiredCheckProvenanceErrorV2 as exc:
        # Surfaced verbatim: the verifier's reason codes are already stable,
        # typed and log-safe, so translating them into a second gate-local
        # vocabulary would only create synonyms.
        raise QualityGateCliError(exc.reason_code) from exc


def _validate_required_checks_complete(
    *, target_profile_path: str, evaluated_identity: RunIdentityV2, checks: list[RequiredCheckResultV2]
) -> None:
    """Confirm the trusted target profile matches the identity being
    gated, then confirm every one of ITS OWN configured
    ``required_checks`` is represented in the submitted ``checks`` list --
    never just that the submitted list is nonempty and individually green
    (a Codex review of #145 found that a target requiring both `pytest`
    and `mypy` was satisfied by a submission containing only a green
    `pytest`). The individual green/red state of each submitted check
    remains governed entirely by ``ReviewReadinessV2.validate_state_
    invariants`` -- this function only checks PRESENCE, never duplicates
    that logic."""

    try:
        profile = load_target_profile_v2(target_profile_path)
    except TargetProfileLoadErrorV2 as exc:
        raise QualityGateCliError(exc.reason_code) from exc

    if compute_profile_hash_v2(profile) != evaluated_identity.profile_hash:
        raise QualityGateCliError(PROFILE_IDENTITY_MISMATCH_REASON_V2)

    submitted_names = {check.check_name for check in checks}
    missing = set(profile.policies.required_checks) - submitted_names
    if missing:
        raise QualityGateCliError(REQUIRED_CHECK_MISSING_REASON_V2)


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

        # Completeness first, then entitlement. The two answer different
        # questions -- "is the required set present?" versus "may each
        # submitted check be here at all?" -- and running completeness first
        # keeps each failure's diagnosis precise: a genuinely missing required
        # check reports `gate_required_check_missing` rather than surfacing as
        # a provenance count mismatch. Neither order is a bypass, because both
        # run before `emit_review_readiness_v2`.
        _validate_required_checks_complete(
            target_profile_path=args.target_profile, evaluated_identity=evaluated_identity, checks=checks
        )
        _validate_required_check_provenance(
            target_profile_path=args.target_profile,
            evaluated_identity=evaluated_identity,
            checks=checks,
            provenance=checks_provenance,
            origin=run_origin,
            snapshot=checks_snapshot,
            toolchain_digest=args.toolchain_digest,
        )

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
    except ReadinessEmissionError as exc:
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
