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

CODEX_LOGINS = {
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
    "openai-codex",
    "openai-codex[bot]",
}
TERMINAL_REVIEW_STATES = {"APPROVED", "COMMENTED"}
ADJUDICATED = {"FIXED", "DISMISSED", "SUPERSEDED"}
EVIDENCE_COLLECTIONS = ("ready_events", "reviews", "summaries", "findings", "checks")


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


def _summary_is_explicitly_completed(body: str) -> bool:
    visible = re.sub(r"<details>.*?</details>", "", body, flags=re.IGNORECASE | re.DOTALL)
    lower = visible.lower()
    if re.search(r"\b(?:not|incomplete|failed|failure|error|cancelled|canceled|running|pending)\b", lower):
        return False
    return bool(re.search(r"(?:✅\s*)?(?:\*\*)?completed(?:\*\*)?\b", lower))


def _is_codex_summary(comment: dict[str, Any], expected_head: str) -> bool:
    body = str(comment.get("body", ""))
    lower = body.lower()
    return (
        comment.get("author_login") in CODEX_LOGINS
        and "codex review summary" in lower
        and _summary_is_explicitly_completed(body)
        and ("draft marked ready" in lower or "manual request" in lower)
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


def _required_checks_pass(
    evidence: dict[str, Any],
    required_checks: tuple[str, ...],
    *,
    expected_head: str,
    trusted_check_producers: tuple[str, ...],
) -> bool | None:
    checks = evidence.get("checks")
    if not isinstance(checks, list) or not required_checks or not trusted_check_producers:
        return None
    for name in required_checks:
        matches = [item for item in checks if isinstance(item, dict) and item.get("name") == name]
        if len(matches) != 1:
            return False
        item = matches[0]
        producer = item.get("app_slug") or item.get("producer")
        run_id = item.get("run_id") or item.get("id")
        if (
            item.get("status") != "completed"
            or item.get("conclusion") != "success"
            or item.get("head_sha") != expected_head
            or producer not in trusted_check_producers
            or not run_id
        ):
            return False
    return True


def _finding_is_adjudicated(finding: dict[str, Any], trusted_adjudicators: tuple[str, ...]) -> bool:
    disposition = str(finding.get("disposition", "")).upper()
    author = str(finding.get("disposition_author", "")).lower()
    trusted = {item.lower() for item in trusted_adjudicators}
    return disposition in ADJUDICATED and author in trusted


def _review_body_residual(body: str) -> str:
    """Remove the connector's boilerplate while preserving non-inline findings."""

    residual = re.sub(r"<details>.*?</details>", "", body, flags=re.IGNORECASE | re.DOTALL)
    residual = re.sub(r"###\s*💡\s*Codex Review", "", residual, flags=re.IGNORECASE)
    residual = re.sub(
        r"Here are some automated review suggestions for this pull request\.",
        "",
        residual,
        flags=re.IGNORECASE,
    )
    residual = re.sub(r"\*\*Reviewed commit:\*\*\s*`[0-9a-f]+`", "", residual, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", residual).strip()


def evaluate_evidence(
    evidence: dict[str, Any],
    *,
    expected_repo: str,
    expected_pr: int,
    expected_head: str,
    expected_base: str | None = None,
    required_checks: tuple[str, ...] = (),
    trusted_adjudicators: tuple[str, ...] = (),
    trusted_check_producers: tuple[str, ...] = ("github-actions",),
) -> GuardResult:
    """Evaluate normalized evidence without trusting self-declared clean flags."""

    if not isinstance(evidence, dict):
        return _held("HELD_CODEX_UNAVAILABLE", "evidence is not an object")
    if evidence.get("collection_errors"):
        return _held("HELD_CODEX_UNAVAILABLE", "one or more evidence surfaces could not be collected", evidence)
    malformed = [key for key in EVIDENCE_COLLECTIONS if not isinstance(evidence.get(key), list)]
    if malformed:
        return _held("HELD_CODEX_UNAVAILABLE", f"evidence collections are malformed: {', '.join(malformed)}", evidence)

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

    checks = _required_checks_pass(
        evidence,
        required_checks,
        expected_head=expected_head,
        trusted_check_producers=trusted_check_producers,
    )
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
    open_findings = [item for item in findings if not _finding_is_adjudicated(item, trusted_adjudicators)]
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
                **({"Content-Type": "application/json"} if data is not None else {}),
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

    def collect(
        self,
        repo: str,
        pr_number: int,
        *,
        trusted_adjudicators: tuple[str, ...] = (),
    ) -> dict[str, Any]:
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
                    "query": "query($owner:String!, $name:String!, $number:Int!) { repository(owner:$owner, name:$name) { pullRequest(number:$number) { reviewThreads(first:100) { pageInfo { hasNextPage } nodes { isResolved isOutdated comments(first:50) { nodes { databaseId body author { login } } } } } } } }",
                    "variables": {"owner": owner, "name": name, "number": pr_number},
                },
            )
            pr_after = self._request("GET", f"{prefix}/pulls/{pr_number}")
        except (KeyError, ValueError, RuntimeError) as exc:
            return {"collection_errors": [str(exc)]}

        def identity(item: dict[str, Any]) -> tuple[Any, ...]:
            base = item.get("base") or {}
            base_repo = base.get("repo") or {}
            return (
                item.get("number"),
                base_repo.get("full_name"),
                item.get("state"),
                item.get("draft"),
                item.get("merged"),
                (item.get("head") or {}).get("sha"),
                base.get("sha"),
            )

        if not isinstance(pr, dict) or not isinstance(pr_after, dict):
            return {"collection_errors": ["PR identity response is malformed"]}
        if identity(pr) != identity(pr_after):
            errors.append("PR identity changed while evidence was collected")
        canonical_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
        canonical_number = pr.get("number")
        if not canonical_repo or canonical_number != pr_number:
            errors.append("PR response does not provide the expected canonical repository identity")

        review_by_id = {item.get("id"): item for item in reviews if isinstance(item, dict)}
        findings: list[dict[str, Any]] = []
        for comment in review_comments:
            review_id = comment.get("pull_request_review_id")
            reviewer = (review_by_id.get(review_id) or {}).get("user") or {}
            if review_id not in review_by_id or reviewer.get("login") not in CODEX_LOGINS:
                continue
            findings.append({
                "id": comment.get("id"),
                "review_id": review_id,
                "material": True,
                "disposition": None,
            })
        for review in reviews:
            reviewer = review.get("user") or {}
            if reviewer.get("login") in CODEX_LOGINS and _review_body_residual(str(review.get("body", ""))):
                findings.append({
                    "id": f"review-body-{review.get('id')}",
                    "review_id": review.get("id"),
                    "material": True,
                    "source": "review_body",
                    "disposition": None,
                    "disposition_author": None,
                })
        if not isinstance(graphql, dict):
            errors.append("GitHub GraphQL response was malformed")
            graphql = {}
        if graphql.get("errors"):
            errors.append("GitHub GraphQL returned review-thread errors")
        threads = (((graphql.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads")
        if not isinstance(threads, dict) or not isinstance(threads.get("nodes"), list):
            errors.append("review-thread evidence is unavailable")
        elif (threads.get("pageInfo") or {}).get("hasNextPage") is True:
            errors.append("review-thread pagination is incomplete")
        else:
            for thread in threads["nodes"]:
                if not isinstance(thread, dict):
                    errors.append("review-thread evidence is malformed")
                    continue
                thread_comment_data = thread.get("comments")
                if not isinstance(thread_comment_data, dict) or not isinstance(thread_comment_data.get("nodes"), list):
                    errors.append("review-thread comment evidence is malformed")
                    continue
                if (thread_comment_data.get("pageInfo") or {}).get("hasNextPage") is True:
                    errors.append("review-thread comment pagination is incomplete")
                thread_comments = thread_comment_data["nodes"]
                if any(not isinstance(node, dict) for node in thread_comments):
                    errors.append("review-thread comments are malformed")
                    continue
                comment_ids = {str(node.get("databaseId")) for node in thread_comments if node.get("databaseId") is not None}
                disposition = None
                disposition_author = None
                for node in thread_comments:
                    body = str(node.get("body", ""))
                    match = re.search(r"\bDisposition\s*:\s*(FIXED|DISMISSED|SUPERSEDED)\b", body, re.I)
                    author_login = ((node.get("author") or {}).get("login"))
                    if match and author_login and author_login.lower() in {item.lower() for item in trusted_adjudicators}:
                        disposition = match.group(1).upper()
                        disposition_author = author_login
                for finding in findings:
                    if str(finding["id"]) in comment_ids:
                        finding["disposition"] = disposition
                        finding["disposition_author"] = disposition_author

        return {
            "pr": {
                "repo": canonical_repo,
                "number": canonical_number,
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
                    "body": item.get("body", ""),
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
                    "head_sha": item.get("head_sha"),
                    "app_slug": ((item.get("app") or {}).get("slug")),
                    "run_id": item.get("id"),
                }
                for item in (checks.get("check_runs", []) if isinstance(checks, dict) else [])
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
    parser.add_argument("--trusted-adjudicator", action="append", default=[])
    parser.add_argument("--trusted-check-producer", action="append", default=["github-actions"])
    parser.add_argument("--evidence-json", type=Path)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    args = parser.parse_args(argv)

    if not args.required_check:
        result = _held("HELD_PENDING_REQUIRED_CI", "at least one required check must be named explicitly")
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return 2

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
        evidence = GitHubReadOnlyClient(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")).collect(
            args.repo,
            args.pr,
            trusted_adjudicators=tuple(args.trusted_adjudicator),
        )

    result = evaluate_evidence(
        evidence,
        expected_repo=args.repo,
        expected_pr=args.pr,
        expected_head=args.expected_head,
        expected_base=args.expected_base,
        required_checks=tuple(args.required_check),
        trusted_adjudicators=tuple(args.trusted_adjudicator),
        trusted_check_producers=tuple(args.trusted_check_producer),
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.state == "READY_FOR_HUMAN_INTEGRATION_DECISION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
