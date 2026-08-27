#!/usr/bin/env python3
"""Execute one AgentReview v2 operational review from a real checkout (`#200-D`).

Thin wiring around ``operational_run_v2.run_operational_review_v2`` -- this
CLI holds no pipeline semantics. It parses explicit inputs, loads canonical
artifacts through the parsers that already own them, selects an existing
transport, calls the library, and writes the existing ``ReviewReadinessV2``
artifact. It interprets no profile, acquires no diff, extracts no content,
parses no Router response and computes no readiness.

Transport modes:

* ``offline`` -- ``offline_file_transport_v2`` over a directory of
  pre-placed ``ChunkReviewTransportEnvelopeV1`` documents;
* ``router``  -- ``agent_router_transport_v2``. The credential is read from
  the environment (default ``AGENT_ROUTER_API_KEY``), never from argv, and
  is never printed or persisted. A missing key fails through the transport's
  own ``router_disabled`` semantics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402

from app.agent_review.authoritative_ci_snapshot_v2 import (  # noqa: E402
    parse_authoritative_ci_snapshot_v2,
)
from app.agent_review.contracts_v2 import (  # noqa: E402
    PullRequestStateV2,
    RequiredCheckResultV2,
    RunOriginV2,
)
from app.agent_review.required_check_provenance_v2 import (  # noqa: E402
    RequiredCheckProvenanceV2,
)
from app.agent_review.review_content_v2 import (  # noqa: E402
    load_dlp_policy_declaration_v2,
)
from app.agent_review.operational_run_v2 import (  # noqa: E402
    OperationalRunError,
    run_operational_review_v2,
)
from app.agent_review.review_transport_v2 import (  # noqa: E402
    ChunkTransportError,
    agent_router_transport_v2,
    offline_file_transport_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (  # noqa: E402
    SemanticGroupingPolicyV2,
)
from app.common.strict_json import strict_json_loads  # noqa: E402

CONTRACT_VERSION_INVALID_REASON_V2 = "contract_version_invalid"
INPUT_INVALID_REASON_V2 = "input_invalid"
TRANSPORT_MODE_INVALID_REASON_V2 = "transport_mode_invalid"
OUTPUT_UNWRITABLE_REASON_V2 = "output_unwritable"

# 1, matching both sibling v2 CLIs. 2 is argparse's usage-error code, so
# reusing it would make "you passed the wrong flags" indistinguishable from
# "the review refused".
REFUSAL_EXIT_CODE = 1

DEFAULT_ROUTER_API_KEY_ENV = "AGENT_ROUTER_API_KEY"


class RunCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="run one AgentReview v2 operational review")
    parser.add_argument("--contract-version", required=True, help="must be exactly 'v2'")
    parser.add_argument("--repo-root", required=True, help="checkout under review")
    parser.add_argument(
        "--target-profile",
        required=True,
        help="TRUSTED base/default checkout containing .aiops/target-profile.v2.yaml",
    )
    parser.add_argument("--grouping-policy", required=True, help="JSON: SemanticGroupingPolicyV2")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tested-merge-sha", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--toolrepo-sha", required=True)
    parser.add_argument("--evidence-hash", required=True)
    parser.add_argument("--max-lines-per-chunk", required=True, type=int)
    parser.add_argument("--pr-state", required=True, choices=["open", "closed", "merged"])
    parser.add_argument("--run-origin", required=True, help="JSON: RunOriginV2")
    parser.add_argument(
        "--checks-snapshot",
        required=True,
        help="AuthoritativeCheckSnapshotV2 JSON, parsed by its canonical parser",
    )
    parser.add_argument("--toolchain-digest", required=True)
    parser.add_argument(
        "--checks",
        help="optional JSON array of RequiredCheckResultV2 CLAIMS; re-verified by `#201-C0`",
    )
    parser.add_argument(
        "--checks-provenance",
        help="optional JSON array of RequiredCheckProvenanceV2, one per --checks entry",
    )
    parser.add_argument(
        "--dlp-policy",
        help=(
            "optional JSON DlpPolicyDeclarationV2; without it a target's "
            "declared inline DLP rules never evaluate"
        ),
    )
    parser.add_argument("--transport", required=True, choices=["offline", "router"])
    parser.add_argument("--offline-responses-dir", help="offline mode: transport envelope directory")
    parser.add_argument("--router-base-url", help="router mode: Agent Router base URL")
    parser.add_argument("--router-model", help="router mode: logical review preset")
    parser.add_argument(
        "--router-api-key-env",
        default=DEFAULT_ROUTER_API_KEY_ENV,
        help=f"env var holding the Router credential (default {DEFAULT_ROUTER_API_KEY_ENV})",
    )
    parser.add_argument("--output", required=True, help="path to write the ReviewReadinessV2 JSON")
    return parser.parse_args(argv)


def _read_json(path: str) -> object:
    """Strict: duplicate keys and non-finite numbers are refused.

    Plain ``json.loads`` is last-wins, so a policy document carrying two
    ``rules`` keys would validate against whichever survived -- and its own
    ``policy_sha256`` self-check would agree, because it hashes the surviving
    material. The repository's ``strict_json_loads`` is the existing authority
    for exactly this.
    """

    try:
        return strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        raise RunCliError(INPUT_INVALID_REASON_V2) from exc


def _build_transport(args: argparse.Namespace):
    """Select an EXISTING transport. This CLI never implements one."""

    if args.transport == "offline":
        if not args.offline_responses_dir:
            raise RunCliError(TRANSPORT_MODE_INVALID_REASON_V2)
        return offline_file_transport_v2(Path(args.offline_responses_dir))

    if not args.router_base_url or not args.router_model:
        raise RunCliError(TRANSPORT_MODE_INVALID_REASON_V2)
    # Credential from the environment only: never argv, never echoed, never
    # written. An absent key is not an error here -- the transport itself
    # refuses with `router_disabled`, which is the established semantics.
    return agent_router_transport_v2(
        base_url=args.router_base_url,
        api_key=os.environ.get(args.router_api_key_env),
        model=args.router_model,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.contract_version != "v2":
            raise RunCliError(CONTRACT_VERSION_INVALID_REASON_V2)

        try:
            grouping_policy = SemanticGroupingPolicyV2.model_validate(
                _read_json(args.grouping_policy)
            )
            origin = RunOriginV2.model_validate(_read_json(args.run_origin))
        except ValidationError as exc:
            raise RunCliError(INPUT_INVALID_REASON_V2) from exc

        try:
            checks = (
                [RequiredCheckResultV2.model_validate(item) for item in _read_json(args.checks)]
                if args.checks
                else []
            )
            provenance = (
                [
                    RequiredCheckProvenanceV2.model_validate(item)
                    for item in _read_json(args.checks_provenance)
                ]
                if args.checks_provenance
                else []
            )
            # The canonical loader, not `model_validate`: it owns the
            # `dlp_policy_not_host_owned` refusal for a policy naming code
            # inside the target repository. Reporting that as generic
            # `input_invalid` would hide a security-specific rejection.
            dlp_policy = (
                load_dlp_policy_declaration_v2(_read_json(args.dlp_policy))
                if args.dlp_policy
                else None
            )
        except (ValidationError, TypeError, ValueError) as exc:
            # TWO BOUNDARIES, TWO RULES -- conflating them is what kept
            # producing untyped escapes:
            #
            #   input parsing (here)   -- the operator handed us a file. ANY
            #       parse failure is their input, so it is always a typed
            #       refusal. A codeless `ValueError` (e.g. a bare JSON string
            #       reaching `dict(raw)`) is bad input, not a defect.
            #   authority delegation   -- a v2 module refused. Preserve ITS
            #       code; a codeless failure there really is our defect and
            #       must stay a crash.
            #
            # A specific code is still preferred when the authority supplied
            # one, so `dlp_policy_not_host_owned` is not flattened away.
            reason_code = getattr(exc, "reason_code", None)
            if isinstance(reason_code, str) and reason_code:
                raise RunCliError(reason_code) from exc
            raise RunCliError(INPUT_INVALID_REASON_V2) from exc

        try:
            snapshot = parse_authoritative_ci_snapshot_v2(
                Path(args.checks_snapshot).read_bytes()
            )
        except OSError as exc:
            raise RunCliError(INPUT_INVALID_REASON_V2) from exc
        except Exception as exc:
            # `parse_authoritative_ci_snapshot_v2` refuses through its own
            # typed family (`RequiredCheckProvenanceErrorV2`), not `OSError`.
            # Preserve its code -- but only if it HAS one: laundering a
            # genuine tool defect into `input_invalid` would blame the
            # operator's file for our bug, inverting the rule this CLI's
            # library states.
            reason_code = getattr(exc, "reason_code", None)
            if not isinstance(reason_code, str) or not reason_code:
                raise
            raise RunCliError(reason_code) from exc

        outcome = run_operational_review_v2(
            repo_root=Path(args.repo_root),
            target_profile_root=Path(args.target_profile),
            grouping_policy=grouping_policy,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            tested_merge_sha=args.tested_merge_sha,
            pr_number=args.pr_number,
            toolrepo_sha=args.toolrepo_sha,
            evidence_hash=args.evidence_hash,
            transport=_build_transport(args),
            pr_state=PullRequestStateV2(args.pr_state),
            origin=origin,
            snapshot=snapshot,
            toolchain_digest=args.toolchain_digest,
            max_lines_per_chunk=args.max_lines_per_chunk,
            checks=checks,
            provenance=provenance,
            dlp_policy=dlp_policy,
        )

    except (RunCliError, OperationalRunError, ChunkTransportError) as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return REFUSAL_EXIT_CODE

    # Outside the refusal block, and narrowed to the write itself: a blanket
    # `except OSError` around the whole run would report `output_unwritable`
    # for, say, a bad `--repo-root` reaching git, misdirecting the operator.
    # The directory is created first, matching `aiops-review-quality-gate-v2`,
    # so a completed and already-billed review is not discarded merely
    # because `artifacts/` did not exist yet.
    try:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            outcome.review.readiness.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    except OSError:
        print(f"error: {OUTPUT_UNWRITABLE_REASON_V2}", file=sys.stderr)
        return REFUSAL_EXIT_CODE

    # stdout carries the decision and any run-level limitations -- both are
    # already-sanitized identifiers, never review material. Dropping the
    # limitations here would re-create the "silently absorbed" condition the
    # payload builder explicitly contracts against.
    for limitation in outcome.prepared.payload_limitations:
        print(f"limitation: {limitation}", file=sys.stderr)
    print(outcome.review.readiness.state.value)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
