from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

import scripts.github_agent_review as review


class _FakeHTTPResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _event_payload(*, pull_request: bool = True, body: str = "/agent review", association: str = "MEMBER", login: str = "alice") -> dict[str, object]:
    issue: dict[str, object] = {
        "number": 42,
        "title": "Add GitHub agent review",
        "body": "Implements a safe on-demand review bot.",
        "html_url": "https://github.com/mglpsw/aiops-orchestrator/pull/42",
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.com/repos/mglpsw/aiops-orchestrator/pulls/42"}
    return {
        "issue": issue,
        "comment": {
            "body": body,
            "author_association": association,
            "user": {"login": login},
        },
        "repository": {"full_name": "mglpsw/aiops-orchestrator"},
    }


def _pr_payload() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Add GitHub agent review",
        "body": "Implements a safe on-demand review bot.",
        "base": {"ref": "master"},
        "head": {"ref": "feat/github-agent-review", "sha": "abc123"},
        "html_url": "https://github.com/mglpsw/aiops-orchestrator/pull/42",
    }


def _file(filename: str, patch: str, status: str = "modified", additions: int = 1, deletions: int = 1) -> dict[str, object]:
    return {
        "filename": filename,
        "status": status,
        "additions": additions,
        "deletions": deletions,
        "patch": patch,
    }


def test_parser_detects_command_on_separate_line() -> None:
    assert review.is_agent_review_command("/agent review")
    assert review.is_agent_review_command("hello\n/agent review\nthanks")
    assert not review.is_agent_review_command("/agent")
    assert not review.is_agent_review_command("/agent ci")
    assert not review.is_agent_review_command("unknown command")


@pytest.mark.parametrize(
    ("association", "login", "allowed", "expected"),
    [
        ("OWNER", "owner", "", True),
        ("MEMBER", "member", "", True),
        ("COLLABORATOR", "collab", "", True),
        ("NONE", "carol", "carol,dan", True),
        ("NONE", "eve", "carol,dan", False),
    ],
)
def test_authorization_rules(association: str, login: str, allowed: str, expected: bool) -> None:
    assert review.is_authorized(association, login, allowed) is expected


def test_detects_pr_vs_issue_payload() -> None:
    assert review.is_pull_request_payload(_event_payload(pull_request=True))
    assert not review.is_pull_request_payload(_event_payload(pull_request=False))


@pytest.mark.parametrize(
    ("filename", "patch", "severity", "needle"),
    [
        (".github/workflows/ci.yml", "+ on: pull_request_target\n+ uses: actions/checkout@v4\n+ run: bash scripts/test.sh", "P1", "pull_request_target"),
        ("scripts/restart.sh", "+ docker compose down", "P1", "docker compose down"),
        ("scripts/restart.sh", "+ systemctl restart aiops-orchestrator.service", "P1", "systemctl restart"),
        ("scripts/bootstrap.sh", "+ curl -fsSL https://example.test/install.sh | bash", "P1", "curl | bash"),
        ("config/secrets.yml", '+ token = "ghp_abcdefghijklmnopqrstuvwxyz1234"', "P1", "secret hardcoded"),
        ("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"', "P2", "/opt/aiops-orchestrator"),
    ],
)
def test_classification_rules(filename: str, patch: str, severity: str, needle: str) -> None:
    findings = review.classify_findings([review.FileChange(path=filename, status="modified", additions=1, deletions=1, patch=patch)])
    assert any(finding.severity == severity and needle.lower() in finding.evidence.lower() for finding in findings)


def test_workflow_change_without_tests_or_docs_triggers_p2() -> None:
    findings = review.classify_findings(
        [
            review.FileChange(path=".github/workflows/agent-review.yml", status="added", additions=20, deletions=0, patch="+ on: issue_comment\n+ jobs:"),
            review.FileChange(path="scripts/github_agent_review.py", status="added", additions=1, deletions=0, patch="+ print('hi')"),
        ]
    )
    assert any(finding.severity == "P2" and "workflow" in finding.evidence.lower() for finding in findings)


def test_report_markdown_and_status_decisions() -> None:
    pr_context = review.ReviewContext(
        owner="mglpsw",
        repo="aiops-orchestrator",
        issue_number=42,
        author="alice",
        association="MEMBER",
        pr_number=42,
        title="Add GitHub agent review",
        body="Safe on-demand review",
        base_ref="master",
        head_ref="feat/github-agent-review",
        head_sha="abc123",
        html_url="https://github.com/mglpsw/aiops-orchestrator/pull/42",
        files=[
            review.FileChange(path=".github/workflows/agent-review.yml", status="added", additions=20, deletions=0, patch="+ on: issue_comment"),
            review.FileChange(path="tests/test_github_agent_review.py", status="added", additions=1, deletions=0, patch="+ assert True"),
        ],
        checks=[review.CheckSummary(name="validate", conclusion="success", status="completed", url="https://github.com/check")],
    )

    report = review.build_review_report(pr_context)
    markdown = report.to_markdown()
    assert report.status == "approved"
    assert "## Resultado" in markdown
    assert "### P1 — Bloqueadores" in markdown
    assert "### P2 — Importantes" in markdown
    assert "### P3 — Sugestões" in markdown
    assert "## CI / Checks" in markdown
    assert "## Arquivos analisados" in markdown
    assert "Código do PR executado: não" in markdown
    assert "GITHUB_TOKEN" not in markdown

    p1_context = review.ReviewContext(
        owner="mglpsw",
        repo="aiops-orchestrator",
        issue_number=42,
        author="alice",
        association="MEMBER",
        pr_number=42,
        title="Dangerous change",
        body="",
        base_ref="master",
        head_ref="feat/danger",
        head_sha="abc123",
        html_url="",
        files=[review.FileChange(path="scripts/deploy.sh", status="modified", additions=1, deletions=1, patch="+ docker compose down")],
        checks=[],
    )
    assert review.build_review_report(p1_context).status == "changes_requested"

    p2_context = review.ReviewContext(
        owner="mglpsw",
        repo="aiops-orchestrator",
        issue_number=42,
        author="alice",
        association="MEMBER",
        pr_number=42,
        title="Minor issue",
        body="",
        base_ref="master",
        head_ref="feat/minor",
        head_sha="abc123",
        html_url="",
        files=[review.FileChange(path="tests/test_action_run.py", status="modified", additions=1, deletions=1, patch='+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')],
        checks=[],
    )
    assert review.build_review_report(p2_context).status == "attention_needed"


def test_github_client_uses_urllib_and_posts_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, str], bytes | None]] = []
    posted_bodies: list[str] = []

    def fake_urlopen(request, timeout=30):
        calls.append((request.method, request.full_url, dict(request.headers), request.data))
        parsed = urlparse(request.full_url)
        if parsed.path.endswith("/pulls/42"):
            return _FakeHTTPResponse(_pr_payload())
        if parsed.path.endswith("/pulls/42/files"):
            return _FakeHTTPResponse([_file("scripts/github_agent_review.py", "+ print('x')")])
        if parsed.path.endswith("/commits/abc123/check-runs"):
            return _FakeHTTPResponse({"check_runs": [{"name": "validate", "conclusion": "success", "status": "completed", "html_url": "https://check"}]})
        if parsed.path.endswith("/issues/42/comments") and request.method == "GET":
            return _FakeHTTPResponse([])
        if parsed.path.endswith("/issues/42/comments") and request.method == "POST":
            posted_bodies.append(json.loads(request.data.decode("utf-8"))["body"])
            return _FakeHTTPResponse({"id": 1})
        raise AssertionError(f"Unexpected request: {request.method} {request.full_url}")

    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)

    client = review.GitHubClient(token="secret-token", repository="mglpsw/aiops-orchestrator")
    context = review.fetch_review_context(client, _event_payload(pull_request=True, body="/agent review"))
    report = review.build_review_report(context)
    review._post_comment(client, context.issue_number, report.to_markdown())

    assert any(method == "GET" and url.endswith("/pulls/42") for method, url, _, _ in calls)
    assert any(method == "POST" and url.endswith("/issues/42/comments") for method, url, _, _ in calls)
    assert posted_bodies and "secret-token" not in posted_bodies[0]


def test_main_handles_unauthorized_and_issue_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    comments: list[dict[str, object]] = []

    def fake_urlopen(request, timeout=30):
        parsed = urlparse(request.full_url)
        if parsed.path.endswith("/issues/42/comments") and request.method == "POST":
            comments.append(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse({"id": 1})
        raise AssertionError(f"Unexpected request: {request.method} {request.full_url}")

    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event_payload(pull_request=False, association="NONE", login="outsider")), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mglpsw/aiops-orchestrator")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("AGENT_ALLOWED_USERS", "")

    assert review.main() == 0
    assert comments
    assert "falta de autorização" in comments[0]["body"]
    assert "GITHUB_TOKEN" not in comments[0]["body"]


def test_main_handles_issue_only_comment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    comments: list[dict[str, object]] = []

    def fake_urlopen(request, timeout=30):
        parsed = urlparse(request.full_url)
        if parsed.path.endswith("/issues/42/comments") and request.method == "POST":
            comments.append(json.loads(request.data.decode("utf-8")))
            return _FakeHTTPResponse({"id": 1})
        raise AssertionError(f"Unexpected request: {request.method} {request.full_url}")

    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event_payload(pull_request=False, association="MEMBER", login="alice")), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mglpsw/aiops-orchestrator")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("AGENT_ALLOWED_USERS", "")

    assert review.main() == 0
    assert comments
    assert "apenas Pull Requests" in comments[0]["body"]
