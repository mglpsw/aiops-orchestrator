"""Fixed read-only runner for the first execution-capable AIOps v1 actions.

This runner never shells out, never executes YAML command strings, and only
performs allowlisted HTTP GET requests to local health/readiness endpoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import perf_counter

import httpx

from app.core.config import get_settings

_ACTION_ENDPOINTS: dict[str, str] = {
    "curl_health_8000": "http://127.0.0.1:8000/health",
    "curl_ready_8000": "http://127.0.0.1:8000/ready",
    "curl_health_8001": "http://127.0.0.1:8001/health",
    "curl_ready_8001": "http://127.0.0.1:8001/ready",
}

_SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s\"']+"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]+"), "[REDACTED]"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9-]{8,}\b"), "[REDACTED]"),
    (re.compile(r'(?i)"(?:authorization|api[_-]?key|token|secret|password)"\s*:\s*"[^"]*"'), '"[REDACTED]":"[REDACTED]"'),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*([^\s,;]+)"), "[REDACTED]"),
)


@dataclass(frozen=True)
class ActionExecutionResult:
    action_id: str
    status: str
    exit_code: int
    duration_ms: int
    output_preview: str
    truncated: bool = False


class ActionRunError(RuntimeError):
    """Raised when a fixed internal action cannot be executed."""


def allowed_action_ids() -> frozenset[str]:
    return frozenset(_ACTION_ENDPOINTS)


def _redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern, replacement in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


redact_sensitive_text = _redact_sensitive_text


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    if max_bytes <= 0:
        return "", False
    data = text.encode("utf-8")
    if len(data) <= max_bytes:
        return text, False
    return data[:max_bytes].decode("utf-8", errors="ignore"), True


async def _run_http_get(action_id: str, url: str) -> ActionExecutionResult:
    settings = get_settings()
    started = perf_counter()
    timeout = httpx.Timeout(settings.run_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(url)
        body = _redact_sensitive_text(response.text)
        preview, truncated = _truncate_text(body, settings.run_output_max_bytes)
        status = "ok" if 200 <= response.status_code < 300 else "failed"
        exit_code = 0 if status == "ok" else response.status_code
        return ActionExecutionResult(
            action_id=action_id,
            status=status,
            exit_code=exit_code,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            output_preview=preview,
            truncated=truncated,
        )
    except Exception as exc:
        preview, truncated = _truncate_text(_redact_sensitive_text(str(exc)), settings.run_output_max_bytes)
        return ActionExecutionResult(
            action_id=action_id,
            status="failed",
            exit_code=1,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            output_preview=preview,
            truncated=truncated,
        )


async def run_curl_health_8000() -> ActionExecutionResult:
    return await _run_http_get("curl_health_8000", _ACTION_ENDPOINTS["curl_health_8000"])


async def run_curl_ready_8000() -> ActionExecutionResult:
    return await _run_http_get("curl_ready_8000", _ACTION_ENDPOINTS["curl_ready_8000"])


async def run_curl_health_8001() -> ActionExecutionResult:
    return await _run_http_get("curl_health_8001", _ACTION_ENDPOINTS["curl_health_8001"])


async def run_curl_ready_8001() -> ActionExecutionResult:
    return await _run_http_get("curl_ready_8001", _ACTION_ENDPOINTS["curl_ready_8001"])


_RUNNERS = {
    "curl_health_8000": run_curl_health_8000,
    "curl_ready_8000": run_curl_ready_8000,
    "curl_health_8001": run_curl_health_8001,
    "curl_ready_8001": run_curl_ready_8001,
}


async def execute_action(action_id: str) -> ActionExecutionResult:
    runner = _RUNNERS.get(action_id)
    if runner is None:
        raise ActionRunError(f"action_id '{action_id}' is not executable in run v1")
    return await runner()
