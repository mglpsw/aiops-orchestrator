#!/usr/bin/env python3
"""`agent-review target ...` -- the `agentreview-v2-target-pack` CLI (#203).

Installs/diagnoses the AgentReview v2 engine's integration into a target
repository WITHOUT forking the engine -- `#203 MAY INSTALL/CONFIGURE
INTEGRATION. #203 MUST NEVER CREATE AUTHORITY, FORK THE ENGINE, OR SILENTLY
PROMOTE ROLLOUT.`

This first slice ships two of the seven subcommands named in the
Execution-Ready Engineering Specification (`/root/.claude/plans/
203-agentreview-v2-target-pack.md`) as a coherent, tested unit:

    agent-review-target-pack-v2.py init    --target-root PATH --toolrepo-root PATH
    agent-review-target-pack-v2.py doctor  --target-root PATH --toolrepo-root PATH

`validate`/`conformance`/`install-workflows`/`upgrade`/`rollback` are
deferred to a follow-up commit on this same branch/PR, per the spec's own
`§12` (not silently dropped -- named there explicitly).

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
from app.agent_review.target_pack_doctor_v2 import run_doctor_v2  # noqa: E402
from app.agent_review.target_pack_install_v2 import (  # noqa: E402
    TargetPackInstallError,
    apply_install_plan_v2,
)
from app.agent_review.target_pack_plan_v2 import PlanError, compute_install_plan_v2  # noqa: E402
from app.agent_review.target_pack_receipt_v2 import (  # noqa: E402
    TargetInstallReceiptV2,
    compute_target_install_receipt_hash_v2,
)
from pydantic import ValidationError  # noqa: E402

RECEIPT_RELATIVE_PATH_V2 = ".aiops/install-receipt.v2.json"

CLI_INPUT_INVALID_REASON_V2 = "target_pack_cli_input_invalid"


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
    target_root.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_target_pack_manifest_v2(
            toolrepo_root=toolrepo_root,
            toolrepo_sha=_resolve_toolrepo_sha(toolrepo_root),
            pack_version=args.pack_version,
        )
    except TargetPackBuildError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1

    plan = compute_install_plan_v2(manifest=manifest, target_root=target_root, previous_receipt=None)
    seed_content = load_seed_content_by_path_v2(toolrepo_root=toolrepo_root)

    try:
        written = apply_install_plan_v2(
            plan=plan, manifest=manifest, target_root=target_root, seed_content_by_path=seed_content
        )
    except TargetPackInstallError as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1

    generated_file_hashes = {
        action.path: action.seed_content_sha256
        for action in plan.file_actions
        if action.path in written
    }
    receipt_without_hash = {
        "schema_id": "agent-review.target-install-receipt.v2",
        "schema_version": 2,
        "pack_version": manifest.pack_version,
        "toolrepo_sha": manifest.toolrepo_sha,
        "target_repo": args.target_repo,
        "target_profile_hash": "0" * 64,
        "target_policy_hash": "0" * 64,
        "review_pack_hashes": {},
        "generated_file_hashes": generated_file_hashes,
        "target_owned_paths": tuple(generated_file_hashes),
        "required_capabilities": manifest.required_capabilities,
        "expected_runner_labels": (),
        "required_secret_names": (),
        "rollout_mode": args.rollout,
        "compatibility": "compatible",
        "previous_install_identity": None,
    }
    receipt_hash = compute_target_install_receipt_hash_v2(
        TargetInstallReceiptV2.model_construct(**receipt_without_hash, receipt_hash="0" * 64)
    )
    receipt = TargetInstallReceiptV2(**receipt_without_hash, receipt_hash=receipt_hash)

    receipt_path = target_root / RECEIPT_RELATIVE_PATH_V2
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"written": list(written) + [RECEIPT_RELATIVE_PATH_V2]}, indent=2))
    return 0


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
        return 1

    report = run_doctor_v2(target_root=target_root, manifest=manifest)
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
    return 0 if report.is_healthy else 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="seed a target repository (TARGET_OWNED files only, once)")
    init_parser.add_argument("--target-root", required=True)
    init_parser.add_argument("--toolrepo-root", required=True)
    init_parser.add_argument("--target-repo", required=True, help="owner/name of the target repository")
    init_parser.add_argument("--pack-version", required=True)
    init_parser.add_argument("--rollout", default="off", choices=["off", "shadow_minimal", "shadow_full"])
    init_parser.set_defaults(handler=_cmd_init)

    doctor_parser = sub.add_parser("doctor", help="read-only diagnostics; never mutates the target")
    doctor_parser.add_argument("--target-root", required=True)
    doctor_parser.add_argument("--toolrepo-root", required=True)
    doctor_parser.add_argument("--pack-version", required=True)
    doctor_parser.set_defaults(handler=_cmd_doctor)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return args.handler(args)
    except (PlanError, TargetPackInstallError, TargetPackBuildError) as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"error: {CLI_INPUT_INVALID_REASON_V2}\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
