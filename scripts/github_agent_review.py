#!/usr/bin/env python3
"""Deterministic PR review with optional Agent Router assistance."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_AGENT_ROUTER_BASE_URL = "https://api.ks-sm.net:9443"
DEFAULT_AGENT_ROUTER_TIMEOUT_SECONDS = 60
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
    "changeme",
    "dummy",
}
_P1_DESTRUCTIVE_RE = re.compile(
    r"(?i)\b(?:docker\s+compose\s+(?:-f\s+\S+\s+)*down|docker\s+stop|docker\s+rm|docker\s+kill|docker\s+exec|systemctl\s+restart|service\s+docker|git\s+push|git\s+pull|rm\s+-rf|chmod\s+777|curl\b.*\|\s*(?:bash|sh|zsh)\b|ssh\s+root)\b"
)
_P1_GUARD_RE = re.compile(r"(?i)\b(?:approval|audit|redact|fail-closed|allowlist)\b")
_P1_WORKFLOW_RE = re.compile(r"(?i)\bpull_request_target\b|\bpermissions:\s*[\s\S]*\bwrite-all\b|\bcontents:\s*write\b|\bactions:\s*write\b|\bpull-requests:\s*write\b")
_P1_RUNNER_RE = re.compile(r"(?i)\bshell\s*=\s*True\b|\bcreate_subprocess_shell\b|\bsubprocess\.run\([^)]*\bcommand\b|\bsubprocess\.run\([^)]*\bargv\b")
_P2_PATH_RE = re.compile(r"/opt/aiops-orchestrator")
_P2_TIMEOUT_RE = re.compile(r"(?i)\bsubprocess\.(?:run|call|check_output|popen)\(")
_P2_NEW_DEP_RE = re.compile(r"(?i)^\+\s*[A-Za-z0-9_.-]+(?:==|>=|<=|~=|!=|>|<)?[A-Za-z0-9*._-]*$")
_ROUTER_TIMEOUT_ENV = "AGENT_ROUTER_TIMEOUT_SECONDS"


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
    return not normalized or normalized.lower() in _PLACEHOLDER_SECRET_VALUES


def _secret_finding_for_line(file: FileChange, line: str) -> Finding | None:
    body = _strip_diff_prefix(line)
    if not body:
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


def scan_patch_for_findings(file: FileChange) -> list[Finding]:
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

    if _P1_DESTRUCTIVE_RE.search(patch):
        _emit(
            findings,
            severity="P1",
            file=file,
            rule_id="destructive_command",
            evidence=_patch_snippet(file, _P1_DESTRUCTIVE_RE),
            risk="Destructive command detected in the diff.",
            recommendation="Replace it with a read-only check or remove it entirely.",
        )

    secret_finding: Finding | None = None
    private_key_match = _PRIVATE_KEY_RE.search(normalized_patch) if normalized_patch else None
    if private_key_match:
        secret_finding = Finding(
            severity="P1",
            file=file.path,
            evidence=_redact_sensitive_text(_truncate_text(private_key_match.group(0), 200)),
            risk="A private key block appears in the patch.",
            recommendation="Remove the key from the diff and rotate it immediately.",
            rule_id="private_key_block",
        )
    else:
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

    if file.path.startswith(("tests/", ".github/workflows/")) and _P2_PATH_RE.search(patch):
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

    if risk["area"] in {"workflow", "scripts", "security-critical"} and any(
        word in patch_lower for word in ("redact", "truncate", "allowlist", "approval", "audit")
    ) and any(line.startswith("-") for line in lines):
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


def scan_checks_for_findings(checks: list[CheckSummary]) -> list[Finding]:
    findings: list[Finding] = []
    for check in checks:
        status = (check.conclusion or check.status or "").lower()
        if status in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
            _emit(
                findings,
                severity="P1",
                file=FileChange(path="CI / Checks", status=status, additions=0, deletions=0, patch=None),
                rule_id=f"check_failed::{check.name}",
                evidence=f"{check.name}: {status}",
                risk="Required CI/check appears to be failing.",
                recommendation="Fix the failing check before merging.",
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
    return {"P1": 0, "P2": 1, "P3": 2}.get(severity, 9)


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
    for file in files:
        candidates.extend(scan_patch_for_findings(file))
    candidates.extend(scan_checks_for_findings(checks))
    candidates.extend(scan_pr_level_gaps(files))
    return rank_findings(candidates)


def review_status(deterministic_findings: list[Finding], llm_findings: list[Finding] | None = None) -> str:
    if any(finding.severity == "P1" for finding in deterministic_findings):
        return "changes_requested"
    if any(finding.severity == "P2" for finding in deterministic_findings):
        return "needs_review"
    if llm_findings:
        if any(finding.severity == "P1" for finding in llm_findings):
            return "changes_requested"
        if any(finding.severity == "P2" for finding in llm_findings):
            return "needs_review"
    return "approved"


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
    llm_findings = llm_findings or []
    status = review_status(findings, llm_findings=llm_findings)
    lines = [
        COMMENT_MARKER,
        "",
        "# Revisão do Agent",
        "",
        "## Resumo",
        f"- Status: {status}",
        f"- PR: {f'#{pr_context.pr_number}' if pr_context.pr_number is not None else 'n/a'}",
        f"- Autor: {pr_context.author}",
        f"- Base: {pr_context.base_ref or 'n/a'}",
        f"- Head: {pr_context.head_ref or 'n/a'}",
        f"- Commit analisado: {pr_context.head_sha or 'n/a'}",
        f"- Modo: {'revisão determinística + Agent Router' if llm_mode else 'revisão determinística'}",
        "- Código do PR executado: não",
        f"- Escopo: {summarize_pr_scope(pr_context.files)}",
        "",
        "## Achados críticos",
    ]
    p1 = [finding for finding in findings if finding.severity == "P1"]
    p2 = [finding for finding in findings if finding.severity == "P2"]
    p3 = [finding for finding in findings if finding.severity == "P3"]
    if not (p1 or p2):
        lines.append("- Não encontrei P1/P2 determinísticos.")
        if p3:
            lines.extend(_render_findings_block("P3 — Sugestões", p3))
    else:
        lines.extend(_render_findings_block("P1 — Bloqueadores", p1))
        lines.extend(_render_findings_block("P2 — Importantes", p2))
    if llm_findings:
        lines.extend(["", "## Achados do LLM"])
        llm_p1 = [finding for finding in llm_findings if finding.severity == "P1"]
        llm_p2 = [finding for finding in llm_findings if finding.severity == "P2"]
        llm_p3 = [finding for finding in llm_findings if finding.severity == "P3"]
        if llm_p1:
            lines.extend(_render_findings_block("P1 — Bloqueadores", llm_p1))
        if llm_p2:
            lines.extend(_render_findings_block("P2 — Importantes", llm_p2))
        if llm_p3:
            lines.extend(_render_findings_block("P3 — Sugestões", llm_p3))
    if checks:
        failing = [check for check in checks if (check.conclusion or check.status or "").lower() not in {"success", "neutral", "skipped"}]
        if failing:
            lines.extend(["", "## Verificações de CI"])
            for check in failing[:3]:
                state = check.conclusion or check.status or "unknown"
                lines.append(f"- {check.name}: {state}{f' ({check.url})' if check.url else ''}")
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
            "## Observação de segurança",
            "Este agent analisou metadados e diff via GitHub API. Ele não executou código do PR, não fez checkout da branch do PR para execução e não teve permissão de deploy.",
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


def _build_sanitized_bundle(pr_context: ReviewContext, deterministic_findings: list[Finding]) -> str:
    lines = [
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
    if pr_context.checks:
        lines.append("Checks:")
        for check in pr_context.checks[:10]:
            state = check.conclusion or check.status or "unknown"
            lines.append(f"- {check.name}: {state}")
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
            "- Seja curto, objetivo e acionável.",
            "- Max 5 achados.",
            "- Priorize P1 e P2.",
            "- Não faça elogios nem resumo longo.",
            "- Não invente arquivos ou linhas.",
        ]
    )
    bundle = "\n".join(lines)
    return _truncate_text(_redact_sensitive_text(bundle), MAX_BUNDLE_CHARS)


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
        ]
    )
    bundle = "\n".join(lines)
    return _truncate_text(_redact_sensitive_text(bundle), MAX_BUNDLE_CHARS)


def build_agent_router_payload(
    pr_context: ReviewContext,
    deterministic_findings: list[Finding],
    *,
    model: str | None,
) -> dict[str, Any]:
    system_prompt = (
        "Você é um reviewer de Pull Request. Responda sempre em pt-BR. Encontre apenas bugs reais, "
        "regressões de segurança, quebras de CI/runtime e violações de contrato. Retorne no máximo "
        "5 achados P1/P2/P3 em formato estruturado com severity, file, evidence, risk e recommendation. "
        "Não elogie. Não faça resumo longo. Não invente arquivos ou linhas. Se não houver P1/P2, diga isso claramente."
    )
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_sanitized_bundle(pr_context, deterministic_findings)},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }
    if model:
        payload["model"] = model
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
        "max_tokens": 300,
    }
    if model:
        payload["model"] = model
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
        if severity not in {"P1", "P2", "P3"}:
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


def parse_agent_router_response(raw_response: str) -> LLMReview:
    sanitized = _redact_sensitive_text(raw_response.strip())
    if not sanitized:
        return LLMReview()

    def _parse_content(content: Any) -> LLMReview:
        if isinstance(content, dict):
            findings = _normalize_llm_findings(content.get("findings"))
            notes = _redact_sensitive_text(_truncate_text(str(content.get("summary") or content.get("notes") or ""), 300)) or None
            warning = None if findings or notes else "LLM response inválida"
            return LLMReview(
                findings=findings,
                notes=notes,
                warning=warning,
            )
        if isinstance(content, str):
            text = _redact_sensitive_text(content.strip())
            if not text:
                return LLMReview()
            try:
                inner = json.loads(text)
            except Exception:
                return LLMReview(notes=text)
            if isinstance(inner, dict):
                return _parse_content(inner)
            return LLMReview(notes=text)
        return LLMReview(notes=_redact_sensitive_text(str(content)))

    try:
        outer = json.loads(sanitized)
    except Exception:
        if sanitized.lstrip().startswith(("{", "[")):
            return LLMReview(notes=sanitized, warning="LLM response inválida")
        return LLMReview(notes=sanitized)

    if isinstance(outer, dict):
        if "choices" in outer and isinstance(outer["choices"], list) and outer["choices"]:
            choice = outer["choices"][0]
            if isinstance(choice, dict):
                message = choice.get("message") or {}
                if isinstance(message, dict) and "content" in message:
                    return _parse_content(message["content"])
                if "text" in choice:
                    return _parse_content(choice["text"])
        if "output_text" in outer:
            return _parse_content(outer["output_text"])
        if "content" in outer:
            return _parse_content(outer["content"])
        if "review" in outer:
            return _parse_content(outer["review"])
        return _parse_content(outer)
    return _parse_content(outer)


def extract_router_response_text(raw_response: str) -> str:
    sanitized = _redact_sensitive_text(raw_response.strip())
    if not sanitized:
        return ""

    def _extract_content(content: Any) -> str:
        if isinstance(content, dict):
            for key in ("answer", "response", "summary", "notes", "content", "text", "message"):
                if key not in content:
                    continue
                extracted = _extract_content(content.get(key))
                if extracted:
                    return extracted
            if "choices" in content and isinstance(content["choices"], list) and content["choices"]:
                return _extract_content(content["choices"][0])
            return _redact_sensitive_text(_truncate_text(json.dumps(content, ensure_ascii=False), 600))
        if isinstance(content, list):
            parts = [_extract_content(item) for item in content]
            joined = " ".join(part for part in parts if part)
            return _redact_sensitive_text(_truncate_text(joined, 600))
        if isinstance(content, str):
            text = _redact_sensitive_text(content.strip())
            if not text:
                return ""
            try:
                inner = json.loads(text)
            except Exception:
                return text
            return _extract_content(inner)
        return _redact_sensitive_text(str(content).strip())

    try:
        outer = json.loads(sanitized)
    except Exception:
        if sanitized.lstrip().startswith(("{", "[")):
            return _redact_sensitive_text(_truncate_text(sanitized, MAX_LLM_ASK_RESPONSE_CHARS))
        return _redact_sensitive_text(_truncate_text(sanitized, MAX_LLM_ASK_RESPONSE_CHARS))

    if isinstance(outer, dict):
        if "choices" in outer and isinstance(outer["choices"], list) and outer["choices"]:
            choice = outer["choices"][0]
            if isinstance(choice, dict):
                message = choice.get("message") or {}
                if isinstance(message, dict) and "content" in message:
                    return _extract_content(message["content"])
                if "text" in choice:
                    return _extract_content(choice["text"])
        if "output_text" in outer:
            return _extract_content(outer["output_text"])
        if "content" in outer:
            return _extract_content(outer["content"])
        if "answer" in outer:
            return _extract_content(outer["answer"])
        if "response" in outer:
            return _extract_content(outer["response"])
        if "review" in outer:
            return _extract_content(outer["review"])
        return _extract_content(outer)
    return _extract_content(outer)


def merge_deterministic_and_llm_findings(
    deterministic_findings: list[Finding],
    llm_review: LLMReview | None,
) -> tuple[list[Finding], str | None]:
    combined = list(deterministic_findings)
    llm_notes = None
    if llm_review:
        combined.extend(llm_review.findings)
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
                raise AgentRouterResponseError("empty ask response")
            if not _publish_comment(client, _issue_comment_path(client, pr_context.issue_number), answer):
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
            fallback = _build_ask_router_warning("resposta inválida")
            _log_agent_router_failure("resposta inválida", base_url=router_base_url, model=router_model, timeout_seconds=router_timeout_seconds)
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
            router_payload = build_agent_router_payload(pr_context, deterministic_findings, model=router_model)
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
