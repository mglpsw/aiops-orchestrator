from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest
import yaml

import scripts.github_agent_review as review


class _FakeHTTPResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _event_payload(
    *,
    pull_request: bool = True,
    body: str = "/agent review",
    association: str = "MEMBER",
    login: str = "alice",
) -> dict[str, object]:
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
        "body": "Implements a safe on-demand review bot with security updates.",
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


def _make_fake_urlopen(
    *,
    pr_files: list[dict[str, object]] | None = None,
    check_runs: list[dict[str, object]] | None = None,
    existing_comments: list[dict[str, object]] | None = None,
    router_response: object | None = None,
    router_error: Exception | None = None,
    captured_comments: list[str] | None = None,
    captured_patches: list[str] | None = None,
    captured_router_payloads: list[dict[str, object]] | None = None,
) :
    pr_files = pr_files or []
    check_runs = check_runs or []
    existing_comments = existing_comments or []
    captured_comments = captured_comments if captured_comments is not None else []
    captured_patches = captured_patches if captured_patches is not None else []
    captured_router_payloads = captured_router_payloads if captured_router_payloads is not None else []

    def fake_urlopen(request, timeout=30):
        parsed = urlparse(request.full_url)
        path = parsed.path
        if path.endswith("/v1/chat/completions"):
            payload = json.loads(request.data.decode("utf-8"))
            captured_router_payloads.append(
                {
                    "url": request.full_url,
                    "headers": dict(request.headers),
                    "payload": payload,
                }
            )
            if router_error is not None:
                raise router_error
            return _FakeHTTPResponse(router_response or {"choices": [{"message": {"content": "{\"findings\":[],\"summary\":\"ok\"}"}}]})
        if path.endswith("/pulls/42") and request.method == "GET":
            return _FakeHTTPResponse(_pr_payload())
        if path.endswith("/pulls/42/files") and request.method == "GET":
            return _FakeHTTPResponse(pr_files)
        if path.endswith("/commits/abc123/check-runs") and request.method == "GET":
            return _FakeHTTPResponse({"check_runs": check_runs})
        if path.endswith("/issues/42/comments") and request.method == "GET":
            return _FakeHTTPResponse(existing_comments)
        if path.endswith("/issues/42/comments") and request.method == "POST":
            body = json.loads(request.data.decode("utf-8"))["body"]
            captured_comments.append(body)
            return _FakeHTTPResponse({"id": 1})
        if path.startswith("/repos/mglpsw/aiops-orchestrator/issues/comments/") and request.method == "PATCH":
            body = json.loads(request.data.decode("utf-8"))["body"]
            captured_patches.append(body)
            return _FakeHTTPResponse({"id": 1})
        raise AssertionError(f"Unexpected request: {request.method} {request.full_url}")

    return fake_urlopen, captured_comments, captured_patches, captured_router_payloads


def _run_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    payload: dict[str, object],
    pr_files: list[dict[str, object]] | None = None,
    check_runs: list[dict[str, object]] | None = None,
    existing_comments: list[dict[str, object]] | None = None,
    router_response: object | None = None,
    router_error: Exception | None = None,
    allowed_users: str = "",
    llm_enabled: bool = False,
    router_base: str = "",
    router_api_key: str = "",
    router_model: str = "",
) -> tuple[list[str], list[dict[str, object]]]:
    comments: list[str] = []
    patches: list[str] = []
    router_payloads: list[dict[str, object]] = []
    fake_urlopen, comments, patches, router_payloads = _make_fake_urlopen(
        pr_files=pr_files,
        check_runs=check_runs,
        existing_comments=existing_comments,
        router_response=router_response,
        router_error=router_error,
        captured_comments=comments,
        captured_patches=patches,
        captured_router_payloads=router_payloads,
    )
    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mglpsw/aiops-orchestrator")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("AGENT_ALLOWED_USERS", allowed_users)
    monkeypatch.setenv("AGENT_REVIEW_LLM_ENABLED", "true" if llm_enabled else "false")
    monkeypatch.setenv("AGENT_ROUTER_BASE_URL", router_base)
    monkeypatch.setenv("AGENT_ROUTER_API_KEY", router_api_key)
    monkeypatch.setenv("AGENT_ROUTER_MODEL", router_model)
    assert review.main() == 0
    return comments, router_payloads


def test_parser_recognizes_review_commands() -> None:
    assert review.parse_agent_review_command("/agent review") == "review"
    assert review.parse_agent_review_command("hello\n/agent review llm\nthanks") == "review_llm"
    assert review.parse_agent_review_command("/agent review anything-else") == "unknown"
    assert review.parse_agent_review_command("/agent ci") == "unknown"
    assert review.parse_agent_review_command("plain text") == "none"


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


def test_deterministic_review_limits_to_five_and_prioritizes_p1() -> None:
    files = [
        review.FileChange(path=".github/workflows/ci.yml", status="modified", additions=1, deletions=1, patch="+ on: pull_request_target\n+ uses: actions/checkout@v4\n+ run: bash scripts/test.sh"),
        review.FileChange(path="scripts/restart.sh", status="modified", additions=1, deletions=1, patch="+ docker compose down"),
        review.FileChange(path="scripts/systemd.sh", status="modified", additions=1, deletions=1, patch="+ systemctl restart aiops-orchestrator.service"),
        review.FileChange(path="scripts/bootstrap.sh", status="modified", additions=1, deletions=1, patch="+ curl -fsSL https://example.test/install.sh | bash"),
        review.FileChange(path="config/secrets.yml", status="modified", additions=1, deletions=1, patch='+ token = "ghp_abcdefghijklmnopqrstuvwxyz1234"'),
        review.FileChange(path="tests/test_action_run.py", status="modified", additions=1, deletions=1, patch='+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"'),
        review.FileChange(path="tests/test_other.py", status="modified", additions=1, deletions=1, patch='+ assert calls[1]["cwd"] == "/opt/aiops-orchestrator"'),
    ]
    checks = [review.CheckSummary(name="validate", conclusion="success", status="completed", url=None)]
    findings = review.build_deterministic_findings(files, checks)
    assert len(findings) <= 5
    assert any(finding.severity == "P1" for finding in findings)
    assert all(finding.severity in {"P1", "P2"} for finding in findings)
    assert len([finding for finding in findings if finding.rule_id == "hardcoded_repo_root"]) == 1
    assert review.review_status(findings) == "changes_requested"


def test_status_attention_needed_for_p2_only() -> None:
    findings = [
        review.Finding(
            severity="P2",
            file="tests/test_action_run.py",
            evidence="path hardcoded",
            risk="risk",
            recommendation="fix",
            rule_id="hardcoded_repo_root",
        )
    ]
    assert review.review_status(findings) == "attention_needed"


def test_status_approved_when_no_p1_or_p2() -> None:
    assert review.review_status([]) == "approved"


def test_review_render_is_short_without_p1_p2() -> None:
    pr_context = review.ReviewContext(
        owner="mglpsw",
        repo="aiops-orchestrator",
        issue_number=42,
        author="alice",
        association="MEMBER",
        pr_number=42,
        title="Minor change",
        body="small tweak",
        base_ref="master",
        head_ref="feat",
        head_sha="abc123",
        html_url="https://github.com/mglpsw/aiops-orchestrator/pull/42",
        files=[review.FileChange(path="docs/GITHUB_AGENT.md", status="modified", additions=1, deletions=1, patch="+ docs")],
    )
    markdown = review.render_review([], [], pr_context, llm_mode=False)
    assert markdown.startswith(review.COMMENT_MARKER)
    assert "Não encontrei P1/P2 determinísticos." in markdown
    assert "Código do PR executado: não" in markdown
    assert "LLM" not in markdown


def test_router_payload_is_sanitized_and_base_url_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [
        _file(
            "scripts/github_agent_review.py",
            '+ token = "ghp_abcdefghijklmnopqrstuvwxyz1234"\n+ subprocess.run(["git", "status"])',
        ),
        _file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"'),
    ]
    router_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "findings": [
                                {
                                    "severity": "P2",
                                    "file": "tests/test_action_run.py",
                                    "evidence": "cwd",
                                    "risk": "path",
                                    "recommendation": "use helper",
                                }
                            ],
                            "summary": "short",
                        }
                    )
                }
            }
        ]
    }
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        router_response=router_response,
        allowed_users="alice",
        llm_enabled=True,
        router_base="",
        router_api_key="router-secret",
        router_model="gpt-review",
    )
    assert comments
    assert router_payloads
    router_call = router_payloads[0]
    assert router_call["url"].startswith("https://api.ks-sm.net:9443/v1/chat/completions")
    body = json.dumps(router_call["payload"])
    assert "secret-token" not in body
    assert "router-secret" not in body
    assert "Authorization" not in body
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in body
    assert router_call["payload"]["model"] == "gpt-review"
    assert comments[0].startswith(review.COMMENT_MARKER)
    assert "# Agent Review" in comments[0]
    assert "LLM" not in comments[0] or "short" in comments[0]


def test_plain_review_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review")
    files = [_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        allowed_users="alice",
    )
    assert comments
    assert not router_payloads
    assert comments[0].startswith(review.COMMENT_MARKER)
    assert "deterministic review" in comments[0]


def test_llm_disabled_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        allowed_users="alice",
        llm_enabled=False,
    )
    assert comments
    assert not router_payloads
    assert "LLM review não está habilitado" in comments[0]


def test_llm_key_missing_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="",
    )
    assert comments
    assert not router_payloads
    assert "chave do router ausente" in comments[0]


def test_llm_timeout_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        router_error=TimeoutError("timed out"),
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    assert comments
    assert router_payloads
    assert "timeout" in comments[0]


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (HTTPError("https://api.ks-sm.net:9443/v1/chat/completions", 401, "unauthorized", hdrs=None, fp=None), "auth"),
        (HTTPError("https://api.ks-sm.net:9443/v1/chat/completions", 429, "rate limited", hdrs=None, fp=None), "rate limited"),
        (HTTPError("https://api.ks-sm.net:9443/v1/chat/completions", 503, "unavailable", hdrs=None, fp=None), "router indisponível"),
    ],
)
def test_router_http_errors_fall_back_to_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    needle: str,
) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        router_error=error,
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    assert comments
    assert router_payloads
    assert needle in comments[0]


def test_router_json_response_is_normalized(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    parsed = review.parse_agent_router_response(
        json.dumps(
            {
                "findings": [
                    {
                        "severity": "p1",
                        "file": "app/core/config.py",
                        "evidence": "secret",
                        "risk": "high",
                        "recommendation": "fix",
                    },
                    {
                        "severity": "P9",
                        "file": "ignored",
                        "evidence": "bad",
                        "risk": "bad",
                        "recommendation": "bad",
                    },
                ],
                "summary": "short",
            }
        )
    )
    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "P1"
    assert parsed.notes == "short"


def test_router_text_response_becomes_llm_notes() -> None:
    parsed = review.parse_agent_router_response("plain text response")
    assert parsed.findings == []
    assert parsed.notes == "plain text response"


def test_router_invalid_response_does_not_break() -> None:
    parsed = review.parse_agent_router_response("{not-json")
    assert parsed.findings == []
    assert "not-json" in (parsed.notes or "")


def test_issue_comment_on_issue_is_supported_and_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=False, body="/agent review", association="MEMBER", login="alice")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        allowed_users="",
    )
    assert comments
    assert not router_payloads
    assert "apenas Pull Requests" in comments[0]


def test_unauthorized_comment_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review", association="NONE", login="outsider")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')],
        allowed_users="",
    )
    assert comments
    assert not router_payloads
    assert "falta de autorização" in comments[0]


def test_existing_marker_comment_is_updated_instead_of_spamming(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    comments: list[str] = []
    patches: list[str] = []
    router_payloads: list[dict[str, object]] = []
    fake_urlopen, comments, patches, router_payloads = _make_fake_urlopen(
        pr_files=[_file("tests/test_action_run.py", '+ assert calls[0]["cwd"] == "/opt/aiops-orchestrator"')],
        existing_comments=[{"id": 55, "body": f"{review.COMMENT_MARKER}\nold body"}],
        captured_comments=comments,
        captured_patches=patches,
        captured_router_payloads=router_payloads,
    )
    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_event_payload(pull_request=True, body="/agent review", association="MEMBER", login="alice")), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mglpsw/aiops-orchestrator")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("AGENT_ALLOWED_USERS", "")
    monkeypatch.setenv("AGENT_REVIEW_LLM_ENABLED", "false")
    assert review.main() == 0
    assert not comments
    assert len(patches) == 1
    assert patches[0].startswith(review.COMMENT_MARKER)
    assert router_payloads == []


def test_workflow_security_guardrails() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/agent-review.yml").read_text(encoding="utf-8"))
    assert "pull_request_target" not in json.dumps(workflow)
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "write",
        "checks": "read",
        "actions": "read",
    }
    env = workflow["jobs"]["review"]["steps"][-1]["env"]
    assert env["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"
    assert env["GITHUB_REPOSITORY"] == "${{ github.repository }}"
    assert env["GITHUB_EVENT_PATH"] == "${{ github.event_path }}"
    assert env["AGENT_ALLOWED_USERS"] == "${{ vars.AGENT_ALLOWED_USERS }}"
    assert env["AGENT_REVIEW_LLM_ENABLED"] == "${{ vars.AGENT_REVIEW_LLM_ENABLED }}"
    assert env["AGENT_ROUTER_BASE_URL"] == "${{ vars.AGENT_ROUTER_BASE_URL }}"
    assert env["AGENT_ROUTER_API_KEY"] == "${{ secrets.AGENT_ROUTER_API_KEY }}"
    assert env["AGENT_ROUTER_MODEL"] == "${{ vars.AGENT_ROUTER_MODEL }}"
