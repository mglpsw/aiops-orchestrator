#!/usr/bin/env python3
"""AgentReview v2 -- external material ingress boundary (`#200-G4`).

## What this script is, and deliberately is not

This is the ingress boundary for an AgentReview v2 operational run, closing
`#200`'s G4 primitive: **every** caller-controlled material source is
validated or safely read before its bytes are handed to any downstream
consumer, and no legitimate-shaped external failure escapes as a raw
exception, filesystem path, or caller-supplied byte on stderr.

It is deliberately **not** the full two-process operational product `#277`
built and then stopped on (`STOP_200F_ARCHITECTURE_NOT_CONVERGING`). Subject
materialisation from committed bytes (`#200`-G1), scope completeness
(`#200`-G3), and run composition/execution are each their own primitive, not
yet independently qualified on `master`, and are not wired here -- wiring
them in this slice would smuggle their authority in under G4's review rather
than getting it independently reviewed on its own terms. `#200`-G5
("operational product recomposition") is where G1-G4 come together, and only
once each is independently `PRIMITIVE_NON_REFUTED`.

What this script proves is narrower than a full run, but load-bearing
regardless of how the primitives above eventually compose: given the exact
external material shapes a real run receives -- nine scalar flags, a profile
document, a grouping-policy document, a diff document, a directory of
per-chunk offline response files, and the inner-control-fd environment
variable -- every one of them is validated or safely read at this boundary,
`operational_ingress_v2` in every case, before use.

## No argv spelling of inner authority

The predecessor two-process design is not reproduced here, so there is no
inner authority channel to protect in this script; documented for continuity
with `#277`'s own note on the subject, not because the concern currently
applies.

## Errors

The boundary catches ``ExpectedOperationalRefusalV2`` -- one family, caught
structurally. It does not enumerate owner exception classes, and it does not
catch anything else: a programmer defect escapes as a traceback, on purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The script's own tree, derived from its location rather than from any
# caller-supplied value.
_OWN_ROOT_V2 = Path(__file__).resolve().parents[1]
if str(_OWN_ROOT_V2) not in sys.path:
    sys.path.insert(0, str(_OWN_ROOT_V2))

from app.agent_review.contracts_v2 import ChunkResponseEnvelopeV2  # noqa: E402
from app.agent_review.diff_acquisition_v2 import parse_unified_diff  # noqa: E402
from app.agent_review.operational_ingress_v2 import (  # noqa: E402
    INGRESS_USAGE_ERROR_REASON_V2,
    NoEchoArgumentParserV2,
    OperationalIngressError,
    read_caller_document_text_v2,
    read_offline_response_document_v2,
    resolve_inner_control_fd_v2,
    validate_caller_document_v2,
    validate_existing_directory_v2,
    validate_existing_file_v2,
    validate_public_inputs_v2,
)
from app.agent_review.operational_refusal_v2 import (  # noqa: E402
    ExpectedOperationalRefusalV2,
)
from app.agent_review.profile_loader_v2 import load_target_profile_text_v2  # noqa: E402
from app.agent_review.semantic_grouping_policy_v2 import (  # noqa: E402
    SemanticGroupingPolicyV2,
)

_EXIT_OK_V2 = 0
#: A typed operational refusal. stderr carries exactly one reason code.
_EXIT_REFUSED_V2 = 2
#: A command-line usage error. Distinct from a refusal on purpose: argparse
#: exits 2 by default, which collides with the refusal code, so a consumer
#: reading "exit 2 => parse the reason code from stderr" would get a usage
#: block instead. sysexits.h EX_USAGE.
_EXIT_USAGE_V2 = 64

#: The environment variable a future inner-control channel (`#200`-G1/G5)
#: will use to hand this process its authority document. No such channel is
#: read here -- there is nothing on `master` to verify a document against
#: yet -- but the value is caller-controlled material the instant a caller
#: (or a CI workflow file under caller control) can set it, so resolving it
#: safely is this slice's concern regardless of who eventually consumes it.
_INNER_CONTROL_FD_ENV_VAR_V2 = "AGENT_REVIEW_INNER_CONTROL_FD_V2"


class _NoEchoUsageParserV2(NoEchoArgumentParserV2, argparse.ArgumentParser):
    """Only public inputs; a usage error names no argv text.

    ``allow_abbrev=False`` is defence in depth, not the mechanism: there is
    no private, authority-bearing flag here for prefix matching to reach.
    """


def _build_public_parser_v2() -> argparse.ArgumentParser:
    parser = _NoEchoUsageParserV2(
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
    return parser


def _validated_inputs_v2(arguments: argparse.Namespace):
    """Pre-seal validation of every scalar public input.

    ``pr_number`` is converted here rather than by ``argparse type=int``: a
    non-numeric value would otherwise make argparse's own ``error()`` fire
    before ingress ever sees it -- closed for the *message* by
    ``NoEchoArgumentParserV2``, but still an error outside this product's own
    reason-code vocabulary if left to argparse's type conversion.
    """
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


def _requested_chunk_ids_v2(file_diffs) -> list[str]:
    """A deterministic, boundary-only stand-in for real chunk planning.

    Real chunk identity is a payload-building concern (`#200`-G5's eventual
    composition), not an ingress one, and is not reproduced here. This
    boundary only needs *some* safe, deterministic identifier per changed
    file to demonstrate that reading an entry out of ``--responses`` is
    closed for every shape of content that entry can hold -- present and
    well-formed, present and malformed, and absent.
    """
    return [f"chunk-{index:04d}" for index in range(len(file_diffs))]


def run_ingress_boundary_v2(argv: list[str]) -> dict[str, object]:
    """Validate and safely read every caller-controlled material source.

    Returns a plain, JSON-serialisable summary. Raises
    ``OperationalIngressError`` -- and only that, or an un-family-marked
    programmer defect -- for any unusable material.
    """
    arguments = _build_public_parser_v2().parse_args(argv)
    inputs = _validated_inputs_v2(arguments)

    # Caller-supplied document CONTENT is ingress material exactly like a
    # flag value. `#277` round 1 read `--profile`/`--grouping-policy` with a
    # raw `model_validate_json` call after validating only the nine scalars;
    # a malformed document produced a raw `pydantic.ValidationError`
    # traceback that echoed the file's own bytes (`input_value=`) to stderr.
    # The profile goes through its owner's typed loader, which rejects the
    # ambiguous-YAML-document shapes a bare JSON parse would silently accept.
    profile = load_target_profile_text_v2(
        read_caller_document_text_v2(arguments.profile, field_name="profile")
    )
    grouping_policy = validate_caller_document_v2(
        arguments.grouping_policy,
        model=SemanticGroupingPolicyV2,
        field_name="grouping_policy",
    )

    diff_text = read_caller_document_text_v2(arguments.diff, field_name="diff")
    file_diffs = parse_unified_diff(diff_text)

    responses_root = validate_existing_directory_v2(arguments.responses)

    # The mandatory RED witness this slice exists to close: `#277` round 2
    # found `--responses` file *content* still read raw. Every requested
    # chunk id is read through the same pre-seal document authority as
    # `--profile`/`--grouping-policy` -- absent is `None` (not yet answered,
    # an ordinary state), present-and-malformed is a typed refusal, and
    # neither the chunk id nor the file's bytes ever reach a reason code.
    answered = 0
    unanswered = 0
    for chunk_id in _requested_chunk_ids_v2(file_diffs):
        envelope = read_offline_response_document_v2(
            responses_root,
            chunk_id,
            model=ChunkResponseEnvelopeV2,
        )
        if envelope is None:
            unanswered += 1
        else:
            answered += 1

    # The inner-control-fd environment variable is caller-controlled material
    # read at the process boundary. No inner channel exists to actually read
    # from on `master` yet (`#200`-G1/G5), so the value is validated and
    # discarded rather than consumed -- but validating it here, before any
    # code that would ever try to use it as a real fd, is what prevents the
    # `OverflowError` and the `fd=0` stdin hang `#277` left open.
    control_fd = resolve_inner_control_fd_v2(
        os.environ.get(_INNER_CONTROL_FD_ENV_VAR_V2)
    )

    return {
        "schema_id": "agent-review.operational-ingress.v2",
        "repo": inputs.repo,
        "pr_number": inputs.pr_number,
        "delivery_id": inputs.delivery_id,
        "profile_network_policy": profile.policies.network_policy,
        "grouping_policy_rule_count": len(grouping_policy.rules),
        "changed_file_count": len(file_diffs),
        "responses": {"answered": answered, "unanswered": unanswered},
        "inner_control_fd_present": control_fd is not None,
    }


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        summary = run_ingress_boundary_v2(raw_argv)
    except ExpectedOperationalRefusalV2 as refusal:
        # ONE family, caught structurally. No enumeration, and deliberately
        # no `except Exception`: a programmer defect must still escape as a
        # traceback rather than be dressed up as an orderly refusal.
        sys.stderr.write(f"{refusal.reason_code}\n")
        if refusal.reason_code == INGRESS_USAGE_ERROR_REASON_V2:
            return _EXIT_USAGE_V2
        return _EXIT_REFUSED_V2

    sys.stdout.write(json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n")
    return _EXIT_OK_V2


if __name__ == "__main__":
    raise SystemExit(main())
