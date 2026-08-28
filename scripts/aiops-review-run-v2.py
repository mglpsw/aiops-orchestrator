#!/usr/bin/env python3
"""`#200-D` successor CLI: run the operational AgentReview v2 composer as a
real process, from the toolrepo, against a real target checkout (issue
#200). Provider-free in this slice -- offline transport by default; the
Router transport is wired but never invoked without an explicit flag and a
real credential.

## This CLI's own epoch (`#200-D` §9)

This CLI owns: argv parsing, reading its own caller-supplied JSON/text
files, UTF-8/file I/O for those inputs, strict JSON parsing, and the Router
credential environment lookup. Each of those operations is translated into
this CLI's OWN `input_invalid`-style reason codes below.

## No target-filesystem output writer (`#200-D` correction)

An earlier revision accepted `--output <path>` and wrote the result there
directly -- a caller could pass `--output <repo_root>/review.json` and the
review engine itself would write into the target checkout it was reviewing,
contradicting this feature's own target-non-mutation claim in a way its own
black-box test (comparing only `HEAD^{tree}`) could not even detect, since a
new untracked file does not change a tree hash.

This CLI has no filesystem-output authority at all: the canonical
`ReviewReadinessV2` JSON is written to **stdout**, and only to stdout, on
success. A host workflow that wants a file redirects stdout itself
(`aiops-review-run-v2.py ... > result.json`) -- that redirection happens
entirely outside this process and is not something this engine can point
at the target checkout, because this engine never receives or interprets a
destination path at all. This is the clean fix rather than a resolve-and-
validate containment check: a check-then-write path can always be
retargeted between the check and the write, and this CLI is new/unreleased,
so there is no existing caller depending on `--output` to preserve.

After material crosses into `run_operational_review_v2` (the composer's own
boundary), this CLI does not generically catch downstream
`pydantic.ValidationError`, does not inspect an arbitrary `reason_code` off
an untyped exception, and never wraps the delegated call in a bare
`except Exception`. It catches exactly the two typed families the composer
and transport document for themselves --
``app.agent_review.operational_run_v2.OperationalRunError`` and
``app.agent_review.review_transport_v2.ChunkTransportError`` -- and nothing
else. A defect surfacing as anything else is a bug in this repository and
must crash, not become a silently-swallowed CLI exit code.

``--toolrepo-sha`` is the CALLER's DECLARATION; the composer independently
proves the executing engine's own source checkout against it
(`toolrepo_identity_v2`) before any semantic review runs. The PROVEN sha,
never the bare declaration, is what reaches assembly's run identity.

Exit code convention:

    exit 0  => the canonical ReviewReadinessV2 JSON was printed to stdout.
               Does NOT mean ready -- the state is INSIDE the artifact.
    exit !=0 => nothing was printed to stdout; a stable reason code was
               printed to stderr instead.
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
from app.agent_review.operational_run_v2 import (  # noqa: E402
    OperationalRunError,
    run_operational_review_v2,
)
from app.agent_review.required_check_provenance_v2 import (  # noqa: E402
    RequiredCheckProvenanceErrorV2,
    RequiredCheckProvenanceV2,
)
from app.agent_review.review_transport_v2 import (  # noqa: E402
    ChunkTransportError,
    agent_router_transport_v2,
    offline_file_transport_v2,
)
from app.agent_review.semantic_grouping_policy_v2 import (  # noqa: E402
    SemanticGroupingPolicyV2,
)

CLI_INPUT_INVALID_REASON_V2 = "run_cli_input_invalid"
CLI_OFFLINE_RESPONSES_DIR_REQUIRED_REASON_V2 = "run_cli_offline_responses_dir_required"
CLI_ROUTER_ARGS_REQUIRED_REASON_V2 = "run_cli_router_args_required"
CLI_ROUTER_CREDENTIAL_MISSING_REASON_V2 = "run_cli_router_credential_missing"

_ROUTER_API_KEY_ENV_VAR_V2 = "AGENT_ROUTER_API_KEY"


class RunCliError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-version", required=True, help="must be exactly 'v2'")
    parser.add_argument("--repo-root", required=True, help="target checkout under review")
    parser.add_argument("--target-profile", required=True, help="trusted target profile root")
    parser.add_argument("--grouping-policy", required=True, help="JSON file: SemanticGroupingPolicyV2")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--tested-merge-sha", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--toolrepo-sha", required=True, help="declared toolrepo source identity")
    parser.add_argument("--evidence-hash", required=True)
    parser.add_argument("--max-lines-per-chunk", required=True, type=int)
    parser.add_argument("--pr-state", required=True, choices=["open", "closed", "merged"])
    parser.add_argument("--run-origin", required=True, help="JSON file: RunOriginV2")
    parser.add_argument("--checks-snapshot", required=True, help="AuthoritativeCheckSnapshotV2 file")
    parser.add_argument("--toolchain-digest", required=True)
    parser.add_argument("--checks", help="JSON file: array of RequiredCheckResultV2 (default: none submitted)")
    parser.add_argument("--checks-provenance", help="JSON file: array of RequiredCheckProvenanceV2")
    parser.add_argument("--dlp-policy", help="optional JSON file: target-owned DLP policy")
    parser.add_argument("--transport", required=True, choices=["offline", "router"])
    parser.add_argument("--offline-responses-dir", help="offline mode: transport envelope directory")
    parser.add_argument("--router-base-url", help="router mode: Agent Router base URL")
    parser.add_argument("--router-model", help="router mode: logical review preset")
    return parser.parse_args(argv)


def _read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc


def _load_grouping_policy(path: str) -> SemanticGroupingPolicyV2:
    try:
        return SemanticGroupingPolicyV2.model_validate(_read_json(path))
    except ValidationError as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc


def _load_run_origin(path: str) -> RunOriginV2:
    try:
        return RunOriginV2.model_validate(_read_json(path))
    except ValidationError as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc


def _load_checks_snapshot(path: str):
    try:
        return parse_authoritative_ci_snapshot_v2(Path(path).read_bytes())
    except OSError as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc
    except RequiredCheckProvenanceErrorV2 as exc:
        # Already this authority's own stable code -- surfaced verbatim,
        # never re-wrapped into a synonym.
        raise RunCliError(exc.reason_code) from exc


def _load_checks(path: str | None) -> list[RequiredCheckResultV2]:
    if path is None:
        return []
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2)
    try:
        return [RequiredCheckResultV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc


def _load_checks_provenance(path: str | None) -> list[RequiredCheckProvenanceV2]:
    if path is None:
        return []
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2)
    try:
        return [RequiredCheckProvenanceV2.model_validate(item) for item in raw]
    except ValidationError as exc:
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2) from exc


def _load_dlp_policy(path: str | None):
    if path is None:
        return None
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise RunCliError(CLI_INPUT_INVALID_REASON_V2)
    return raw


def _build_transport(args: argparse.Namespace):
    if args.transport == "offline":
        if not args.offline_responses_dir:
            raise RunCliError(CLI_OFFLINE_RESPONSES_DIR_REQUIRED_REASON_V2)
        return offline_file_transport_v2(Path(args.offline_responses_dir))

    # router
    if not args.router_base_url or not args.router_model:
        raise RunCliError(CLI_ROUTER_ARGS_REQUIRED_REASON_V2)
    # The credential is read from the environment ONLY -- never from argv,
    # which would land it in process listings and shell history. This CLI
    # never persists it: it is handed straight to the transport constructor
    # and never written to `--output` or any log line below.
    api_key = os.environ.get(_ROUTER_API_KEY_ENV_VAR_V2, "")
    if not api_key:
        raise RunCliError(CLI_ROUTER_CREDENTIAL_MISSING_REASON_V2)
    return agent_router_transport_v2(
        base_url=args.router_base_url, api_key=api_key, model=args.router_model
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.contract_version != "v2":
        print(f"error: {CLI_INPUT_INVALID_REASON_V2}", file=sys.stderr)
        return 1

    try:
        grouping_policy = _load_grouping_policy(args.grouping_policy)
        run_origin = _load_run_origin(args.run_origin)
        checks_snapshot = _load_checks_snapshot(args.checks_snapshot)
        checks = _load_checks(args.checks)
        checks_provenance = _load_checks_provenance(args.checks_provenance)
        dlp_policy = _load_dlp_policy(args.dlp_policy)
        transport = _build_transport(args)

        outcome = run_operational_review_v2(
            repo_root=args.repo_root,
            target_profile_root=args.target_profile,
            grouping_policy=grouping_policy,
            base_sha=args.base_sha,
            head_sha=args.head_sha,
            tested_merge_sha=args.tested_merge_sha,
            pr_number=args.pr_number,
            declared_toolrepo_sha=args.toolrepo_sha,
            evidence_hash=args.evidence_hash,
            transport=transport,
            pr_state=PullRequestStateV2(args.pr_state),
            origin=run_origin,
            snapshot=checks_snapshot,
            toolchain_digest=args.toolchain_digest,
            max_lines_per_chunk=args.max_lines_per_chunk,
            dlp_policy=dlp_policy,
            checks=checks,
            provenance=checks_provenance,
            executing_script=Path(__file__),
        )
    except (RunCliError, OperationalRunError, ChunkTransportError) as exc:
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1

    # No filesystem output writer: the canonical result goes to stdout only.
    # A caller redirects stdout itself; this process never receives or
    # interprets a destination path, so it structurally cannot write into
    # the target checkout it just reviewed.
    print(
        json.dumps(
            outcome.review.readiness.model_dump(mode="json"),
            ensure_ascii=False, indent=2, sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
