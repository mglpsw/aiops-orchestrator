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

## Producer attestations are FETCHED, not deferred

GitHub exposes no execution-tree SHA, so the producer emits one from a job that
performs no checkout and runs none of the pull request's code, and uploads it as
a run artifact. This script downloads that artifact on the live path and binds
it to its workflow run.

An earlier revision only carried attestations through from a recorded payload
and described fetching them as "a separate concern". A Codex review pointed out
the consequence: on the live path every observation would carry
`producer_attestation=None`, every required check would be refused as
`producer_attestation_missing`, and only the fixture path could ever promote --
the same shape of defect as shipping a bridge that promotes nothing.

The artifact is untrusted input: it is bounded in size and entry count, only the
expected member is read, and it is parsed with the strict parser. Being able to
read it establishes nothing on its own -- the assembler still checks every field
against the run identity.

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
import io
import json
import subprocess
import sys
import zipfile
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
from app.common.strict_json import (  # noqa: E402
    canonical_json_text,
    raw_bytes_digest_hex,
    strict_json_loads,
)

ACQUIRER_IDENTITY = "aiops-acquire-authoritative-checks-v2"

# Convention shared with the reusable producer workflow. `#203` owns installing
# a caller that uploads under exactly this name.
ATTESTATION_ARTIFACT_NAME = "aiops-authoritative-check-attestation"
ATTESTATION_MEMBER_NAME = "attestation.json"

# The artifact is produced by a workflow, so it is untrusted input. These bounds
# exist so a hostile or corrupt zip cannot exhaust memory before the assembler
# ever gets to reject its contents.
MAX_ATTESTATION_ZIP_BYTES = 1 * 1024 * 1024
MAX_ATTESTATION_MEMBER_BYTES = 256 * 1024
MAX_ATTESTATION_ZIP_ENTRIES = 16

ACQUISITION_FAILED_REASON = "authoritative_check_acquisition_failed"
OUTPUT_OVERWRITES_INPUT_REASON = "authoritative_check_output_overwrites_input"
ATTESTATION_INVALID_REASON = "authoritative_check_attestation_artifact_invalid"
ATTESTATION_AMBIGUOUS_REASON = "authoritative_check_attestation_artifact_ambiguous"
GIT_OBSERVATION_FAILED_REASON = "authoritative_check_git_observation_failed"

# GitHub's list endpoints page at 30 by default and 100 at most. An acquirer
# that reads only the first page reports a producer that ran as MISSING, which
# is a false negative dressed as evidence -- so every list call pages, and runs
# out of pages loudly rather than truncating quietly.
ACQUISITION_PAGE_SIZE = 100
MAX_ACQUISITION_PAGES = 50


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
    check_runs = paginate_envelope_v2(
        get_json=client.get_json,
        path=f"/repos/{owner}/{repo}/commits/{args.head_sha}/check-runs",
        key="check_runs",
    )
    workflow_runs = paginate_envelope_v2(
        get_json=client.get_json,
        path=f"/repos/{owner}/{repo}/actions/runs?head_sha={args.head_sha}",
        key="workflow_runs",
    )

    def list_artifacts(run_id):
        return paginate_envelope_v2(
            get_json=client.get_json,
            path=f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts",
            key="artifacts",
        )

    def download_artifact(artifact_id):
        return _download_bytes(
            url=f"{args.api_base_url}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip",
            token=token,
        )

    payload = {
        "check_runs": check_runs,
        "workflow_runs": workflow_runs,
        # Fetched here rather than deferred: without them the live path could
        # never promote anything, which is exactly the defect a Codex review
        # found in the previous revision.
        "producer_attestations": collect_attestations_v2(
            workflow_runs=workflow_runs,
            list_artifacts=list_artifacts,
            download_artifact=download_artifact,
        ),
    }
    return payload, json.dumps(payload, sort_keys=True).encode("utf-8")


def paginate_envelope_v2(*, get_json, path: str, key: str) -> list:
    """Read every page of a GitHub list endpoint, or refuse.

    GitHub returns these lists inside an envelope (`{"total_count": N, "<key>":
    [...]}`), paged at 30 by default. Reading page 1 only means a producer that
    genuinely ran is reported as missing whenever the head has more runs than
    fit on a page -- and "missing" here is not a neutral outcome, it is evidence
    the gate acts on.

    `get_json` is injected so the paging discipline is testable without a token
    and without the network."""

    items: list = []
    for page in range(1, MAX_ACQUISITION_PAGES + 1):
        separator = "&" if "?" in path else "?"
        response = get_json(f"{path}{separator}per_page={ACQUISITION_PAGE_SIZE}&page={page}")
        if not isinstance(response, dict):
            raise AcquisitionError(ACQUISITION_FAILED_REASON)
        batch = response.get(key)
        if not isinstance(batch, list):
            raise AcquisitionError(ACQUISITION_FAILED_REASON)
        items.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < ACQUISITION_PAGE_SIZE:
            return items

    # Never return a truncated view: a silently short list is indistinguishable
    # from "the producer did not run", and one of those is a lie.
    raise AcquisitionError(ACQUISITION_FAILED_REASON)


def _download_bytes(*, url: str, token: str) -> bytes:
    """Raw artifact download. The reviewed `GitHubClient` speaks JSON only, and
    an artifact is a zip, so this is the one place that reads bytes."""

    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aiops-acquire-authoritative-checks/2.0",
            "Authorization": f"Bearer {token}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read(MAX_ATTESTATION_ZIP_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise AcquisitionError(ACQUISITION_FAILED_REASON) from exc


def extract_attestation_from_zip_v2(raw: bytes) -> dict:
    """Read the attestation out of a downloaded artifact zip, fail-closed.

    Bounded on every axis a zip can be hostile on, and it reads ONLY the
    expected member -- never "whatever single file is inside", which would let
    an attacker choose the payload by choosing the filename."""

    if len(raw) > MAX_ATTESTATION_ZIP_BYTES:
        raise AcquisitionError(ATTESTATION_INVALID_REASON)

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ATTESTATION_ZIP_ENTRIES:
                raise AcquisitionError(ATTESTATION_INVALID_REASON)
            member = next((e for e in entries if e.filename == ATTESTATION_MEMBER_NAME), None)
            if member is None or member.file_size > MAX_ATTESTATION_MEMBER_BYTES:
                raise AcquisitionError(ATTESTATION_INVALID_REASON)
            with archive.open(member) as handle:
                content = handle.read(MAX_ATTESTATION_MEMBER_BYTES + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise AcquisitionError(ATTESTATION_INVALID_REASON) from exc

    if len(content) > MAX_ATTESTATION_MEMBER_BYTES:
        raise AcquisitionError(ATTESTATION_INVALID_REASON)

    try:
        document = strict_json_loads(content)
    except ValueError as exc:
        raise AcquisitionError(ATTESTATION_INVALID_REASON) from exc
    if not isinstance(document, dict):
        raise AcquisitionError(ATTESTATION_INVALID_REASON)
    return document


def collect_attestations_v2(*, workflow_runs: list, list_artifacts, download_artifact) -> dict:
    """Map each workflow run id to the attestation its run uploaded.

    The two GitHub calls are injected so the binding logic is testable without
    a token or a network, and so the HTTP plumbing stays in one reviewed place.
    A run with no attestation is simply absent -- refusing here would conflate
    "this run published nothing" with "this artifact is malformed"."""

    attestations: dict[str, dict] = {}
    for run in workflow_runs:
        run_id = run.get("id")
        if run_id is None:
            continue
        candidates = [
            artifact
            for artifact in list_artifacts(run_id)
            if artifact.get("name") == ATTESTATION_ARTIFACT_NAME and not artifact.get("expired")
        ]
        if not candidates:
            continue
        if len(candidates) > 1:
            # Taking the first match would let an attacker win simply by
            # uploading a second artifact under the same name. Two candidates
            # is a contradiction about which one speaks for the run, and there
            # is no safe way to pick between them.
            raise AcquisitionError(ATTESTATION_AMBIGUOUS_REASON)
        attestations[str(run_id)] = extract_attestation_from_zip_v2(
            download_artifact(candidates[0].get("id"))
        )
    return attestations


def _workflow_ref(run: dict) -> str:
    """Record the ref the WORKFLOW DEFINITION was loaded from.

    The two pull-request events differ in exactly the property this whole
    slice cares about, so they must not be mapped the same way:

    - `pull_request` loads the workflow from the pull request's own merge
      commit, so the honest answer is the pull ref. No base-ownership is
      asserted -- see the KNOWN LIMITATION in the module docstring.
    - `pull_request_target` loads it from the BASE branch. That is the event's
      defining property (and the reason it is dangerous to use carelessly).
      Recording a pull ref for it would be factually wrong.

    Since the producer-evidence model landed, no authorisation decision keys
    off this field at all -- producer identity is the SHA-pinned workflow, and
    the executed tree is attested. This is a recorded observation, and it is
    kept accurate for the same reason every other observation is: the acquirer
    never writes down something it did not see.

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


def _run_started_at(run: dict) -> str:
    """When this workflow run started, used to order distinct runs.

    `run_attempt` counts within a single workflow run, so it cannot order two
    runs against each other. Refused rather than defaulted: a missing timestamp
    means the selection cannot establish which run is latest, and guessing
    there is how a stale green outranks a current red."""

    started = run.get("run_started_at") or run.get("created_at")
    if not isinstance(started, str) or not started:
        raise AcquisitionError(ACQUISITION_FAILED_REASON)
    return started


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

    attestations = payload.get("producer_attestations") or {}
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
                "workflow_execution_ref": _workflow_ref(run),
                # What the run LOADED, with GitHub's own full commit SHA. This
                # is the fact a pull request cannot forge, and it is what the
                # base-owned policy pins.
                "referenced_workflows": [
                    {
                        "path": ref.get("path"),
                        "sha": ref.get("sha"),
                        "ref": ref.get("ref"),
                    }
                    for ref in (run.get("referenced_workflows") or [])
                ],
                "producer_trigger": _run_event(run),
                # Emitted by the producer's checkout-free attestation job and
                # acquired alongside the run. Absent means unpromotable, never
                # "assume it ran the merge".
                "producer_attestation": attestations.get(str(run.get("id"))),
                "workflow_run_id": str(run.get("id")),
                "run_attempt": run.get("run_attempt") or 1,
                "run_started_at": _run_started_at(run),
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


def _check_no_output_input_collision(args: argparse.Namespace) -> None:
    """Reject `--output` aliasing an input, by RESOLVED PATH, before anything
    is read or written.

    The recorded payload is the raw GitHub evidence a later audit or rerun
    depends on. Reading it and then overwriting it with the derived snapshot --
    while returning success -- destroys the only copy of what was observed, and
    a snapshot that has eaten its own source cannot be re-derived or checked.

    The quality gate already refuses this class twice over (#145, #156). A new
    CLI that writes an artifact does not get to relearn it the hard way."""

    output_resolved = Path(args.output).resolve()
    for name in ("observations", "git_dir"):
        value = getattr(args, name, None)
        if value is not None and Path(value).resolve() == output_resolved:
            raise AcquisitionError(OUTPUT_OVERWRITES_INPUT_REASON)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        _check_no_output_input_collision(args)
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
