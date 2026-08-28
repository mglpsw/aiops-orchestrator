#!/usr/bin/env python3
"""`#200-E` Phase 3 -- the AgentReview v2 product CLI (issue #200,
successor to the `FROZEN_FORENSIC` `#274`).

## Two process epochs, not one script doing two jobs

**Outer bootstrap** (default invocation): parses only its own bootstrap
inputs, materializes the exact `ToolrepoExecutionSubjectV2` for this
toolrepo's own declared HEAD, and launches the SAME script -- the copy now
living inside that materialized subject, not this mutable development
checkout -- as the inner semantic child, under a bounded environment. It
propagates the child's stdout/stderr/exit code unchanged and performs NO
review semantics itself: no profile interpretation, no diff acquisition, no
manifest/payload/content/transport/synthesis/readiness.

**Inner semantic child** (`--_controlled-inner`, never a public contract --
an internal marker the outer process uses to invoke itself from inside the
subject): owns the actual review. It runs `app.agent_review.operational_
run_v2.run_operational_review_v2` and prints the canonical
`ReviewReadinessV2` JSON to stdout, nothing else.

## Exit/output contract

stdout carries the canonical readiness JSON on success, and nothing else.
stderr carries diagnostics/refusals. Exit `0` means a canonical readiness
result was emitted -- readiness itself may be `manual_required`,
`blocked_pipeline`, or any other non-ready state; this CLI never forces
`ready`. Exit nonzero means no canonical artifact was produced. There is no
`--output <path>`: this CLI has no filesystem-output authority at all.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_THIS_SCRIPT = Path(__file__).resolve()
_TOOLREPO_ROOT = _THIS_SCRIPT.parents[1]
if str(_TOOLREPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLREPO_ROOT))

from app.agent_review._bounded_git_child_env_v2 import bounded_child_env_v2  # noqa: E402
from app.agent_review.toolrepo_execution_subject_v2 import (  # noqa: E402
    ToolrepoExecutionSubjectError,
    materialize_toolrepo_execution_subject_v2,
)

_TOOLREPO_BOUNDED_PATHS = ("app", "scripts/aiops-review-run-v2.py")

CLI_BOOTSTRAP_INPUT_INVALID_REASON_V2 = "cli_bootstrap_input_invalid"
CLI_INNER_LAUNCH_FAILED_REASON_V2 = "cli_inner_launch_failed"
CLI_INNER_INPUT_INVALID_REASON_V2 = "cli_inner_input_invalid"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aiops-review-run-v2.py",
        description="AgentReview v2 operational product CLI (provider-free offline transport).",
    )
    parser.add_argument("--_controlled-inner", action="store_true", dest="controlled_inner",
                         help=argparse.SUPPRESS)
    parser.add_argument("--_inner-subject-root", dest="inner_subject_root", default=None,
                         help=argparse.SUPPRESS)
    parser.add_argument("--_inner-declared-toolrepo-sha", dest="inner_declared_toolrepo_sha", default=None,
                         help=argparse.SUPPRESS)
    parser.add_argument("--_diagnose-source-origin", action="store_true", dest="diagnose_source_origin",
                         help=argparse.SUPPRESS)
    parser.add_argument("--toolchain-digest", required=True,
                         help="sha256 identity of the interpreter/third-party dependency set -- "
                              "a DISTINCT authority from --_inner-declared-toolrepo-sha (project "
                              "source identity); never derived from one another.")
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tested-merge-sha", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--trusted-profile-root", required=True, type=Path)
    parser.add_argument("--grouping-policy", required=True, type=Path,
                         help="Path to a SemanticGroupingPolicyV2 JSON document.")
    parser.add_argument("--responses-dir", required=True, type=Path,
                         help="Offline transport: one pre-placed {chunk_id}.json response per chunk.")
    parser.add_argument("--pr-state", required=True, choices=["open", "closed", "merged"])
    parser.add_argument("--event-type", required=True,
                         choices=["pull_request", "pull_request_target", "manual", "replay"])
    parser.add_argument("--event-action", required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--max-lines-per-chunk", type=int, default=200)
    return parser


def _run_outer_bootstrap(argv: list[str]) -> int:
    toolrepo_sha_probe = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=_TOOLREPO_ROOT, capture_output=True, text=True, check=False
    )
    if toolrepo_sha_probe.returncode != 0:
        print(json.dumps({"error_class": CLI_BOOTSTRAP_INPUT_INVALID_REASON_V2}), file=sys.stderr)
        return 1
    declared_toolrepo_sha = toolrepo_sha_probe.stdout.strip()

    try:
        with materialize_toolrepo_execution_subject_v2(
            _TOOLREPO_ROOT, declared_toolrepo_sha=declared_toolrepo_sha, bounded_paths=_TOOLREPO_BOUNDED_PATHS
        ) as subject:
            inner_script = subject.root / "scripts" / "aiops-review-run-v2.py"
            home = subject.root.parent / "inner-home"
            home.mkdir(parents=True, exist_ok=True)
            child_env = bounded_child_env_v2(isolated_home=home)
            result = subprocess.run(
                [
                    sys.executable, "-I", "-B", str(inner_script),
                    "--_controlled-inner",
                    "--_inner-subject-root", str(subject.root),
                    "--_inner-declared-toolrepo-sha", declared_toolrepo_sha,
                    *argv,
                ],
                cwd=subject.root, env=child_env,
            )
            return result.returncode
    except ToolrepoExecutionSubjectError as exc:
        print(json.dumps({"error_class": exc.reason_code}), file=sys.stderr)
        return 1


def _run_inner_semantic_child(args: argparse.Namespace) -> int:
    # Deliberately imported HERE, not at module top level: importing these
    # inside outer-bootstrap mode would make the OUTER process capable of
    # running review semantics merely by having them loaded, even if never
    # called -- the import boundary is part of the epoch boundary, not just
    # the call boundary.
    from app.agent_review.contracts_v2 import PullRequestStateV2, RunOriginV2
    from app.agent_review.operational_run_v2 import OperationalRunError, run_operational_review_v2
    from app.agent_review.operational_run_v2 import OperationalReviewInputsV2
    from app.agent_review.controlled_subject_v2 import ControlledSubjectError
    from app.agent_review.diff_acquisition_v2 import DiffAcquisitionError
    from app.agent_review.payload_builder_v2 import PayloadBuilderError
    from app.agent_review.profile_loader_v2 import TargetProfileLoadErrorV2
    from app.agent_review.review_content_extraction_v2 import ExtractionBlockedError
    from app.agent_review.review_transport_v2 import offline_file_transport_v2
    from app.agent_review.run_assembly_v2 import RunAssemblyError
    from app.agent_review.semantic_grouping_policy_v2 import SemanticGroupingError, SemanticGroupingPolicyV2

    if args.diagnose_source_origin:
        # §5: private/test-only diagnostic proving the semantic child's
        # own project-owned source imported from the materialized
        # ToolrepoExecutionSubjectV2, not the mutable development
        # checkout. Never part of the canonical stdout contract -- printed
        # to stderr, and this mode exits without running any review.
        import app.agent_review.controlled_subject_v2 as _csv2
        import app.agent_review.operational_run_v2 as _orv2
        import app.agent_review.review_transport_v2 as _rtv2
        print(json.dumps({
            "operational_run_v2_file": _orv2.__file__,
            "controlled_subject_v2_file": _csv2.__file__,
            "review_transport_v2_file": _rtv2.__file__,
            "this_script_file": str(_THIS_SCRIPT),
        }), file=sys.stderr)
        return 0

    try:
        grouping_policy_text = args.grouping_policy.read_text(encoding="utf-8")
        grouping_policy = SemanticGroupingPolicyV2.model_validate_json(grouping_policy_text)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(json.dumps({"error_class": CLI_INNER_INPUT_INVALID_REASON_V2}), file=sys.stderr)
        return 1

    inputs = OperationalReviewInputsV2(
        source_target_root=args.target_root,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        tested_merge_sha=args.tested_merge_sha,
        toolrepo_sha=args.inner_declared_toolrepo_sha,
        toolchain_digest=args.toolchain_digest,
        evidence_hash="0" * 64,
        repo=args.repo,
        pr_number=args.pr_number,
        trusted_profile_root=args.trusted_profile_root,
        grouping_policy=grouping_policy,
        transport=offline_file_transport_v2(args.responses_dir),
        pr_state=PullRequestStateV2(args.pr_state),
        origin=RunOriginV2(
            event_type=args.event_type, event_action=args.event_action, delivery_id=args.delivery_id
        ),
        max_lines_per_chunk=args.max_lines_per_chunk,
    )

    try:
        readiness = run_operational_review_v2(inputs)
    except (
        TargetProfileLoadErrorV2,
        SemanticGroupingError,
        ControlledSubjectError,
        DiffAcquisitionError,
        RunAssemblyError,
        PayloadBuilderError,
        ExtractionBlockedError,
        OperationalRunError,
    ) as exc:
        print(json.dumps({"error_class": exc.reason_code}), file=sys.stderr)
        return 1

    print(readiness.model_dump_json())
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_argument_parser()
    args = parser.parse_args(raw_argv)

    if args.controlled_inner:
        return _run_inner_semantic_child(args)

    return _run_outer_bootstrap(raw_argv)


if __name__ == "__main__":
    raise SystemExit(main())
