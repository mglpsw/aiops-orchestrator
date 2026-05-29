"""Deterministic redaction for AgentReview intake artifacts."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.agent_review.schemas import RedactionReport


REDACTED = "[REDACTED]"

SENSITIVE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "password",
    "secret",
    "client_secret",
    "access_token",
    "refresh_token",
}

PLACEHOLDER_VALUES = {
    REDACTED.lower(),
    "***masked***",
    "placeholder",
    "fake-token",
    "test-token",
    "dummy",
    "example",
    "local-only",
}

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTHORIZATION_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*)bearer\s+([^\s,;]+)")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{8,})")
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(token|api_key|password|secret|client_secret|access_token|refresh_token)\s*=\s*([^\s&;,\"']+)"
)
_COOKIE_RE = re.compile(r"(?i)\b(set-cookie|cookie)\s*:\s*([^\r\n]+)")
_GITHUB_TOKEN_RE = re.compile(r"\b(ghp_[A-Za-z0-9_]{10,}|github_pat_[A-Za-z0-9_]{10,})\b")
_OPENAI_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(DATABASE_URL\s*=\s*)([a-z][a-z0-9+.-]*://)([^:\s/@]+):([^@\s]+)@"
)
_CREDENTIAL_URL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^:/@\s]+):([^/@\s]+)@")


class RedactionState:
    def __init__(self) -> None:
        self.files_processed = 0
        self.replacements_by_type: Counter[str] = Counter()
        self.limitations: list[str] = []

    @property
    def secret_like_values_found(self) -> int:
        return sum(self.replacements_by_type.values())

    def record_file(self) -> None:
        self.files_processed += 1

    def record(self, replacement_type: str) -> None:
        self.replacements_by_type[replacement_type] += 1

    def to_report(self, *, source: str = "aiops-review-intake", output_safe_for_llm: bool = True) -> RedactionReport:
        redacted = self.secret_like_values_found > 0
        return RedactionReport(
            source=source,
            files_processed=self.files_processed,
            replacements_by_type=dict(sorted(self.replacements_by_type.items())),
            secret_like_values_found=self.secret_like_values_found,
            redacted_lines_present=redacted,
            redaction_is_sanitizer_artifact=redacted,
            hardcoded_secret_confirmed=False,
            output_safe_for_llm=output_safe_for_llm,
            limitations=list(self.limitations),
        )


def redact_content(value: Any, *, source: str = "redaction-test") -> tuple[Any, RedactionReport]:
    state = RedactionState()
    state.record_file()
    redacted = redact_value(value, state)
    return redacted, state.to_report(source=source)


def redact_value(value: Any, state: RedactionState) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_field(key, child, state)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, state) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, state) for item in value]
    if isinstance(value, str):
        return redact_text(value, state)
    return value


def redact_text(text: str, state: RedactionState) -> str:
    redacted = _sub_private_keys(text, state)
    redacted = _sub_authorization_bearer(redacted, state)
    redacted = _sub_bearer(redacted, state)
    redacted = _sub_assignments(redacted, state)
    redacted = _sub_cookie_headers(redacted, state)
    redacted = _sub_simple_tokens(redacted, state)
    redacted = _sub_database_urls(redacted, state)
    redacted = _sub_credential_urls(redacted, state)
    return redacted


def _redact_sensitive_field(key: Any, value: Any, state: RedactionState) -> Any:
    if _normalize_key(key) in SENSITIVE_KEYS and isinstance(value, str) and not _is_placeholder(value):
        state.record("sensitive_json_field")
        return REDACTED
    return redact_value(value, state)


def _sub_private_keys(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("private_key")
        return REDACTED

    return _PRIVATE_KEY_RE.sub(replace, text)


def _sub_authorization_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(2)
        if _is_placeholder(token):
            return match.group(0)
        state.record("authorization_bearer")
        return f"{match.group(1)}Bearer {REDACTED}"

    return _AUTHORIZATION_BEARER_RE.sub(replace, text)


def _sub_bearer(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token) or token == REDACTED:
            return match.group(0)
        state.record("bearer_token")
        return f"Bearer {REDACTED}"

    return _BEARER_RE.sub(replace, text)


def _sub_assignments(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(2)
        if _is_placeholder(value):
            return match.group(0)
        state.record(f"{match.group(1).lower()}_assignment")
        return f"{match.group(1)}={REDACTED}"

    return _ASSIGNMENT_RE.sub(replace, text)


def _sub_cookie_headers(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(2)
        if _is_placeholder(value):
            return match.group(0)
        state.record("cookie")
        return f"{match.group(1)}: {REDACTED}"

    return _COOKIE_RE.sub(replace, text)


def _sub_simple_tokens(text: str, state: RedactionState) -> str:
    def replace_github(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_placeholder(token):
            return token
        state.record("github_token")
        return REDACTED

    def replace_openai(match: re.Match[str]) -> str:
        token = match.group(0)
        if _is_placeholder(token):
            return token
        state.record("openai_token")
        return REDACTED

    return _OPENAI_TOKEN_RE.sub(replace_openai, _GITHUB_TOKEN_RE.sub(replace_github, text))


def _sub_database_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        state.record("database_url_credentials")
        return f"{match.group(1)}{match.group(2)}{REDACTED}:{REDACTED}@"

    return _DATABASE_URL_RE.sub(replace, text)


def _sub_credential_urls(text: str, state: RedactionState) -> str:
    def replace(match: re.Match[str]) -> str:
        username = match.group(2)
        password = match.group(3)
        if REDACTED in {username, password} or _is_placeholder(username) or _is_placeholder(password):
            return match.group(0)
        state.record("url_credentials")
        return f"{match.group(1)}{REDACTED}:{REDACTED}@"

    return _CREDENTIAL_URL_RE.sub(replace, text)


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().lower().strip("\"'")
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith("example") or normalized.endswith("-example"):
        return True
    return False
