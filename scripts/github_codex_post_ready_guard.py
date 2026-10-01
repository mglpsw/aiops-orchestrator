#!/usr/bin/env python3
"""Read-only evidence guard for the post-Ready Codex lifecycle barrier.

The collector reads GitHub lifecycle/review surfaces. The evaluator is pure
and accepts only evidence bound to the expected repository, PR, base, HEAD,
Ready event, Codex review, and required checks. It never performs a write,
merge, Ready transition, or thread resolution.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CODEX_LOGINS = {"chatgpt-codex-connector", "openai-codex"}
TERMINAL_REVIEW_STATES = {"APPROVED", "COMMENTED"}
ADJUDICATED = {"FIXED", "DISMISSED", "SUPERSEDED"}


@dataclass(frozen=True)
class GuardResult:
    state: str
    reason: str
    merge_authorized: bool = False
    evidence: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "reason": self.reason,
            "merge_authorized": self.merge_authorized,
            "evidence": self.evidence or {},
        }


def _held(state: str, reason: str, evidence: dict[str, Any] | None = None) -> GuardResult:
    return GuardResult(state=state, reason=reason, evidence=evidence)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _short_sha_in_text(body: str, full_sha: str) -> bool:
    return bool(re.search(rf"(?<![0-9a-f]){re.escape(full_sha[:7])}(?![0-9a-f])", body.lower()))


def _is_codex_summary(comment: dict[str, Any], expected_head: str) -> bool:
    body = str(comment.get("body", ""))
    lower = body.lower()
    return (
        comment.get("author_login") in CODEX_LOGINS
        and "codex review summary" in lower
        and "completed" in lower
        and "draft marked ready" in lower
        and _short_sha_in_text(lower, expected_head)
    )


def _latest_ready_event(evidence: dict[str, Any]) -> datetime | None:
    timestamps = [
        timestamp
        for event in evidence.get("ready_events", [])
        if event.get("event") == "ready_for_review"
        for timestamp in [_parse_time(event.get("created_at"))]
        if timestamp is not None
    ]
    return max(timestamps) if timestamps else None


def _required_checks_pass(evidence: dict[str, Any], required_checks: tuple[str, ...]) -> bool | None:
    checks = evidence.get("checks")
    if not isinstance(checks, list):
        return None
    by_name = {item.get("name"): item for item in checks if isinstance(item, dict)}
    for name in required_checks:
        item = by_name.get(name)
        if not item or item.get("status") != "completed" or item.get("conclusion") != "success":
            return False
    return True


def _finding_is_adjudicated(finding: dict[str, Any]) -> bool:
    disposition = str(finding.get("disposition", "")).upper()
    return disposition in ADJUDICATED


def evaluate_evidence(
    evidence: dict[str, Any],
    *,
    expected_repo: str,
    expected_pr: int,
    expected_head: str,
    expected_base: str | None = None,
    required_checks: tuple[str, ...] = (),
) -> GuardResult:
    """Evaluate normalized evidence without trusting self-declared clean flags."""

    if not isinstance(evidence, dict):
        return _held("HELD_CODEX_UNAVAILABLE", "evidence is not an object")
    if evidence.get("collection_errors"):
        return _held("HELD_CODEX_UNAVAILABLE", "one or more evidence surfaces could not be collected", evidence)

    pr = evidence.get("pr")
    if not isinstance(pr, dict):
        return _held("HELD_CODEX_UNAVAILABLE", "PR lifecycle evidence is absent", evidence)
    if pr.get("repo") != expected_repo or pr.get("number") != expected_pr:
        return _held("HELD_STALE", "repository or PR identity differs from the expected subject", evidence)
    if pr.get("head_sha") != expected_head:
        return _held("HELD_STALE", "current PR HEAD differs from the expected subject", evidence)
    if expected_base is not None and pr.get("base_sha") != expected_base:
        return _held("HELD_STALE", "current PR base differs from the expected subject", evidence)
    if pr.get("state") != "open" or pr.get("merged") is True:
        return _held("HELD_STALE", "the pre-merge PR lifecycle is no longer open", evidence)
    if pr.get("draft") is True:
        return _held("HELD_PENDING_CODEX", "the PR has not crossed the Ready transition", evidence)

    checks = _required_checks_pass(evidence, required_checks)
    if checks is None:
        return _held("HELD_PENDING_REQUIRED_CI", "required-check evidence is absent", evidence)
    if checks is False:
        return _held("HELD_PENDING_REQUIRED_CI", "a required check is not completed successfully", evidence)

    reviews = [
        item for item in evidence.get("reviews", [])
        if isinstance(item, dict)
        and item.get("author_login") in CODEX_LOGINS
        and item.get("commit_id") == expected_head
        and item.get("state") in TERMINAL_REVIEW_STATES
    ]
    summaries = [
        item for item in evidence.get("summaries", [])
        if isinstance(item, dict) and _is_codex_summary(item, expected_head)
    ]
    if not reviews or not summaries:
        return _held("HELD_PENDING_CODEX", "no terminal Codex review bound to the current HEAD and Ready cycle", evidence)

    review = max(reviews, key=lambda item: item.get("submitted_at", ""))
    review_time = _parse_time(review.get("submitted_at"))
    ready_time = _latest_ready_event(evidence)
    if review_time is None or ready_time is None or review_time < ready_time:
        return _held("HELD_STALE", "the Codex review is not demonstrably after the Ready event", evidence)
    summaries = [
        item for item in summaries
        if (summary_time := _parse_time(item.get("created_at"))) is not None and summary_time >= ready_time
    ]
    if not summaries:
        return _held("HELD_PENDING_CODEX", "the terminal summary predates the latest Ready cycle", evidence)

    findings = [
        item for item in evidence.get("findings", [])
        if isinstance(item, dict)
        and item.get("review_id") in {candidate.get("id") for candidate in reviews}
        and item.get("material", True) is not False
    ]
    open_findings = [item for item in findings if not _finding_is_adjudicated(item)]
    if open_findings:
        return _held(
            "HELD_WITH_MATERIAL_FINDINGS",
            "material Codex findings remain without an explicit disposition",
            {**evidence, "open_findings": open_findings},
        )

    return GuardResult(
        state="READY_FOR_HUMAN_INTEGRATION_DECISION",
        reason="current HEAD, Ready cycle, terminal Codex review, findings, and required checks are observed",
        merge_authorized=False,
        evidence=evidence,
    )


class GitHubReadOnlyClient:
    """Minimal GitHub REST/GraphQL reader; no mutation endpoint exists here."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_url}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"GitHub read failed ({type(exc).__name__})") from exc

    def _pages(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 21):
            query = urlencode({"per_page": 100, "page": page})
            batch = self._request("GET", f"{path}?{query}")
            if not isinstance(batch, list):
                raise RuntimeError("GitHub list response was not an array")
            result.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < 100:
                return result
        raise RuntimeError("GitHub pagination exceeded the bounded page budget")

    def collect(self, repo: str, pr_number: int) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        prefix = f"/repos/{owner}/{name}"
        errors: list[str] = []
        try:
            pr = self._request("GET", f"{prefix}/pulls/{pr_number}")
            reviews = self._pages(f"{prefix}/pulls/{pr_number}/reviews")
            review_comments = self._pages(f"{prefix}/pulls/{pr_number}/comments")
            summaries = self._pages(f"{prefix}/issues/{pr_number}/comments")
            timeline = self._pages(f"{prefix}/issues/{pr_number}/timeline")
            checks = self._request("GET", f"{prefix}/commits/{pr['head']['sha']}/check-runs")
            graphql = self._request(
                "POST",
                "/graphql",
                {
                    "query": "query($owner:String!, $name:String!, $number:Int!) { repository(owner:$owner, name:$name) { pullRequest(number:$number) { reviewThreads(first:100) { nodes { isResolved isOutdated comments(first:50) { nodes { databaseId body author { login } } } } } } } }",
                    "variables": {"owner": owner, "name": name, "number": pr_number},
                },
            )
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"collection_errors": [str(exc)]}

        review_by_id = {item.get("id"): item for item in reviews}
        findings: list[dict[str, Any]] = []
        for comment in review_comments:
            review_id = comment.get("pull_request_review_id")
            if review_id not in review_by_id or review_by_id[review_id].get("user", {}).get("login") not in CODEX_LOGINS:
                continue
            findings.append({
                "id": comment.get("id"),
                "review_id": review_id,
                "material": True,
                "disposition": None,
            })
        threads = (((graphql.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")
        if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
            errors.append("review-thread evidence is unavailable")
        else:
            for thread in threads["nodes"]:
                thread_comments = (thread.get("comments") or {}).get("nodes", [])
                bodies = [str(node.get("body", "")) for node in thread_comments]
                comment_ids = {str(node.get("databaseId")) for node in thread_comments if node.get("databaseId") is not None}
                disposition = None
                for body in bodies:
                    match = re.search(r"\bDisposition\s*:\s*(FIXED|DISMISSED|SUPERSEDED)\b", body, re.I)
                    if match:
                        disposition = match.group(1).upper()
                for finding in findings:
                    if str(finding["id"]) in comment_ids:
                        finding["disposition"] = disposition

        return {
            "pr": {
                "repo": repo,
                "number": pr_number,
                "state": pr.get("state"),
                "draft": pr.get("draft"),
                "merged": pr.get("merged"),
                "head_sha": pr.get("head", {}).get("sha"),
                "base_sha": pr.get("base", {}).get("sha"),
            },
            "ready_events": [
                {"event": item.get("event"), "created_at": item.get("created_at")}
                for item in timeline if item.get("event") == "ready_for_review"
            ],
            "reviews": [
                {
                    "id": item.get("id"),
                    "author_login": (item.get("user") or {}).get("login"),
                    "state": item.get("state"),
                    "commit_id": item.get("commit_id"),
                    "submitted_at": item.get("submitted_at"),
                }
                for item in reviews
            ],
            "summaries": [
                {
                    "id": item.get("id"),
                    "author_login": (item.get("user") or {}).get("login"),
                    "body": item.get("body", ""),
                    "created_at": item.get("created_at"),
                }
                for item in summaries
            ],
            "findings": findings,
            "checks": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "conclusion": item.get("conclusion"),
                }
                for item in checks.get("check_runs", [])
            ],
            "collection_errors": errors,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base")
    parser.add_argument("--required-check", action="append", default=[])
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    args = parser.parse_args(argv)

    if args.evidence_json:
        try:
            evidence = json.loads(args.evidence_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result = _held("HELD_CODEX_UNAVAILABLE", f"evidence file could not be read: {type(exc).__name__}")
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
            return 2
    else:
        token = os.environ.get(args.token_env)
        if not token:
            result = _held("HELD_CODEX_UNAVAILABLE", f"token environment variable {args.token_env} is absent")
            print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
            return 2
        evidence = GitHubReadOnlyClient(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")).collect(args.repo, args.pr)

    result = evaluate_evidence(
        evidence,
        expected_repo=args.repo,
        expected_pr=args.pr,
        expected_head=args.expected_head,
        expected_base=args.expected_base,
        required_checks=tuple(args.required_check),
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.state == "READY_FOR_HUMAN_INTEGRATION_DECISION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
