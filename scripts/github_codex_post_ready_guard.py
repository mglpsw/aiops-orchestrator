#!/usr/bin/env python3
"""Read-only evidence guard for the post-Ready Codex lifecycle barrier.

The collector reads GitHub lifecycle/review surfaces. The evaluator is pure
and accepts only evidence bound to the expected repository, PR, base, HEAD,
Ready event, Codex review, and required checks. It never performs a write,
merge, Ready transition, or thread resolution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

CODEX_LOGINS = {
    "chatgpt-codex-connector",
    "chatgpt-codex-connector[bot]",
    "openai-codex",
    "openai-codex[bot]",
}
TERMINAL_REVIEW_STATES = {"APPROVED", "COMMENTED"}
ADJUDICATED = {"FIXED", "DISMISSED", "SUPERSEDED"}
EVIDENCE_COLLECTIONS = ("ready_events", "reviews", "summaries", "findings", "checks", "reactions")
CANONICAL_WORKFLOW_PATH = ".github/workflows/ci.yml"
REQUIRED_JOBS = ("Validate repository", "AgentReview release gates")
CODEX_BOILERPLATE = """<details> <summary>ℹ️ About Codex in GitHub</summary>
<br/>
[Your team has set up Codex to review pull requests in this repo](https://chatgpt.com/codex/cloud/settings/general). Reviews are triggered when you
- Open a pull request for review
- Mark a draft as ready
- Comment "@codex review".
If Codex has suggestions, it will comment; otherwise it will react with 👍.
Codex can also answer questions or update the PR. Try commenting "@codex address that feedback".
</details>"""


class GitHubReadError(RuntimeError):
    """Retain HTTP status without exposing response bodies or credentials."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


@dataclass(frozen=True)
class _LiveEvidence:
    """Process-local collector result, never reconstructed from a JSON file."""

    data: dict[str, Any]


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("evidence object is malformed")
    return value


def _objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("evidence collection/member is malformed")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("evidence text is malformed")
    return value


def _positive_id(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("GitHub identity/attempt is malformed")
    return value


def _canonical_run_matches(run: dict[str, Any], *, repo: str, pr: int, head: str,
                           base: str, workflow_path: str) -> bool:
    for key in ("id", "workflow_id", "run_attempt", "check_suite_id"):
        _positive_id(run.get(key))
    for key in ("path", "event", "head_sha", "status"):
        _text(run.get(key))
    _text(_object(run.get("repository")).get("full_name"))
    pulls = _objects(run.get("pull_requests"))
    for pull in pulls:
        _positive_id(pull.get("number"))
        _text(pull.get("url"))
        _text(_object(pull.get("head")).get("sha"))
        _text(_object(pull.get("base")).get("sha"))
    return (
        run["repository"]["full_name"] == repo and run["path"] == workflow_path
        and run["event"] == "pull_request" and run["head_sha"] == head
        and len(pulls) == 1 and pulls[0]["number"] == pr
        and pulls[0]["url"].endswith(f"/repos/{repo}/pulls/{pr}")
        and pulls[0]["head"]["sha"] == head and pulls[0]["base"]["sha"] == base
    )


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
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _review_submission(review: dict[str, Any]) -> datetime | None:
    """An unsubmitted draft has no submission time and supplies no terminal evidence."""
    if review.get("state") == "PENDING" and review.get("submitted_at") is None:
        return None
    submitted = _parse_time(review.get("submitted_at"))
    if submitted is None:
        raise ValueError("submitted review timestamp is malformed")
    return submitted


def _summary_is_explicitly_completed(body: str) -> bool:
    visible = re.sub(r"<details>.*?</details>", "", body, flags=re.IGNORECASE | re.DOTALL)
    lower = visible.lower()
    if re.search(r"\b(?:not|incomplete|failed|failure|error|cancelled|canceled|running|pending)\b", lower):
        return False
    return bool(re.search(r"(?:✅\s*)?(?:\*\*)?completed(?:\*\*)?\b", lower))


def _is_codex_summary(
    comment: dict[str, Any],
    expected_head: str,
    *,
    review_id: int | str | None = None,
    reaction_id: int | None = None,
    ready_event_id: int | str,
) -> bool:
    body = str(comment.get("body", ""))
    lower = body.lower()
    return (
        comment.get("author_login") in CODEX_LOGINS
        and "codex review summary" in lower
        and _summary_is_explicitly_completed(body)
        and ("draft marked ready" in lower or "manual request" in lower)
        and comment.get("bound_head_sha") == expected_head
        and (
            (review_id is not None and comment.get("bound_terminal_kind") == "review"
             and comment.get("bound_review_id") == review_id and "bound_reaction_id" not in comment)
            or (reaction_id is not None and comment.get("bound_terminal_kind") == "clean_reaction"
                and comment.get("bound_reaction_id") == reaction_id and "bound_review_id" not in comment)
        )
        and str(comment.get("bound_ready_event_id")) == str(ready_event_id)
    )


def _clean_reaction_matches(reaction: dict[str, Any], summary: dict[str, Any], *,
                            repo: str, pr: int, start: datetime, completed: datetime) -> bool:
    created = _parse_time(reaction.get("created_at"))
    return (
        reaction.get("author_login") in CODEX_LOGINS
        and reaction.get("author_login") == summary.get("author_login")
        and reaction.get("content") == "+1"
        and reaction.get("target_repo") == repo and reaction.get("target_pr") == pr
        and created is not None and created > start and created >= completed
    )


def _latest_ready_event(evidence: dict[str, Any]) -> dict[str, Any] | None:
    candidates = []
    for event in evidence.get("ready_events", []):
        if event.get("event") != "ready_for_review":
            continue
        timestamp = _parse_time(event.get("created_at"))
        if timestamp is None or event.get("id") in (None, ""):
            return None
        candidates.append((timestamp, event))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _collection_members_are_objects(evidence: dict[str, Any]) -> str | None:
    for key in EVIDENCE_COLLECTIONS:
        collection = evidence.get(key)
        if not isinstance(collection, list):
            return f"evidence collection {key} is not a list"
        if any(not isinstance(member, dict) for member in collection):
            return f"evidence collection {key} has a malformed member"
    return None


def _required_checks_pass(
    evidence: dict[str, Any],
    required_checks: tuple[str, ...],
    *,
    expected_head: str,
    trusted_check_producers: tuple[str, ...],
    expected_repo: str,
    expected_pr: int,
    expected_base: str,
    canonical_workflow_path: str,
) -> bool | None:
    checks = evidence.get("checks")
    if not isinstance(checks, list) or not required_checks or not trusted_check_producers:
        return None
    canonical = evidence.get("canonical_ci")
    canonical_run = canonical.get("run") if isinstance(canonical, dict) else None
    if not isinstance(canonical_run, dict) or not _canonical_run_matches(
        canonical_run, repo=expected_repo, pr=expected_pr, head=expected_head,
        base=expected_base, workflow_path=canonical_workflow_path,
    ):
        return False
    bound_runs = set()
    for name in required_checks:
        matches = []
        for item in checks:
            if not isinstance(item, dict) or item.get("name") != name:
                continue
            suite = item.get("check_suite_id")
            if type(suite) is not int or suite <= 0:
                return False  # unknown membership is not evidence of a foreign suite
            if suite == canonical_run["check_suite_id"]:
                matches.append(item)
        if len(matches) != 1:
            return False
        item = matches[0]
        producer = item.get("app_slug") or item.get("producer")
        run_id = item.get("run_id") or item.get("id")
        binding = item.get("ci_binding")
        if not isinstance(binding, dict):
            return False
        run, job = _object(binding.get("run")), _object(binding.get("job"))
        if run != canonical_run:
            return False
        if (item.get("head_sha") != expected_head or producer not in trusted_check_producers
            or item.get("check_suite_id") != run["check_suite_id"]
            or job.get("id") != run_id or job.get("name") != name
            or job.get("run_id") != run["id"] or job.get("run_attempt") != run["run_attempt"]
            or job.get("head_sha") != expected_head):
            return False
        if (run["status"] != "completed" or run.get("conclusion") != "success"
            or item.get("status") != "completed" or item.get("conclusion") != "success"
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"):
            return False
        bound_runs.add((run["id"], run["run_attempt"]))
    if len(bound_runs) != 1:
        return False
    return True


def _finding_is_adjudicated(finding: dict[str, Any], trusted_adjudicators: tuple[str, ...]) -> bool:
    disposition = str(finding.get("disposition", "")).upper()
    author = str(finding.get("disposition_author", "")).lower()
    trusted = {item.lower() for item in trusted_adjudicators}
    return disposition in ADJUDICATED and author in trusted


def _review_body_residual(body: str) -> str:
    """Remove the connector's boilerplate while preserving non-inline findings."""

    def strip_known_boilerplate(match: re.Match[str]) -> str:
        normalize = lambda value: re.sub(r"\s+", " ", value).strip()
        return "" if normalize(match[0]) == normalize(CODEX_BOILERPLATE) else match[0]

    residual = re.sub(r"<details>.*?</details>", strip_known_boilerplate, body, flags=re.DOTALL)
    residual = re.sub(r"###\s*💡\s*Codex Review", "", residual, flags=re.IGNORECASE)
    residual = re.sub(
        r"Here are some automated review suggestions for this pull request\.",
        "",
        residual,
        flags=re.IGNORECASE,
    )
    residual = re.sub(r"\*\*Reviewed commit:\*\*\s*`[0-9a-f]+`", "", residual, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", residual).strip()


def _parse_disposition(body: str) -> dict[str, str] | None:
    """Strict unquoted issue-comment or inline-reply channel, tied to a subject."""
    match = re.fullmatch(
        r"Guard-Disposition: ([A-Za-z0-9:_-]+) (FIXED|DISMISSED|SUPERSEDED)\n"
        r"Subject-Head: ([0-9a-f]{40})\nRepair-Commit: ([0-9a-f]{40})\nEvidence: (\S[^\n]*(?:\n[^\n]+)*)\s*",
        body.strip(),
    )
    return dict(zip(("id", "state", "subject", "repair", "evidence"), match.groups())) if match else None


def evaluate_evidence(
    evidence: dict[str, Any] | _LiveEvidence,
    *,
    expected_repo: str,
    expected_pr: int,
    expected_head: str,
    expected_base: str | None = None,
    required_checks: tuple[str, ...] = (),
    trusted_adjudicators: tuple[str, ...] = (),
    trusted_check_producers: tuple[str, ...] = ("github-actions",),
    canonical_workflow_path: str = CANONICAL_WORKFLOW_PATH,
) -> GuardResult:
    """Evaluate normalized evidence without trusting self-declared clean flags."""

    if not isinstance(evidence, _LiveEvidence):
        return _held("HELD_CODEX_UNAVAILABLE", "offline/fixture evidence cannot establish live readiness")
    try:
        return _evaluate_live(evidence.data, expected_repo=expected_repo, expected_pr=expected_pr,
                              expected_head=expected_head, expected_base=expected_base,
                              required_checks=required_checks, trusted_adjudicators=trusted_adjudicators,
                              trusted_check_producers=trusted_check_producers,
                              canonical_workflow_path=canonical_workflow_path)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return _held("HELD_CODEX_UNAVAILABLE", f"live evidence shape is malformed ({type(exc).__name__})")


def _evaluate_live(evidence: dict[str, Any], **policy: Any) -> GuardResult:
    expected_repo, expected_pr = policy["expected_repo"], policy["expected_pr"]
    expected_head, expected_base = policy["expected_head"], policy["expected_base"]
    required_checks = policy["required_checks"]
    trusted_adjudicators = policy["trusted_adjudicators"]
    trusted_check_producers = policy["trusted_check_producers"]

    if not all(isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (expected_head, expected_base)):
        return _held("HELD_CODEX_UNAVAILABLE", "the expected subject requires full base and HEAD SHAs")

    if not isinstance(evidence, dict):
        return _held("HELD_CODEX_UNAVAILABLE", "evidence is not an object")
    if evidence.get("revalidated") is not True:
        return _held("HELD_CODEX_UNAVAILABLE", "live evidence was not revalidated after collection", evidence)
    if evidence.get("collection_errors"):
        return _held("HELD_CODEX_UNAVAILABLE", "one or more evidence surfaces could not be collected", evidence)
    malformed = _collection_members_are_objects(evidence)
    if malformed:
        return _held("HELD_CODEX_UNAVAILABLE", malformed, evidence)
    for event in evidence["ready_events"]:
        if not isinstance(event.get("created_at"), str) or type(event.get("id")) is not int:
            raise ValueError("Ready event fields are malformed")
    for review in evidence["reviews"]:
        if type(review.get("id")) is not int or any(not isinstance(review.get(key), str) for key in ("author_login", "commit_id", "state")):
            raise ValueError("review fields are malformed")
        _review_submission(review)
    for summary in evidence["summaries"]:
        if type(summary.get("id")) is not int or any(not isinstance(summary.get(key), str) for key in ("body", "author_login", "updated_at")):
            raise ValueError("summary fields are malformed")
    for finding in evidence["findings"]:
        if not isinstance(finding.get("id"), (int, str)) or type(finding.get("review_id")) is not int or not isinstance(finding.get("commit_id"), str):
            raise ValueError("finding fields are malformed")
    for check in evidence["checks"]:
        if type(check.get("run_id")) is not int or any(not isinstance(check.get(key), str) for key in ("name", "head_sha", "app_slug", "status")):
            raise ValueError("check fields are malformed")
    for reaction in evidence["reactions"]:
        _positive_id(reaction.get("id"))
        _positive_id(reaction.get("target_pr"))
        for key in ("author_login", "content", "target_repo"):
            _text(reaction.get(key))
        if _parse_time(reaction.get("created_at")) is None:
            raise ValueError("reaction timestamp is malformed")

    pr = evidence.get("pr")
    if not isinstance(pr, dict):
        return _held("HELD_CODEX_UNAVAILABLE", "PR lifecycle evidence is absent", evidence)
    if pr.get("repo") != expected_repo or pr.get("number") != expected_pr:
        return _held("HELD_STALE", "repository or PR identity differs from the expected subject", evidence)
    if pr.get("head_sha") != expected_head:
        return _held("HELD_STALE", "current PR HEAD differs from the expected subject", evidence)
    if expected_base is None or pr.get("base_sha") != expected_base:
        return _held("HELD_STALE", "current PR base differs from the expected subject", evidence)
    if pr.get("state") != "open" or pr.get("merged") is not False:
        return _held("HELD_STALE", "the pre-merge PR lifecycle is no longer open", evidence)
    if pr.get("draft") is not False:
        return _held("HELD_PENDING_CODEX", "the PR has not crossed the Ready transition", evidence)

    checks = _required_checks_pass(
        evidence,
        required_checks,
        expected_head=expected_head,
        trusted_check_producers=trusted_check_producers,
        expected_repo=expected_repo, expected_pr=expected_pr, expected_base=expected_base,
        canonical_workflow_path=policy.get("canonical_workflow_path", CANONICAL_WORKFLOW_PATH),
    )
    if checks is None:
        return _held("HELD_PENDING_REQUIRED_CI", "required-check evidence is absent", evidence)
    if checks is False:
        return _held("HELD_PENDING_REQUIRED_CI", "a required check is not completed successfully", evidence)

    reviews = [
        item for item in evidence.get("reviews", [])
        if item.get("author_login") in CODEX_LOGINS
        and item.get("commit_id") == expected_head
        and item.get("state") in TERMINAL_REVIEW_STATES
        and item.get("id") not in (None, "")
    ]
    ready_event = _latest_ready_event(evidence)
    if ready_event is None:
        return _held("HELD_CODEX_UNAVAILABLE", "Ready-cycle evidence is missing, malformed, or lacks an identity", evidence)
    ready_time = _parse_time(ready_event.get("created_at"))
    if ready_time is None:
        return _held("HELD_CODEX_UNAVAILABLE", "Ready-cycle timestamp is malformed", evidence)
    if any(item.get("author_login") in CODEX_LOGINS and item.get("commit_id") == expected_head
           and item.get("state") == "PENDING" for item in evidence["reviews"]):
        return _held("HELD_PENDING_CODEX", "a current Codex review is still unsubmitted", evidence)
    summaries = [
        item for item in evidence.get("summaries", [])
        if _is_codex_summary(
            item,
            expected_head,
            review_id=item.get("bound_review_id"),
            reaction_id=item.get("bound_reaction_id"),
            ready_event_id=ready_event.get("id"),
        )
    ]
    if not summaries:
        if reviews and max(_review_submission(item) for item in reviews) < ready_time:
            return _held("HELD_STALE", "the Codex review predates the Ready event", evidence)
        return _held("HELD_PENDING_CODEX", "no exact-cycle terminal summary is bound to the current review", evidence)
    if len(summaries) != 1:
        return _held("HELD_CODEX_UNAVAILABLE", "terminal summary binding is ambiguous", evidence)
    summary = summaries[0]
    start, completed = _parse_time(summary.get("bound_trigger_at")), _parse_time(summary.get("completed_at"))
    if start is None or completed is None or start < ready_time or completed < start:
        return _held("HELD_PENDING_CODEX", "terminal completion is not bound to the current trigger", evidence)
    if any(r["author_login"] in CODEX_LOGINS and r["content"] == "eyes"
           and r["target_repo"] == expected_repo and r["target_pr"] == expected_pr
           and _parse_time(r["created_at"]) >= start for r in evidence["reactions"]):
        return _held("HELD_PENDING_CODEX", "the connector still signals an active review", evidence)
    current_reviews = [item for item in evidence["reviews"]
                       if item["author_login"] in CODEX_LOGINS and item["commit_id"] == expected_head
                       and _review_submission(item) is not None and _review_submission(item) >= start]
    if summary["bound_terminal_kind"] == "review":
        if (len(current_reviews) != 1 or current_reviews[0]["id"] != summary["bound_review_id"]
            or current_reviews[0]["author_login"] != summary["author_login"]
            or current_reviews[0]["state"] not in TERMINAL_REVIEW_STATES
            or _review_submission(current_reviews[0]) > completed
            or _parse_time(summary["updated_at"]) < _review_submission(current_reviews[0])):
            return _held("HELD_PENDING_CODEX", "formal review completion binding is not unique/current", evidence)
    else:
        reactions = [r for r in evidence["reactions"] if _clean_reaction_matches(
            r, summary, repo=expected_repo, pr=expected_pr, start=start, completed=completed)]
        if current_reviews or len(reactions) != 1 or reactions[0]["id"] != summary["bound_reaction_id"]:
            return _held("HELD_PENDING_CODEX", "clean completion lacks a unique new reaction on this PR", evidence)

    findings = [
        item for item in evidence.get("findings", [])
        if item.get("material", True) is not False
    ]
    open_findings = [item for item in findings if not (
        _finding_is_adjudicated(item, trusted_adjudicators)
        and item.get("disposition_verified") is True
        and item.get("disposition_subject") == expected_head
    )]
    if open_findings:
        return _held(
            "HELD_WITH_MATERIAL_FINDINGS",
            "material Codex findings remain without an explicit disposition",
            {**evidence, "open_findings": open_findings},
        )

    return GuardResult(
        state="READY_FOR_HUMAN_INTEGRATION_DECISION",
        reason="current HEAD, Ready cycle, terminal Codex completion, findings, and required checks are observed",
        merge_authorized=False,
        evidence=evidence,
    )


class GitHubReadOnlyClient:
    """Minimal GitHub REST reader; no mutation endpoint exists here."""

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
        except HTTPError as exc:
            raise GitHubReadError("GitHub read failed (HTTPError)", http_status=exc.code) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"GitHub read failed ({type(exc).__name__})") from exc

    def _pages(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 21):
            query = urlencode({"per_page": 100, "page": page})
            batch = self._request("GET", f"{path}?{query}")
            if not isinstance(batch, list):
                raise RuntimeError("GitHub list response was not an array")
            if any(not isinstance(item, dict) for item in batch):
                raise RuntimeError("GitHub list response contained a malformed member")
            if any(type(item.get("id")) is not int for item in batch):
                raise RuntimeError("GitHub list member identity is malformed")
            result.extend(batch)
            if len(batch) < 100:
                if len({item["id"] for item in result}) != len(result):
                    raise RuntimeError("GitHub pagination contained duplicate identities")
                return result
        raise RuntimeError("GitHub pagination exceeded the bounded page budget")

    def _envelope_pages(self, path: str, key: str) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        total_count: int | None = None
        for page in range(1, 21):
            query = urlencode({"per_page": 100, "page": page})
            payload = self._request("GET", f"{path}{'&' if '?' in path else '?'}{query}")
            if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
                raise RuntimeError(f"GitHub {key} response was malformed")
            if any(not isinstance(item, dict) for item in payload[key]):
                raise RuntimeError(f"GitHub {key} response contained a malformed member")
            if any(type(item.get("id")) is not int or item["id"] <= 0 for item in payload[key]):
                raise RuntimeError(f"GitHub {key} identity is malformed")
            if type(payload.get("total_count")) is not int or payload["total_count"] < 0:
                raise RuntimeError(f"GitHub {key} response lacked a completeness count")
            if total_count is None:
                total_count = payload["total_count"]
            elif payload["total_count"] != total_count:
                raise RuntimeError(f"GitHub {key} total changed during collection")
            result.extend(payload[key])
            if len(payload[key]) < 100:
                if len(result) != total_count:
                    raise RuntimeError(f"GitHub {key} pagination is incomplete")
                if len({item["id"] for item in result}) != len(result):
                    raise RuntimeError(f"GitHub {key} pages contain duplicate identities")
                return {"total_count": total_count, key: result}
        raise RuntimeError(f"GitHub {key} pagination exceeded the bounded page budget")

    def _check_pages(self, path: str) -> dict[str, Any]:
        return self._envelope_pages(path, "check_runs")

    def _canonical_ci(self, prefix: str, pr: dict[str, Any], workflow_path: str) -> dict[str, Any]:
        # The suite relation, not details_url or job name, selects the Actions
        # run. All plausible exact-subject runs are observed before selection.
        head, base = _text(pr["head"]["sha"]), _text(pr["base"]["sha"])
        runs = self._envelope_pages(
            f"{prefix}/actions/runs?{urlencode({'head_sha': head, 'event': 'pull_request'})}",
            "workflow_runs",
        )["workflow_runs"]
        candidates = [run for run in runs if _canonical_run_matches(
            run, repo=pr["base"]["repo"]["full_name"], pr=pr["number"], head=head,
            base=base, workflow_path=workflow_path,
        )]
        if len(candidates) != 1:
            return {"runs": runs, "run": None, "jobs": []}
        selected = candidates[0]
        run = _object(self._request("GET", f"{prefix}/actions/runs/{selected['id']}"))
        fields = ("id", "workflow_id", "path", "event", "head_sha", "run_attempt",
                  "check_suite_id", "repository", "pull_requests", "status", "conclusion")
        if any(run.get(key) != selected.get(key) for key in fields):
            raise RuntimeError("canonical Actions run changed during collection")
        jobs = self._envelope_pages(
            f"{prefix}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs",
        )["jobs"]
        for job in jobs:
            for key in ("id", "run_id", "run_attempt"):
                _positive_id(job.get(key))
            for key in ("name", "head_sha", "status"):
                _text(job.get(key))
            if job.get("conclusion") is not None:
                _text(job["conclusion"])
        return {"runs": runs, "run": run, "jobs": jobs}

    def _content_sha(self, prefix: str, path: str, ref: str, *, allow_missing: bool = False) -> str | None:
        try:
            payload = self._request(
                "GET",
                f"{prefix}/contents/{quote(path, safe='/')}?{urlencode({'ref': ref})}",
            )
        except GitHubReadError as exc:
            if allow_missing and exc.http_status == 404:
                return None
            raise
        if (not isinstance(payload, dict) or payload.get("type") != "file"
            or not isinstance(payload.get("sha"), str) or not re.fullmatch(r"[0-9a-f]{40}", payload["sha"])):
            raise RuntimeError("affected-file byte identity could not be collected")
        return payload["sha"]

    def _bind_summaries(self, prefix: str, head: str, reviews: list[dict[str, Any]],
                        comments: list[dict[str, Any]], ready_events: list[dict[str, Any]],
                        reactions: list[dict[str, Any]], *, repo: str, pr: int,
                        trusted_requesters: tuple[str, ...]) -> list[dict[str, Any]]:
        result = [{"id": item["id"], "author_login": _object(item["user"])["login"],
                   "body": item["body"], "updated_at": _text(item.get("updated_at"))}
                  for item in comments if _object(item["user"])["login"] in CODEX_LOGINS]
        if not ready_events:
            return result
        ready = max(ready_events, key=lambda event: _parse_time(event["created_at"]))
        ready_time = _parse_time(ready["created_at"])
        if any(item["user"]["login"] in CODEX_LOGINS and item["commit_id"] == head
               and item["state"] == "PENDING" for item in reviews):
            return result
        manual_commands = [item for item in comments if item["body"].strip() == "@codex review"
                    and _parse_time(item.get("created_at")) is not None
                    and _parse_time(item["created_at"]) >= ready_time]
        if manual_commands and not trusted_requesters:
            return result  # requester policy is unknown; no positive cycle inference
        trusted = {login.lower() for login in trusted_requesters}
        requests = [item for item in manual_commands if item["user"]["login"].lower() in trusted]
        for summary in result:
            # Only the connector table row supplies the ref and completion time.
            rows = re.findall(
                r'^\|[^\n|]*\*\*Code Review\*\*\s*\|\s*✅\s*\*\*Completed\*\*\s*'
                r'<relative-time datetime="([^"]+)"[^>]*>[^<]*</relative-time>\s*\|\s*`([0-9a-f]{7,40})`\s*\|\s*([^|]+)\|\s*$',
                summary["body"], re.M,
            )
            if len(rows) != 1:
                continue
            timestamp, ref, trigger = rows[0]
            completed = _parse_time(timestamp)
            if completed is None or trigger.strip() not in {"Manual request", "Draft marked ready"}:
                continue
            start = ready_time
            latest_request = max((_parse_time(item["created_at"]) for item in requests), default=None)
            if latest_request is not None and latest_request > completed:
                continue  # a newer requested round supersedes even a completed Ready row
            if trigger.strip() == "Manual request":
                if not requests:
                    continue
                start = latest_request
            elif latest_request is not None:
                continue  # the latest requested round must have its own Manual completion
            # GitHub resolves the abbreviated ref uniquely; prefix equality is
            # never a binding. Ambiguous refs/API failures keep the guard held.
            resolved = _object(self._request("GET", f"{prefix}/commits/{ref}"))
            full_sha = _text(resolved.get("sha"))
            if not re.fullmatch(r"[0-9a-f]{40}", full_sha) or full_sha != head or completed < start:
                continue
            current_reviews = []
            for review in reviews:
                submitted = _review_submission(review)
                if (_object(review["user"])["login"] in CODEX_LOGINS and review["commit_id"] == full_sha
                    and submitted is not None and submitted >= start):
                    current_reviews.append(review)
            if any(r["author_login"] in CODEX_LOGINS and r["content"] == "eyes"
                   and _parse_time(r["created_at"]) >= start for r in reactions):
                continue
            binding = {}
            if (len(current_reviews) == 1 and current_reviews[0]["state"] in TERMINAL_REVIEW_STATES
                and current_reviews[0]["user"]["login"] == summary["author_login"]
                and _review_submission(current_reviews[0]) <= completed):
                binding = {"bound_terminal_kind": "review", "bound_review_id": current_reviews[0]["id"]}
            elif not current_reviews:
                clean = [r for r in reactions if _clean_reaction_matches(
                    r, summary, repo=repo, pr=pr, start=start, completed=completed)]
                if len(clean) == 1:
                    binding = {"bound_terminal_kind": "clean_reaction", "bound_reaction_id": clean[0]["id"]}
            if binding:
                summary.update(binding, bound_head_sha=full_sha, bound_ready_event_id=ready["id"],
                               completed_at=completed.isoformat(), bound_trigger_at=start.isoformat())
        return result

    def collect(
        self,
        repo: str,
        pr_number: int,
        *,
        trusted_adjudicators: tuple[str, ...] = (),
        trusted_requesters: tuple[str, ...] = (),
        required_checks: tuple[str, ...] = REQUIRED_JOBS,
        canonical_workflow_path: str = CANONICAL_WORKFLOW_PATH,
    ) -> _LiveEvidence:
        try:
            if not self.token:
                raise ValueError("authenticated live collection requires a token")
            data = self._collect(repo, pr_number, trusted_adjudicators=trusted_adjudicators,
                                 trusted_requesters=trusted_requesters, required_checks=required_checks,
                                 canonical_workflow_path=canonical_workflow_path)
            return _LiveEvidence(data)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            return _LiveEvidence({"collection_errors": [str(exc)], "revalidated": False})

    def _collect(self, repo: str, pr_number: int, *, trusted_adjudicators: tuple[str, ...],
                 trusted_requesters: tuple[str, ...], required_checks: tuple[str, ...],
                 canonical_workflow_path: str) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        prefix = f"/repos/{owner}/{name}"
        errors: list[str] = []
        try:
            pr = _object(self._request("GET", f"{prefix}/pulls/{pr_number}"))
            _object(pr.get("head"))
            _object(_object(pr.get("base")).get("repo"))
            reviews = self._pages(f"{prefix}/pulls/{pr_number}/reviews")
            review_comments = self._pages(f"{prefix}/pulls/{pr_number}/comments")
            summaries = self._pages(f"{prefix}/issues/{pr_number}/comments")
            # Issue events provide stable IDs/times for Ready transitions; unlike
            # the heterogeneous timeline they do not contain ID-less commits.
            timeline = self._pages(f"{prefix}/issues/{pr_number}/events")
            reactions = self._pages(f"{prefix}/issues/{pr_number}/reactions")
            checks = self._check_pages(f"{prefix}/commits/{pr['head']['sha']}/check-runs")
            canonical_ci = self._canonical_ci(prefix, pr, canonical_workflow_path)
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

        canonical_repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name")
        canonical_number = pr.get("number")
        if not canonical_repo or canonical_number != pr_number:
            errors.append("PR response does not provide the expected canonical repository identity")

        for item in reviews + review_comments + summaries:
            _text(_object(item.get("user")).get("login"))
            _text(item.get("body"))
            if type(item.get("id")) is not int:
                raise ValueError("review/comment identity is malformed")
        for item in reviews:
            _text(item.get("commit_id"))
            _text(item.get("state"))
            _review_submission(item)
        for item in reactions:
            _positive_id(item["id"])
            _text(_object(item.get("user")).get("login"))
            _text(item.get("content"))
            if _parse_time(item.get("created_at")) is None:
                raise ValueError("reaction timestamp is malformed")
        normalized_reactions = [{"id": item["id"], "author_login": item["user"]["login"],
                                 "content": item["content"], "created_at": item["created_at"],
                                 "target_repo": canonical_repo, "target_pr": canonical_number}
                                for item in reactions]
        for item in summaries:
            if any(_parse_time(item.get(key)) is None for key in ("created_at", "updated_at")):
                raise ValueError("issue-comment timestamp is malformed")
        for item in timeline:
            _text(item.get("event"))
            if type(item.get("id")) is not int or _parse_time(item.get("created_at")) is None:
                raise ValueError("timeline event is malformed")
        for item in checks["check_runs"]:
            _text(_object(item.get("app")).get("slug"))
            for key in ("name", "head_sha", "status"):
                _text(item.get(key))
            if type(item.get("id")) is not int or (item.get("conclusion") is not None and not isinstance(item.get("conclusion"), str)):
                raise ValueError("check-run member is malformed")

        review_by_id = {item.get("id"): item for item in reviews if isinstance(item, dict)}
        findings: list[dict[str, Any]] = []
        for comment in review_comments:
            if comment.get("in_reply_to_id") is not None:
                _positive_id(comment["in_reply_to_id"])
                continue  # replies remain available below for disposition processing
            review_id = comment.get("pull_request_review_id")
            if review_id not in review_by_id:
                raise ValueError("inline comment has an unavailable review identity")
            reviewer = (review_by_id.get(review_id) or {}).get("user") or {}
            if review_by_id[review_id]["state"] == "PENDING":
                continue
            if review_id not in review_by_id or reviewer.get("login") not in CODEX_LOGINS or _object(comment.get("user")).get("login") not in CODEX_LOGINS:
                continue
            findings.append({
                "id": comment.get("id"),
                "review_id": review_id,
                "material": True,
                "source": "inline",
                "disposition": None,
                "disposition_author": None,
                "path": _text(comment.get("path")),
                "commit_id": review_by_id[review_id]["commit_id"],
            })
        for review in reviews:
            if review["state"] == "PENDING":
                continue
            reviewer = review.get("user") or {}
            residual = _review_body_residual(str(review.get("body", "")))
            if reviewer.get("login") in CODEX_LOGINS and residual:
                body_digest = hashlib.sha256(residual.encode("utf-8")).hexdigest()
                findings.append({
                    "id": f"review-body:{review.get('id')}:{body_digest}",
                    "review_id": review.get("id"),
                    "material": True,
                    "source": "review_body",
                    "disposition": None,
                    "disposition_author": None,
                    "path": None,
                    "commit_id": review.get("commit_id"),
                })
        # REST /pulls/comments includes all inline roots and replies, paginated.
        # Issue comments form the explicit channel for review-body dispositions.
        trusted = {login.lower() for login in trusted_adjudicators}
        for finding in findings:
            finding["applicability"] = "current"
            if finding["commit_id"] != pr["head"]["sha"]:
                finding["applicability"] = "unknown"
                if finding["path"]:
                    old_blob = self._content_sha(prefix, finding["path"], finding["commit_id"])
                    current_blob = self._content_sha(prefix, finding["path"], pr["head"]["sha"], allow_missing=True)
                    finding.update(affected_blob_before=old_blob, affected_blob_current=current_blob,
                                   applicability="byte_identical" if old_blob == current_blob else "changed_requires_disposition")
            for comment in review_comments + summaries:
                author = _object(comment.get("user"))["login"]
                if author.lower() not in trusted:
                    continue
                disposition = _parse_disposition(comment["body"])
                if not disposition or disposition["id"] != str(finding["id"]) or disposition["subject"] != pr["head"]["sha"]:
                    continue
                comparison = _object(self._request("GET", f"{prefix}/compare/{disposition['repair']}...{pr['head']['sha']}"))
                if comparison.get("status") not in {"ahead", "identical"}:
                    continue
                if disposition["state"] == "FIXED":
                    if finding["commit_id"] == disposition["repair"]:
                        continue
                    causal = _object(self._request("GET", f"{prefix}/compare/{finding['commit_id']}...{disposition['repair']}"))
                    if causal.get("status") != "ahead":
                        continue
                if disposition["state"] == "FIXED" and finding["path"]:
                    old_blob = self._content_sha(prefix, finding["path"], finding["commit_id"])
                    repaired_blob = self._content_sha(prefix, finding["path"], disposition["repair"], allow_missing=True)
                    current_blob = self._content_sha(prefix, finding["path"], pr["head"]["sha"], allow_missing=True)
                    if old_blob == repaired_blob or old_blob == current_blob:
                        continue
                finding.update(disposition=disposition["state"], disposition_author=author,
                               disposition_subject=disposition["subject"], disposition_verified=True,
                               disposition_comment_id=comment["id"], repair_commit=disposition["repair"],
                               repair_evidence=disposition["evidence"])

        ready_events = [item for item in timeline if item["event"] == "ready_for_review"]
        bound_summaries = self._bind_summaries(prefix, pr["head"]["sha"], reviews, summaries, ready_events,
                                             normalized_reactions, repo=canonical_repo, pr=canonical_number,
                                             trusted_requesters=trusted_requesters)
        # Complete all dependent reads before the final mutable-channel/PR check.
        if reviews != self._pages(f"{prefix}/pulls/{pr_number}/reviews") or review_comments != self._pages(f"{prefix}/pulls/{pr_number}/comments"):
            errors.append("review evidence changed during collection")
        if summaries != self._pages(f"{prefix}/issues/{pr_number}/comments") or timeline != self._pages(f"{prefix}/issues/{pr_number}/events"):
            errors.append("cycle/disposition evidence changed during collection")
        if reactions != self._pages(f"{prefix}/issues/{pr_number}/reactions"):
            errors.append("reaction evidence changed during collection")
        if checks != self._check_pages(f"{prefix}/commits/{pr['head']['sha']}/check-runs"):
            errors.append("CI evidence changed during collection")
        if canonical_ci != self._canonical_ci(prefix, pr, canonical_workflow_path):
            errors.append("canonical Actions provenance changed during collection")
        pr_after = _object(self._request("GET", f"{prefix}/pulls/{pr_number}"))
        _object(pr_after.get("head"))
        _object(_object(pr_after.get("base")).get("repo"))
        if identity(pr) != identity(pr_after):
            errors.append("PR identity changed during collection")

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
                {"id": item["id"], "event": item.get("event"), "created_at": item.get("created_at")}
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
            "summaries": bound_summaries,
            "findings": findings,
            "reactions": normalized_reactions,
            "checks": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "conclusion": item.get("conclusion"),
                    "head_sha": item.get("head_sha"),
                    "app_slug": ((item.get("app") or {}).get("slug")),
                    "run_id": item.get("id"),
                    "check_suite_id": _positive_id(_object(item.get("check_suite")).get("id"))
                        if item["name"] in required_checks else None,
                    "ci_binding": self._job_binding(item, canonical_ci)
                        if item["name"] in required_checks else None,
                }
                for item in (checks.get("check_runs", []) if isinstance(checks, dict) else [])
            ],
            "collection_errors": errors,
            "revalidated": not errors,
            "canonical_ci": canonical_ci,
        }

    @staticmethod
    def _job_binding(check: dict[str, Any], canonical_ci: dict[str, Any]) -> dict[str, Any] | None:
        run = canonical_ci["run"]
        if run is None or _object(check.get("check_suite")).get("id") != run["check_suite_id"]:
            return None
        # Match both identity and name; duplicate names/IDs are never last-wins.
        matches = [job for job in canonical_ci["jobs"] if job["id"] == check["id"] or job["name"] == check["name"]]
        if len(matches) != 1 or matches[0]["id"] != check["id"] or matches[0]["name"] != check["name"]:
            return None
        return {"run": run, "job": matches[0]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument("--required-check", action="append", default=[])
    parser.add_argument("--trusted-adjudicator", action="append", default=[])
    parser.add_argument("--trusted-requester", action="append", default=[])
    parser.add_argument("--trusted-check-producer", action="append", default=["github-actions"])
    parser.add_argument("--canonical-workflow-path", default=CANONICAL_WORKFLOW_PATH)
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
            trusted_requesters=tuple(args.trusted_requester),
            required_checks=tuple(args.required_check),
            canonical_workflow_path=args.canonical_workflow_path,
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
        canonical_workflow_path=args.canonical_workflow_path,
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return 0 if result.state == "READY_FOR_HUMAN_INTEGRATION_DECISION" else 2


if __name__ == "__main__":
    raise SystemExit(main())
