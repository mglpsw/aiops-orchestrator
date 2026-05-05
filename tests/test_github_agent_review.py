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


def test_os_system_real_command_triggers_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/cleanup.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ os.system("docker exec aiops-orchestrator python3 -c \\"print(1)\\"")',
            )
        ],
        [],
    )
    assert any(finding.severity == "P1" for finding in findings)


def test_shell_true_in_action_runner_triggers_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="app/agent_router/services/action_runner.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ subprocess.run("docker exec aiops-orchestrator ls", shell=True)',
            )
        ],
        [],
    )
    assert any(finding.rule_id == "runner_arbitrary_command" for finding in findings)
    assert any(finding.severity == "P1" for finding in findings)


def test_scanner_regex_reference_does_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ _P1_DESTRUCTIVE_RE = re.compile(r"(?i)\\b(?:docker\\s+exec|git\\s+push)\\b")',
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_placeholder_private_key_block_does_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="tests/test_github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch=(
                    "+ -----BEGIN PRIVATE KEY-----\n"
                    "+ [REDACTED PRIVATE KEY]\n"
                    "+ -----END PRIVATE KEY-----"
                ),
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_fixture_command_string_does_not_trigger_p1() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="tests/test_github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='+ command = "docker exec aiops-orchestrator python3 -c \\"print(1)\\""',
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_scanner_change_with_matching_test_does_not_emit_generic_p2() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="scripts/github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch="- MAX_BUNDLE_CHARS = 6000\n+ MAX_BUNDLE_CHARS = 7000",
            ),
            review.FileChange(
                path="tests/test_github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch="+ assert review.MAX_BUNDLE_CHARS == 7000",
            ),
        ],
        [],
    )
    assert all(finding.rule_id != "safety_behavior_regression" for finding in findings)


def test_helper_path_construction_does_not_trigger_p2() -> None:
    findings = review.build_deterministic_findings(
        [
            review.FileChange(
                path="tests/test_github_agent_review.py",
                status="modified",
                additions=1,
                deletions=1,
                patch='REPO_ROOT = str(Path("/opt") / "aiops-orchestrator")',
            )
        ],
        [],
    )
    assert all(finding.rule_id != "hardcoded_repo_root" for finding in findings)


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
                path="tests/test_aiops_chat_router.py",
                status="modified",
                additions=1,
                deletions=1,
                patch=(
                    '+ monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")\n'
                    '+ JSON = {"token":"test-token"}\n'
                    '+ JSON_REDACTED = {"token":"[REDACTED]"}\n'
                    '+ API_KEY="<token>"\n'
                    '+ TOKEN=REDACTED\n'
                    '+ secret=dummy\n'
                    '+ password=fake\n'
                    '+ client_secret=example\n'
                    '+ session_id=fake-token\n'
                    '+ local_flag=local-only'
                ),
            )
        ],
        [],
    )
    assert all(finding.severity != "P1" for finding in findings)


def test_placeholder_based_llm_findings_do_not_promote_status() -> None:
    llm_findings = [
        review.Finding(
            severity="P1",
            file="tests/test_aiops_chat_router.py",
            evidence='JSON {"token":"test-token"}',
            risk="placeholder de teste",
            recommendation="ignorar",
            rule_id="llm::P1::placeholder",
        )
    ]
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
        files=[review.FileChange(path="tests/test_aiops_chat_router.py", status="modified", additions=1, deletions=1, patch='+ monkeypatch.setenv("AGENT_ROUTER_API_TOKEN", "test-token")')],
    )
    assert review.review_status([], llm_findings=llm_findings) == "approved"
    markdown = review.render_review([], [], pr_context, llm_mode=True, llm_findings=llm_findings)
    assert "changes_requested" not in markdown
    assert "placeholder" not in markdown.lower()


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
    assert "### Riscos não confirmados" in markdown
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
    assert "## 🤖 Agent Review" in markdown
    assert "### Escopo entendido" in markdown
    assert "Nenhum achado confirmado." in markdown
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
    system_prompt = router_call["payload"]["messages"][0]["content"]
    assert "secret-token" not in body
    assert "router-secret" not in body
    assert "Authorization" not in body
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in body
    assert "pt-BR" in body
    assert "Não dê comentários genéricos" in system_prompt
    assert "Arquivo/linha ou trecho" in system_prompt
    assert "Se não encontrar problema real" in system_prompt
    assert router_call["payload"]["model"] == "gpt-review"
    assert router_call["payload"]["stream"] is False
    assert comments[0].startswith(review.COMMENT_MARKER)
    assert "## 🤖 Agent Review" in comments[0]
    assert "## Notas do LLM" in comments[0]
    assert "### Escopo entendido" in comments[0]


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


def test_review_payload_defaults_to_code_model_and_logs_truncation_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = _event_payload(pull_request=True, body="/agent review llm")
    long_patch = "+ line 1\n+ line 2\n+ line 3\n+ line 4\n+ line 5\n+ line 6\n+ line 7\n+ secret-token-should-not-appear"
    files = [_file(f"app/module_{index}.py", long_patch, additions=7, deletions=0) for index in range(10)]
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=files,
        router_response={"choices": [{"message": {"content": review.NO_BLOCKING_FINDINGS_RESPONSE}}]},
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
        router_model="",
    )
    captured = capsys.readouterr()
    assert comments
    assert router_payloads
    assert router_payloads[0]["payload"]["model"] == "code"
    assert router_payloads[0]["payload"]["stream"] is False
    user_content = router_payloads[0]["payload"]["messages"][1]["content"]
    assert "diff_chars=" in user_content
    assert "files_count=10" in user_content
    assert "truncated=true" in user_content
    assert "secret-token-should-not-appear" not in captured.err
    assert "diff_chars=" in captured.err
    assert "files_count=10" in captured.err
    assert "truncated=true" in captured.err


def test_parse_textual_llm_review_blocks_into_findings() -> None:
    parsed = review.parse_agent_router_response(
        """
- Severidade: P1
- Arquivo/linha ou trecho: app/api/routes.py:42
- Problema concreto: o handler passou a retornar 500 quando `detail` vem ausente.
- Por que isso quebra algo: clientes que enviam payload válido sem esse campo agora quebram em runtime.
- Correção sugerida: manter fallback para `detail` ausente ou validar antes de acessar.

Itens verificados:
- contrato HTTP do endpoint
- cobertura de teste para payload mínimo
"""
    )
    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "P1"
    assert parsed.findings[0].file == "app/api/routes.py:42"
    assert "handler passou a retornar 500" in parsed.findings[0].evidence
    assert "Itens verificados" not in (parsed.notes or "")
    assert "contrato HTTP do endpoint" in (parsed.notes or "")


def test_generic_llm_review_text_is_rejected() -> None:
    parsed = review.parse_agent_router_response("Parece bom no geral, só recomendo melhorar a clareza e adicionar testes.")
    assert not parsed.findings
    assert parsed.warning == "LLM response inválida"


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
    assert comments[0].startswith("Resposta para @alice sobre `/agent ask`.")
    assert "Pergunta: explique esse achado" in comments[0]
    assert "Comentário separado da revisão principal." in comments[0]
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


def test_ask_command_writes_separate_comment_without_upserting_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    comments: list[str] = []
    patches: list[str] = []
    router_payloads: list[dict[str, object]] = []
    fake_urlopen, comments, patches, router_payloads = _make_fake_urlopen(
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        existing_comments=[{"id": 55, "body": f"{review.COMMENT_MARKER}\n# Revisão do Agent\ncomentário anterior"}],
        router_response={"choices": [{"message": {"content": "Resposta contextual para a pergunta."}}]},
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
    monkeypatch.setenv("AGENT_ALLOWED_USERS", "alice")
    monkeypatch.setenv("AGENT_REVIEW_LLM_ENABLED", "true")
    monkeypatch.setenv("AGENT_ROUTER_API_KEY", "router-secret")
    monkeypatch.setenv("AGENT_ROUTER_MODEL", "gpt-review")
    assert review.main() == 0
    assert len(comments) == 1
    assert not patches
    assert comments[0].startswith("Resposta para @alice sobre `/agent ask`.")
    assert "comentário anterior" not in comments[0]
    assert "Resposta contextual para a pergunta." in comments[0]


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
        ({"choices": [{"message": {"content": json.dumps({"findings": [], "summary": review.NO_BLOCKING_FINDINGS_RESPONSE})}}]}, review.NO_BLOCKING_FINDINGS_RESPONSE),
        ({"choices": [{"text": json.dumps({"findings": [], "summary": review.NO_BLOCKING_FINDINGS_RESPONSE})}]}, review.NO_BLOCKING_FINDINGS_RESPONSE),
        ({"output_text": json.dumps({"findings": [], "summary": review.NO_BLOCKING_FINDINGS_RESPONSE})}, review.NO_BLOCKING_FINDINGS_RESPONSE),
        ({"content": json.dumps({"findings": [], "summary": review.NO_BLOCKING_FINDINGS_RESPONSE})}, review.NO_BLOCKING_FINDINGS_RESPONSE),
        ({"review": json.dumps({"findings": [], "summary": review.NO_BLOCKING_FINDINGS_RESPONSE})}, review.NO_BLOCKING_FINDINGS_RESPONSE),
    ],
)
def test_router_response_formats_are_normalized(
    router_response: object,
    expected_note: str,
) -> None:
    parsed = review.parse_agent_router_response(json.dumps(router_response))
    assert parsed.notes == expected_note


@pytest.mark.parametrize(
    ("router_response", "expected_text"),
    [
        ({"choices": [{"message": {"content": "texto via choices message"}}]}, "texto via choices message"),
        ({"choices": [{"text": "texto via choices text"}]}, "texto via choices text"),
        ({"message": {"content": "texto via message"}}, "texto via message"),
        ({"output_text": "texto via output_text"}, "texto via output_text"),
        ({"content": "texto via content"}, "texto via content"),
        ({"review": "texto via review"}, "texto via review"),
        ({"response": "texto via response"}, "texto via response"),
        ({"answer": "texto via answer"}, "texto via answer"),
        ({"text": "texto via text"}, "texto via text"),
        ({"data": {"content": "texto via data content"}}, "texto via data content"),
        ({"data": {"response": "texto via data response"}}, "texto via data response"),
        ({"data": {"answer": "texto via data answer"}}, "texto via data answer"),
        ({"data": {"text": "texto via data text"}}, "texto via data text"),
        ({"result": {"content": "texto via result content"}}, "texto via result content"),
        ({"result": {"response": "texto via result response"}}, "texto via result response"),
        ({"result": {"answer": "texto via result answer"}}, "texto via result answer"),
        ({"result": {"text": "texto via result text"}}, "texto via result text"),
        ({"type": "message", "content": [{"type": "text", "text": "texto via content array"}]}, "texto via content array"),
        ('{"answer":"texto via json direto"}', "texto via json direto"),
        ("texto puro do router", "texto puro do router"),
    ],
)
def test_ask_router_response_formats_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    router_response: object,
    expected_text: str,
) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        router_response=router_response,
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    assert comments
    assert router_payloads
    assert expected_text in comments[0]
    assert "secret-token" not in comments[0]


def test_ask_router_response_is_redacted_and_truncated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    long_response = " ".join(
        [
            "texto",
            "com",
            "Authorization: Bearer valor_longo_suficiente",
            "e",
            "sk-abcdef1234567890",
        ]
        + ["palavra"] * 200
    )
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        router_response=long_response,
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    assert comments
    assert router_payloads
    assert "valor_longo_suficiente" not in comments[0]
    assert "sk-abcdef1234567890" not in comments[0]
    assert len(comments[0]) <= review.MAX_LLM_ASK_RESPONSE_CHARS


def test_ask_comment_write_403_falls_back_to_step_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    step_summary = tmp_path / "step-summary.md"
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        router_response={"choices": [{"message": {"content": "Resposta contextual para a pergunta."}}]},
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
        comment_error=HTTPError("https://api.github.com/repos/mglpsw/aiops-orchestrator/issues/42/comments", 403, "forbidden", hdrs=None, fp=None),
        step_summary_path=step_summary,
    )
    assert not comments
    assert router_payloads
    assert step_summary.exists()
    summary = step_summary.read_text(encoding="utf-8")
    assert "Resposta para @alice sobre `/agent ask`." in summary
    assert "Resposta contextual para a pergunta." in summary
    assert "secret-token" not in summary
    assert "AGENT_ROUTER_API_KEY" not in summary


def test_ask_unrecognized_router_shape_uses_pt_br_parse_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = _event_payload(pull_request=True, body="/agent ask explique esse achado", association="MEMBER", login="alice")
    comments, router_payloads = _run_agent(
        monkeypatch,
        tmp_path,
        payload=payload,
        pr_files=[_file("tests/test_action_run.py", f'+ assert calls[0]["cwd"] == "{REPO_ROOT}"')],
        router_response={"unexpected": {"nested": "shape"}},
        allowed_users="alice",
        llm_enabled=True,
        router_api_key="router-secret",
    )
    captured = capsys.readouterr()
    assert comments
    assert router_payloads
    assert comments[0] == "Agent ask indisponível (não consegui interpretar a resposta do Agent Router); use /agent review para revisão determinística."
    assert "shape=dict(keys=unexpected)" in captured.err
    assert "Traceback" not in captured.err


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
                "summary": review.NO_BLOCKING_FINDINGS_RESPONSE,
            }
        )
    )
    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "P1"
    assert parsed.notes == review.NO_BLOCKING_FINDINGS_RESPONSE


def test_router_text_response_accepts_explicit_no_blocking_result() -> None:
    parsed = review.parse_agent_router_response(review.NO_BLOCKING_FINDINGS_RESPONSE)
    assert parsed.findings == []
    assert parsed.notes == review.NO_BLOCKING_FINDINGS_RESPONSE


def test_router_invalid_response_does_not_break() -> None:
    parsed = review.parse_agent_router_response("{not-json")
    assert parsed.findings == []
    assert parsed.warning == "LLM response inválida"


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
    assert "## 🤖 Agent Review" in summary
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


# ---------------------------------------------------------------------------
# Tests for evidence-based check reporting (docs-only, status labels, etc.)
# ---------------------------------------------------------------------------

def _check(name: str, conclusion: str | None, status: str | None = "completed", url: str | None = None) -> review.CheckSummary:
    return review.CheckSummary(name=name, conclusion=conclusion, status=status, url=url)


class TestIsDocsOnly:
    def test_all_docs_files_returns_true(self) -> None:
        assert review.is_docs_only(["docs/ARCHITECTURE.md", "CHANGELOG.md", "README.md"])

    def test_mixed_docs_and_code_returns_false(self) -> None:
        assert not review.is_docs_only(["docs/ARCHITECTURE.md", "app/main.py"])

    def test_single_md_file_returns_true(self) -> None:
        assert review.is_docs_only(["README.md"])

    def test_changelog_alone_returns_true(self) -> None:
        assert review.is_docs_only(["CHANGELOG.md"])

    def test_empty_list_returns_false(self) -> None:
        assert not review.is_docs_only([])

    def test_docs_slash_prefix_returns_true(self) -> None:
        assert review.is_docs_only(["docs/TESTING.md", "docs/OPERATIONS.md"])


class TestCheckStatusLabel:
    def test_success_is_passed(self) -> None:
        assert review._check_status_label(_check("Pytest", "success")) == "passed"

    def test_neutral_is_passed(self) -> None:
        assert review._check_status_label(_check("Build", "neutral")) == "passed"

    def test_failure_is_failed(self) -> None:
        assert review._check_status_label(_check("Pytest", "failure")) == "failed"

    def test_timed_out_is_timeout(self) -> None:
        assert review._check_status_label(_check("Vitest", "timed_out")) == "timeout"

    def test_cancelled_is_not_run(self) -> None:
        assert review._check_status_label(_check("ESLint", "cancelled")) == "not_run"

    def test_action_required_is_environment_error(self) -> None:
        assert review._check_status_label(_check("Setup", "action_required")) == "environment_error"

    def test_startup_failure_is_environment_error(self) -> None:
        assert review._check_status_label(_check("Runner", "startup_failure")) == "environment_error"

    def test_skipped_conclusion_is_skipped(self) -> None:
        assert review._check_status_label(_check("Optional", "skipped")) == "skipped"

    def test_queued_status_is_not_run(self) -> None:
        assert review._check_status_label(_check("Pytest", None, status="queued")) == "not_run"

    def test_in_progress_status_is_not_run(self) -> None:
        assert review._check_status_label(_check("Pytest", None, status="in_progress")) == "not_run"


class TestScanChecksForFindings:
    def test_failure_conclusion_emits_p1(self) -> None:
        checks = [_check("Pytest", "failure", url="https://ci.example.com/1")]
        findings = review.scan_checks_for_findings(checks)
        assert len(findings) == 1
        assert findings[0].severity == "P1"
        assert "conclusion=failure" in findings[0].evidence
        assert "https://ci.example.com/1" in findings[0].evidence

    def test_cancelled_does_not_emit_p1(self) -> None:
        checks = [_check("Pytest", "cancelled")]
        findings = review.scan_checks_for_findings(checks)
        assert findings == []

    def test_timed_out_does_not_emit_p1(self) -> None:
        checks = [_check("Vitest", "timed_out")]
        findings = review.scan_checks_for_findings(checks)
        assert findings == []

    def test_action_required_does_not_emit_p1(self) -> None:
        checks = [_check("Setup", "action_required")]
        findings = review.scan_checks_for_findings(checks)
        assert findings == []

    def test_startup_failure_does_not_emit_p1(self) -> None:
        checks = [_check("Runner", "startup_failure")]
        findings = review.scan_checks_for_findings(checks)
        assert findings == []

    def test_docs_only_pr_skips_functional_test_failure(self) -> None:
        checks = [_check("Pytest", "failure"), _check("Vitest", "failure"), _check("ESLint", "failure")]
        findings = review.scan_checks_for_findings(checks, docs_only=True)
        assert findings == [], "docs-only PR must not generate P1 for functional test failures"

    def test_docs_only_pr_still_reports_non_functional_failure(self) -> None:
        checks = [_check("Security-Scan", "failure")]
        findings = review.scan_checks_for_findings(checks, docs_only=True)
        assert len(findings) == 1
        assert findings[0].severity == "P1"

    def test_non_docs_pr_still_reports_pytest_failure(self) -> None:
        checks = [_check("Pytest", "failure")]
        findings = review.scan_checks_for_findings(checks, docs_only=False)
        assert len(findings) == 1
        assert findings[0].severity == "P1"

    def test_evidence_never_says_falhou(self) -> None:
        checks = [_check("Pytest", "failure"), _check("ESLint", "timed_out")]
        for docs_only in (True, False):
            findings = review.scan_checks_for_findings(checks, docs_only=docs_only)
            for f in findings:
                assert "FALHOU" not in f.evidence, "evidence must not contain 'FALHOU'"
                assert "FALHOU" not in f.risk
                assert "FALHOU" not in f.recommendation


class TestRenderChecksTable:
    def test_empty_checks_returns_empty(self) -> None:
        assert review._render_checks_table([]) == []

    def test_passed_check_shows_passed(self) -> None:
        rows = review._render_checks_table([_check("Pytest", "success")])
        table = "\n".join(rows)
        assert "Pytest" in table
        assert "passed" in table

    def test_failed_check_shows_conclusion_failure(self) -> None:
        rows = review._render_checks_table([_check("Pytest", "failure", url="https://ci/1")])
        table = "\n".join(rows)
        assert "**failed**" in table
        assert "conclusion=failure" in table
        assert "https://ci/1" in table

    def test_timeout_check_shows_timeout_label(self) -> None:
        rows = review._render_checks_table([_check("Vitest", "timed_out")])
        table = "\n".join(rows)
        assert "timeout" in table
        assert "timed_out" in table

    def test_cancelled_check_shows_not_run(self) -> None:
        rows = review._render_checks_table([_check("ESLint", "cancelled")])
        table = "\n".join(rows)
        assert "not_run" in table

    def test_docs_only_skips_functional_test_failure(self) -> None:
        rows = review._render_checks_table(
            [_check("Pytest", "failure"), _check("Vitest", "failure")],
            docs_only=True,
        )
        table = "\n".join(rows)
        assert "**failed**" not in table, "docs-only table must not show '**failed**' for functional tests"
        assert "skipped" in table

    def test_table_never_contains_falhou(self) -> None:
        checks = [
            _check("Pytest", "failure"),
            _check("ESLint", "timed_out"),
            _check("Build", "cancelled"),
            _check("Setup", "action_required"),
        ]
        for docs_only in (True, False):
            rows = review._render_checks_table(checks, docs_only=docs_only)
            table = "\n".join(rows)
            assert "FALHOU" not in table


class TestBuildDeterministicFindingsDocsOnly:
    def test_docs_only_pr_with_failing_checks_has_no_p1(self) -> None:
        files = [
            review.FileChange("CHANGELOG.md", "modified", 5, 0),
            review.FileChange("docs/ARCHITECTURE.md", "modified", 3, 1),
        ]
        checks = [
            _check("Pytest", "failure"),
            _check("Vitest", "failure"),
            _check("ESLint", "failure"),
        ]
        findings = review.build_deterministic_findings(files, checks)
        p1 = [f for f in findings if f.severity == "P1"]
        assert p1 == [], "docs-only PR with failing functional checks must not produce P1 findings"

    def test_code_pr_with_failing_pytest_has_p1(self) -> None:
        files = [review.FileChange("app/main.py", "modified", 10, 2)]
        checks = [_check("Pytest", "failure", url="https://ci/2")]
        findings = review.build_deterministic_findings(files, checks)
        p1 = [f for f in findings if f.severity == "P1"]
        assert any("check_failed::Pytest" in f.rule_id for f in p1)


class TestRenderReviewNoFalhou:
    """The rendered comment must never say 'FALHOU' unless backed by evidence."""

    def _make_ctx(self, files: list[review.FileChange]) -> review.ReviewContext:
        return review.ReviewContext(
            owner="org",
            repo="repo",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="Test PR",
            body="body",
            base_ref="master",
            head_ref="feat/x",
            head_sha="abc123",
            html_url="https://github.com/org/repo/pull/1",
            files=files,
            checks=[],
        )

    def test_comment_without_logs_does_not_contain_falhou(self) -> None:
        ctx = self._make_ctx([review.FileChange("CHANGELOG.md", "modified", 1, 0)])
        rendered = review.render_review([], [], ctx, llm_mode=False)
        assert "FALHOU" not in rendered

    def test_docs_only_pr_shows_documental_flag(self) -> None:
        ctx = self._make_ctx([review.FileChange("docs/TESTING.md", "modified", 1, 0)])
        rendered = review.render_review([], [], ctx, llm_mode=False)
        assert "PR documental: sim" in rendered

    def test_code_pr_does_not_show_documental_flag(self) -> None:
        ctx = self._make_ctx([review.FileChange("app/main.py", "modified", 1, 0)])
        rendered = review.render_review([], [], ctx, llm_mode=False)
        assert "PR documental: não" in rendered

    def test_checks_table_rendered_not_plain_list(self) -> None:
        ctx = self._make_ctx([review.FileChange("app/main.py", "modified", 1, 0)])
        ctx.checks.append(_check("Pytest", "success"))
        rendered = review.render_review([], ctx.checks, ctx, llm_mode=False)
        assert "## Validações de CI" in rendered
        assert "| Check | Status | Evidência |" in rendered

    def test_failed_check_shows_conclusion_failure_in_comment(self) -> None:
        ctx = self._make_ctx([review.FileChange("app/main.py", "modified", 1, 0)])
        ctx.checks.append(_check("Pytest", "failure", url="https://ci/99"))
        findings = review.build_deterministic_findings(ctx.files, ctx.checks)
        rendered = review.render_review(findings, ctx.checks, ctx, llm_mode=False)
        assert "conclusion=failure" in rendered
        assert "FALHOU" not in rendered

    def test_docs_only_checks_table_shows_skipped_not_failed(self) -> None:
        ctx = self._make_ctx([review.FileChange("CHANGELOG.md", "modified", 2, 0)])
        ctx.checks.extend([_check("Pytest", "failure"), _check("Vitest", "failure")])
        rendered = review.render_review([], ctx.checks, ctx, llm_mode=False)
        assert "**failed**" not in rendered
        assert "skipped" in rendered
        assert "FALHOU" not in rendered


# ---------------------------------------------------------------------------
# Tests for CheckResult / run_check  (step 7 of the task)
# ---------------------------------------------------------------------------

class TestCheckResult:
    """CheckResult fields must be set according to the strict status rules."""

    def test_status_literals_are_valid(self) -> None:
        """CheckResult accepts all valid status strings."""
        valid = ("passed", "failed", "skipped", "not_run", "timeout", "missing_command", "environment_error")
        for status in valid:
            cr = review.CheckResult(name="x", command=["x"], cwd="/", status=status)  # type: ignore[arg-type]
            assert cr.status == status

    def test_failed_requires_nonzero_exit_code(self) -> None:
        """status=failed must carry a non-zero exit_code, never None."""
        cr = review.CheckResult(name="pytest", command=["pytest"], cwd="/", status="failed", exit_code=1)
        assert cr.exit_code is not None
        assert cr.exit_code != 0

    def test_passed_carries_zero_exit_code(self) -> None:
        cr = review.CheckResult(name="pytest", command=["pytest"], cwd="/", status="passed", exit_code=0)
        assert cr.exit_code == 0

    def test_missing_command_has_no_exit_code(self) -> None:
        cr = review.CheckResult(name="vitest", command=["vitest"], cwd="/", status="missing_command")
        assert cr.exit_code is None

    def test_timeout_has_no_exit_code(self) -> None:
        cr = review.CheckResult(name="eslint", command=["eslint", "."], cwd="/", status="timeout")
        assert cr.exit_code is None

    def test_not_run_has_no_exit_code(self) -> None:
        cr = review.CheckResult(name="pytest", command=["pytest"], cwd="/", status="not_run")
        assert cr.exit_code is None


class TestRunCheck:
    """run_check() must classify outcomes strictly and never invent failure."""

    # --- true command, exit_code=0 ----------------------------------------

    def test_exit_code_zero_yields_passed(self) -> None:
        result = review.run_check("true", ["true"], cwd="/")
        assert result.status == "passed"
        assert result.exit_code == 0

    # --- command that exits non-zero --------------------------------------

    def test_nonzero_exit_code_yields_failed_with_log(self) -> None:
        result = review.run_check("false", ["false"], cwd="/")
        assert result.status == "failed"
        assert result.exit_code is not None
        assert result.exit_code != 0

    def test_failed_result_carries_exit_code(self) -> None:
        """exit_code must be set when status=failed (never None for failed)."""
        result = review.run_check("sh_exit1", ["sh", "-c", "exit 1"], cwd="/")
        assert result.status == "failed"
        assert result.exit_code == 1

    # --- missing command --------------------------------------------------

    def test_missing_command_yields_missing_command_not_failed(self) -> None:
        result = review.run_check(
            "no-such-binary-xyzzy",
            ["no-such-binary-xyzzy", "--version"],
            cwd="/",
        )
        assert result.status == "missing_command", (
            f"missing binary must yield 'missing_command', got '{result.status}'"
        )
        assert result.exit_code is None
        assert result.status != "failed"

    def test_missing_command_has_reason(self) -> None:
        result = review.run_check("phantom", ["phantom-999", "--help"], cwd="/")
        assert result.status == "missing_command"
        assert result.reason  # must not be empty

    # --- timeout ----------------------------------------------------------

    def test_timeout_yields_timeout_not_failed(self) -> None:
        result = review.run_check(
            "sleep",
            ["sh", "-c", "sleep 10"],
            cwd="/",
            timeout=1,
        )
        assert result.status == "timeout", (
            f"timed-out command must yield 'timeout', got '{result.status}'"
        )
        assert result.exit_code is None

    def test_timeout_has_reason(self) -> None:
        result = review.run_check("sleep", ["sh", "-c", "sleep 10"], cwd="/", timeout=1)
        assert result.status == "timeout"
        assert "1s" in result.reason or "timeout" in result.reason.lower()

    # --- environment error ------------------------------------------------

    def test_missing_env_var_yields_environment_error(self) -> None:
        import uuid
        unique_var = f"_TEST_MISSING_{uuid.uuid4().hex.upper()}"
        result = review.run_check("echo", ["echo", "hello"], cwd="/", env_vars=[unique_var])
        assert result.status == "environment_error"
        assert result.exit_code is None

    def test_environment_error_has_reason(self) -> None:
        import uuid
        unique_var = f"_TEST_MISSING_{uuid.uuid4().hex.upper()}"
        result = review.run_check("echo", ["echo", "hello"], cwd="/", env_vars=[unique_var])
        assert result.reason
        assert unique_var in result.reason

    # --- no false FALHOU generation ---------------------------------------

    def test_missing_command_result_does_not_contain_falhou(self) -> None:
        result = review.run_check("no-binary-abc", ["no-binary-abc"], cwd="/")
        haystack = " ".join([result.status, result.reason, result.stdout_tail, result.stderr_tail])
        assert "FALHOU" not in haystack

    def test_timeout_result_does_not_contain_falhou(self) -> None:
        result = review.run_check("sleep", ["sh", "-c", "sleep 10"], cwd="/", timeout=1)
        haystack = " ".join([result.status, result.reason, result.stdout_tail, result.stderr_tail])
        assert "FALHOU" not in haystack


class TestNoGenericRecommendationsWithoutEvidence:
    """render_review must not produce generic security/performance recs without diff evidence."""

    def _make_ctx(self, files: list[review.FileChange]) -> review.ReviewContext:
        return review.ReviewContext(
            owner="org", repo="repo", issue_number=1, author="alice",
            association="MEMBER", pr_number=1, title="PR", body="body",
            base_ref="master", head_ref="feat/x", head_sha="abc",
            html_url="https://github.com/org/repo/pull/1",
            files=files, checks=[],
        )

    def test_docs_only_pr_no_generic_security_recommendation(self) -> None:
        """A docs-only PR with no findings must not contain generic security text."""
        ctx = self._make_ctx([review.FileChange("docs/SECURITY.md", "modified", 2, 0)])
        rendered = review.render_review([], [], ctx, llm_mode=False)
        generic_phrases = [
            "validar autenticação",
            "otimizar SQL",
            "revisar regras ESLint",
            "adicionar rate-limit",
            "sanitizar input",
        ]
        for phrase in generic_phrases:
            assert phrase.lower() not in rendered.lower(), (
                f"Generic phrase '{phrase}' must not appear without evidence"
            )

    def test_no_p1_p2_findings_renders_clean_summary(self) -> None:
        ctx = self._make_ctx([review.FileChange("README.md", "modified", 1, 0)])
        rendered = review.render_review([], [], ctx, llm_mode=False)
        assert "Nenhum achado confirmado." in rendered
        assert "FALHOU" not in rendered

    def test_check_without_failure_conclusion_produces_no_p1(self) -> None:
        """Checks that are timeout/cancelled/queued/in_progress must not produce P1 findings."""
        non_failure_checks = [
            review.CheckSummary("Pytest", conclusion="timed_out", status="completed"),
            review.CheckSummary("ESLint", conclusion="cancelled", status="completed"),
            review.CheckSummary("Vitest", conclusion=None, status="queued"),
            review.CheckSummary("Build", conclusion="action_required", status="completed"),
        ]
        files = [review.FileChange("app/main.py", "modified", 5, 1)]
        findings = review.build_deterministic_findings(files, non_failure_checks)
        p1 = [f for f in findings if f.severity == "P1" and "check_failed" in f.rule_id]
        assert p1 == [], (
            "Non-failure check conclusions must not produce check_failed P1 findings"
        )


# ---------------------------------------------------------------------------
# Session R2: AgentEscala context + truncated-diff awareness
# ---------------------------------------------------------------------------


class TestSessionR2AgentEscala:
    """Tests for Session R2: AgentEscala context, truncated-diff caution,
    final-file reading, speculative language normalisation, and checks-observed."""

    # ---- helpers -----------------------------------------------------------

    def _make_ctx(
        self,
        owner: str = "mglpsw",
        repo: str = "AgentEscala",
        files: list[review.FileChange] | None = None,
        body: str = "",
        checks: list[review.CheckSummary] | None = None,
    ) -> review.ReviewContext:
        return review.ReviewContext(
            owner=owner,
            repo=repo,
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="PR title",
            body=body,
            base_ref="master",
            head_ref="feat/x",
            head_sha="abc",
            html_url="https://github.com/mglpsw/AgentEscala/pull/1",
            files=files or [review.FileChange("frontend/Calendar.tsx", "modified", 5, 2)],
            checks=checks or [],
        )

    # ---- test 1: AgentEscala context is included for mglpsw/AgentEscala ----

    def test_agentescala_context_included_for_agentescala_repo(self) -> None:
        ctx = self._make_ctx(owner="mglpsw", repo="AgentEscala")
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "Contexto obrigatório do AgentEscala" in bundle.content, (
            "AgentEscala context block must appear in bundle for mglpsw/AgentEscala"
        )
        assert "CT104" in bundle.content
        assert "CT102" in bundle.content
        assert "10-22H" in bundle.content

    # ---- test 2: AgentEscala context NOT included for other repos -----------

    def test_agentescala_context_excluded_for_other_repos(self) -> None:
        ctx = self._make_ctx(owner="mglpsw", repo="aiops-orchestrator")
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "Contexto obrigatório do AgentEscala" not in bundle.content, (
            "AgentEscala context block must NOT appear in bundle for other repos"
        )

    # ---- test 3: truncated diff triggers explicit caution lines -------------

    def test_truncated_diff_adds_caution_to_bundle(self) -> None:
        # Produce a truncated bundle by adding a file with a very long patch
        big_patch = "+linha\n" * 5000
        files = [review.FileChange("frontend/Big.tsx", "modified", 5000, 0, patch=big_patch)]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert bundle.truncated is True
        assert "ATENÇÃO DIFF TRUNCADO" in bundle.content
        assert "não classifique como P0/P1" in bundle.content
        assert "import não utilizado" in bundle.content

    # ---- test 4: suspicious import in truncated diff does NOT become P1 ----

    def test_suspicious_import_in_truncated_diff_not_confirmed_p1(self) -> None:
        speculative_finding = review.Finding(
            rule_id="llm_unused_import",
            severity="P1",
            file="frontend/Calendar.tsx",
            evidence="possivelmente ShellIcon não é usado neste arquivo",
            risk="talvez cause bundle inflation",
            recommendation="remover import",
        )
        status = review.review_status([], llm_findings=[speculative_finding])
        assert status != "changes_requested", (
            "Speculative P1 with 'possivelmente'/'talvez' must not promote to request_changes"
        )

    # ---- test 5: final-file context showing symbol usage prevents unused-import finding ----

    def test_final_file_showing_usage_prevents_unused_import_finding(self) -> None:
        """_fetch_file_at_ref returns content from GitHub API (base64) with source marker."""
        import base64
        import json as _json
        from urllib.parse import urlparse

        file_content = (
            "import { ShellIcon } from './icons';\n"
            "export function App() { return <ShellIcon size={24} />; }\n"
        )
        encoded = base64.b64encode(file_content.encode()).decode()

        class _FakeClient:
            def get_json(self, path: str):
                return {"encoding": "base64", "content": encoded}

        ctx = review._fetch_file_at_ref(
            _FakeClient(),  # type: ignore[arg-type]
            "mglpsw",
            "AgentEscala",
            "frontend/Calendar.tsx",
            "abc1234",
            2000,
        )
        assert ctx is not None
        assert "final_file_context" in ctx
        assert "ShellIcon" in ctx
        assert "<ShellIcon" in ctx
        # ref slug must appear in the marker
        assert "abc1234"[:8] in ctx

    # ---- test 6: speculative P1 does not promote to request_changes ---------

    def test_speculative_p1_does_not_promote_to_request_changes(self) -> None:
        for phrase in [
            "possivelmente causa bug",
            "talvez quebre produção",
            "pode ser que falhe",
            "não está claro se funciona",
            "não consegui confirmar",
            "parece estar errado",
        ]:
            finding = review.Finding(
                rule_id="llm_risk",
                severity="P1",
                file="app.py",
                evidence=phrase,
                risk=phrase,
                recommendation="investigar",
            )
            status = review.review_status([], llm_findings=[finding])
            assert status != "changes_requested", (
                f"P1 with speculative phrase '{phrase}' must not produce changes_requested"
            )

    # ---- test 7: green checks/tests in PR body do not generate "sem garantias" ----

    def test_green_checks_in_pr_body_avoids_no_test_language(self) -> None:
        ctx = self._make_ctx(
            body="Todos os testes passaram. CI verde. ✅",
            checks=[review.CheckSummary("pytest", conclusion="success", status="completed")],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        content = bundle.content
        # The bundle must not *state* these as conclusions — they may appear only
        # as negated instructions inside the contract, not as top-level assertions.
        # We verify the instruction/rule (D) is present and the phrase appears only
        # inside it (prefixed by "NÃO escreva" / negation), not as a standalone claim.
        assert "NÃO escreva 'sem garantias de testes'" in content or \
               "NÃO escreva 'sem garantias" in content, \
               "Rule (D) about 'sem garantias' must be present in bundle"
        # Also verify: contract says "não validei localmente" (positive phrasing)
        assert "não validei localmente" in content

    # ---- test 8: rendered comment contains all 3 required sections ----------

    def test_comment_contains_all_three_sections(self) -> None:
        ctx = self._make_ctx()
        # confirmed finding
        confirmed = review.Finding(
            rule_id="secret_exposed",
            severity="P1",
            file="app.py",
            evidence="GITHUB_TOKEN hardcoded",
            risk="credential leak",
            recommendation="use secrets manager",
        )
        # speculative llm finding
        unconfirmed = review.Finding(
            rule_id="llm_risk",
            severity="P2",
            file="calendar.tsx",
            evidence="possivelmente pode causar regressão",
            risk="incerto",
            recommendation="revisar",
        )
        rendered = review.render_review(
            [confirmed],
            [],
            ctx,
            llm_mode=True,
            llm_findings=[unconfirmed],
        )
        assert "Achados confirmados" in rendered, "Section 'Achados confirmados' must be present"
        assert "Riscos não confirmados" in rendered, "Section 'Riscos não confirmados' must be present"
        assert (
            "Testes/checks observados" in rendered
            or "Observação sobre testes" in rendered
            or "não executei localmente" in rendered.lower()
            or "checks" in rendered.lower()
        ), "Section about tests/checks must be present"


class TestSessionR3TruncationMetadata:
    """Tests for Session R3: accurate truncation metadata, small-PR full-patch,
    corrected bundle/comment language about diff receipt."""

    MAX_SMALL = review.MAX_SMALL_DIFF_FILES  # 3
    MAX_SMALL_CHARS = review.MAX_SMALL_DIFF_CHARS  # 30_000

    def _make_ctx(
        self,
        owner: str = "mglpsw",
        repo: str = "SomeRepo",
        files: list[review.FileChange] | None = None,
        body: str = "",
        checks: list[review.CheckSummary] | None = None,
        title: str = "PR title",
    ) -> review.ReviewContext:
        return review.ReviewContext(
            owner=owner,
            repo=repo,
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title=title,
            body=body,
            base_ref="master",
            head_ref="feat/x",
            head_sha="sha123",
            html_url="https://github.com/mglpsw/SomeRepo/pull/1",
            files=files or [review.FileChange("src/index.ts", "modified", 5, 2)],
            checks=checks or [],
        )

    # 1. bundle always contains diff_received=true
    def test_bundle_always_has_diff_received_true(self):
        ctx = self._make_ctx()
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "diff_received=true" in bundle.content

    # 2. small PR (2 files, ~2k chars) → bundle_truncated=false
    def test_bundle_has_bundle_truncated_false_for_small_pr(self):
        patch = "+" + "x" * 1000
        files = [
            review.FileChange("src/a.ts", "modified", 10, 2, patch=patch),
            review.FileChange("src/b.ts", "modified", 10, 2, patch=patch),
        ]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert bundle.files_count == 2
        diff_chars = sum(len(f.patch or "") for f in files)
        assert diff_chars <= self.MAX_SMALL_CHARS
        assert bundle.truncated is False
        assert "bundle_truncated=false" in bundle.content

    # 3. large PR (>MAX_FILES) → bundle_truncated=true
    def test_bundle_has_bundle_truncated_true_for_large_pr(self):
        files = [
            review.FileChange(f"src/file{i}.ts", "modified", 1, 1, patch="+x")
            for i in range(review.MAX_FILES_ANALYZED + 2)
        ]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert bundle.truncated is True
        assert "bundle_truncated=true" in bundle.content

    # 4. truncation_reasons includes too_many_files when files exceed MAX_FILES_ANALYZED
    def test_truncation_reasons_include_too_many_files(self):
        files = [
            review.FileChange(f"src/file{i}.ts", "modified", 1, 1, patch="+x")
            for i in range(review.MAX_FILES_ANALYZED + 1)
        ]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "too_many_files" in bundle.truncation_reasons
        assert "too_many_files" in bundle.content

    # 5. truncation_reasons includes patch_snippet_truncated for large-PR long patch
    def test_truncation_reasons_include_patch_snippet_truncated(self):
        # More than MAX_SMALL_DIFF_FILES so small-PR path is not taken
        big_patch = "+line\n" * 50
        files = [
            review.FileChange(f"src/file{i}.ts", "modified", 50, 5, patch=big_patch)
            for i in range(review.MAX_SMALL_DIFF_FILES + 1)
        ]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "patch_snippet_truncated" in bundle.truncation_reasons

    # 6. small PR uses full patch block (no patch_snippet_truncated)
    def test_small_pr_uses_full_patch_no_truncation(self):
        patch = "+line1\n+line2\n+line3\n"
        files = [
            review.FileChange("src/a.ts", "modified", 3, 0, patch=patch),
        ]
        ctx = self._make_ctx(files=files)
        bundle = review._build_sanitized_bundle(ctx, [])
        assert bundle.truncated is False
        assert "patch_snippet_truncated" not in bundle.truncation_reasons
        # Full patch content should be in the bundle (not just a snippet)
        assert "line1" in bundle.content

    # 7. render_review uses "Diff recebido, mas parte do patch" when truncated
    def test_comment_uses_diff_received_phrasing_when_truncated(self):
        from dataclasses import replace as dc_replace
        truncated_bundle = review.ReviewBundle(
            content="x",
            diff_chars=50000,
            files_count=10,
            truncated=True,
            truncation_reasons=("too_many_files",),
            final_file_context_count=0,
        )
        ctx = self._make_ctx()
        rendered = review.render_review(
            [],
            [],
            ctx,
            llm_mode=False,
            review_bundle=truncated_bundle,
        )
        assert "Diff recebido, mas parte do patch foi resumida" in rendered

    # 8. render_review does NOT say "não recebi o diff" or "sem diff"
    def test_comment_does_not_say_diff_not_received(self):
        from dataclasses import replace as dc_replace
        truncated_bundle = review.ReviewBundle(
            content="x",
            diff_chars=50000,
            files_count=10,
            truncated=True,
            truncation_reasons=("too_many_files",),
            final_file_context_count=0,
        )
        ctx = self._make_ctx()
        rendered = review.render_review(
            [],
            [],
            ctx,
            llm_mode=False,
            review_bundle=truncated_bundle,
        )
        lower = rendered.lower()
        assert "não recebi o diff" not in lower
        assert "não tenho o diff" not in lower
        assert "sem diff" not in lower

    # 9. final_file_context via GitHub API: final_file_context_count reflects injections
    def test_final_file_context_count_reflects_injection(self):
        class _FakeClient:
            def get_json(self, path, *, params=None):
                import base64
                content = base64.b64encode(b"import ShellIcon from './icons';\nShellIcon();\n").decode()
                return {"content": content, "encoding": "base64"}

        import_patch = "+import ShellIcon from './icons';\n+ShellIcon();\n"
        files = [
            review.FileChange("src/Calendar.tsx", "modified", 2, 0, patch=import_patch),
        ]
        ctx = self._make_ctx(
            owner="mglpsw",
            repo="AgentEscala",
            files=files,
        )
        bundle = review._build_sanitized_bundle(ctx, [], client=_FakeClient())
        assert bundle.final_file_context_count == 1
        assert "final_file_context_source=github_contents_api" in bundle.content
        assert "final_file_context_count=1" in bundle.content


class TestSessionR3BundleCharBudgetRecompute:
    """P1 fix: truncation must be recomputed AFTER sentinel replacement.

    The sentinel __BUNDLE_DIFF_META__ is ~25 chars; the real metadata line is
    ~200+ chars.  A bundle that fits inside MAX_BUNDLE_CHARS with the sentinel
    may exceed it after the replacement.  _finalize_review_bundle() must detect
    this in a second pass and set bundle_truncated=true + bundle_char_budget_exceeded.
    """

    def test_bundle_char_budget_exceeded_detected_after_sentinel_replacement(self):
        """Bundle OK with sentinel but over limit after metadata expansion → truncated=True."""
        # Craft a sanitized_bundle_with_sentinel whose len is just below MAX_BUNDLE_CHARS
        # so that replacing the sentinel (~25 chars) with the real metadata line (~220 chars)
        # pushes it over the limit.
        meta_example = review._render_bundle_metadata(
            diff_chars=100, files_count=1, truncated=False,
            reasons=[], final_file_context_count=0, final_file_source="none",
        )
        sentinel_len = len(review._BUNDLE_METADATA_SENTINEL)
        meta_len = len(meta_example)
        # Sentinel placed FIRST (mirrors real bundle layout) so truncation from the
        # tail doesn't cut it off.  Padding fills the rest up to just under the limit.
        padding = review.MAX_BUNDLE_CHARS - 1 - sentinel_len - len("\npad=\n")
        assert padding > 0, "padding must be positive for this test to be meaningful"
        fake_bundle = f"{review._BUNDLE_METADATA_SENTINEL}\npad={'x' * padding}\n"
        assert len(fake_bundle) < review.MAX_BUNDLE_CHARS, "sentinel version must be under limit"
        # After replacement the bundle grows by (meta_len - sentinel_len) bytes → over limit
        assert len(fake_bundle) - sentinel_len + meta_len > review.MAX_BUNDLE_CHARS, (
            "test invariant: post-replacement bundle must exceed MAX_BUNDLE_CHARS"
        )

        result = review._finalize_review_bundle(
            fake_bundle,
            diff_chars=100,
            files_count=1,
            truncated=False,
            reasons=[],
            final_file_context_count=0,
            final_file_source="none",
        )

        assert result.truncated is True
        assert "bundle_char_budget_exceeded" in result.truncation_reasons
        assert "bundle_truncated=true" in result.content
        assert "bundle_char_budget_exceeded" in result.content

    def test_bundle_well_under_limit_stays_not_truncated(self):
        """Bundle comfortably under MAX_BUNDLE_CHARS stays truncated=False."""
        tiny_bundle = f"short content\n{review._BUNDLE_METADATA_SENTINEL}"
        assert len(tiny_bundle) < review.MAX_BUNDLE_CHARS

        result = review._finalize_review_bundle(
            tiny_bundle,
            diff_chars=50,
            files_count=1,
            truncated=False,
            reasons=[],
            final_file_context_count=0,
            final_file_source="none",
        )

        assert result.truncated is False
        assert "bundle_char_budget_exceeded" not in result.truncation_reasons
        assert "bundle_truncated=false" in result.content
        assert "bundle_char_budget_exceeded" not in result.content


class TestSessionR3LLMBundleLimit:
    """Session R3 – MAX_LLM_BUNDLE_CHARS fixes small-PR truncation in LLM mode."""

    def _make_ctx(self, *, diff_chars: int, files_count: int = 2) -> review.ReviewContext:
        patch_per_file = diff_chars // max(files_count, 1)
        files = [
            review.FileChange(
                path=f"src/Component{i}.tsx",
                patch="+" + "x" * (patch_per_file - 1),
                status="modified",
                additions=5,
                deletions=1,
            )
            for i in range(files_count)
        ]
        return review.ReviewContext(
            owner="mglpsw",
            repo="AgentEscala",
            issue_number=99,
            author="alice",
            association="MEMBER",
            pr_number=99,
            title="feat: small PR",
            body="",
            base_ref="master",
            head_ref="feat/test",
            head_sha="head",
            html_url="https://github.com/mglpsw/AgentEscala/pull/99",
            files=files,
            checks=[],
        )

    def test_small_pr_21k_llm_mode_not_truncated(self):
        """2-file ~21k diff fits in MAX_LLM_BUNDLE_CHARS=32000 → bundle_truncated=false."""
        ctx = self._make_ctx(diff_chars=21_600, files_count=2)
        bundle = review._build_sanitized_bundle(
            ctx, [], max_bundle_chars=review.MAX_LLM_BUNDLE_CHARS
        )
        assert bundle.truncated is False, f"Expected not truncated; reasons={bundle.truncation_reasons}"
        assert bundle.bundle_max_chars == review.MAX_LLM_BUNDLE_CHARS
        assert "bundle_truncated=false" in bundle.content
        assert "bundle_char_budget_exceeded" not in bundle.truncation_reasons
        assert f"bundle_max_chars={review.MAX_LLM_BUNDLE_CHARS}" in bundle.content

    def test_bundle_max_chars_in_metadata_line(self):
        """bundle_max_chars is embedded in the metadata line."""
        ctx = self._make_ctx(diff_chars=500)
        bundle = review._build_sanitized_bundle(
            ctx, [], max_bundle_chars=review.MAX_LLM_BUNDLE_CHARS
        )
        assert f"bundle_max_chars={review.MAX_LLM_BUNDLE_CHARS}" in bundle.content

    def test_bundle_exceeding_llm_limit_is_truncated(self):
        """Bundle that genuinely exceeds MAX_LLM_BUNDLE_CHARS → bundle_truncated=true."""
        # Sentinel first so metadata line survives truncation; padding after pushes over limit
        padding = "x" * (review.MAX_LLM_BUNDLE_CHARS + 500)
        synthetic = f"{review._BUNDLE_METADATA_SENTINEL}\n{padding}"
        result = review._finalize_review_bundle(
            synthetic,
            diff_chars=50_000,
            files_count=5,
            truncated=False,
            reasons=[],
            final_file_context_count=0,
            final_file_source="none",
            max_bundle_chars=review.MAX_LLM_BUNDLE_CHARS,
        )
        assert result.truncated is True
        assert "bundle_char_budget_exceeded" in result.truncation_reasons
        assert "bundle_truncated=true" in result.content

    def test_old_max_bundle_chars_not_used_for_llm_mode(self):
        """A 21k diff should NOT be truncated by old MAX_BUNDLE_CHARS=6000."""
        ctx = self._make_ctx(diff_chars=21_600, files_count=2)
        # Build with LLM limit; if it were using old 6000, it would be truncated
        bundle = review._build_sanitized_bundle(
            ctx, [], max_bundle_chars=review.MAX_LLM_BUNDLE_CHARS
        )
        # 21600 chars of patch < MAX_LLM_BUNDLE_CHARS=32000 → should not be truncated
        assert bundle.bundle_max_chars != review.MAX_BUNDLE_CHARS
        assert bundle.bundle_max_chars == review.MAX_LLM_BUNDLE_CHARS
        assert bundle.truncated is False

    def test_diff_received_true_always_present(self):
        """diff_received=true must always be in the metadata line."""
        ctx = self._make_ctx(diff_chars=500)
        bundle = review._build_sanitized_bundle(
            ctx, [], max_bundle_chars=review.MAX_LLM_BUNDLE_CHARS
        )
        assert "diff_received=true" in bundle.content


class TestFalsePositivePrevention:
    """Tests to prevent false positives in LLM reviews, especially for AgentEscala PRs."""

    def test_diff_absent_does_not_generate_critical_findings(self):
        """When diff is truncated, speculative P0/P1 (without concrete evidence) should be downgraded.

        This test covers hypothetical/speculative claims, NOT visible critical evidence.
        For real secrets/destructive commands visible in truncated diff, see dedicated tests below.
        """
        # Simulate truncated diff scenario with SPECULATIVE finding (no concrete evidence)
        findings = [
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="possivelmente falta validação de entrada",
                risk="pode permitir injeção",
                recommendation="adicionar validação",
                rule_id="llm::P1::backend/api.py",
            )
        ]
        # Post-process with truncated=True and no checks
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should downgrade to P2 because it's speculative
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado de P1" in processed[0].evidence

    def test_diff_absent_does_not_invent_technical_details(self):
        """LLM should not invent endpoints/schemas/migrations without evidence."""
        findings = [
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="endpoint /api/users não existe",
                risk="quebra contrato da API",
                recommendation="criar endpoint",
                rule_id="llm::P1::backend/api.py",
            )
        ]
        # Post-process without checks or concrete evidence
        processed = review._postprocess_llm_findings(findings, checks=[])
        # Should downgrade to P2
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado de P1 - detalhes técnicos não verificados]" in processed[0].evidence

    def test_pytest_eslint_vitest_only_fail_with_real_check(self):
        """pytest/eslint/vitest should only be marked as FAILED if check shows conclusion=failure."""
        # LLM claims pytest failed without evidence
        findings = [
            review.Finding(
                severity="P1",
                file="tests/test_api.py",
                evidence="pytest falhou",
                risk="testes quebrados",
                recommendation="corrigir testes",
                rule_id="llm::P1::tests/test_api.py",
            )
        ]
        # No failed checks
        checks = [
            review.CheckSummary(name="pytest", conclusion="success", status="completed", url=None)
        ]
        processed = review._postprocess_llm_findings(findings, checks=checks)
        # Should downgrade to P2
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado de P1 - falha de teste não comprovada]" in processed[0].evidence

    def test_pytest_fail_with_real_failed_check_stays_p1(self):
        """When pytest actually failed (conclusion=failure), P1 should stay P1."""
        findings = [
            review.Finding(
                severity="P1",
                file="tests/test_api.py",
                evidence="pytest falhou com erro de importação",
                risk="testes quebrados",
                recommendation="corrigir import",
                rule_id="llm::P1::tests/test_api.py",
            )
        ]
        # Real failed check
        checks = [
            review.CheckSummary(name="pytest", conclusion="failure", status="completed", url="http://example.com")
        ]
        processed = review._postprocess_llm_findings(findings, checks=checks)
        # Should stay P1 (no downgrade for truncation since we have failed check)
        assert len(processed) == 1
        # May still be P2 if language is speculative, but not if concrete
        # In this case it should stay P1 because we have evidence
        assert processed[0].severity == "P1" or "[Rebaixado" not in processed[0].evidence

    def test_speculative_p1_downgraded_to_risk(self):
        """P1 with speculative language ('possivelmente', 'talvez') should be downgraded to P2."""
        findings = [
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="possivelmente há problema de segurança",
                risk="talvez permita injeção",
                recommendation="verificar",
                rule_id="llm::P1::backend/api.py",
            )
        ]
        processed = review._postprocess_llm_findings(findings)
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado de P1 por linguagem especulativa]" in processed[0].evidence

    def test_without_p0_p1_verdict_cannot_be_request_changes(self):
        """Without confirmed P0/P1, review_status cannot return changes_requested."""
        # Only P2 findings
        deterministic_findings = []
        llm_findings = [
            review.Finding(
                severity="P2",
                file="backend/api.py",
                evidence="possível risco de validação",
                risk="pode permitir dados inválidos",
                recommendation="adicionar validação",
                rule_id="llm::P2::backend/api.py",
            )
        ]
        status = review.review_status(deterministic_findings, llm_findings)
        assert status != "changes_requested"
        assert status == "needs_review"

    def test_agentescala_context_injection(self):
        """AgentEscala PRs should get context injection."""
        ctx = review.ReviewContext(
            owner="mglpsw",
            repo="AgentEscala",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="feat: test",
            body="",
            base_ref="master",
            head_ref="feat/test",
            head_sha="abc123",
            html_url="https://github.com/mglpsw/AgentEscala/pull/1",
            files=[],
            checks=[],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "AgentEscala é sistema de escala médica" in bundle.content
        assert "CT102 é produção" in bundle.content

    def test_agentescala_admin_notification_pr_uses_template(self):
        """Admin/Notification PRs for AgentEscala should use structured template."""
        ctx = review.ReviewContext(
            owner="mglpsw",
            repo="AgentEscala",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="feat(admin): adicionar notificações WhatsApp",
            body="Implementa notificações via WhatsApp",
            base_ref="master",
            head_ref="feat/whatsapp-notifications",
            head_sha="abc123",
            html_url="https://github.com/mglpsw/AgentEscala/pull/1",
            files=[
                review.FileChange(
                    path="backend/notifications/whatsapp.py",
                    patch="+def send_whatsapp():\n+    pass",
                    status="added",
                    additions=2,
                    deletions=0,
                )
            ],
            checks=[],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        assert "Template obrigatório para PR de Admin/Notificações" in bundle.content
        assert "Veredito:" in bundle.content
        assert "Achados confirmados:" in bundle.content
        assert "Riscos não confirmados:" in bundle.content

    def test_frontend_only_pr_no_backend_suggestion(self):
        """Frontend-only PR should not suggest backend/migration without bug."""
        # This is enforced by prompt, but we can test detection
        ctx = review.ReviewContext(
            owner="mglpsw",
            repo="AgentEscala",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="feat(frontend): update calendar UI",
            body="",
            base_ref="master",
            head_ref="feat/calendar-ui",
            head_sha="abc123",
            html_url="https://github.com/mglpsw/AgentEscala/pull/1",
            files=[
                review.FileChange(
                    path="frontend/Calendar.tsx",
                    patch="+const Calendar = () => <div>Calendar</div>",
                    status="modified",
                    additions=1,
                    deletions=0,
                )
            ],
            checks=[],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        # Should include AgentEscala context mentioning this rule
        assert "PR frontend-only não deve sugerir mudanças de backend ou migration sem bug real" in bundle.content

    def test_diff_truncated_forces_explicit_caution(self):
        """When diff is truncated, bundle should have explicit warnings."""
        ctx = review.ReviewContext(
            owner="mglpsw",
            repo="aiops-orchestrator",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="feat: large change",
            body="",
            base_ref="master",
            head_ref="feat/large",
            head_sha="abc123",
            html_url="https://github.com/mglpsw/aiops-orchestrator/pull/1",
            files=[
                review.FileChange(
                    path=f"src/file{i}.py",
                    patch="+" + "x" * 5000,
                    status="modified",
                    additions=100,
                    deletions=10,
                )
                for i in range(10)  # More than MAX_FILES_ANALYZED
            ],
            checks=[],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        assert bundle.truncated is True
        assert "ATENÇÃO DIFF TRUNCADO" in bundle.content

    def test_pr_body_with_green_checks_no_invented_test_failures(self):
        """PR with green checks should not generate 'sem garantias de testes'."""
        # This is enforced in the prompt
        ctx = review.ReviewContext(
            owner="mglpsw",
            repo="aiops-orchestrator",
            issue_number=1,
            author="alice",
            association="MEMBER",
            pr_number=1,
            title="feat: test",
            body="Todos os testes passando ✓",
            base_ref="master",
            head_ref="feat/test",
            head_sha="abc123",
            html_url="https://github.com/mglpsw/aiops-orchestrator/pull/1",
            files=[],
            checks=[
                review.CheckSummary(name="pytest", conclusion="success", status="completed", url=None),
                review.CheckSummary(name="eslint", conclusion="success", status="completed", url=None),
            ],
        )
        bundle = review._build_sanitized_bundle(ctx, [])
        # Prompt includes rule 10 about this
        assert "Checks (status real do GitHub API):" in bundle.content
        assert "pytest: passed" in bundle.content or "pytest:" in bundle.content

    def test_bad_review_fixture_scenario(self):
        """
        Fixture for the bad review scenario described in problem statement:
        - Diff not available
        - LLM said pytest FAILED, eslint FAILED, vitest FAILED
        - Invented possible API/contract failures
        - Invented validation, injection, duplicates, secrets risks
        - Created 'Achados críticos' with N/A
        - Ended saying no blocking problem (contradiction)

        After fixes, this scenario should:
        - Not claim test failures
        - Not invent technical issues
        - Not generate critical findings
        - Use needs_review verdict
        - Put limitations in 'Riscos não confirmados'
        - Declare 'Diff não disponível' and 'Não validei localmente'
        """
        # Simulate LLM returning bad findings
        bad_llm_findings = [
            review.Finding(
                severity="P1",
                file="backend/notifications.py",
                evidence="pytest falhou",
                risk="testes quebrados",
                recommendation="corrigir testes",
                rule_id="llm::P1::backend/notifications.py",
            ),
            review.Finding(
                severity="P1",
                file="frontend/Admin.tsx",
                evidence="eslint falhou",
                risk="código não segue padrões",
                recommendation="corrigir lint",
                rule_id="llm::P1::frontend/Admin.tsx",
            ),
            review.Finding(
                severity="P1",
                file="tests/admin.test.ts",
                evidence="vitest falhou",
                risk="testes quebrados",
                recommendation="corrigir testes",
                rule_id="llm::P1::tests/admin.test.ts",
            ),
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="possível falha de contrato da API",
                risk="pode quebrar integrações",
                recommendation="verificar contrato",
                rule_id="llm::P1::backend/api.py",
            ),
            review.Finding(
                severity="P1",
                file="backend/notifications.py",
                evidence="possível risco de injeção SQL",
                risk="pode permitir ataque",
                recommendation="sanitizar entrada",
                rule_id="llm::P1::backend/notifications.py",
            ),
        ]

        # No actual failed checks
        checks = [
            review.CheckSummary(name="pytest", conclusion="success", status="completed", url=None),
            review.CheckSummary(name="eslint", conclusion="success", status="completed", url=None),
        ]

        # Post-process with truncated diff
        processed = review._postprocess_llm_findings(bad_llm_findings, checks=checks, truncated=True)

        # All should be downgraded to P2
        for finding in processed:
            assert finding.severity == "P2", f"Expected P2, got {finding.severity} for {finding.evidence}"
            assert "[Rebaixado" in finding.evidence

        # Verify specific downgrades
        test_failure_findings = [f for f in processed if any(
            tool in f.evidence.lower() for tool in ["pytest", "eslint", "vitest"]
        )]
        for f in test_failure_findings:
            assert "falha de teste não comprovada" in f.evidence or "linguagem especulativa" in f.evidence

        speculative_findings = [f for f in processed if "possível" in f.evidence.lower()]
        for f in speculative_findings:
            assert "linguagem especulativa" in f.evidence or "diff truncado" in f.evidence or "detalhes técnicos não verificados" in f.evidence

        # Verdict should be needs_review, not changes_requested
        status = review.review_status([], processed)
        assert status == "needs_review", f"Expected needs_review, got {status}"

    def test_truncated_diff_keeps_p0_for_visible_real_secret(self):
        """Even with truncated diff, P0 should stay if finding contains real secret visible in evidence."""
        findings = [
            review.Finding(
                severity="P0",
                file=".env",
                evidence="OPENAI_API_KEY=sk-proj-abcdef1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890",
                risk="secret real exposto pode permitir acesso não autorizado",
                recommendation="remover chave da .env e usar variável de ambiente",
                rule_id="llm::P0::.env",
            )
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should KEEP P0 because real secret is visible
        assert len(processed) == 1
        assert processed[0].severity == "P0", f"Expected P0, got {processed[0].severity}"
        assert "[Rebaixado" not in processed[0].evidence

    def test_truncated_diff_keeps_p1_for_visible_destructive_command(self):
        """Even with truncated diff, P1 should stay if finding contains real destructive command."""
        findings = [
            review.Finding(
                severity="P1",
                file="deploy.sh",
                evidence="script executa 'docker compose -f prod.yml down' sem aprovação",
                risk="pode derrubar produção",
                recommendation="adicionar approval gate",
                rule_id="llm::P1::deploy.sh",
            )
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should KEEP P1 because destructive command is visible
        assert len(processed) == 1
        assert processed[0].severity == "P1", f"Expected P1, got {processed[0].severity}"
        assert "[Rebaixado" not in processed[0].evidence

    def test_truncated_diff_keeps_p1_for_visible_workflow_pull_request_target_exec(self):
        """Even with truncated diff, P1 should stay for pull_request_target with dangerous checkout/run."""
        findings = [
            review.Finding(
                severity="P1",
                file=".github/workflows/ci.yml",
                evidence="workflow usa pull_request_target com actions/checkout e executa código do PR",
                risk="permite execução de código arbitrário no contexto do repositório base",
                recommendation="usar pull_request ou adicionar validação de origem",
                rule_id="llm::P1::.github/workflows/ci.yml",
            )
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should KEEP P1 because workflow vulnerability is visible
        assert len(processed) == 1
        assert processed[0].severity == "P1", f"Expected P1, got {processed[0].severity}"
        assert "[Rebaixado" not in processed[0].evidence

    def test_truncated_diff_downgrades_speculative_security_claim(self):
        """Truncated diff should downgrade speculative security claims without concrete evidence."""
        findings = [
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="possivelmente vulnerável a XSS",
                risk="pode permitir injeção de script",
                recommendation="adicionar sanitização",
                rule_id="llm::P1::backend/api.py",
            )
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should downgrade to P2 because it's speculative
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado de P1 por linguagem especulativa]" in processed[0].evidence

    def test_truncated_diff_downgrades_invented_contract_claim(self):
        """Truncated diff should downgrade claims about broken contracts without evidence."""
        findings = [
            review.Finding(
                severity="P1",
                file="backend/routes.py",
                evidence="endpoint /api/users pode não existir mais",
                risk="contrato quebrado pode afetar clientes",
                recommendation="verificar documentação da API",
                rule_id="llm::P1::backend/routes.py",
            )
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=[], truncated=True)
        # Should downgrade because no concrete evidence
        assert len(processed) == 1
        assert processed[0].severity == "P2"
        assert "[Rebaixado" in processed[0].evidence

    def test_failed_check_still_keeps_p1_even_when_truncated(self):
        """Failed check evidence should preserve P1 even with truncated diff."""
        findings = [
            review.Finding(
                severity="P1",
                file="backend/api.py",
                evidence="mypy reportou erro de tipo no handler authenticate",
                risk="tipo incorreto pode causar runtime error",
                recommendation="corrigir anotação de tipo",
                rule_id="llm::P1::backend/api.py",
            )
        ]
        # Real failed check
        checks = [
            review.CheckSummary(name="mypy", conclusion="failure", status="completed", url="http://example.com")
        ]
        # Post-process with truncated=True
        processed = review._postprocess_llm_findings(findings, checks=checks, truncated=True)
        # Should KEEP P1 because failed check provides concrete evidence
        assert len(processed) == 1
        assert processed[0].severity == "P1", f"Expected P1, got {processed[0].severity}"
        assert "[Rebaixado" not in processed[0].evidence
