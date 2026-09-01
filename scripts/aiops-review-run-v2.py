#!/usr/bin/env python3
"""AgentReview v2 operational run -- the product boundary (`#200-F`).

Two processes, one entry point.

**Outer (bootstrap).** Parses ordinary public flags, validates every caller
input *before anything is sealed*, materialises the exact toolrepo execution
subject from committed bytes, and starts the semantic child with an inherited
control descriptor.

**Inner (semantic).** Reads its authority from that descriptor, verifies the
document against the code it is actually executing, and only then composes the
run.

## No argv spelling of inner authority

`#276` carried inner authority on private flags and guarded them textually;
``argparse`` prefix abbreviation walked past the guard. There are no such flags
here. The parser below accepts only public inputs, and which process this is
is decided by *whether the channel is there*, not by a flag anybody can type.
``--_controlled-inner``, ``--_inner-subject-root`` and every abbreviation of
them are simply unknown options.

Note the consequence, which is stronger than the predecessor's goal: there is
no inner entry point to invoke. Running this script without a channel does not
produce a forged inner -- it produces an ordinary outer that derives its own
authority.

## What the outer bootstrap does *not* establish

The outer necessarily executes from the ordinary checkout, before any subject
is sealed, so an untracked module planted next to it can still run first. That
is a real, recorded limitation:

    bootstrap:
      remotely_attested: false

The inner is not exposed to it: the inner executes from a subject materialised
from committed bytes, where an untracked shadow cannot exist. Closing it for
the outer needs an attested launcher, which this slice does not build and does
not claim.

## Errors

The boundary catches ``ExpectedOperationalRefusalV2`` -- one family, caught
structurally. It does not enumerate owner exception classes, and it does not
catch anything else: a programmer defect escapes as a traceback, on purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The script's own tree, derived from its location rather than from any
# caller-supplied value. In the inner this is the controlled subject, because
# that is the copy the outer started.
_OWN_ROOT_V2 = Path(__file__).resolve().parents[1]
if str(_OWN_ROOT_V2) not in sys.path:
    sys.path.insert(0, str(_OWN_ROOT_V2))

from app.agent_review.operational_inner_control_v2 import (  # noqa: E402
    INNER_CONTROL_CHANNEL_ABSENT_REASON_V2,
    INNER_CONTROL_FD_V2,
    InnerControlChannelError,
    InnerControlDocumentV2,
    encode_inner_control_document_v2,
    read_inner_control_document_v2,
    verify_inner_control_document_v2,
)
from app.agent_review.operational_refusal_v2 import (  # noqa: E402
    ExpectedOperationalRefusalV2,
)

_EXIT_OK_V2 = 0
_EXIT_REFUSED_V2 = 2


def _build_public_parser_v2() -> argparse.ArgumentParser:
    """Only public inputs. Nothing here can express inner authority.

    ``allow_abbrev=False`` is defence in depth, not the mechanism: with no
    authority-bearing option to abbreviate, prefix matching has nothing to
    reach. `#276` relied on the equivalent guard *as* the mechanism and it was
    bypassed.
    """
    parser = argparse.ArgumentParser(
        prog="aiops-review-run-v2", allow_abbrev=False, add_help=True
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tested-merge-sha", required=True)
    parser.add_argument("--toolchain-digest", required=True)
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--event-action", required=True)
    parser.add_argument("--delivery-id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--grouping-policy", required=True)
    parser.add_argument("--diff", required=True)
    parser.add_argument("--responses", required=True)
    parser.add_argument("--target-root", required=True)
    return parser


def _validated_inputs_v2(arguments: argparse.Namespace):
    """Pre-seal validation of every public input.

    ``pr_number`` is converted here rather than by ``argparse type=int``: a
    non-numeric value would otherwise make argparse exit(2) with its own
    message before the ingress authority ever sees it, producing an error
    outside the product's own vocabulary.
    """
    from app.agent_review.operational_ingress_v2 import (
        INGRESS_INVALID_PUBLIC_INPUT_REASON_V2,
        OperationalIngressError,
        validate_public_inputs_v2,
    )

    try:
        pr_number: object = int(arguments.pr_number)
    except (TypeError, ValueError):
        raise OperationalIngressError("operational_ingress_invalid_pr_number") from None

    return validate_public_inputs_v2(
        {
            "repo": arguments.repo,
            "pr_number": pr_number,
            "base_sha": arguments.base_sha,
            "head_sha": arguments.head_sha,
            "tested_merge_sha": arguments.tested_merge_sha,
            "toolchain_digest": arguments.toolchain_digest,
            "event_type": arguments.event_type,
            "event_action": arguments.event_action,
            "delivery_id": arguments.delivery_id,
        }
    )


def _run_outer_bootstrap_v2(argv: list[str]) -> int:
    """Validate, materialise the execution subject, start the inner."""
    from app.agent_review.operational_bounded_git_v2 import run_bounded_git_v2
    from app.agent_review.operational_ingress_v2 import (
        validate_existing_directory_v2,
        validate_existing_file_v2,
    )
    from app.agent_review.operational_subject_v2 import (
        materialise_toolrepo_execution_subject_v2,
    )

    arguments = _build_public_parser_v2().parse_args(argv)
    _validated_inputs_v2(arguments)

    # Caller-supplied paths are checked here too: existence and absoluteness
    # are public-input properties, and the inner must not be started for a run
    # that cannot possibly proceed.
    for candidate in (arguments.profile, arguments.grouping_policy, arguments.diff):
        validate_existing_file_v2(candidate)
    validate_existing_directory_v2(arguments.responses)
    validate_existing_directory_v2(arguments.target_root)

    toolrepo_sha = (
        run_bounded_git_v2(["rev-parse", "HEAD"], cwd=_OWN_ROOT_V2)
        .stdout.decode("utf-8")
        .strip()
    )

    workspace = Path(tempfile.mkdtemp(prefix="agent-review-v2-"))
    try:
        subject = materialise_toolrepo_execution_subject_v2(
            toolrepo_root=_OWN_ROOT_V2,
            toolrepo_sha=toolrepo_sha,
            destination=workspace / "toolrepo",
        )
        document = InnerControlDocumentV2(
            subject_root=str(subject.root),
            declared_toolrepo_sha=subject.toolrepo_sha,
            subject_digest=subject.subject_digest,
        )

        read_end, write_end = os.pipe()
        try:
            os.write(write_end, encode_inner_control_document_v2(document))
        finally:
            os.close(write_end)

        try:
            # -I: isolated. No PYTHONPATH, no user site-packages, no cwd on
            # sys.path -- so the inner cannot be steered by ambient
            # environment the way the outer's own startup can.
            # -B: no bytecode written, so a stale .pyc cannot execute.
            completed = subprocess.run(  # noqa: S603 -- fixed argv, no shell
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(subject.root / "scripts" / "aiops-review-run-v2.py"),
                    *argv,
                ],
                pass_fds=(read_end,),
                env={
                    "PATH": os.defpath,
                    "LC_ALL": "C",
                    "LANG": "C",
                    "AGENT_REVIEW_INNER_CONTROL_FD_V2": str(read_end),
                },
                capture_output=True,
                check=False,
            )
        finally:
            os.close(read_end)

        sys.stdout.write(completed.stdout.decode("utf-8", "replace"))
        sys.stderr.write(completed.stderr.decode("utf-8", "replace"))
        return completed.returncode
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _run_inner_semantic_v2(argv: list[str], document: InnerControlDocumentV2) -> int:
    """Verify the document against the running code, then compose."""
    import json as _json

    import tempfile as _tempfile

    from app.agent_review.diff_acquisition_v2 import parse_unified_diff
    from app.agent_review.contracts_v2 import TargetProfileV2
    from app.agent_review.operational_ingress_v2 import (
        validate_existing_directory_v2,
        validate_existing_file_v2,
    )
    from app.agent_review.operational_run_v2 import execute_operational_run_v2
    from app.agent_review.operational_subject_v2 import (
        materialise_controlled_target_subject_v2,
    )
    from app.agent_review.semantic_grouping_policy_v2 import SemanticGroupingPolicyV2

    verified = verify_inner_control_document_v2(
        document, executing_module_path=Path(__file__).resolve()
    )

    arguments = _build_public_parser_v2().parse_args(argv)
    inputs = _validated_inputs_v2(arguments)

    profile = TargetProfileV2.model_validate_json(
        validate_existing_file_v2(arguments.profile).read_text(encoding="utf-8")
    )
    grouping_policy = SemanticGroupingPolicyV2.model_validate_json(
        validate_existing_file_v2(arguments.grouping_policy).read_text(encoding="utf-8")
    )
    diff_text = validate_existing_file_v2(arguments.diff).read_text(encoding="utf-8")
    file_diffs = parse_unified_diff(diff_text)

    # The controlled target subject: the target's committed bytes at the head
    # under review, severed from the checkout they came from. Materialised
    # before any review material is read, so everything downstream sees a
    # subject that cannot change underneath it -- and so a target repository
    # that is rewritten or deleted mid-run cannot alter what was reviewed.
    target_subject_root = Path(_tempfile.mkdtemp(prefix="agent-review-target-"))
    target_subject = materialise_controlled_target_subject_v2(
        target_root=validate_existing_directory_v2(arguments.target_root),
        head_sha=inputs.head_sha,
        destination=target_subject_root / "subject",
    )

    responses_root = Path(arguments.responses)

    def _offline_transport_v2(payload):
        """Read one prepared response. No network, no provider, ever."""
        response_path = responses_root / f"{payload.chunk_id}.json"
        if not response_path.is_file():
            return None
        return response_path.read_text(encoding="utf-8")

    result = execute_operational_run_v2(
        inputs=inputs,
        # The identity of the code that is actually running, established by
        # the verification immediately above -- never the caller's claim.
        verified_toolrepo_sha=verified.declared_toolrepo_sha,
        profile=profile,
        grouping_policy=grouping_policy,
        file_diffs=file_diffs,
        transport=_offline_transport_v2,
        evidence_hash=arguments.toolchain_digest,
    )

    sys.stdout.write(
        _json.dumps(
            {
                "schema_id": "agent-review.operational-run.v2",
                "run_id": result.manifest.run_id,
                "toolrepo_sha": verified.declared_toolrepo_sha,
                "target_subject": {
                    "head_sha": target_subject.head_sha,
                    "file_count": target_subject.file_count,
                },
                "readiness_state": result.readiness_state.value,
                "reason_codes": list(result.reason_codes),
                "finding_count": len(result.findings),
                "scope": {
                    "complete": result.scope.scope_complete,
                    "blocked": result.scope.blocked,
                    "changed_paths": list(result.scope.changed_paths),
                    "reviewable_paths": list(result.scope.reviewable_paths),
                    "metadata_only_paths": list(result.scope.metadata_only_paths),
                    "unsupported_paths": list(result.scope.unsupported_paths),
                    "must_review_blocked_paths": list(
                        result.scope.must_review_blocked_paths
                    ),
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    shutil.rmtree(target_subject_root, ignore_errors=True)
    return _EXIT_OK_V2


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    control_fd = int(
        os.environ.get("AGENT_REVIEW_INNER_CONTROL_FD_V2", INNER_CONTROL_FD_V2)
    )
    try:
        document = read_inner_control_document_v2(control_fd)
    except InnerControlChannelError as exc:
        if exc.reason_code != INNER_CONTROL_CHANNEL_ABSENT_REASON_V2:
            # A channel that is present but wrong is never silently demoted to
            # "then I must be the outer" -- that would let a malformed
            # document buy a second, unconstrained attempt.
            sys.stderr.write(f"{exc.reason_code}\n")
            return _EXIT_REFUSED_V2
        document = None

    try:
        if document is None:
            return _run_outer_bootstrap_v2(raw_argv)
        return _run_inner_semantic_v2(raw_argv, document)
    except ExpectedOperationalRefusalV2 as refusal:
        # ONE family, caught structurally. No enumeration, and deliberately no
        # `except Exception`: a programmer defect must still escape as a
        # traceback rather than be dressed up as an orderly refusal.
        sys.stderr.write(f"{refusal.reason_code}\n")
        return _EXIT_REFUSED_V2


if __name__ == "__main__":
    raise SystemExit(main())
