from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest
import yaml

import scripts.github_agent_review as review

REPO_ROOT = str(Path("/opt") / "aiops-orchestrator")


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
    comment_error: Exception | None = None,
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
            if comment_error is not None:
                raise comment_error
            body = json.loads(request.data.decode("utf-8"))["body"]
            captured_comments.append(body)
            return _FakeHTTPResponse({"id": 1})
        if path.startswith("/repos/mglpsw/aiops-orchestrator/issues/comments/") and request.method == "PATCH":
            if comment_error is not None:
                raise comment_error
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
    comment_error: Exception | None = None,
    allowed_users: str = "",
    llm_enabled: bool = False,
    router_base: str = "",
    router_api_key: str = "",
    router_model: str = "",
    router_timeout_seconds: str = "",
    step_summary_path: Path | None = None,
    expected_exit_code: int = 0,
) -> tuple[list[str], list[dict[str, object]]]:
    comments: list[str] = []
    patches: list[str] = []
    router_payloads: list[dict[str, object]] = []
    fake_urlopen, comments, patches, router_payloads = _make_fake_urlopen(
        pr_files=pr_files,
        check_runs=check_runs,
        existing_comments=existing_comments,
        comment_error=comment_error,
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
    monkeypatch.setenv("AGENT_ROUTER_TIMEOUT_SECONDS", router_timeout_seconds)
    if step_summary_path is not None:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(step_summary_path))
    else:
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert review.main() == expected_exit_code
    return comments, router_payloads


def test_parser_recognizes_review_commands() -> None:
    assert review.parse_agent_review_command("/agent review") == "review"
    assert review.parse_agent_review_command("hello\n/agent review llm\nthanks") == "review_llm"
    assert review.parse_agent_review_command("/agent ask explica esse achado") == "ask"
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
        review.FileChange(path="tests/test_action_run.py", status="modified", additions=1, deletions=1, patch=f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"'),
        review.FileChange(path="tests/test_other.py", status="modified", additions=1, deletions=1, patch=f'+ assert calls[1]["cwd"] == "{REPO_ROOT}"'),
    ]
    checks = [review.CheckSummary(name="validate", conclusion="success", status="completed", url=None)]
    findings = review.build_deterministic_findings(files, checks)
    assert len(findings) <= 5
    assert any(finding.severity == "P1" for finding in findings)
    assert all(finding.severity in {"P1", "P2"} for finding in findings)
    assert len([finding for finding in findings if finding.rule_id == "hardcoded_repo_root"]) == 1
    assert review.review_status(findings) == "changes_requested"


@pytest.mark.parametrize(
    "patch",
    [
        '+ docs mention Authorization, tokens, segredos e URLs without leaking values',
        '+ keep not persistir secrets/tokens in docs and comments',
        '+ não usa docker exec, SSH ou deploy',
    ],
)
def test_secret_keywords_in_docs_do_not_trigger_p1(patch: str) -> None:
    findings = review.build_deterministic_findings(
        [review.FileChange(path="docs/GITHUB_AGENT.md", status="modified", additions=1, deletions=1, patch=patch)],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_negative_command_context_does_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="docs/GITHUB_AGENT.md",
                status="modified",
                additions=1,
                deletions=1,
                patch="+ não usa docker exec, SSH ou deploy",
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_real_command_in_script_triggers_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/restart.sh",
                status="modified",
                additions=1,
                deletions=1,
                patch="+ docker exec aiops-orchestrator python3 -c 'print(1)'",
            )
        ],
        [],
    )
    assert any(finding.rule_id == "destructive_command" for finding in findings)
    assert any(finding.severity == "P1" for finding in findings)


@pytest.mark.parametrize(
    "patch, needle",
    [
        ('+ Authorization: Bearer valor_longo_suficiente', "authorization_bearer_secret"),
        ('+ AGENT_ROUTER_API_KEY=valor_longo_suficiente', "generic_secret_assignment"),
        ('+ api_key=os.getenv("AGENT_ROUTER_API_KEY")', None),
        ('+ github_pat_abcdefghijklmnopqrstuvwxyz1234', "well_known_token"),
        ('+ sk-abcdef1234567890', "well_known_token"),
        ('+ https://user:password@host.example/path', "url_credentials"),
        ('+ -----BEGIN PRIVATE KEY-----\n+ abc123\n+ -----END PRIVATE KEY-----', "private_key_block"),
        ('+ Cookie: session=valor_longo_suficiente', "cookie_secret"),
    ],
)
def test_real_secret_values_trigger_p1(patch: str, needle: str | None) -> None:
    findings = review.build_deterministic_findings(
        [review.FileChange(path="docs/GITHUB_AGENT.md", status="modified", additions=1, deletions=1, patch=patch)],
        [],
    )
    if needle is None:
        assert all(finding.severity != "P1" for finding in findings)
        return
    assert any(finding.rule_id == needle for finding in findings)
    assert any(finding.severity == "P1" for finding in findings)


def test_placeholder_tokens_and_examples_do_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="docs/GITHUB_AGENT.md",
                status="modified",
                additions=1,
                deletions=1,
                patch=(
                    '+ API_KEY="<token>"\n'
                    '+ TOKEN=REDACTED\n'
                    '+ secret=dummy\n'
                    '+ password=fake\n'
                    '+ client_secret=example'
                ),
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_env_lookup_assignment_does_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/configure.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ api_key=os.getenv("AGENT_ROUTER_API_KEY")',
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_realistic_token_finding_is_redacted() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/configure.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ API_KEY="valor_longo_suspeito_1234567890"',
            )
        ],
        [],
    )
    assert any(finding.severity == "P1" for finding in findings)
    assert all("valor_longo_suspeito" not in finding.evidence for finding in findings)


def test_redaction_masks_real_secret_values() -> None:
    text = (
        "Authorization: Bearer valor_longo_suficiente "
        "AGENT_ROUTER_API_KEY=valor_longo_suficiente "
        "https://user:password@host.example/path "
        "-----BEGIN PRIVATE KEY-----\nabc123\n-----END PRIVATE KEY-----"
    )
    redacted = review._redact_sensitive_text(text)
    assert "valor_longo_suficiente" not in redacted
    assert "password@" not in redacted
    assert "[REDACTED" in redacted


def test_status_needs_review_for_p2_only() -> None:
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
    assert review.review_status(findings) == "needs_review"


def test_status_approved_when_no_p1_or_p2() -> None:
    assert review.review_status([]) == "approved"


def test_structured_llm_p1_or_p2_updates_status_without_deterministic_blockers() -> None:
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
    structured_p2 = [
        review.Finding(
            severity="P2",
            file="tests/test_action_run.py",
            evidence="path hardcoded",
            risk="risk",
            recommendation="fix",
            rule_id="llm::P2::tests/test_action_run.py",
        )
    ]
    markdown = review.render_review([], [], pr_context, llm_mode=True, llm_notes="resumo", llm_findings=structured_p2)
    assert "- Status: needs_review" in markdown
    assert "## Achados do LLM" in markdown
    assert "## Notas do LLM" in markdown

    structured_p1 = [
        review.Finding(
            severity="P1",
            file="scripts/github_agent_review.py",
            evidence="execução perigosa",
            risk="risk",
            recommendation="fix",
            rule_id="llm::P1::scripts/github_agent_review.py",
        )
    ]
    markdown_p1 = review.render_review([], [], pr_context, llm_mode=True, llm_notes="resumo", llm_findings=structured_p1)
    assert "- Status: changes_requested" in markdown_p1


def test_unstructured_llm_text_with_p1_does_not_change_status() -> None:
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
    markdown = review.render_review([], [], pr_context, llm_mode=True, llm_notes="P1 talvez, mas não tenho certeza", llm_findings=[])
    assert "- Status: approved" in markdown
    assert "P1 talvez" in markdown


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
    assert "# Revisão do Agent" in markdown
    assert "## Resumo" in markdown
    assert "Não encontrei P1/P2 determinísticos." in markdown
    assert "Código do PR executado: não" in markdown
    assert "Notas do LLM" not in markdown


def test_router_payload_is_sanitized_and_base_url_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [
        _file(
            "scripts/github_agent_review.py",
            '+ token = "ghp_abcdefghijklmnopqrstuvwxyz1234"\n+ subprocess.run(["git", "status"])',
        ),
        _file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"'),
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
    assert "pt-BR" in body
    assert router_call["payload"]["model"] == "gpt-review"
    assert comments[0].startswith(review.COMMENT_MARKER)
    assert "# Revisão do Agent" in comments[0]
    assert "## Notas do LLM" in comments[0]
    assert "## Resumo" in comments[0]


def test_router_timeout_seconds_controls_timeout_and_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        router_error=TimeoutError("timed out"),
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
        router_timeout_seconds="90",
    )
    captured = capsys.readouterr()
    assert comments
    assert router_payloads
    assert "timeout após 90s" in comments[0]
    assert "use /agent review para review determinístico" in comments[0]
    assert "Agent Router timeout:" in captured.err
    assert "timeout=90s" in captured.err
    assert "router-secret" not in captured.err
    assert "secret-token" not in captured.err


def test_plain_review_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review")
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
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
    assert "revisão determinística" in comments[0]


def test_ask_command_calls_router_with_sanitized_payload_and_pt_br_response(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    files = [
        _file(
            "scripts/github_agent_review.py",
            '+ token = "ghp_abcdefghijklmnopqrstuvwxyz1234"\n+ subprocess.run(["git", "status"])',
        ),
        _file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"'),
    ]
    existing_comments = [{"id": 55, "body": f"{review.COMMENT_MARKER}\n# Revisão do Agent\ncomentário anterior"}]
    router_response = {
        "choices": [
            {
                "message": {
                    "content": "Claro, isso parece um falso positivo porque o diff não executa código nem altera permissões."
                }
            }
        ]
    }
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        existing_comments=existing_comments,
        router_response=router_response,
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
        router_model="gpt-review",
    )
    assert len(comments) == 1
    assert "falso positivo" in comments[0]
    assert "secret-token" not in comments[0]
    assert "router-secret" not in comments[0]
    assert router_payloads
    router_call = router_payloads[0]["payload"]
    user_content = router_call["messages"][1]["content"]
    assert "Pergunta do usuário: explique esse achado" in user_content
    assert "Título do PR:" in user_content
    assert "Descrição do PR:" in user_content
    assert "Último comentário do bot:" in user_content
    assert "Achados determinísticos recentes:" in user_content
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in user_content
    assert "pt-BR" in json.dumps(router_call)


def test_ask_command_on_issue_does_not_execute(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=False, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        allowed_users="",
    )
    assert comments
    assert not router_payloads
    assert "Pull Requests" in comments[0]


def test_ask_unauthorized_comment_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="NONE", login="outsider")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        allowed_users="",
    )
    assert comments
    assert not router_payloads
    assert "não está autorizado" in comments[0]


def test_ask_llm_disabled_falls_back_in_portuguese(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        allowed_users="alice",
        llm_enabled=False,
    )
    assert comments
    assert not router_payloads
    assert comments[0] == "Agent ask requer LLM habilitado; use /agent review para review determinístico."


def test_ask_key_missing_falls_back_in_portuguese(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="",
    )
    assert comments
    assert not router_payloads
    assert comments[0] == "Agent ask requer LLM habilitado; use /agent review para review determinístico."


def test_ask_timeout_falls_back_in_portuguese(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        router_error=TimeoutError("timed out"),
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    assert comments
    assert router_payloads
    assert "Agent ask indisponível (timeout após 60s)" in comments[0]
    assert "use /agent review para review determinístico" in comments[0]


def test_llm_disabled_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
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
    assert "LLM desabilitado" in comments[0]
    assert "use /agent review para review determinístico" in comments[0]


def test_llm_key_missing_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
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
    assert "LLM desabilitado ou chave do router ausente" in comments[0]


def test_llm_timeout_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
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
    assert "timeout após 60s" in comments[0]


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (HTTPError("https://api.ks-sm.net:9443/v1/chat/completions", 401, "unauthorized", hdrs=None, fp=None), "autenticação falhou"),
        (HTTPError("https://api.ks-sm.net:9443/v1/chat/completions", 429, "rate limited", hdrs=None, fp=None), "limite de taxa"),
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
    files = [_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')]
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


@pytest.mark.parametrize(
    ("router_response", "expected_note"),
    [
        ({"choices": [{"message": {"content": json.dumps({"findings": [], "summary": "short"})}}]}, "short"),
        ({"choices": [{"text": json.dumps({"findings": [], "summary": "text choice"})}]}, "text choice"),
        ({"output_text": json.dumps({"findings": [], "summary": "output"})}, "output"),
        ({"content": json.dumps({"findings": [], "summary": "content"})}, "content"),
        ({"review": json.dumps({"findings": [], "summary": "review"})}, "review"),
    ],
)
def test_router_response_formats_are_normalized(
    router_response: object,
    expected_note: str,
) -> None:
    parsed = review.parse_agent_router_response(json.dumps(router_response))
    assert parsed.notes == expected_note


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
    assert "Pull Requests" in comments[0]


@pytest.mark.parametrize("existing_comments", [[], [{"id": 55, "body": f"{review.COMMENT_MARKER}\nold body"}]])
def test_comment_write_403_falls_back_to_step_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    existing_comments: list[dict[str, object]],
) -> None:
    payload = _event_payload(pull_request=True, body="/agent review", association="MEMBER", login="alice")
    step_summary = tmp_path / "step-summary.md"
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        existing_comments=existing_comments,
        allowed_users="alice",
        comment_error=HTTPError("https://api.github.com/repos/mglpsw/aiops-orchestrator/issues/42/comments", 403, "forbidden", hdrs=None, fp=None),
        step_summary_path=step_summary,
    )
    captured = capsys.readouterr()
    assert not comments
    assert not router_payloads
    assert review._COMMENT_403_LOG_MESSAGE in captured.err
    assert "Traceback" not in captured.err
    assert "secret-token" not in captured.err
    assert "Authorization" not in captured.err
    assert "AGENT_ROUTER_API_KEY" not in captured.err
    assert step_summary.exists()
    summary = step_summary.read_text(encoding="utf-8")
    assert review.COMMENT_MARKER in summary
    assert "# Revisão do Agent" in summary
    assert "secret-token" not in summary
    assert "AGENT_ROUTER_API_KEY" not in summary


def test_unauthorized_comment_does_not_call_router(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent review", association="NONE", login="outsider")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        allowed_users="",
    )
    assert comments
    assert not router_payloads
    assert "não está autorizado" in comments[0]


def test_existing_marker_comment_is_updated_instead_of_spamming(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    comments: list[str] = []
    patches: list[str] = []
    router_payloads: list[dict[str, object]] = []
    fake_urlopen, comments, patches, router_payloads = _make_fake_urlopen(
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
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


def test_comment_write_403_without_step_summary_stays_silent_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _event_payload(pull_request=True, body="/agent review", association="MEMBER", login="alice")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        allowed_users="alice",
        comment_error=HTTPError("https://api.github.com/repos/mglpsw/aiops-orchestrator/issues/42/comments", 403, "forbidden", hdrs=None, fp=None),
        expected_exit_code=0,
    )
    captured = capsys.readouterr()
    assert not comments
    assert not router_payloads
    assert review._COMMENT_403_LOG_MESSAGE in captured.err
    assert "Step summary unavailable; short review:" in captured.err
    assert "Traceback" not in captured.err
    assert "secret-token" not in captured.err
    assert "AGENT_ROUTER_API_KEY" not in captured.err


def test_internal_error_still_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _event_payload(pull_request=True, body="/agent review", association="MEMBER", login="alice")
    fake_urlopen, _, _, _ = _make_fake_urlopen(
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
    )
    monkeypatch.setattr(review.urllib.request, "urlopen", fake_urlopen)
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "mglpsw/aiops-orchestrator")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("AGENT_ALLOWED_USERS", "alice")
    monkeypatch.setenv("AGENT_REVIEW_LLM_ENABLED", "false")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setattr(
        review,
        "build_deterministic_findings",
        lambda files, checks: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="boom"):
        review.main()


def test_workflow_security_guardrails() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/agent-review.yml").read_text(encoding="utf-8"))
    assert "pull_request_target" not in json.dumps(workflow)
    assert "/agent ask" in json.dumps(workflow)
    assert "fromJSON" in workflow["jobs"]["review"]["if"]
    assert "/agent review" in workflow["jobs"]["review"]["if"]
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "write",
        "checks": "read",
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
    assert env["AGENT_ROUTER_TIMEOUT_SECONDS"] == "${{ vars.AGENT_ROUTER_TIMEOUT_SECONDS }}"
    assert "actions" not in workflow["permissions"]
