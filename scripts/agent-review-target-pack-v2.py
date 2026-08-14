#!/usr/bin/env python3
"""`agent-review target ...` -- the `agentreview-v2-target-pack` CLI (#203).

Installs/diagnoses the AgentReview v2 engine's integration into a target
repository WITHOUT forking the engine -- `#203 MAY INSTALL/CONFIGURE
INTEGRATION. #203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY
PROMOTE ROLLOUT.`

This first slice ships two of the seven subcommands named in the
Execution-Ready Engineering Specification
(`docs/checkpoints/AGENT_REVIEW_V2_203_TARGET_PACK_SPEC.md`) as a coherent,
tested unit:

    agent-review-target-pack-v2.py init    --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME
    agent-review-target-pack-v2.py doctor  --target-root PATH --toolrepo-root PATH --target-repo OWNER/NAME

`install-workflows`/`upgrade`/`rollback` remain deferred per the spec's own
`§12`/`§14` (not silently dropped -- named there explicitly). `validate` and
`conformance` shipped in slice 2 (`#203-S2`): `validate` is read-only and
target-only (no toolrepo checkout required), and `conformance` is synthetic
and offline -- never a real target, which is `#204`'s charter.

Every subcommand is a thin wrapper: it parses args, calls exactly one
library function in `app.agent_review.target_pack_*`, and prints/writes
that function's result. No subcommand re-implements any decision the
library layer already owns -- the same discipline `#201-C`'s own CLI
(`aiops-review-quality-gate-v2.py`) already follows for readiness.

`doctor` is READ-ONLY -- see `target_pack_doctor_v2.run_doctor_v2`'s own
docstring and `tests/agent_review/test_target_pack_arch_v2.py` for the
mechanical (AST) proof.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.target_pack_build_v2 import (  # noqa: E402
    TargetPackBuildError,
    build_target_pack_manifest_v2,
    load_seed_content_by_path_v2,
)
from app.common.strict_json import strict_json_loads  # noqa: E402
from app.agent_review.target_pack_conformance_v2 import (  # noqa: E402
    ConformanceCaseV2,
    ConformanceExpectationV2,
    run_conformance_v2,
)
from app.agent_review.target_pack_doctor_v2 import run_doctor_v2  # noqa: E402
from app.agent_review.target_pack_validate_v2 import run_validate_v2  # noqa: E402
from app.agent_review.target_pack_install_v2 import (  # noqa: E402
    RECEIPT_RELATIVE_PATH_V2,
    TargetPackInstallError,
    apply_install_plan_v2,
    write_receipt_v2,
)
from app.agent_review.target_pack_operation_v2 import (  # noqa: E402
    compute_target_pack_operation_plan_v2,
    require_target_owned_reconciliation_acceptance_v2,
)
from app.agent_review.target_pack_plan_v2 import (  # noqa: E402
    PlanError,
    validate_rollout_within_pack_capability_v2,
)
from app.agent_review.target_pack_receipt_v2 import TargetInstallReceiptV2  # noqa: E402
from pydantic import ValidationError  # noqa: E402

CLI_INPUT_INVALID_REASON_V2 = "target_pack_cli_input_invalid"
CLI_EXPECTED_PLAN_REQUIRED_REASON_V2 = "target_pack_cli_expected_plan_required"
CLI_EXPECTED_PLAN_MISMATCH_REASON_V2 = "target_pack_cli_expected_plan_mismatch"
CLI_PREVIOUS_RECEIPT_INVALID_REASON_V2 = "target_pack_cli_previous_receipt_invalid"
CONFORMANCE_MATRIX_UNREADABLE_REASON_V2 = "target_pack_conformance_matrix_unreadable"
CONFORMANCE_MATRIX_INVALID_REASON_V2 = "target_pack_conformance_matrix_invalid"

CLI_EXIT_SATISFIED_OR_NOOP_V2 = 0
CLI_EXIT_VALID_REPORT_WITH_FAILURE_V2 = 1
CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2 = 2
CLI_EXIT_ENVIRONMENT_OR_GATE_UNAVAILABLE_V2 = 3


CLI_TOOLREPO_SHA_UNRESOLVED_REASON_V2 = "target_pack_cli_toolrepo_sha_unresolved"
_ALL_ZERO_SHA_V2 = "0" * 40
_GIT_SHA_HEX_RE = re.compile(r"^[0-9a-f]{40}$")


def _resolve_toolrepo_sha(toolrepo_root: Path) -> str:
    """The real `git rev-parse HEAD` of `toolrepo_root`, or a refusal.

    Adversarial review finding, confirmed and fixed: the previous version
    silently fell back to an all-zero SHA (`"0" * 40`) whenever `git
    rev-parse` failed (e.g. `--toolrepo-root` not a real git checkout),
    and every caller wrote that fabricated value straight into
    `TargetPackManifestV2.toolrepo_sha`/`TargetInstallReceiptV2.
    toolrepo_sha` -- a receipt whose entire stated purpose (spec `§4`) is
    to be "provenance-carrying". Reproduced: `init` against a
    non-git-checkout `--toolrepo-root` exited 0 and wrote a receipt
    claiming `toolrepo_sha: "0000...0000"`, a fabricated identity, not a
    refusal. Never silently fabricate provenance -- refuse instead, by
    name."""

    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(toolrepo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = completed.stdout.strip()
    if completed.returncode != 0 or not _GIT_SHA_HEX_RE.fullmatch(sha) or sha == _ALL_ZERO_SHA_V2:
        raise TargetPackBuildError(CLI_TOOLREPO_SHA_UNRESOLVED_REASON_V2)
    return sha


def _cmd_init(args: argparse.Namespace) -> int:
    target_root = Path(args.target_root)
    toolrepo_root = Path(args.toolrepo_root)

    # Adversarial review finding, confirmed and fixed (spec rev.2 §5.1,
    # "preflight before mutation"): this used to call `target_root.mkdir(...)`
    # here, before toolrepo resolution, manifest build, or the rollout check
    # below -- so a refused `init` against a nonexistent target still left
    # the directory behind, directly contradicting the rollout check's own
    # (then false) "leaves nothing behind" comment and the spec's own
    # "compute plan before touching the filesystem". Reproduced: `init
    # --rollout shadow_full` against a nonexistent target correctly exited 1
    # but the directory existed afterwards regardless. No directory or file
    # creation happens until every preflight gate below has passed --
    # `compute_install_plan_v2` tolerates a missing `target_root` (treats it
    # as "nothing on disk yet"), and `_atomic_write_v2` creates parent
    # directories itself, immediately before each write.
    try:
        manifest = build_target_pack_manifest_v2(
            toolrepo_root=toolrepo_root,
            toolrepo_sha=_resolve_toolrepo_sha(toolrepo_root),
            pack_version=args.pack_version,
        )
    except TargetPackBuildError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    # Nothing previously checked `--rollout` against what this pack version
    # can actually deliver -- `init --rollout shadow_full` exited 0 and wrote
    # `rollout_mode: shadow_full` into the receipt even though this slice
    # ships no trusted-check integration at all. Checked before any
    # filesystem write.
    try:
        validate_rollout_within_pack_capability_v2(
            requested=args.rollout, max_supported=manifest.max_supported_rollout_mode
        )
    except PlanError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    receipt_path = target_root / RECEIPT_RELATIVE_PATH_V2
    previous_receipt = None
    if receipt_path.is_file():
        try:
            previous_receipt = TargetInstallReceiptV2.model_validate_json(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError):
            print(f"error: {CLI_PREVIOUS_RECEIPT_INVALID_REASON_V2}", file=sys.stderr)
            return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    seed_content = load_seed_content_by_path_v2(toolrepo_root=toolrepo_root, toolrepo_sha=manifest.toolrepo_sha)
    operation = compute_target_pack_operation_plan_v2(
        manifest=manifest,
        target_root=target_root,
        target_repo=args.target_repo,
        rollout=args.rollout,
        seed_content_by_path=seed_content,
        previous_receipt=previous_receipt,
        accepted_target_owned_paths=tuple(args.accept_target_owned),
    )
    if not args.apply:
        print(json.dumps(operation.plan.model_dump(mode="json"), indent=2, sort_keys=True))
        return CLI_EXIT_SATISFIED_OR_NOOP_V2
    if not args.expected_plan_sha256:
        print(f"error: {CLI_EXPECTED_PLAN_REQUIRED_REASON_V2}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2
    if args.expected_plan_sha256 != operation.plan.operation_plan_hash:
        print(f"error: {CLI_EXPECTED_PLAN_MISMATCH_REASON_V2}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2
    require_target_owned_reconciliation_acceptance_v2(operation.plan)
    try:
        written = apply_install_plan_v2(
            plan=operation.install_plan,
            manifest=manifest,
            target_root=target_root,
            seed_content_by_path=seed_content,
        )
        if operation.should_write_receipt:
            write_receipt_v2(
                target_root=target_root,
                receipt=operation.expected_receipt,
                expected_target_root_real=operation.install_plan.target_root_real,
            )
    except TargetPackInstallError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2
    written_paths = list(written)
    if operation.should_write_receipt:
        written_paths.append(RECEIPT_RELATIVE_PATH_V2)
    print(json.dumps({"operation_plan_hash": operation.plan.operation_plan_hash, "written": written_paths}, indent=2))
    return CLI_EXIT_SATISFIED_OR_NOOP_V2


def _cmd_doctor(args: argparse.Namespace) -> int:
    target_root = Path(args.target_root)
    toolrepo_root = Path(args.toolrepo_root)
    try:
        manifest = build_target_pack_manifest_v2(
            toolrepo_root=toolrepo_root,
            toolrepo_sha=_resolve_toolrepo_sha(toolrepo_root),
            pack_version=args.pack_version,
        )
    except TargetPackBuildError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    report = run_doctor_v2(target_root=target_root, manifest=manifest, target_repo=args.target_repo)
    output = {
        "target_root": report.target_root,
        "healthy": report.is_healthy,
        "profile": {"status": report.profile.status, "reason_code": report.profile.reason_code},
        "receipt": {"status": report.receipt.status, "reason_code": report.receipt.reason_code},
        "secret_names": [
            {"name": c.name, "declared_present": c.declared_present} for c in report.secret_names
        ],
        "required_capabilities_declared": list(report.required_capabilities_declared),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return CLI_EXIT_SATISFIED_OR_NOOP_V2 if report.is_healthy else CLI_EXIT_VALID_REPORT_WITH_FAILURE_V2


def _cmd_validate(args: argparse.Namespace) -> int:
    """Thin wrapper: parses args, calls exactly one library function, prints
    its result. Read-only -- `run_validate_v2` is held to that mechanically
    by `tests/agent_review/test_target_pack_arch_v2.py`.

    Note `validate` needs only `--target-root`: unlike `doctor` it never
    rebuilds the upstream manifest, so a consumer repository can run it in
    its own CI with no toolrepo checkout at all.
    """

    report = run_validate_v2(target_root=Path(args.target_root))
    output = {
        "target_root": report.target_root,
        "valid": report.is_valid,
        "checks": [
            {"name": c.name, "status": c.status, "reason_code": c.reason_code} for c in report.checks
        ],
        # Surfaced explicitly so a consumer cannot read `valid: true` as
        # "everything the final architecture will check has been checked".
        "unvalidated_capabilities": list(report.unvalidated_capabilities),
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return CLI_EXIT_SATISFIED_OR_NOOP_V2 if report.is_valid else CLI_EXIT_VALID_REPORT_WITH_FAILURE_V2


def _cmd_conformance(args: argparse.Namespace) -> int:
    """Synthetic/offline conformance over a declared matrix of targets.

    The matrix is a JSON file: `{"cases": [{"case_id", "target_root",
    "expectation"}]}` where `expectation` is `eligible`/`ineligible`. It is
    target-authored input, never a pack-generated artifact, and nothing in
    it may name a real consumer -- `#204` owns real targets.
    """

    matrix_path = Path(args.matrix)
    try:
        # strict_json_loads, not json.loads (PR #235 review round 1,
        # confirmed): plain json.loads silently keeps only the LAST of a
        # duplicated key, so a matrix with two `cases` keys would be
        # interpreted from one of them while still being eligible for a
        # successful conformance result. This is target-authored contract
        # input; ambiguity in it must be refused, not resolved by
        # last-writer-wins. Reuses the repository's existing primitive
        # rather than adding a second parser.
        raw = strict_json_loads(matrix_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"error: {CONFORMANCE_MATRIX_UNREADABLE_REASON_V2}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    try:
        cases = tuple(
            ConformanceCaseV2(
                case_id=str(entry["case_id"]),
                target_root=Path(entry["target_root"]),
                expectation=ConformanceExpectationV2(entry["expectation"]),
            )
            for entry in raw["cases"]
        )
    except (KeyError, TypeError, ValueError):
        print(f"error: {CONFORMANCE_MATRIX_INVALID_REASON_V2}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2

    report = run_conformance_v2(cases=cases)
    output = {
        # Deliberately NOT "dual_target_conformance": this is synthetic and
        # offline. Real dual-target adoption is #204 (spec §10).
        "synthetic_pack_conformance": report.is_conformant,
        "reason_codes": list(report.reason_codes),
        "cases": [
            {
                "case_id": case.case_id,
                "expectation": case.expectation.value,
                "matched_expectation": case.matched_expectation,
                "observed_reason_codes": list(case.observed_reason_codes),
            }
            for case in report.cases
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return CLI_EXIT_SATISFIED_OR_NOOP_V2 if report.is_conformant else CLI_EXIT_VALID_REPORT_WITH_FAILURE_V2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="seed a target repository (TARGET_OWNED files only, once)")
    init_parser.add_argument("--target-root", required=True)
    init_parser.add_argument("--toolrepo-root", required=True)
    init_parser.add_argument("--target-repo", required=True, help="owner/name of the target repository")
    init_parser.add_argument("--pack-version", required=True)
    init_parser.add_argument("--rollout", default="off", choices=["off", "shadow_minimal", "shadow_full"])
    init_parser.add_argument("--apply", action="store_true", help="apply a previewed operation plan")
    init_parser.add_argument("--expected-plan-sha256")
    init_parser.add_argument("--accept-target-owned", action="append", default=[])
    init_parser.set_defaults(handler=_cmd_init)

    doctor_parser = sub.add_parser("doctor", help="read-only diagnostics; never mutates the target")
    doctor_parser.add_argument("--target-root", required=True)
    doctor_parser.add_argument("--toolrepo-root", required=True)
    doctor_parser.add_argument("--target-repo", required=True, help="owner/name of the target repository")
    doctor_parser.add_argument("--pack-version", required=True)
    doctor_parser.set_defaults(handler=_cmd_doctor)

    validate_parser = sub.add_parser(
        "validate",
        help="read-only, target-only validation of an installed pack; needs no toolrepo checkout",
    )
    validate_parser.add_argument("--target-root", required=True)
    validate_parser.set_defaults(handler=_cmd_validate)

    conformance_parser = sub.add_parser(
        "conformance",
        help="synthetic/offline conformance over a declared matrix of targets (never a real target)",
    )
    conformance_parser.add_argument("--matrix", required=True, help="path to the conformance matrix JSON")
    conformance_parser.set_defaults(handler=_cmd_conformance)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return args.handler(args)
    except (PlanError, TargetPackInstallError, TargetPackBuildError) as exc:
        # Operation planning normalises invalid TARGET_OWNED profile bytes
        # into a typed PlanError, so every expected refusal crosses this
        # same clean CLI boundary without exposing target content.
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2
    except ValidationError as exc:
        print(f"error: {CLI_INPUT_INVALID_REASON_V2}\n{exc}", file=sys.stderr)
        return CLI_EXIT_INVALID_INPUT_OR_CONTRACT_V2


if __name__ == "__main__":
    raise SystemExit(main())
