#!/usr/bin/env python3
"""Acquire an AuthoritativeCheckSnapshotV2 from GitHub (`#201-C0`, C0-6).

This is the ONLY component in `#201-C0` that touches the network. It observes,
canonicalises, and writes; it decides nothing. The assembler
(`app.agent_review.required_check_assembly_v2`) then reads the file back and
derives authority offline, from the base-owned policy and the run identity.

One chain only:

```text
acquire -> canonical snapshot on disk -> verifier/assembler -> quality gate
```

There is deliberately no in-memory shortcut. A convenience wrapper may call
this script and then read its output, but no API response object may reach the
assembler without passing through the file -- otherwise there would be two
paths to authority and only one of them would be adversarially tested.

## What is observed, and what is not

Check runs come from the API. The parents of the tested merge commit come from
LOCAL GIT, not from the API: the whole point of `#201-C0`'s parentage rule is
to prove which tree ran, and asking the same service that reported the check to
also vouch for the tree would be circular.

The acquisition identity (`acquired_by`, `api_host`, and the
`repository`/`head_sha` the query was scoped to) is part of the snapshot's
canonical material, so an observation cannot later be re-attributed to a
different acquirer or scope.

## KNOWN LIMITATION -- `workflow_ref` and base-owned workflows

GitHub's Actions API reports a workflow run's `path` but no field asserting
which REF the workflow DEFINITION was loaded from. For `pull_request` events
GitHub executes the workflow file as it exists in the pull request's own merge
commit, so a pull request can modify the very workflow that produces its
checks. That is threat C0-T4, and GitHub metadata alone does not close it.

This script therefore records `workflow_ref` as what actually happened
(`refs/pull/<n>/merge` for pull-request runs, `refs/heads/<branch>` for branch
runs) and never asserts a base-owned origin it cannot observe. The consequence
is deliberate and visible: a target whose policy pins `workflow_ref` to its
default branch will NOT match a `pull_request`-triggered run, and nothing will
be promoted. Failing closed and loudly is correct here -- the alternative is a
policy that appears to prove base ownership while proving nothing.

Closing C0-T4 properly is a TARGET CONFIGURATION decision, not something this
script can fake: the authoritative producer has to be triggered in a way that
runs the base-owned definition (for example a `workflow_run`-triggered job, or
a reusable workflow pinned by the base, or branch protection plus CODEOWNERS on
the workflow path). Recorded here rather than resolved, because inventing a
resolution would mean claiming an assurance the platform does not give.

## Why the queries are scoped to the PR head

Both requests filter by `--head-sha`, which retrieves exactly the runs GitHub
associates with the pull request's head -- and `pull_request` is the only origin
`#201-C0` can promote (see `authoritative_check_policy_v2`: every other origin
requires `explicit_tested_tree`, which has no verifiable producer evidence and
is refused when the policy is loaded).

A second Codex review noted that `pull_request_target` runs are associated with
the BASE commit and so would not be retrieved by a head-scoped query. That is
true, and it is consistent rather than a gap: such a run cannot be promoted, so
not retrieving it changes no verdict. Adding a base-scoped query would collect
evidence for a path that fails closed anyway, and would invite the impression
that the path works. If a future producer makes `pull_request_target`
verifiable -- by emitting authenticated evidence of the tree it checked out --
the query scope has to be revisited together with that change, not before it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.authoritative_ci_snapshot_v2 import (  # noqa: E402
    parse_authoritative_ci_snapshot_v2,
)
from app.agent_review.required_check_provenance_v2 import (  # noqa: E402
    RequiredCheckProvenanceErrorV2,
)
from app.common.strict_json import canonical_json_text, raw_bytes_digest_hex  # noqa: E402

ACQUIRER_IDENTITY = "aiops-acquire-authoritative-checks-v2"

ACQUISITION_FAILED_REASON = "authoritative_check_acquisition_failed"
GIT_OBSERVATION_FAILED_REASON = "authoritative_check_git_observation_failed"


class AcquisitionError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True, help="owner/repo")
    parser.add_argument("--head-sha", required=True, help="the pull request HEAD (review_subject_sha)")
    parser.add_argument(
        "--tested-merge-sha",
        required=True,
        help="the commit CI actually executed (execution_subject_sha)",
    )
    parser.add_argument("--git-dir", default=str(REPO_ROOT), help="local checkout used to observe parentage")
    parser.add_argument("--output", required=True, help="path to write the snapshot JSON")
    parser.add_argument(
        "--observations",
        help=(
            "optional path to a pre-fetched GitHub payload "
            "{'check_runs': [...], 'workflow_runs': [...]}; when supplied no network call is made"
        ),
    )
    parser.add_argument("--token", help="GitHub token; defaults to $GITHUB_TOKEN")
    parser.add_argument("--api-base-url", default="https://api.github.com")
    return parser.parse_args(argv)


def _observe_parents(git_dir: str, commit: str) -> list[str]:
    """Read the tested merge commit's parents from local git.

    Deliberately not taken from the API -- see the module docstring."""

    try:
        completed = subprocess.run(
            ["git", "-C", git_dir, "rev-list", "--parents", "-n", "1", commit],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcquisitionError(GIT_OBSERVATION_FAILED_REASON) from exc

    parts = completed.stdout.split()
    if len(parts) < 2:
        # A commit with no parents cannot be a merge, so it cannot be a tested
        # merge commit. Refused rather than recorded as an empty parent list.
        raise AcquisitionError(GIT_OBSERVATION_FAILED_REASON)
    return parts[1:]


def _fetch_payload(args: argparse.Namespace) -> tuple[dict, bytes]:
    if args.observations is not None:
        raw = Path(args.observations).read_bytes()
        return json.loads(raw), raw

    import importlib.util
    import os

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AcquisitionError(ACQUISITION_FAILED_REASON)

    # Reuse the existing, already-reviewed HTTP client rather than adding a
    # second way for this repository to talk to GitHub. It lives in a sibling
    # script, so it is loaded by path instead of by package import.
    spec = importlib.util.spec_from_file_location(
        "_aiops_github_client", REPO_ROOT / "scripts" / "github_agent_review.py"
    )
    if spec is None or spec.loader is None:
        raise AcquisitionError(ACQUISITION_FAILED_REASON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    client = module.GitHubClient(token, args.repository, args.api_base_url)
    owner, repo = args.repository.split("/", 1)
    payload = {
        "check_runs": client.get_json(f"/repos/{owner}/{repo}/commits/{args.head_sha}/check-runs").get(
            "check_runs", []
        ),
        "workflow_runs": client.get_json(
            f"/repos/{owner}/{repo}/actions/runs?head_sha={args.head_sha}"
        ).get("workflow_runs", []),
    }
    return payload, json.dumps(payload, sort_keys=True).encode("utf-8")


def _workflow_ref(run: dict) -> str:
    """Record the ref the WORKFLOW DEFINITION was loaded from.

    The two pull-request events differ in exactly the property this whole
    slice cares about, so they must not be mapped the same way:

    - `pull_request` loads the workflow from the pull request's own merge
      commit, so the honest answer is the pull ref. No base-ownership is
      asserted -- see the KNOWN LIMITATION in the module docstring.
    - `pull_request_target` loads it from the BASE branch. That is the event's
      defining property (and the reason it is dangerous to use carelessly).
      Recording a pull ref for it would be factually wrong, and would make
      every otherwise-authorised `pull_request_target` run permanently
      unauthorisable, since policy only admits the default branch.

    For `pull_request_target` the base ref comes from the run's own
    `pull_requests[].base.ref` rather than `head_branch`, which names the pull
    request's head branch and is not where the definition came from."""

    event = run.get("event")
    pull = (run.get("pull_requests") or [{}])[0]

    if event == "pull_request":
        number = pull.get("number")
        if number is not None:
            return f"refs/pull/{number}/merge"
    elif event == "pull_request_target":
        base_ref = (pull.get("base") or {}).get("ref")
        if base_ref:
            return f"refs/heads/{base_ref}"

    branch = run.get("head_branch")
    if branch:
        return f"refs/heads/{branch}"
    raise AcquisitionError(ACQUISITION_FAILED_REASON)


def _run_event(run: dict) -> str:
    """Normalise the trigger, refusing anything unrecognised.

    An unknown event cannot be reasoned about, so it is refused rather than
    bucketed into an `other` value that later code would have to guess at."""

    event = run.get("event")
    if event not in {
        "pull_request",
        "pull_request_target",
        "push",
        "merge_group",
        "workflow_run",
        "workflow_dispatch",
        "schedule",
    }:
        raise AcquisitionError(ACQUISITION_FAILED_REASON)
    return event


def build_snapshot_document(
    *, args: argparse.Namespace, payload: dict, payload_bytes: bytes, parents: list[str]
) -> dict:
    runs_by_suite = {
        run.get("check_suite_id"): run for run in payload.get("workflow_runs", []) if run.get("check_suite_id")
    }

    observations = []
    for check in payload.get("check_runs", []):
        suite_id = (check.get("check_suite") or {}).get("id")
        run = runs_by_suite.get(suite_id)
        if run is None:
            # A check run with no matching workflow run cannot be attributed to
            # a workflow identity, so it is dropped rather than recorded with
            # invented fields. Dropping here is safe: a required check that
            # ends up absent fails closed downstream.
            continue
        pull = (run.get("pull_requests") or [{}])[0]
        observations.append(
            {
                "repository": args.repository,
                "head_sha": args.head_sha,
                "check_run_id": str(check.get("id")),
                "check_run_name": check.get("name"),
                "status": check.get("status"),
                "conclusion": check.get("conclusion"),
                "app_slug": (check.get("app") or {}).get("slug"),
                "workflow_path": run.get("path"),
                "workflow_ref": _workflow_ref(run),
                "workflow_run_id": str(run.get("id")),
                "run_attempt": run.get("run_attempt") or 1,
                "run_event": _run_event(run),
                # The run's OWN base and head, as GitHub recorded them. Without
                # these the run cannot be bound to a base/head pair, and a green
                # produced against a previous base would be indistinguishable
                # from one produced against the current merge.
                "run_base_sha": (pull.get("base") or {}).get("sha"),
                "run_head_sha": (pull.get("head") or {}).get("sha"),
            }
        )

    return {
        "schema_id": "agent-review.authoritative-check-snapshot.v2",
        "schema_version": 2,
        "source": "aiops-acquire-authoritative-checks",
        "acquisition": {
            "acquired_by": ACQUIRER_IDENTITY,
            "api_host": args.api_base_url.replace("https://", "").replace("http://", "").rstrip("/"),
            "repository": args.repository,
            "head_sha": args.head_sha,
        },
        "observations": observations,
        "tested_merge_sha": args.tested_merge_sha,
        "tested_merge_parents": parents,
        "observation_bytes_digest": raw_bytes_digest_hex(payload_bytes),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        payload, payload_bytes = _fetch_payload(args)
        parents = _observe_parents(args.git_dir, args.tested_merge_sha)
        document = build_snapshot_document(
            args=args, payload=payload, payload_bytes=payload_bytes, parents=parents
        )
        # Materialise only what the assembler would accept: writing a snapshot
        # the offline parser would reject just moves the failure downstream.
        parse_authoritative_ci_snapshot_v2(canonical_json_text(document))
    except (AcquisitionError, RequiredCheckProvenanceErrorV2) as exc:
        # Surface the parser's own reason code rather than collapsing it into a
        # generic failure: "the snapshot I was about to write is malformed" and
        # "GitHub was unreachable" need different responses.
        print(f"error: {exc.reason_code}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"error: {ACQUISITION_FAILED_REASON} ({type(exc).__name__})", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_json_text(document) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
