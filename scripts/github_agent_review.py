#!/usr/bin/env python3
"""Deterministic PR review with optional Agent Router assistance."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

DEFAULT_AGENT_ROUTER_BASE_URL = "https://api.ks-sm.net:9443"
DEFAULT_AGENT_ROUTER_TIMEOUT_SECONDS = 60
DEFAULT_AGENT_REVIEW_MODEL = "code"
ALLOWED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MAX_FINDINGS = 5
MAX_RECENT_COMMENTS = 20
MAX_FILES_ANALYZED = 8
MAX_PATCH_SNIPPET_CHARS = 180
MAX_BUNDLE_CHARS = 6000
MAX_COMMENT_CHARS = 5000
MAX_LLM_NOTE_CHARS = 320
MAX_LLM_ASK_RESPONSE_CHARS = 420
COMMENT_MARKER = "<!-- aiops-agent-review:v2 -->"
NO_BLOCKING_FINDINGS_RESPONSE = "Não encontrei problema bloqueante no diff analisado."
_COMMENT_403_LOG_MESSAGE = "GitHub token cannot write PR comments; wrote review to step summary instead"

_COMMAND_REVIEW = "/agent review"
_COMMAND_REVIEW_LLM = "/agent review llm"
_COMMAND_ASK = "/agent ask"
_PRIVATE_KEY_RE = re.compile(r"(?i)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")
_ENV_BLOCK_RE = re.compile(r"(?i)\b\.env\b")
_GITHUB_TOKEN_VALUE_RE = re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9_]{16,}\b|\bgithub_pat_[A-Za-z0-9_]{16,}\b|\bsk-[A-Za-z0-9-]{8,}\b")
_AUTH_BEARER_VALUE_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s\"'`<>;,#]{8,})")
_GENERIC_SECRET_ASSIGNMENT_RE = re.compile(
    r'(?i)(\b(?:AGENT_ROUTER_API_KEY|OPENAI_API_KEY|API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CLIENT_SECRET)\b\s*[:=]\s*(?:["\']?))([^\s"\'`<>;,#]{8,})(["\']?)'
)
_JSON_SECRET_ASSIGNMENT_RE = re.compile(
    r'(?i)("(?:authorization|api[_-]?key|token|secret|password|client_secret)"\s*:\s*")([^"]{8,})(")'
)
_COOKIE_SECRET_RE = re.compile(r"(?i)\b(?:cookie|set-cookie)\b\s*[:=]\s*[^=\s;]+=[^;\s]{8,}")
_URL_CREDENTIALS_RE = re.compile(r"(?i)\bhttps?://[^/\s:@]+:[^/\s@]+@[^/\s]+")
_PLACEHOLDER_SECRET_VALUES = {
    "example",
    "example-value",
    "placeholder",
    "redacted",
    "sample",
    "secret",
    "token",
    "value",
    "test-token",
    "test_token",
    "fake-token",
    "fake_token",
    "dummy-token",
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "local-only",
}
_PLACEHOLDER_SECRET_BRACKET_RE = re.compile(r"(?i)^\[(?:redacted|placeholder|secret|token)\]$")
_PLACEHOLDER_SECRET_CONTEXT_RE = re.compile(
    r"(?i)\b(?:os\.getenv|os\.environ\.get|os\.environ\s*\[|getenv)\s*\("
)
_PLACEHOLDER_SECRET_TOKEN_RE = re.compile(r"(?i)<[^>]+>")
_P1_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(?:docker\s+exec|docker\s+compose\s+(?:-f\s+\S+\s+)*(?:up|down|restart|pull|build)\b|systemctl\s+(?:restart|stop|start|reload)\b|git\s+(?:push|pull|checkout|reset|clean)\b|ssh\b|rm\s+-rf|chmod\s+777|curl\b.*\|\s*(?:bash|sh|zsh)\b)\b"
)
_P1_NEGATED_COMMAND_CONTEXT_RE = re.compile(
    r"(?i)\b(?:não|sem|proibido|proibida|proibidos|proibidas|evite|evitar|não adicionar|não usar|não usa|não executar|do not|don't)\b[\s\S]{0,80}\b(?:docker\s+exec|docker\s+compose|systemctl|git\s+(?:push|pull|checkout|reset|clean)|ssh|deploy)\b"
)
_P1_GUARD_RE = re.compile(r"(?i)\b(?:approval|audit|redact|fail-closed|allowlist)\b")
_P1_WORKFLOW_RE = re.compile(r"(?i)\bpull_request_target\b|\bpermissions:\s*[\s\S]*\bwrite-all\b|\bcontents:\s*write\b|\bactions:\s*write\b|\bpull-requests:\s*write\b")
_P1_RUNNER_RE = re.compile(r"(?i)\bshell\s*=\s*True\b|\bcreate_subprocess_shell\b|\bsubprocess\.run\([^)]*\bcommand\b|\bsubprocess\.run\([^)]*\bargv\b")
_P2_PATH_RE = re.compile(r"/opt/aiops-orchestrator")
_P2_TIMEOUT_RE = re.compile(r"(?i)\bsubprocess\.(?:run|call|check_output|popen)\(")
_P2_NEW_DEP_RE = re.compile(r"(?i)^\+\s*[A-Za-z0-9_.-]+(?:==|>=|<=|~=|!=|>|<)?[A-Za-z0-9*._-]*$")
_EXECUTABLE_COMMAND_CONTEXT_RE = re.compile(
    r"(?i)\b(?:run:|script:|subprocess|os\.system|shell\s*=\s*True|execstart\s*[:=]|execstop\s*[:=]|bash\s+-c|sh\s+-c|zsh\s+-c|python3?\s+-c|docker\s+compose|systemctl\b|git\s+(?:push|pull|checkout|reset|clean)\b)\b"
)
_META_COMMAND_REFERENCE_RE = re.compile(
    r"(?i)\b(?:re\.compile|_P1_DESTRUCTIVE_RE|DESTRUCTIVE_PATTERNS|_P1_NEGATED_COMMAND_CONTEXT_RE|assert\b|fixture\b|patch=|example\b|sample\b|dummy\b|fake\b|placeholder\b|tests/test_github_agent_review\.py)\b"
)
_ROUTER_TIMEOUT_ENV = "AGENT_ROUTER_TIMEOUT_SECONDS"

AGENTESCALA_REPO = "mglpsw/AgentEscala"
MAX_FINAL_FILE_CHARS = 2000

_AGENTESCALA_CONTEXT_LINES = [
    "=== Contexto obrigatório do AgentEscala ===",
    "- AgentEscala é sistema de escala médica.",
    "- Calendário é a interface operacional principal.",
    "- Backend mantém a regra canônica; frontend apenas agrupa visualmente.",
    "- CT104 é ambiente dev/staging. CT102 é produção e NÃO deve ser usado como staging.",
    "- 10-22H é sempre independente.",
    "- 12H DIA independente nunca some por causa de 24H.",
    "- 24H ocupado cobre sua própria metade DIA/NOITE.",
    "- 24H não cria VAGO 12H NOITE falso.",
    "- 10-22H nunca deve ser covered_by_24h.",
    "- Notificações consomem audit_events e não alteram a regra da escala.",
    "- PR frontend-only não deve sugerir mudanças de backend ou migration sem bug real.",
    "- PR de notificação não deve alterar coverage/swap/fill/exportação.",
    "- Não tocar CT102/produção.",
    "=== Fim do Contexto obrigatório do AgentEscala ===",
]

_SPECULATIVE_LANGUAGE_RE = re.compile(
    r"(?i)\b(?:possivelmente|talvez|pode\s+ser|não\s+está\s+claro|não\s+consegui\s+confirmar|parece)\b"
)


@dataclass(frozen=True)
class FileChange:
    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None = None


@dataclass(frozen=True)
class CheckSummary:
    name: str
    conclusion: str | None
    status: str | None
    url: str | None = None


@dataclass(frozen=True)
class Finding:
    severity: str
    file: str
    evidence: str
    risk: str
    recommendation: str
    rule_id: str
    related_files: tuple[str, ...] = ()


_CheckStatus = Literal[
    "passed",
    "failed",
    "skipped",
    "not_run",
    "timeout",
    "missing_command",
    "environment_error",
]

_DEFAULT_CHECK_TIMEOUT = 120


@dataclass
class CheckResult:
    """Result of a locally-executed validation command.

    Rules:
    - ``failed``            only when exit_code is non-zero.
    - ``missing_command``   when the executable is not found on PATH.
    - ``not_run``           when the check was never invoked (e.g. out of scope).
    - ``skipped``           when a docs-only PR makes a functional test irrelevant.
    - ``environment_error`` when a required env-var or dependency is absent.
    - ``timeout``           when the process exceeded the allotted time.
    """

    name: str
    command: list[str]
    cwd: str
    status: _CheckStatus
    exit_code: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    reason: str = ""


def run_check(
    name: str,
    command: list[str],
    *,
    cwd: str | None = None,
    timeout: int = _DEFAULT_CHECK_TIMEOUT,
    env_vars: list[str] | None = None,
) -> CheckResult:
    """Execute *command* and return a :class:`CheckResult` with evidence.

    Missing environment variables listed in *env_vars* yield
    ``environment_error`` without attempting to run the command.
    A missing executable yields ``missing_command``.
    A process timeout yields ``timeout``.
    Any non-zero exit code yields ``failed`` with captured output.
    """
    resolved_cwd = cwd or os.getcwd()

    # --- environment guard -------------------------------------------------
    if env_vars:
        missing = [v for v in env_vars if not os.getenv(v)]
        if missing:
            return CheckResult(
                name=name,
                command=command,
                cwd=resolved_cwd,
                status="environment_error",
                reason=f"missing env vars: {', '.join(missing)}",
            )

    # --- command availability guard ----------------------------------------
    executable = command[0] if command else ""
    if executable and not shutil.which(executable):
        return CheckResult(
            name=name,
            command=command,
            cwd=resolved_cwd,
            status="missing_command",
            reason=f"command not found on PATH: {executable}",
        )

    # --- execution ---------------------------------------------------------
    try:
        proc = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            text=True,
            cwd=resolved_cwd,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_raw = (exc.stdout or b"")
        stderr_raw = (exc.stderr or b"")
        stdout_text = stdout_raw.decode("utf-8", errors="replace") if isinstance(stdout_raw, bytes) else str(stdout_raw)
        stderr_text = stderr_raw.decode("utf-8", errors="replace") if isinstance(stderr_raw, bytes) else str(stderr_raw)
        return CheckResult(
            name=name,
            command=command,
            cwd=resolved_cwd,
            status="timeout",
            stdout_tail=_tail_lines(stdout_text, 20),
            stderr_tail=_tail_lines(stderr_text, 20),
            reason=f"timed out after {timeout}s",
        )
    except FileNotFoundError:
        return CheckResult(
            name=name,
            command=command,
            cwd=resolved_cwd,
            status="missing_command",
            reason=f"command not found: {executable}",
        )
    except OSError as exc:
        return CheckResult(
            name=name,
            command=command,
            cwd=resolved_cwd,
            status="environment_error",
            reason=str(exc),
        )

    status: _CheckStatus = "passed" if proc.returncode == 0 else "failed"
    return CheckResult(
        name=name,
        command=command,
        cwd=resolved_cwd,
        status=status,
        exit_code=proc.returncode,
        stdout_tail=_tail_lines(proc.stdout, 40),
        stderr_tail=_tail_lines(proc.stderr, 40),
    )


def _tail_lines(text: str, n: int) -> str:
    """Return the last *n* lines of *text*, stripped of leading blank lines."""
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


@dataclass
class ReviewContext:
    owner: str
    repo: str
    issue_number: int
    author: str
    association: str | None
    pr_number: int | None
    title: str | None
    body: str | None
    base_ref: str | None
    head_ref: str | None
    head_sha: str | None
    html_url: str | None
    files: list[FileChange] = field(default_factory=list)
    checks: list[CheckSummary] = field(default_factory=list)
    recent_comments: list[dict[str, Any]] = field(default_factory=list)
    command_mode: str = "none"


@dataclass(frozen=True)
class ReviewBundle:
    content: str
    diff_chars: int
    files_count: int
    truncated: bool


@dataclass(frozen=True)
class LLMReview:
    findings: list[Finding] = field(default_factory=list)
    notes: str | None = None
    warning: str | None = None


class AgentRouterError(RuntimeError):
    """Base error for Agent Router calls."""


class AgentRouterDisabledError(AgentRouterError):
    """Raised when the LLM path is not enabled or not fully configured."""


class AgentRouterTimeoutError(AgentRouterError):
    """Raised when the Agent Router call times out."""


class AgentRouterUnavailableError(AgentRouterError):
    """Raised for DNS/TLS/connection failures and 5xx responses."""


class AgentRouterAuthError(AgentRouterError):
    """Raised for 401/403 responses."""


class AgentRouterRateLimitError(AgentRouterError):
    """Raised for 429 responses."""


class AgentRouterResponseError(AgentRouterError):
    """Raised when the router response cannot be normalized."""


class GitHubClient:
    def __init__(self, token: str, repository: str, api_base_url: str | None = None) -> None:
        self.token = token
        self.repository = repository
        self.api_base_url = (api_base_url or "https://api.github.com").rstrip("/")

    def get_json(self, path: str) -> Any:
        return self._request_json("GET", path)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json("POST", path, payload)

    def patch_json(self, path: str, payload: dict[str, Any]) -> Any:
        return self._request_json("PATCH", path, payload)

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.api_base_url}{path}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "aiops-orchestrator-agent-review/2.0",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise GitHubAPIError(method=method, path=path, code=exc.code) from exc
        return json.loads(raw) if raw else {}


class GitHubAPIError(RuntimeError):
    """Raised when a GitHub API call fails."""

    def __init__(self, *, method: str, path: str, code: int) -> None:
        super().__init__(f"GitHub API request failed: {method} {path} -> {code}")
        self.method = method
        self.path = path
        self.code = code


def _redact_sensitive_text(text: str) -> str:
    redacted = text
    redacted = _GITHUB_TOKEN_VALUE_RE.sub("[REDACTED]", redacted)
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", redacted)
    redacted = _ENV_BLOCK_RE.sub("[REDACTED ENV]", redacted)
    redacted = _AUTH_BEARER_VALUE_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _JSON_SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = _GENERIC_SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]\3", redacted)
    redacted = _COOKIE_SECRET_RE.sub("[REDACTED COOKIE]", redacted)
    redacted = _URL_CREDENTIALS_RE.sub(lambda match: re.sub(r"://[^/\s:@]+:[^/\s@]+@", "://[REDACTED]@", match.group(0)), redacted)
    redacted = re.sub(r"(?i)(x-api-key\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", redacted)
    redacted = re.sub(r'(?i)"(?:authorization|api[_-]?key|token|secret|password|client_secret)"\s*:\s*"[^"]*"', '"[REDACTED]":"[REDACTED]"', redacted)
    return redacted


def _strip_diff_prefix(line: str) -> str:
    if not line:
        return line
    if line[0] in "+- ":
        return line[1:].lstrip()
    return line.lstrip()


def _placeholder_secret_value(value: str) -> bool:
    normalized = value.strip().strip('"\'`<>')
    lowered = normalized.lower()
    if not lowered:
        return True
    if _PLACEHOLDER_SECRET_BRACKET_RE.fullmatch(normalized):
        return True
    if lowered in _PLACEHOLDER_SECRET_VALUES:
        return True
    if _PLACEHOLDER_SECRET_CONTEXT_RE.search(normalized):
        return True
    if _PLACEHOLDER_SECRET_TOKEN_RE.fullmatch(normalized):
        return True
    return False


def _looks_like_private_key_placeholder(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    lowered = normalized.lower()
    return any(
        marker in lowered
        for marker in (
            "[redacted private key]",
            "<private key>",
            "fake_private_key",
            "dummy private key",
            "example private key",
            "placeholder private key",
        )
    )


def _is_executable_command_context(text: str) -> bool:
    return bool(_EXECUTABLE_COMMAND_CONTEXT_RE.search(text))


def _is_meta_command_reference(file: FileChange, body: str) -> bool:
    lowered = body.lower()
    if file.path == "scripts/github_agent_review.py" and _META_COMMAND_REFERENCE_RE.search(body):
        return True
    if file.path.startswith("tests/") and not _is_executable_command_context(body):
        return True
    if file.path == "scripts/github_agent_review.py" and not _is_executable_command_context(body):
        return bool(_META_COMMAND_REFERENCE_RE.search(body)) or ("docker exec" in lowered and "re.compile" in lowered)
    return False


def _is_negated_command_context(text: str) -> bool:
    return bool(_P1_NEGATED_COMMAND_CONTEXT_RE.search(text))


def _is_real_destructive_command(text: str) -> bool:
    lowered = text.lstrip().lower()
    if not lowered or lowered.startswith("#"):
        return False
    return bool(_P1_DESTRUCTIVE_RE.search(text))


def _secret_finding_for_line(file: FileChange, line: str) -> Finding | None:
    body = _strip_diff_prefix(line)
    if not body:
        return None
    if _looks_like_private_key_placeholder(body):
        return None
    if _placeholder_secret_value(body):
        return None
    for rule_id, pattern, risk, recommendation in (
        (
            "authorization_bearer_secret",
            _AUTH_BEARER_VALUE_RE,
            "Authorization header contains a bearer token-like value.",
            "Move the credential to a secret store and keep it out of docs or code.",
        ),
        (
            "generic_secret_assignment",
            _GENERIC_SECRET_ASSIGNMENT_RE,
            "A secret-like assignment contains a real value.",
            "Move the value to a secret manager or GitHub secret and redact it from code.",
        ),
        (
            "json_secret_assignment",
            _JSON_SECRET_ASSIGNMENT_RE,
            "A JSON secret field contains a real value.",
            "Move the value to a secret manager or GitHub secret and redact it from code.",
        ),
        (
            "cookie_secret",
            _COOKIE_SECRET_RE,
            "A cookie-like header contains a real value.",
            "Remove the cookie from the diff or replace it with a redacted placeholder.",
        ),
        (
            "url_credentials",
            _URL_CREDENTIALS_RE,
            "A URL with embedded credentials appears in the patch.",
            "Remove embedded credentials and use a secret-managed credential flow.",
        ),
    ):
        match = pattern.search(body)
        if not match:
            continue
        value = match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(match.lastindex or 0)
        if _placeholder_secret_value(value):
            continue
        return Finding(
            severity="P1",
            file=file.path,
            evidence=_redact_sensitive_text(_truncate_text(match.group(0), 200)),
            risk=risk,
            recommendation=recommendation,
            rule_id=rule_id,
        )
    token_match = _GITHUB_TOKEN_VALUE_RE.search(body)
    if token_match:
        if _placeholder_secret_value(body):
            return None
        return Finding(
            severity="P1",
            file=file.path,
            evidence=_redact_sensitive_text(_truncate_text(token_match.group(0), 200)),
            risk="A well-known token format appears in the patch.",
            recommendation="Rotate the token and keep it out of source control.",
            rule_id="well_known_token",
        )
    return None


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _normalize_patch_text(patch: str | None) -> list[str]:
    if not patch:
        return []
    lines = []
    for raw in patch.splitlines():
        stripped = raw.rstrip()
        if not stripped or stripped.startswith("@@"):
            continue
        lines.append(stripped)
    return lines


def _looks_like_review_command_line(line: str) -> bool:
    return line == _COMMAND_REVIEW or line == _COMMAND_REVIEW_LLM or line.startswith("/agent review") or line.startswith(_COMMAND_ASK)


def parse_agent_review_command(body: str) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == _COMMAND_REVIEW:
            return "review"
        if line == _COMMAND_REVIEW_LLM:
            return "review_llm"
        if line.startswith(_COMMAND_ASK):
            return "ask"
        if line.startswith("/agent review"):
            return "unknown"
        if line.startswith("/agent"):
            return "unknown"
    return "none"


def extract_agent_ask_question(body: str) -> str:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith(_COMMAND_ASK):
            continue
        question = line[len(_COMMAND_ASK) :].strip()
        return _truncate_text(question, 1000)
    return ""


def _allowed_users_from_env(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_authorized(association: str | None, login: str | None, allowed_users: str | None) -> bool:
    if association and association.upper() in ALLOWED_ASSOCIATIONS:
        return True
    return bool(login and login.lower() in _allowed_users_from_env(allowed_users))


def is_pull_request_payload(payload: dict[str, Any]) -> bool:
    issue = payload.get("issue")
    return isinstance(issue, dict) and isinstance(issue.get("pull_request"), dict)


def _repo_parts(repository: str) -> tuple[str, str]:
    if "/" not in repository:
        raise ValueError("GITHUB_REPOSITORY must be in owner/repo form")
    return repository.split("/", 1)


def _paginate_list(client: GitHubClient, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        response = client.get_json(f"{path}{separator}per_page=100&page={page}")
        if not isinstance(response, list):
            return items
        items.extend(item for item in response if isinstance(item, dict))
        if len(response) < 100:
            return items
        page += 1


def _file_area(path: str) -> str:
    lowered = path.lower()
    if lowered.startswith(".github/workflows/"):
        return "workflow"
    if lowered.startswith("scripts/"):
        return "scripts"
    if lowered.startswith("deploy/"):
        return "deploy"
    if lowered.startswith("tests/"):
        return "tests"
    if lowered.startswith("docs/security") or lowered.startswith("docs/actions") or lowered.startswith("docs/github"):
        return "docs"
    if lowered in {"app/core/config.py", "app/agent_router/services/action_runner.py", "config/actions.yaml"}:
        return "security-critical"
    if lowered.startswith("requirements") or lowered.endswith("pyproject.toml"):
        return "dependencies"
    return "other"


def classify_file_risk(file: FileChange) -> dict[str, str | bool]:
    area = _file_area(file.path)
    sensitive = area in {"workflow", "scripts", "deploy", "security-critical", "tests", "dependencies"}
    return {"area": area, "sensitive": sensitive}


def _path_evidence(file: FileChange, needle: str) -> str:
    return f"{file.path}: {needle}"


def _patch_snippet(file: FileChange, pattern: re.Pattern[str]) -> str:
    lines = _normalize_patch_text(file.patch)
    for line in lines:
        if pattern.search(line):
            return _truncate_text(line, MAX_PATCH_SNIPPET_CHARS)
    if lines:
        return _truncate_text(lines[0], MAX_PATCH_SNIPPET_CHARS)
    return _truncate_text((file.patch or "").replace("\n", " "), MAX_PATCH_SNIPPET_CHARS)


def _emit(candidates: list[Finding], *, severity: str, file: FileChange, rule_id: str, evidence: str, risk: str, recommendation: str) -> None:
    candidates.append(
        Finding(
            severity=severity,
            file=file.path,
            evidence=_redact_sensitive_text(_truncate_text(evidence, 200)),
            risk=risk,
            recommendation=recommendation,
            rule_id=rule_id,
        )
    )


def scan_patch_for_findings(file: FileChange, *, scanner_meta_test_coverage: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    patch = file.patch or ""
    lines = _normalize_patch_text(file.patch)
    normalized_patch = "\n".join(_strip_diff_prefix(line) for line in lines)
    patch_lower = patch.lower()
    risk = classify_file_risk(file)

    if file.path.startswith(".github/workflows/") and "pull_request_target" in patch_lower and (
        "actions/checkout" in patch_lower or " run:" in patch_lower or "\n+run:" in patch_lower or "uses:" in patch_lower
    ):
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="workflow_pull_request_target_exec",
            evidence=_patch_snippet(file, re.compile(r"(?i)\bpull_request_target\b")),
            risk="Workflow could execute untrusted PR context with elevated token scope.",
            recommendation="Use `pull_request` and keep this review path API-only.",
        )

    if file.path.startswith(".github/workflows/") and _P1_WORKFLOW_RE.search(patch):
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="workflow_write_permissions",
            evidence=_patch_snippet(file, _P1_WORKFLOW_RE),
            risk="Broad workflow permissions can expose the token to unnecessary write scope.",
            recommendation="Keep permissions minimal; only grant the exact scopes needed.",
        )

    if file.path == "app/agent_router/services/action_runner.py" and _P1_RUNNER_RE.search(patch):
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="runner_arbitrary_command",
            evidence=_patch_snippet(file, _P1_RUNNER_RE),
            risk="Runner may accept shell text or arbitrary command execution.",
            recommendation="Keep the runner fixed-argv, allowlisted, and fail-closed.",
        )

    if file.path in {"app/core/config.py", "app/agent_router/services/action_runner.py", "config/actions.yaml"} and any(
        line.startswith("-") and _P1_GUARD_RE.search(line) for line in lines
    ):
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="security_gate_removed",
            evidence=_patch_snippet(file, _P1_GUARD_RE),
            risk="Approval, audit, redaction, or fail-closed behavior appears to be removed.",
            recommendation="Restore the gate and keep the change fail-closed.",
        )

    if any(line.startswith("-") and _P1_GUARD_RE.search(line) for line in lines) and risk["sensitive"]:
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="security_gate_removed_generic",
            evidence=_patch_snippet(file, _P1_GUARD_RE),
            risk="A core safety term was removed from a sensitive file.",
            recommendation="Keep approval, audit, redaction, and allowlist guarantees intact.",
        )

    if risk["area"] in {"workflow", "scripts", "deploy", "security-critical"}:
        for line in lines:
            if not line.startswith("+"):
                continue
            body = _strip_diff_prefix(line)
            if _is_meta_command_reference(file, body):
                continue
            if _is_real_destructive_command(body) and not _is_negated_command_context(body):
                _emit(
                    findings,
                    severity="P1",
                    file=file,
                    rule_id="destructive_command",
                    evidence=_truncate_text(_redact_sensitive_text(body), MAX_PATCH_SNIPPET_CHARS),
                    risk="Destructive command detected in the diff.",
                    recommendation="Replace it with a read-only check or remove it entirely.",
                )
                break

    secret_finding: Finding | None = None
    private_key_match = _PRIVATE_KEY_RE.search(normalized_patch) if normalized_patch else None
    if private_key_match:
        if _looks_like_private_key_placeholder(normalized_patch):
            private_key_match = None
        else:
            secret_finding = Finding(
                severity="P1",
                file=file.path,
                evidence=_redact_sensitive_text(_truncate_text(private_key_match.group(0), 200)),
                risk="A private key block appears in the patch.",
                recommendation="Remove the key from the diff and rotate it immediately.",
                rule_id="private_key_block",
            )
    if private_key_match is None:
        pass
    if secret_finding is None:
        for line in lines:
            secret_finding = _secret_finding_for_line(file, line)
            if secret_finding:
                break
    if secret_finding:
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id=secret_finding.rule_id,
            evidence=secret_finding.evidence,
            risk=secret_finding.risk,
            recommendation=secret_finding.recommendation,
        )

    if file.path.startswith(("tests/", ".github/workflows/")) and _P2_PATH_RE.search(patch) and not (
        "REPO_ROOT" in patch or "tmp_path" in patch or 'Path("/opt")' in patch or "Path('/opt')" in patch
    ):
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="hardcoded_repo_root",
            evidence=_path_evidence(file, "/opt/aiops-orchestrator"),
            risk="Hardcoded repo path can break portability in CI and GitHub Actions.",
            recommendation="Use the configured repo root helper instead of a fixed path.",
        )

    if risk["area"] in {"workflow", "scripts", "deploy", "security-critical"} and _P2_TIMEOUT_RE.search(patch) and "timeout=" not in patch_lower:
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="missing_timeout",
            evidence=_patch_snippet(file, _P2_TIMEOUT_RE),
            risk="Subprocess work without a timeout can hang CI or a review job.",
            recommendation="Add an explicit timeout and keep the path fail-closed.",
        )

    if (
        risk["area"] in {"workflow", "scripts", "security-critical"}
        and not (scanner_meta_test_coverage and file.path == "scripts/github_agent_review.py")
        and any(word in patch_lower for word in ("redact", "truncate", "allowlist", "approval", "audit"))
        and any(line.startswith("-") for line in lines)
    ):
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="safety_behavior_regression",
            evidence=_patch_snippet(file, re.compile(r"(?i)\b(redact|truncate|allowlist|approval|audit)\b")),
            risk="A safety-related behavior appears to be modified in a sensitive file.",
            recommendation="Keep truncation, redaction, and approval gates intact and covered by tests.",
        )

    if file.path.startswith("requirements") or file.path.endswith("pyproject.toml"):
        for line in lines:
            if line.startswith("+") and _P2_NEW_DEP_RE.match(line):
                _emit(
                    findings,
                    severity="P2",
                    file=file,
                    rule_id="new_dependency",
                    evidence=_truncate_text(line, MAX_PATCH_SNIPPET_CHARS),
                    risk="New dependency should be pinned and documented.",
                    recommendation="Pin the dependency version and add a short rationale in the PR.",
                )
                break

    return findings


_FUNCTIONAL_TEST_CHECK_RE = re.compile(
    r"(?i)\b(pytest|vitest|jest|eslint|mypy|flake8|ruff|build|test|lint|check)\b"
)


def is_docs_only(changed_files: list[str]) -> bool:
    """Return True when every changed file is documentation or changelog only."""
    if not changed_files:
        return False
    return all(
        path.startswith("docs/")
        or path.endswith(".md")
        or path == "CHANGELOG.md"
        for path in changed_files
    )


def _check_is_functional_test(name: str) -> bool:
    """Return True when a check name looks like a functional test or linter."""
    return bool(_FUNCTIONAL_TEST_CHECK_RE.search(name))


def _check_status_label(check: CheckSummary) -> str:
    """Map a GitHub check conclusion/status to a normalised label.

    Labels: passed | failed | skipped | not_run | timeout | environment_error
    """
    conclusion = (check.conclusion or "").lower()
    status = (check.status or "").lower()
    if conclusion in {"success", "neutral"}:
        return "passed"
    if conclusion == "skipped":
        return "skipped"
    if conclusion == "failure":
        return "failed"
    if conclusion == "timed_out":
        return "timeout"
    if conclusion == "cancelled":
        return "not_run"
    if conclusion in {"action_required", "startup_failure"}:
        return "environment_error"
    if status in {"queued", "in_progress"}:
        return "not_run"
    return "not_run"


def scan_checks_for_findings(checks: list[CheckSummary], *, docs_only: bool = False) -> list[Finding]:
    """Emit a P1 only when a check has conclusion=failure.

    Cancelled, timed-out, and environment checks are NOT the same as failure:
    they must not be reported as "FALHOU" in the review comment.
    For docs-only PRs, functional-test failures are skipped — the tests were
    not meant to catch documentation changes.
    """
    findings: list[Finding] = []
    for check in checks:
        label = _check_status_label(check)
        if label != "failed":
            continue
        if docs_only and _check_is_functional_test(check.name):
            # Docs-only PR: a functional test failure is not evidence of a bug
            # introduced by this PR — mark as not_run in the table, not P1.
            continue
        evidence = f"{check.name}: conclusion=failure"
        if check.url:
            evidence += f" — {check.url}"
        _emit(
            findings,
            severity="P1",
            file=FileChange(path="CI / Checks", status="failure", additions=0, deletions=0, patch=None),
            rule_id=f"check_failed::{check.name}",
            evidence=evidence,
            risk=f"CI check '{check.name}' retornou conclusion=failure.",
            recommendation=f"Verifique os logs do check '{check.name}' ({check.url or 'sem URL'}) antes de fazer merge.",
        )
        break
    return findings


def scan_pr_level_gaps(files: list[FileChange]) -> list[Finding]:
    findings: list[Finding] = []
    has_tests = any(_file_area(file.path) == "tests" for file in files)
    has_docs = any(_file_area(file.path) == "docs" for file in files)
    sensitive_changes = [file for file in files if _file_area(file.path) in {"workflow", "scripts", "deploy", "security-critical", "dependencies"}]
    if sensitive_changes and not has_tests:
        file = sensitive_changes[0]
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="missing_test_coverage",
            evidence=f"{file.path}: changed without a paired test file",
            risk="Sensitive changes should carry a matching test to prevent regressions.",
            recommendation="Add or update tests that cover the changed contract.",
        )
    if any(_file_area(file.path) == "workflow" for file in files) and not has_tests and not has_docs:
        file = next(file for file in files if _file_area(file.path) == "workflow")
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="workflow_docs_gap",
            evidence=f"{file.path}: workflow changed without docs",
            risk="Workflow and security-contract changes are harder to review without docs.",
            recommendation="Update the docs that explain the new workflow contract.",
        )
    if any(file.path == "app/agent_router/services/action_runner.py" for file in files) and not has_tests:
        file = next(file for file in files if file.path == "app/agent_router/services/action_runner.py")
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="runner_tests_gap",
            evidence="action_runner.py changed without a matching test",
            risk="Runner changes are security-sensitive and need direct coverage.",
            recommendation="Add a focused test for the updated runner behavior.",
        )
    if any(file.path == "config/actions.yaml" for file in files) and not has_tests:
        file = next(file for file in files if file.path == "config/actions.yaml")
        _emit(
            findings,
            severity="P2",
            file=file,
            rule_id="actions_catalog_test_gap",
            evidence="config/actions.yaml changed without a matching test",
            risk="Action catalog changes alter the allowlist and should be covered.",
            recommendation="Add or update a catalog validation test.",
        )
    return findings


def summarize_pr_scope(files: list[FileChange]) -> str:
    areas: list[str] = []
    for file in files:
        area = _file_area(file.path)
        if area not in areas:
            areas.append(area)
    if not areas:
        return "sem arquivos"
    return ", ".join(areas[:5])


def _severity_rank(severity: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(severity, 9)


def _finding_key(finding: Finding) -> tuple[str, str, str]:
    return finding.rule_id, finding.severity, finding.recommendation


def _merge_related_files(existing: tuple[str, ...], extra: str) -> tuple[str, ...]:
    if extra in existing:
        return existing
    return existing + (extra,)


def rank_findings(findings: list[Finding]) -> list[Finding]:
    grouped: dict[tuple[str, str, str], Finding] = {}
    for finding in findings:
        key = _finding_key(finding)
        if key not in grouped:
            grouped[key] = finding
            continue
        current = grouped[key]
        if finding.file != current.file:
            grouped[key] = Finding(
                severity=current.severity,
                file=current.file,
                evidence=current.evidence,
                risk=current.risk,
                recommendation=current.recommendation,
                rule_id=current.rule_id,
                related_files=_merge_related_files(current.related_files, finding.file),
            )
    ranked = sorted(grouped.values(), key=lambda item: (_severity_rank(item.severity), item.file, item.rule_id))
    high_priority = [item for item in ranked if item.severity in {"P1", "P2"}]
    if high_priority:
        return high_priority[:MAX_FINDINGS]
    return ranked[:MAX_FINDINGS]


def build_deterministic_findings(files: list[FileChange], checks: list[CheckSummary]) -> list[Finding]:
    candidates: list[Finding] = []
    scanner_meta_test_coverage = any(file.path == "tests/test_github_agent_review.py" for file in files)
    docs_only = is_docs_only([f.path for f in files])
    for file in files:
        candidates.extend(scan_patch_for_findings(file, scanner_meta_test_coverage=scanner_meta_test_coverage))
    candidates.extend(scan_checks_for_findings(checks, docs_only=docs_only))
    candidates.extend(scan_pr_level_gaps(files))
    return rank_findings(candidates)


def review_status(deterministic_findings: list[Finding], llm_findings: list[Finding] | None = None) -> str:
    if any(finding.severity in {"P0", "P1"} for finding in deterministic_findings):
        return "changes_requested"
    if any(finding.severity == "P2" for finding in deterministic_findings):
        return "needs_review"
    filtered_llm_findings = _filter_placeholder_llm_findings(llm_findings or [])
    if filtered_llm_findings:
        if any(finding.severity in {"P0", "P1"} for finding in filtered_llm_findings):
            # Only promote to changes_requested if at least one P0/P1 uses non-speculative language.
            has_confirmed = any(
                finding.severity in {"P0", "P1"}
                and not _has_speculative_language(
                    f"{finding.evidence} {finding.risk} {finding.recommendation}"
                )
                for finding in filtered_llm_findings
            )
            return "changes_requested" if has_confirmed else "needs_review"
        if any(finding.severity == "P2" for finding in filtered_llm_findings):
            return "needs_review"
    return "approved"


def _render_checks_table(checks: list[CheckSummary], *, docs_only: bool = False) -> list[str]:
    """Render a markdown table of CI checks with normalised status labels.

    Rules:
    - failed    → only when conclusion=failure
    - timeout   → conclusion=timed_out
    - not_run   → cancelled / queued / in_progress or no conclusion
    - skipped   → conclusion=skipped OR docs-only PR + functional test
    - environment_error → action_required / startup_failure
    - passed    → success / neutral
    """
    if not checks:
        return []
    lines: list[str] = ["", "## Validações de CI", ""]
    lines.append("| Check | Status | Evidência |")
    lines.append("|---|---|---|")
    for check in checks[:10]:
        label = _check_status_label(check)
        if docs_only and _check_is_functional_test(check.name) and label == "failed":
            display = "skipped"
            evidence = "PR documental — não computado como falha"
        elif label == "failed":
            url_part = f" — [{check.url}]({check.url})" if check.url else ""
            display = "**failed**"
            evidence = f"conclusion=failure{url_part}"
        elif label == "timeout":
            url_part = f" — [{check.url}]({check.url})" if check.url else ""
            display = "timeout"
            evidence = f"conclusion=timed_out{url_part}"
        elif label == "environment_error":
            url_part = f" — [{check.url}]({check.url})" if check.url else ""
            display = "environment_error"
            evidence = f"conclusion={check.conclusion or 'startup_failure'}{url_part}"
        elif label == "not_run":
            display = "not_run"
            evidence = f"status={check.status or 'cancelled'}"
        elif label == "skipped":
            display = "skipped"
            evidence = "conclusion=skipped"
        else:
            display = label
            evidence = "—"
        lines.append(f"| {check.name} | {display} | {evidence} |")
    return lines


def _render_findings_block(title: str, findings: list[Finding]) -> list[str]:
    if not findings:
        return [f"### {title}", "- Nenhum encontrado."]
    lines = [f"### {title}"]
    for finding in findings:
        file_text = finding.file
        if finding.related_files:
            file_text = f"{file_text} (+{len(finding.related_files)} outros)"
        lines.extend(
            [
                f"- Severidade: {finding.severity}",
                f"  Arquivo: {file_text}",
                f"  Evidência: {finding.evidence}",
                f"  Risco: {finding.risk}",
                f"  Recomendação: {finding.recommendation}",
            ]
        )
    return lines


def render_review(
    findings: list[Finding],
    checks: list[CheckSummary],
    pr_context: ReviewContext,
    *,
    llm_mode: bool,
    llm_warning: str | None = None,
    llm_notes: str | None = None,
    llm_findings: list[Finding] | None = None,
) -> str:
    llm_findings = _filter_placeholder_llm_findings(llm_findings or [])
    status = review_status(findings, llm_findings=llm_findings)
    _pr_docs_only = is_docs_only([f.path for f in pr_context.files])

    all_findings = list(findings) + list(llm_findings)
    p0_all = [f for f in all_findings if f.severity == "P0"]
    p1_all = [f for f in all_findings if f.severity == "P1"]
    p2_all = [f for f in all_findings if f.severity == "P2"]
    p3_all = [f for f in all_findings if f.severity == "P3"]

    lines = [
        COMMENT_MARKER,
        "",
        "## 🤖 Agent Review",
        "",
        "### Veredito",
        f"- Status: {status}",
        "",
        "### Escopo entendido",
        f"- PR: {f'#{pr_context.pr_number}' if pr_context.pr_number is not None else 'n/a'}",
        f"- Autor: {pr_context.author}",
        f"- Base: {pr_context.base_ref or 'n/a'} → Head: {pr_context.head_ref or 'n/a'}",
        f"- Commit analisado: {pr_context.head_sha or 'n/a'}",
        f"- Modo: {'revisão determinística + Agent Router' if llm_mode else 'revisão determinística'}",
        f"- Escopo: {summarize_pr_scope(pr_context.files)}",
        f"- PR documental: {'sim' if _pr_docs_only else 'não'}",
        "- Código do PR executado: não",
        "",
        "### Achados confirmados",
    ]

    confirmed = p0_all + p1_all
    if confirmed:
        if p0_all:
            lines.extend(_render_findings_block("P0 — Bloqueadores críticos", p0_all))
        if p1_all:
            lines.extend(_render_findings_block("P1 — Bloqueadores", p1_all))
    else:
        lines.append("Nenhum achado confirmado.")

    lines.extend(["", "### Riscos não confirmados"])
    if p2_all:
        lines.extend(_render_findings_block("P2 — Riscos prováveis", p2_all))
    else:
        lines.append("Nenhum risco identificado.")

    if p3_all:
        lines.extend(["", "### Sugestões"])
        lines.extend(_render_findings_block("P3 — Sugestões", p3_all))

    lines.append("")
    lines.append("### Testes/checks observados")
    if checks:
        lines.extend(_render_checks_table(checks, docs_only=_pr_docs_only))
    else:
        lines.append("- Nenhum check reportado.")

    llm_note_lines: list[str] = []
    if llm_warning:
        llm_note_lines.append(f"- {llm_warning}")
    if llm_notes:
        llm_note_lines.append(f"- {_truncate_text(_redact_sensitive_text(llm_notes), MAX_LLM_NOTE_CHARS)}")
    if llm_note_lines:
        lines.extend(["", "## Notas do LLM", *llm_note_lines])

    lines.extend(
        [
            "",
            "### O que NÃO deve mudar",
            "- Este agente analisou metadados e diff via GitHub API. Ele não executou código do PR, não fez checkout da branch do PR para execução e não teve permissão de deploy.",
        ]
    )
    rendered = "\n".join(lines).rstrip() + "\n"
    if len(rendered) > MAX_COMMENT_CHARS:
        rendered = _truncate_text(rendered, MAX_COMMENT_CHARS - 80) + "\n\n> Comentário truncado por limite de tamanho."
    return rendered


def _load_event_payload(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fetch_review_context(client: GitHubClient, payload: dict[str, Any], command_mode: str) -> ReviewContext:
    owner, repo = _repo_parts(client.repository)
    issue = payload["issue"]
    comment = payload["comment"]
    issue_number = int(issue["number"])
    author = str(comment.get("user", {}).get("login", "unknown"))
    association = comment.get("author_association")
    if not is_pull_request_payload(payload):
        return ReviewContext(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            author=author,
            association=association,
            pr_number=None,
            title=str(issue.get("title") or ""),
            body=str(issue.get("body") or ""),
            base_ref=None,
            head_ref=None,
            head_sha=None,
            html_url=str(issue.get("html_url") or ""),
            command_mode=command_mode,
        )

    pr = client.get_json(f"/repos/{owner}/{repo}/pulls/{issue_number}")
    if not isinstance(pr, dict):
        raise RuntimeError("Unexpected PR payload from GitHub API")
    files = [
        FileChange(
            path=str(item.get("filename") or item.get("path") or ""),
            status=str(item.get("status") or "unknown"),
            additions=int(item.get("additions") or 0),
            deletions=int(item.get("deletions") or 0),
            patch=item.get("patch"),
        )
        for item in _paginate_list(client, f"/repos/{owner}/{repo}/pulls/{issue_number}/files")
    ]
    checks: list[CheckSummary] = []
    head_sha = str(pr.get("head", {}).get("sha") or "")
    if head_sha:
        try:
            check_payload = client.get_json(f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs")
        except RuntimeError:
            check_payload = {}
        if isinstance(check_payload, dict):
            for item in check_payload.get("check_runs") or []:
                if not isinstance(item, dict):
                    continue
                checks.append(
                    CheckSummary(
                        name=str(item.get("name") or "check"),
                        conclusion=item.get("conclusion"),
                        status=item.get("status"),
                        url=item.get("html_url"),
                    )
                )
    recent_comments = _paginate_list(client, f"/repos/{owner}/{repo}/issues/{issue_number}/comments")[-MAX_RECENT_COMMENTS:]
    return ReviewContext(
        owner=owner,
        repo=repo,
        issue_number=issue_number,
        author=author,
        association=association,
        pr_number=int(pr.get("number") or issue_number),
        title=str(pr.get("title") or issue.get("title") or ""),
        body=str(pr.get("body") or issue.get("body") or ""),
        base_ref=str(pr.get("base", {}).get("ref") or ""),
        head_ref=str(pr.get("head", {}).get("ref") or ""),
        head_sha=head_sha or None,
        html_url=str(pr.get("html_url") or issue.get("html_url") or ""),
        files=files,
        checks=checks,
        recent_comments=recent_comments,
        command_mode=command_mode,
    )


def _is_patch_snippet_truncated(patch: str | None) -> bool:
    if not patch:
        return False
    normalized_lines = _normalize_patch_text(patch)
    if len(normalized_lines) > 6:
        return True
    snippet = " | ".join(normalized_lines[:6]) if normalized_lines else patch.replace("\n", " ")
    return len(_redact_sensitive_text(snippet)) > MAX_PATCH_SNIPPET_CHARS


def _build_sanitized_bundle(
    pr_context: ReviewContext,
    deterministic_findings: list[Finding],
    *,
    client: "GitHubClient | None" = None,
) -> ReviewBundle:
    _docs_only = is_docs_only([f.path for f in pr_context.files[:MAX_FILES_ANALYZED]])
    diff_chars = sum(len(file.patch or "") for file in pr_context.files)
    truncated = len(pr_context.files) > MAX_FILES_ANALYZED or any(_is_patch_snippet_truncated(file.patch) for file in pr_context.files)
    _agentescala = _is_agentescala_repo(pr_context.owner, pr_context.repo)
    lines = [
        f"Título do PR: {_redact_sensitive_text(pr_context.title or '')}",
        f"Descrição do PR: {_redact_sensitive_text(_truncate_text(pr_context.body or '', 600))}",
        f"Escopo: {summarize_pr_scope(pr_context.files)}",
        f"PR documental: {'sim — apenas docs/ e .md alterados' if _docs_only else 'não'}",
        f"Metadados do diff: diff_chars={diff_chars}, files_count={len(pr_context.files)}, truncated={'true' if truncated else 'false'}",
    ]
    if truncated:
        lines.extend([
            "ATENÇÃO DIFF TRUNCADO: O diff/contexto disponível está truncado; não classifique como P0/P1 achados que dependem de arquivo final completo, lint, build ou checks.",
            "ATENÇÃO DIFF TRUNCADO: Não confirme import não utilizado, variável não usada ou call site ausente com base apenas em diff truncado.",
        ])
    if _agentescala:
        lines.extend(_AGENTESCALA_CONTEXT_LINES)
    lines.extend([
        "Se o diff estiver incompleto ou truncado, diga isso explicitamente.",
        "Se faltar contexto de arquivo, diga qual contexto falta.",
        "Arquivos alterados:",
    ])
    for file in pr_context.files[:MAX_FILES_ANALYZED]:
        snippet = _short_patch_for_bundle(file.patch)
        lines.append(
            f"- {file.path} [{file.status}] +{file.additions} -{file.deletions}: {_redact_sensitive_text(snippet)}"
        )
    # Attach final-file context for frontend files with suspected import changes.
    # Content is fetched at head_sha via GitHub API so it reflects the PR head,
    # not the base-checkout state (the workflow only checks out the base).
    _final_file_budget = MAX_FINAL_FILE_CHARS * 3
    _ref = pr_context.head_sha or pr_context.head_ref or ""
    for file in pr_context.files[:MAX_FILES_ANALYZED]:
        if _final_file_budget <= 200:
            break
        if not _is_frontend_file(file.path) or not _diff_has_import_lines(file.patch):
            continue
        if client is None or not _ref:
            break
        ctx = _fetch_file_at_ref(
            client,
            pr_context.owner,
            pr_context.repo,
            file.path,
            _ref,
            min(MAX_FINAL_FILE_CHARS, _final_file_budget - 100),
        )
        if ctx:
            lines.append(ctx)
            _final_file_budget -= len(ctx)
    if pr_context.checks:
        lines.append("Checks (status real do GitHub API):")
        for check in pr_context.checks[:10]:
            label = _check_status_label(check)
            if _docs_only and _check_is_functional_test(check.name) and label == "failed":
                label = "skipped"
            lines.append(f"- {check.name}: {label}")
    if deterministic_findings:
        lines.append("Achados determinísticos:")
        for finding in deterministic_findings[:MAX_FINDINGS]:
            lines.append(
                f"- {finding.severity} {finding.file}: {_redact_sensitive_text(finding.evidence)} | {_redact_sensitive_text(finding.risk)} | {_redact_sensitive_text(finding.recommendation)}"
            )
    lines.extend(
        [
            "Contrato de revisão:",
            "- Responda em pt-BR.",
            "- Você é reviewer sênior de código.",
            "- Revise o diff abaixo procurando apenas problemas concretos.",
            "- Não dê comentários genéricos.",
            "- Não sugira melhorias cosméticas.",
            "- Não elogie o código.",
            "- Não invente problemas sem evidência no diff.",
            "- Max 5 achados.",
            "- Priorize: bug funcional, regressão, quebra de contrato/API, métrica Prometheus incorreta, risco de segurança, teste ausente para comportamento alterado, risco de deploy/runtime e inconsistência com documentação/contrato existente.",
            "- TAXONOMIA DE SEVERIDADE OBRIGATÓRIA:",
            "  P0 = segredo real, produção crítica, comando destrutivo, perda de dados ou bypass de autenticação.",
            "  P1 = bug confirmado por arquivo final, check/teste falhando ou contrato quebrado — NUNCA hipóteses.",
            "  P2 = risco provável mas não confirmado; use quando há indício concreto sem prova definitiva.",
            "  P3 = melhoria, refactoring ou sugestão sem impacto direto em produção.",
            "- Para cada achado, responda exatamente neste formato:",
            "  - Severidade: P0/P1/P2/P3",
            "  - Arquivo/linha ou trecho: ...",
            "  - Problema concreto: ...",
            "  - Por que isso quebra algo: ...",
            "  - Correção sugerida: ...",
            f"- Se não encontrar problema real, responda exatamente: \"{NO_BLOCKING_FINDINGS_RESPONSE}\"",
            "- Depois liste no máximo 5 itens verificados.",
            "- Não retornar checklist genérico.",
            "- Não retornar recomendações sem relação direta com o diff.",
            "- Preferir poucos achados bons a muitos achados fracos.",
            "- Falsos positivos baseados em placeholders de teste como test-token, test_token, fake-token, fake_token, dummy-token, dummy, example, placeholder, redacted, [REDACTED], <token>, <secret>, changeme e local-only não são segredos reais.",
            "- Não trate ajuste isolado de MAX_BUNDLE_CHARS como P2; só sinalize se houver regressão de truncamento sem teste.",
            "- Não invente arquivos ou linhas.",
            "- NUNCA diga que um check 'FALHOU' se o status não for 'failed' (conclusion=failure).",
            "- Para PR documental, checks funcionais são 'skipped', não 'falhou'.",
            "- Não adicione recomendações genéricas de segurança/performance sem evidência no diff.",
            "- REGRAS DE EVIDÊNCIA ANTI-FALSO-POSITIVO:",
            "  (A) Diff truncado NÃO gera P1; só promova P1 se a parte visível do diff contém prova suficiente.",
            "  (B) Import ou variável não usada só é P1/P2 se confirmado por lint/check ou pelo arquivo final completo — NUNCA por diff parcial.",
            "  (C) Problemas descritos com 'possivelmente', 'talvez' ou 'pode ser' são no máximo P2 — são riscos, não bugs confirmados.",
            "  (D) Se o corpo da PR ou os checks reportam testes verdes, NÃO escreva 'sem garantias de testes'; escreva no máximo 'não validei localmente'.",
            "  (E) Não sugira mudanças de backend ou migrations em PR exclusivamente frontend sem evidência direta no diff.",
            "  (F) Nunca finja ter executado testes, lint ou checagens locais.",
        ]
    )
    bundle = "\n".join(lines)
    sanitized_bundle = _redact_sensitive_text(bundle)
    if len(sanitized_bundle) > MAX_BUNDLE_CHARS:
        truncated = True
    return ReviewBundle(
        content=_truncate_text(sanitized_bundle, MAX_BUNDLE_CHARS),
        diff_chars=diff_chars,
        files_count=len(pr_context.files),
        truncated=truncated,
    )


def _last_bot_comment(pr_context: ReviewContext) -> str | None:
    for comment in reversed(pr_context.recent_comments):
        body = str(comment.get("body") or "")
        if COMMENT_MARKER in body:
            return _truncate_text(_redact_sensitive_text(body), 1200)
    return None


def _build_ask_bundle(pr_context: ReviewContext, deterministic_findings: list[Finding], question: str) -> str:
    lines = [
        f"Pergunta do usuário: {_redact_sensitive_text(_truncate_text(question, 300))}",
        f"Título do PR: {_redact_sensitive_text(pr_context.title or '')}",
        f"Descrição do PR: {_redact_sensitive_text(_truncate_text(pr_context.body or '', 600))}",
        f"Escopo: {summarize_pr_scope(pr_context.files)}",
        "Arquivos alterados:",
    ]
    for file in pr_context.files[:MAX_FILES_ANALYZED]:
        snippet = _short_patch_for_bundle(file.patch)
        lines.append(
            f"- {file.path} [{file.status}] +{file.additions} -{file.deletions}: {_redact_sensitive_text(snippet)}"
        )
    last_bot_comment = _last_bot_comment(pr_context)
    if last_bot_comment:
        lines.extend(["Último comentário do bot:", last_bot_comment])
    if deterministic_findings:
        lines.append("Achados determinísticos recentes:")
        for finding in deterministic_findings[:MAX_FINDINGS]:
            lines.append(
                f"- {finding.severity} {finding.file}: {_redact_sensitive_text(finding.evidence)} | {_redact_sensitive_text(finding.risk)} | {_redact_sensitive_text(finding.recommendation)}"
            )
    lines.extend(
        [
            "Contrato de resposta:",
            "- Responda em pt-BR se a pergunta estiver em português; caso contrário, responda no idioma do usuário.",
            "- Seja curto, direto e específico ao diff.",
            "- Não invente arquivos, linhas, logs ou fatos fora do payload.",
            "- Se houver incerteza, diga isso explicitamente.",
            "- Se o usuário pedir falso positivo, explique com base no diff/contexto.",
            "- Falsos positivos baseados em placeholders de teste como test-token, test_token, fake-token, fake_token, dummy-token, dummy, example, placeholder, redacted, [REDACTED], <token>, <secret>, changeme e local-only não são segredos reais.",
        ]
    )
    bundle = "\n".join(lines)
    return _truncate_text(_redact_sensitive_text(bundle), MAX_BUNDLE_CHARS)


def _llm_finding_uses_obvious_placeholder(finding: Finding) -> bool:
    haystack = " ".join((finding.file, finding.evidence, finding.risk, finding.recommendation)).lower()
    return any(
        marker in haystack
        for marker in (
            "test-token",
            "test_token",
            "fake-token",
            "fake_token",
            "dummy-token",
            "dummy",
            "example",
            "placeholder",
            "changeme",
            "local-only",
        )
    )


def _filter_placeholder_llm_findings(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if not _llm_finding_uses_obvious_placeholder(finding)]


def _is_agentescala_repo(owner: str, repo: str) -> bool:
    return f"{owner}/{repo}" == AGENTESCALA_REPO


def _has_speculative_language(text: str) -> bool:
    return bool(_SPECULATIVE_LANGUAGE_RE.search(text))


def _is_frontend_file(path: str) -> bool:
    return path.lower().endswith((".tsx", ".ts", ".jsx", ".js", ".vue", ".svelte"))


def _diff_has_import_lines(patch: str | None) -> bool:
    """Return True if the diff adds import statements (Python or JS/TS)."""
    if not patch:
        return False
    for line in patch.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("+") and not stripped.startswith("+++"):
            content = stripped[1:].lstrip()
            if content.startswith("import ") or content.startswith("from "):
                return True
    return False


def _fetch_file_at_ref(
    client: "GitHubClient",
    owner: str,
    repo: str,
    path: str,
    ref: str,
    max_chars: int = MAX_FINAL_FILE_CHARS,
) -> str | None:
    """Fetch a file's content at a specific git ref via GitHub API.

    Uses GET /repos/{owner}/{repo}/contents/{path}?ref={ref}.
    Returns sanitized content (up to max_chars) with a source marker, or None
    on any error.  Never returns raw secrets.
    """
    import base64

    try:
        api_path = f"/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        data = client.get_json(api_path)
        if not isinstance(data, dict):
            return None
        encoding = data.get("encoding", "")
        raw_content = data.get("content", "")
        if encoding == "base64":
            decoded = base64.b64decode(raw_content.replace("\n", "")).decode("utf-8", errors="replace")
        elif encoding == "utf-8" or encoding == "":
            decoded = str(raw_content)
        else:
            return None
        sanitized = _redact_sensitive_text(decoded)
        truncated_content = _truncate_text(sanitized, max_chars)
        return f"[final_file_context: {path} @ {ref[:8]}]\n{truncated_content}"
    except (GitHubAPIError, OSError, UnicodeDecodeError, Exception):
        return None


def build_agent_router_payload(
    pr_context: ReviewContext,
    deterministic_findings: list[Finding],
    *,
    model: str | None,
    client: "GitHubClient | None" = None,
) -> dict[str, Any]:
    review_bundle = _build_sanitized_bundle(pr_context, deterministic_findings, client=client)
    _agentescala_suffix = ""
    if _is_agentescala_repo(pr_context.owner, pr_context.repo):
        _agentescala_suffix = " " + " ".join(_AGENTESCALA_CONTEXT_LINES)
    system_prompt = (
        "Você é reviewer sênior de código. Responda sempre em pt-BR. "
        "Revise o diff enviado procurando apenas problemas concretos. "
        "Não dê comentários genéricos. Não sugira melhorias cosméticas. Não elogie o código. "
        "Não invente problemas sem evidência no diff. "
        "Priorize, nesta ordem: bug funcional, regressão, quebra de contrato/API, métrica Prometheus incorreta, risco de segurança, "
        "teste ausente para comportamento alterado, risco de deploy/runtime e inconsistência com documentação ou contrato existente. "
        "TAXONOMIA DE SEVERIDADE OBRIGATÓRIA: "
        "P0 = segredo real, produção crítica, comando destrutivo, perda de dados ou bypass de autenticação. "
        "P1 = bug confirmado por arquivo final, check/teste falhando ou contrato quebrado — NUNCA hipóteses. "
        "P2 = risco provável mas não confirmado; use quando há indício concreto sem prova definitiva. "
        "P3 = melhoria, refactoring ou sugestão sem impacto direto em produção. "
        "Para cada achado, responda exatamente neste formato textual: "
        "'- Severidade: P0/P1/P2/P3', '- Arquivo/linha ou trecho: ...', '- Problema concreto: ...', '- Por que isso quebra algo: ...', '- Correção sugerida: ...'. "
        f"Se não encontrar problema real, responda exatamente: '{NO_BLOCKING_FINDINGS_RESPONSE}'. "
        "Depois liste no máximo 5 itens verificados. "
        "Não retornar checklist genérico. Não retornar recomendações sem relação direta com o diff. "
        "Preferir poucos achados bons a muitos achados fracos. "
        "Se o diff estiver incompleto ou truncado, diga isso explicitamente. Se faltar contexto de arquivo, diga qual contexto falta. "
        "Não trate ajuste isolado de MAX_BUNDLE_CHARS como P2; só sinalize se houver regressão de truncamento sem teste. "
        "REGRAS OBRIGATÓRIAS DE EVIDÊNCIA: "
        "(1) Nunca diga que uma verificação 'falhou' (FALHOU, failed, falhando) a menos que o payload mostre conclusion=failure. "
        "(2) Checks com conclusion=timed_out devem ser descritos como 'timeout', não como falha. "
        "(3) Checks com cancelled/action_required/startup_failure devem ser descritos pelo status real. "
        "(4) Em PRs documentais (apenas docs/ e .md), não reporte checks funcionais como falha. "
        "(5) Não adicione recomendações genéricas de segurança ou performance sem evidência no diff. "
        "(6) Se uma validação não foi executada, diga 'não executado', nunca 'falhou'. "
        "REGRAS ANTI-FALSO-POSITIVO: "
        "(7) Diff truncado NÃO gera P1; só promova P1 se a parte visível do diff contém prova suficiente. "
        "(8) Import ou variável não usada só é P1/P2 se confirmado por lint/check ou pelo arquivo final completo — NUNCA por diff parcial. "
        "(9) Problemas descritos com 'possivelmente', 'talvez' ou 'pode ser' são no máximo P2 — riscos não confirmados, não bloqueadores. "
        "(10) Se o corpo da PR ou os checks reportam testes verdes, NÃO escreva 'sem garantias de testes'; escreva no máximo 'não validei localmente'. "
        "(11) Não sugira mudanças de backend ou migrations em PR exclusivamente frontend sem evidência direta no diff. "
        "(12) Nunca finja ter executado testes, lint ou checagens locais."
    ) + _agentescala_suffix
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": review_bundle.content},
        ],
        "temperature": 0.1,
        "stream": False,
        "max_tokens": 1200,
    }
    payload["model"] = (model or "").strip() or DEFAULT_AGENT_REVIEW_MODEL
    return payload


def build_agent_router_ask_payload(
    pr_context: ReviewContext,
    deterministic_findings: list[Finding],
    *,
    question: str,
    model: str | None,
) -> dict[str, Any]:
    system_prompt = (
        "Você é um assistente de follow-up para Pull Requests. Responda sempre em pt-BR quando a pergunta estiver em português; "
        "caso contrário, responda no idioma do usuário. Seja curto, objetivo e acionável. Baseie-se apenas no payload fornecido. "
        "Não invente arquivos, linhas, logs ou segredos. Se o usuário pedir falso positivo, explique sua incerteza quando existir."
    )
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_ask_bundle(pr_context, deterministic_findings, question)},
        ],
        "temperature": 0.1,
        "stream": False,
        "max_tokens": 300,
    }
    payload["model"] = (model or "").strip() or DEFAULT_AGENT_REVIEW_MODEL
    return payload


def call_agent_router_review(
    payload: dict[str, Any],
    *,
    base_url: str = DEFAULT_AGENT_ROUTER_BASE_URL,
    api_key: str | None = None,
    timeout_seconds: int = DEFAULT_AGENT_ROUTER_TIMEOUT_SECONDS,
) -> str:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if not api_key:
        raise AgentRouterDisabledError("Agent Router API key is missing.")
    headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise AgentRouterAuthError(f"router auth failed: {exc.code}") from exc
        if exc.code == 429:
            raise AgentRouterRateLimitError("router rate limited") from exc
        if 500 <= exc.code < 600:
            raise AgentRouterUnavailableError(f"router unavailable: {exc.code}") from exc
        raise AgentRouterResponseError(f"router unexpected response: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise AgentRouterTimeoutError(str(exc)) from exc


def _normalize_llm_findings(raw_findings: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(raw_findings, list):
        return findings
    seen: set[tuple[str, str, str, str]] = set()
    for item in raw_findings:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").upper().strip()
        if severity not in {"P0", "P1", "P2", "P3"}:
            continue
        file = _redact_sensitive_text(str(item.get("file") or "n/a").strip() or "n/a")
        evidence = _redact_sensitive_text(_truncate_text(str(item.get("evidence") or "").strip() or "sem evidência", 200))
        risk = _redact_sensitive_text(_truncate_text(str(item.get("risk") or "").strip() or "risco não informado", 220))
        recommendation = _redact_sensitive_text(_truncate_text(str(item.get("recommendation") or "").strip() or "corrigir o problema", 220))
        key = (severity, file, evidence, recommendation)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            Finding(
                severity=severity,
                file=file,
                evidence=evidence,
                risk=risk,
                recommendation=recommendation,
                rule_id=f"llm::{severity}::{file}",
            )
        )
        if len(findings) >= MAX_FINDINGS:
            break
    return findings


def _parse_textual_llm_review(raw_text: str) -> LLMReview:
    text = _redact_sensitive_text(raw_text.strip())
    if not text:
        return LLMReview()

    current: dict[str, str] = {}
    findings: list[Finding] = []
    checked_items: list[str] = []
    collecting_checked_items = False

    def _flush_current() -> None:
        nonlocal current
        if not current:
            return
        severity = current.get("severidade", "").upper()
        file_text = current.get("arquivo/linha ou trecho", "")
        problem = current.get("problema concreto", "")
        risk = current.get("por que isso quebra algo", "")
        recommendation = current.get("correção sugerida", "")
        if severity in {"P0", "P1", "P2", "P3"} and file_text and problem and risk and recommendation:
            findings.append(
                Finding(
                    severity=severity,
                    file=file_text,
                    evidence=problem,
                    risk=risk,
                    recommendation=recommendation,
                    rule_id=f"llm::{severity}::{file_text}",
                )
            )
        current = {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Itens verificados"):
            _flush_current()
            collecting_checked_items = True
            continue
        normalized_line = line[2:].strip() if line.startswith("- ") else line
        if collecting_checked_items and line.startswith("- "):
            checked_items.append(line)
            continue
        if ":" not in normalized_line:
            continue
        key, value = normalized_line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "severidade":
            _flush_current()
            collecting_checked_items = False
            current = {"severidade": value}
            continue
        if key in {"arquivo/linha ou trecho", "problema concreto", "por que isso quebra algo", "correção sugerida"} and current:
            current[key] = value

    _flush_current()

    if findings:
        notes = "\n".join(checked_items[:MAX_FINDINGS]) if checked_items else None
        return LLMReview(findings=findings[:MAX_FINDINGS], notes=notes)

    if NO_BLOCKING_FINDINGS_RESPONSE in text:
        notes = NO_BLOCKING_FINDINGS_RESPONSE
        if checked_items:
            notes = f"{notes}\n" + "\n".join(checked_items[:MAX_FINDINGS])
        return LLMReview(notes=notes)

    return LLMReview(warning="LLM response inválida")


def parse_agent_router_response(raw_response: str) -> LLMReview:
    sanitized = _redact_sensitive_text(raw_response.strip())
    if not sanitized:
        return LLMReview()

    def _parse_content(content: Any) -> LLMReview:
        if isinstance(content, dict):
            if content.get("type") == "message" and "content" in content:
                return _parse_content(content.get("content"))
            if "choices" in content and isinstance(content["choices"], list) and content["choices"]:
                choice = content["choices"][0]
                if isinstance(choice, dict):
                    message = choice.get("message")
                    if isinstance(message, dict) and "content" in message:
                        return _parse_content(message["content"])
                    if "text" in choice:
                        return _parse_content(choice["text"])
            findings = _normalize_llm_findings(content.get("findings"))
            notes = extract_router_response_text(json.dumps(content, ensure_ascii=False))
            if not notes and "summary" in content:
                notes = extract_router_response_text(json.dumps(content.get("summary"), ensure_ascii=False))
            if not notes and "notes" in content:
                notes = extract_router_response_text(json.dumps(content.get("notes"), ensure_ascii=False))
            notes = _redact_sensitive_text(_truncate_text(notes, 300)) or None
            warning = None if findings or notes else "LLM response inválida"
            return LLMReview(findings=findings, notes=notes, warning=warning)
        if isinstance(content, list):
            notes = extract_router_response_text(json.dumps(content, ensure_ascii=False))
            return LLMReview(notes=_redact_sensitive_text(_truncate_text(notes, 300)) or None)
        if isinstance(content, str):
            text = _redact_sensitive_text(content.strip())
            if not text:
                return LLMReview()
            if text.lstrip().startswith(("{", "[")):
                try:
                    inner = json.loads(text)
                except Exception:
                    return LLMReview(warning="LLM response inválida")
                return _parse_content(inner)
            textual_review = _parse_textual_llm_review(text)
            if textual_review.findings or textual_review.notes or textual_review.warning:
                return textual_review
            return LLMReview(warning="LLM response inválida")
        return LLMReview(notes=_redact_sensitive_text(str(content)))

    try:
        outer = json.loads(sanitized)
    except Exception:
        textual_review = _parse_textual_llm_review(sanitized)
        if textual_review.findings or textual_review.notes or textual_review.warning:
            return textual_review
        if sanitized.lstrip().startswith(("{", "[")):
            return LLMReview(notes=sanitized, warning="LLM response inválida")
        return LLMReview(warning="LLM response inválida")

    if isinstance(outer, dict):
        for key in ("choices", "output_text", "content", "answer", "response", "review", "data", "result", "message", "text"):
            if key in outer:
                parsed = _parse_content(outer[key])
                if parsed.findings or parsed.notes or parsed.warning:
                    return parsed
        return _parse_content(outer)
    return _parse_content(outer)


def extract_router_response_text(raw_response: str) -> str:
    sanitized = _redact_sensitive_text(raw_response.strip())
    if not sanitized:
        return ""

    def _extract_content(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            text = _redact_sensitive_text(content.strip())
            if not text:
                return ""
            try:
                inner = json.loads(text)
            except Exception:
                return text
            if inner is content:
                return text
            extracted = _extract_content(inner)
            return extracted or text
        if isinstance(content, list):
            parts = [_extract_content(item) for item in content]
            joined = " ".join(part for part in parts if part).strip()
            return _redact_sensitive_text(_truncate_text(joined, MAX_LLM_ASK_RESPONSE_CHARS)) if joined else ""
        if isinstance(content, dict):
            if content.get("type") == "message" and "content" in content:
                extracted = _extract_content(content.get("content"))
                if extracted:
                    return extracted
            for key in ("answer", "response", "summary", "notes", "content", "text", "message", "output_text", "review", "data", "result"):
                if key not in content:
                    continue
                extracted = _extract_content(content.get(key))
                if extracted:
                    return extracted
            if "choices" in content and isinstance(content["choices"], list) and content["choices"]:
                extracted = _extract_content(content["choices"][0])
                if extracted:
                    return extracted
            return ""
        return _redact_sensitive_text(str(content).strip())

    try:
        outer = json.loads(sanitized)
    except Exception:
        if sanitized.lstrip().startswith(("{", "[")):
            return _redact_sensitive_text(_truncate_text(sanitized, MAX_LLM_ASK_RESPONSE_CHARS))
        return _redact_sensitive_text(_truncate_text(sanitized, MAX_LLM_ASK_RESPONSE_CHARS))

    if isinstance(outer, dict):
        for key in ("choices", "output_text", "content", "answer", "response", "review", "data", "result", "message", "text"):
            if key in outer:
                extracted = _extract_content(outer[key])
                if extracted:
                    return extracted
        return _extract_content(outer)
    return _extract_content(outer)


def merge_deterministic_and_llm_findings(
    deterministic_findings: list[Finding],
    llm_review: LLMReview | None,
) -> tuple[list[Finding], str | None]:
    combined = list(deterministic_findings)
    llm_notes = None
    if llm_review:
        combined.extend(_filter_placeholder_llm_findings(llm_review.findings))
        llm_notes = llm_review.notes
        if llm_review.warning:
            llm_notes = f"{llm_review.warning}" if not llm_notes else f"{llm_review.warning} {llm_notes}"
    merged = rank_findings(combined)
    if any(finding.severity in {"P1", "P2"} for finding in merged):
        merged = [finding for finding in merged if finding.severity in {"P1", "P2"}]
    return merged[:MAX_FINDINGS], llm_notes


def _build_unauthorized_message() -> str:
    return "Comentário ignorado: você não está autorizado a acionar este agent."


def _build_issue_message() -> str:
    return "Este agent só responde em Pull Requests."


def _build_llm_disabled_message() -> str:
    return "LLM desabilitado; use /agent review para review determinístico."


def _build_llm_key_missing_message() -> str:
    return "LLM desabilitado ou chave do router ausente; use /agent review para review determinístico."


def _build_llm_router_warning(reason: str) -> str:
    return f"LLM indisponível ({reason}); use /agent review para review determinístico."


def _build_llm_timeout_warning(timeout_seconds: int) -> str:
    return f"LLM indisponível (timeout após {timeout_seconds}s); use /agent review para review determinístico."


def _build_ask_disabled_message() -> str:
    return "Agent ask requer LLM habilitado; use /agent review para review determinístico."


def _build_ask_router_warning(reason: str) -> str:
    return f"Agent ask indisponível ({reason}); use /agent review para review determinístico."


def _build_ask_router_parse_warning() -> str:
    return "Agent ask indisponível (não consegui interpretar a resposta do Agent Router); use /agent review para revisão determinística."


def _build_ask_reply_body(pr_context: ReviewContext, question: str, answer: str) -> str:
    lines = [
        f"Resposta para @{pr_context.author} sobre `/agent ask`.",
        "",
        f"Pergunta: {_truncate_text(_redact_sensitive_text(question), 240)}",
        "",
        _truncate_text(_redact_sensitive_text(answer), MAX_LLM_ASK_RESPONSE_CHARS).strip(),
        "",
        "Comentário separado da revisão principal.",
    ]
    return _truncate_text("\n".join(lines).strip(), MAX_LLM_ASK_RESPONSE_CHARS)


def _sanitize_router_base_url(base_url: str) -> str:
    parsed = urlparse(base_url.strip() or DEFAULT_AGENT_ROUTER_BASE_URL)
    if not parsed.scheme or not parsed.netloc:
        return DEFAULT_AGENT_ROUTER_BASE_URL
    sanitized = f"{parsed.scheme}://{parsed.hostname or ''}"
    if parsed.port:
        sanitized += f":{parsed.port}"
    if parsed.path and parsed.path != "/":
        sanitized += parsed.path.rstrip("/")
    return sanitized.rstrip("/")


def _parse_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _log_agent_router_failure(reason: str, *, base_url: str, model: str | None, timeout_seconds: int) -> None:
    model_text = model or "n/a"
    print(
        f"Agent Router {reason}: {_sanitize_router_base_url(base_url)} (model={model_text}, timeout={timeout_seconds}s)",
        file=sys.stderr,
    )


def _log_review_bundle_metadata(bundle: ReviewBundle, *, model: str | None) -> None:
    model_text = (model or "").strip() or DEFAULT_AGENT_REVIEW_MODEL
    print(
        f"Agent Router review metadata: model={model_text}, diff_chars={bundle.diff_chars}, files_count={bundle.files_count}, truncated={'true' if bundle.truncated else 'false'}",
        file=sys.stderr,
    )


def _describe_router_response_shape(raw_response: str) -> str:
    text = raw_response.strip()
    if not text:
        return "empty"
    try:
        parsed = json.loads(text)
    except Exception:
        return f"text(len={len(text)})"

    def _shape(value: Any) -> str:
        if isinstance(value, dict):
            keys = ",".join(sorted(str(key) for key in value.keys())[:6])
            return f"dict(keys={keys or 'none'})"
        if isinstance(value, list):
            item_types = ",".join(sorted({type(item).__name__ for item in value})[:4])
            return f"list(len={len(value)},items={item_types or 'none'})"
        if isinstance(value, str):
            return f"str(len={len(value)})"
        return type(value).__name__

    if isinstance(parsed, dict):
        return _shape(parsed)
    if isinstance(parsed, list):
        return _shape(parsed)
    return _shape(parsed)


def _issue_comment_path(client: GitHubClient, issue_number: int) -> str:
    owner, repo = _repo_parts(client.repository)
    return f"/repos/{owner}/{repo}/issues/{issue_number}/comments"


def _step_summary_path() -> str | None:
    path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    return path or None


def _short_summary_for_log(body: str) -> str:
    short = _redact_sensitive_text(body.replace("\n", " "))
    return _truncate_text(short, 240)


def _write_step_summary(body: str) -> bool:
    path = _step_summary_path()
    if not path:
        return False
    try:
        summary_path = Path(path)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("a", encoding="utf-8") as handle:
            handle.write(body.rstrip() + "\n")
        return True
    except OSError:
        return False


def _log_comment_write_fallback(body: str, *, wrote_summary: bool) -> None:
    print(_COMMENT_403_LOG_MESSAGE, file=sys.stderr)
    if not wrote_summary:
        print(f"Step summary unavailable; short review: {_short_summary_for_log(body)}", file=sys.stderr)


def _handle_comment_write_403(body: str) -> bool:
    wrote_summary = _write_step_summary(body)
    _log_comment_write_fallback(body, wrote_summary=wrote_summary)
    return True


def _publish_comment(client: GitHubClient, path: str, body: str) -> bool:
    try:
        client.post_json(path, {"body": body})
        return True
    except GitHubAPIError as exc:
        if exc.code == 403:
            return _handle_comment_write_403(body)
        raise


def _short_patch_for_bundle(patch: str | None) -> str:
    if not patch:
        return "sem patch"
    lines = _normalize_patch_text(patch)
    if not lines:
        return _truncate_text(_redact_sensitive_text(patch.replace("\n", " ")), MAX_PATCH_SNIPPET_CHARS)
    snippet = " | ".join(lines[:6])
    return _truncate_text(_redact_sensitive_text(snippet), MAX_PATCH_SNIPPET_CHARS)


def _find_existing_review_comment(recent_comments: list[dict[str, Any]]) -> int | None:
    for comment in recent_comments:
        body = str(comment.get("body") or "")
        if COMMENT_MARKER in body and "id" in comment:
            try:
                return int(comment["id"])
            except (TypeError, ValueError):
                continue
    return None


def _publish_review_comment(client: GitHubClient, pr_context: ReviewContext, body: str) -> bool:
    payload = {"body": body}
    existing_comment_id = _find_existing_review_comment(pr_context.recent_comments)
    try:
        if existing_comment_id is not None:
            owner, repo = _repo_parts(client.repository)
            client.patch_json(f"/repos/{owner}/{repo}/issues/comments/{existing_comment_id}", payload)
            return True
    except GitHubAPIError as exc:
        if exc.code == 403:
            return _handle_comment_write_403(body)
        raise
    return _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), body)


def main() -> int:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    event_path = os.getenv("GITHUB_EVENT_PATH", "").strip()
    allowed_users = os.getenv("AGENT_ALLOWED_USERS", "")
    llm_enabled = os.getenv("AGENT_REVIEW_LLM_ENABLED", "").strip().lower() == "true"
    router_base_url = os.getenv("AGENT_ROUTER_BASE_URL", "").strip() or DEFAULT_AGENT_ROUTER_BASE_URL
    router_api_key = os.getenv("AGENT_ROUTER_API_KEY", "").strip()
    router_model = os.getenv("AGENT_ROUTER_MODEL", "").strip() or None
    router_timeout_seconds = _parse_positive_int_env(_ROUTER_TIMEOUT_ENV, DEFAULT_AGENT_ROUTER_TIMEOUT_SECONDS)

    if not token or not repository or not event_path:
        raise SystemExit("Missing required GitHub environment variables.")

    payload = _load_event_payload(event_path)
    comment = payload.get("comment") or {}
    body = str(comment.get("body") or "")
    command_mode = parse_agent_review_command(body)
    if command_mode == "none" or command_mode == "unknown":
        return 0

    issue = payload.get("issue") or {}
    issue_number = int(issue.get("number") or 0)
    user = comment.get("user") or {}
    association = comment.get("author_association")
    login = str(user.get("login") or "")
    client = GitHubClient(token=token, repository=repository)

    if not is_authorized(association, login, allowed_users):
        if not _publish_comment(client, _issue_comment_path(client, issue_number), _build_unauthorized_message()):
            return 1
        return 0

    if not is_pull_request_payload(payload):
        if not _publish_comment(client, _issue_comment_path(client, issue_number), _build_issue_message()):
            return 1
        return 0

    pr_context = fetch_review_context(client, payload, command_mode)
    deterministic_findings = build_deterministic_findings(pr_context.files, pr_context.checks)
    if command_mode == "ask":
        question = extract_agent_ask_question(body)
        if not question:
            if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), "Escreva sua pergunta após `/agent ask`."):
                return 1
            return 0
        if not llm_enabled:
            if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), _build_ask_disabled_message()):
                return 1
            return 0
        if not router_api_key:
            if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), _build_ask_disabled_message()):
                return 1
            return 0
        router_payload = build_agent_router_ask_payload(pr_context, deterministic_findings, question=question, model=router_model)
        try:
            raw_router = call_agent_router_review(
                router_payload,
                base_url=router_base_url,
                api_key=router_api_key or None,
                timeout_seconds=router_timeout_seconds,
            )
            answer = _truncate_text(_redact_sensitive_text(extract_router_response_text(raw_router)), MAX_LLM_ASK_RESPONSE_CHARS).strip()
            if not answer:
                raise AgentRouterResponseError(_describe_router_response_shape(raw_router))
            reply_body = _build_ask_reply_body(pr_context, question, answer)
            if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), reply_body):
                return 1
            return 0
        except AgentRouterTimeoutError:
            fallback = _build_ask_router_warning(f"timeout após {router_timeout_seconds}s")
            _log_agent_router_failure("timeout", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
        except AgentRouterAuthError:
            fallback = _build_ask_router_warning("autenticação falhou")
            _log_agent_router_failure("autenticação falhou", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
        except AgentRouterRateLimitError:
            fallback = _build_ask_router_warning("limite de taxa")
            _log_agent_router_failure("limite de taxa", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
        except AgentRouterUnavailableError:
            fallback = _build_ask_router_warning("router indisponível")
            _log_agent_router_failure("router indisponível", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
        except AgentRouterResponseError:
            fallback = _build_ask_router_parse_warning()
            _log_agent_router_failure(
                f"resposta não interpretável; shape={_describe_router_response_shape(raw_router)}",
                base_url=router_base_url,
                model=router_model,
                timeout_seconds=router_timeout_seconds,
            )
        except Exception:
            fallback = _build_ask_router_warning("erro inesperado")
            _log_agent_router_failure("erro inesperado", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
        if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), fallback):
            return 1
        return 0

    llm_review: LLMReview | None = None
    llm_warning: str | None = None
    if command_mode == "review_llm":
        if not llm_enabled:
            llm_warning = _build_llm_disabled_message()
        elif not router_api_key:
            llm_warning = _build_llm_key_missing_message()
        else:
            review_bundle = _build_sanitized_bundle(pr_context, deterministic_findings, client=client)
            _log_review_bundle_metadata(review_bundle, model=router_model)
            router_payload = build_agent_router_payload(pr_context, deterministic_findings, model=router_model, client=client)
            try:
                raw_router = call_agent_router_review(
                    router_payload,
                    base_url=router_base_url,
                    api_key=router_api_key or None,
                    timeout_seconds=router_timeout_seconds,
                )
                llm_review = parse_agent_router_response(raw_router)
                llm_warning = llm_review.warning
            except AgentRouterTimeoutError:
                llm_warning = _build_llm_timeout_warning(router_timeout_seconds)
                _log_agent_router_failure("timeout", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
            except AgentRouterAuthError:
                llm_warning = _build_llm_router_warning("autenticação falhou")
                _log_agent_router_failure("autenticação falhou", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
            except AgentRouterRateLimitError:
                llm_warning = _build_llm_router_warning("limite de taxa")
                _log_agent_router_failure("limite de taxa", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
            except AgentRouterUnavailableError:
                llm_warning = _build_llm_router_warning("router indisponível")
                _log_agent_router_failure("router indisponível", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
            except AgentRouterResponseError:
                llm_warning = _build_llm_router_warning("resposta inválida")
                _log_agent_router_failure("resposta inválida", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
            except Exception:
                llm_warning = _build_llm_router_warning("erro inesperado")
                _log_agent_router_failure("erro inesperado", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)

    markdown = render_review(
        deterministic_findings,
        pr_context.checks,
        pr_context,
        llm_mode=command_mode == "review_llm",
        llm_warning=llm_warning,
        llm_notes=llm_review.notes if llm_review else None,
        llm_findings=llm_review.findings if llm_review else None,
    )
    if not _publish_review_comment(client, pr_context, markdown):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
