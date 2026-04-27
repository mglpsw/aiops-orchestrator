"""Fixed read-only runner for allowlisted AIOps v1 actions.

This runner never accepts shell text from requests or YAML. HTTP actions are
performed through fixed URLs, and the local inspection actions use subprocess
only through a tightly controlled helper with shell=False, fixed argv, fixed
cwd, sanitized env, timeout, truncation, and redaction.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import httpx

from app.core.config import BASE_DIR, get_settings

_HTTP_ACTION_ENDPOINTS: dict[str, str] = {
    "curl_health_8000": "http://127.0.0.1:8000/health",
    "curl_ready_8000": "http://127.0.0.1:8000/ready",
    "curl_health_8001": "http://127.0.0.1:8001/health",
    "curl_ready_8001": "http://127.0.0.1:8001/ready",
}

_PROCESS_ACTIONS = frozenset(
    {
        "git_status",
        "docker_compose_config",
        "git_diff_stat",
        "docker_compose_bluegreen_config",
        "systemctl_status_aiops",
        "journalctl_aiops_recent",
    }
)
_FIXED_PATH = "/usr/bin:/bin"

_SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)authorization\s*:\s*bearer\s+[^\s\"']+"), "[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]+"), "[REDACTED]"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9-]{8,}\b"), "[REDACTED]"),
    (re.compile(r"(?i)\bx-api-key\s*[:=]\s*([^\s,;]+)"), "[REDACTED]"),
    (
        re.compile(r'(?i)"(?:authorization|api[_-]?key|token|secret|password)"\s*:\s*"[^"]*"'),
        '"[REDACTED]":"[REDACTED]"',
    ),
    (re.compile(r"(?i)\b(?:api[_-]?key|token|secret|password|passwd|pwd|private_key|access_key|refresh_token|session|cookie|set-cookie|client_secret|database_url)\b\s*[:=]\s*([^\s,;]+)"), "[REDACTED]"),
    (re.compile(r"(?i)\b(?:postgres|mysql|redis)://[^\s\"']+"), "[REDACTED]"),
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


def resolve_action_repo_root() -> Path:
    settings = get_settings()
    path = Path(settings.action_repo_root)
    if not path.is_absolute():
        path = BASE_DIR / path
    path = path.resolve()
    if not path.exists() or not path.is_dir():
        raise ActionRunError(f"Action repo root not found: {path}")
    required = [path / "config" / "actions.yaml", path / "deploy" / "docker-compose.yml"]
    missing = [candidate for candidate in required if not candidate.exists()]
    if missing:
        raise ActionRunError(f"Action repo root missing expected files: {', '.join(str(item) for item in missing)}")
    return path


def allowed_action_ids() -> frozenset[str]:
    return frozenset(_HTTP_ACTION_ENDPOINTS) | _PROCESS_ACTIONS


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


def _sanitized_env(cwd: Path) -> dict[str, str]:
    return {
        "PATH": _FIXED_PATH,
        "HOME": str(cwd),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "COMPOSE_DISABLE_ENV_FILE": "1",
        "COMPOSE_PROJECT_NAME": "aiops-orchestrator",
    }


def _format_success_preview(action_id: str, stdout: str, stderr: str, success_message: str | None) -> str:
    if success_message is not None:
        return success_message
    parts = [part for part in (stdout.strip(), stderr.strip()) if part]
    return "\n".join(parts)


def _run_fixed_process(
    *,
    action_id: str,
    argv: list[str],
    cwd: Path,
    timeout_seconds: int,
    success_message: str | None = None,
) -> ActionExecutionResult:
    settings = get_settings()
    started = perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603 - tightly controlled allowlisted runner
            argv,
            shell=False,
            cwd=str(cwd),
            env=_sanitized_env(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        status = "ok" if completed.returncode == 0 else "failed"
        exit_code = int(completed.returncode)
        if status == "ok":
            raw_preview = _format_success_preview(action_id, stdout, stderr, success_message)
        else:
            raw_preview = _format_success_preview(action_id, stdout, stderr, None)
        preview, truncated = _truncate_text(_redact_sensitive_text(raw_preview), settings.run_output_max_bytes)
        return ActionExecutionResult(
            action_id=action_id,
            status=status,
            exit_code=exit_code,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            output_preview=preview,
            truncated=truncated,
        )
    except subprocess.TimeoutExpired:
        preview, truncated = _truncate_text(
            _redact_sensitive_text(f"{action_id} timed out after {timeout_seconds}s"),
            settings.run_output_max_bytes,
        )
        return ActionExecutionResult(
            action_id=action_id,
            status="failed",
            exit_code=124,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            output_preview=preview,
            truncated=truncated,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        exit_code = 127 if isinstance(exc, FileNotFoundError) else 126 if isinstance(exc, PermissionError) else 1
        preview, truncated = _truncate_text(_redact_sensitive_text(str(exc)), settings.run_output_max_bytes)
        return ActionExecutionResult(
            action_id=action_id,
            status="failed",
            exit_code=exit_code,
            duration_ms=max(1, int((perf_counter() - started) * 1000)),
            output_preview=preview,
            truncated=truncated,
        )


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
    return await _run_http_get("curl_health_8000", _HTTP_ACTION_ENDPOINTS["curl_health_8000"])


async def run_curl_ready_8000() -> ActionExecutionResult:
    return await _run_http_get("curl_ready_8000", _HTTP_ACTION_ENDPOINTS["curl_ready_8000"])


async def run_curl_health_8001() -> ActionExecutionResult:
    return await _run_http_get("curl_health_8001", _HTTP_ACTION_ENDPOINTS["curl_health_8001"])


async def run_curl_ready_8001() -> ActionExecutionResult:
    return await _run_http_get("curl_ready_8001", _HTTP_ACTION_ENDPOINTS["curl_ready_8001"])


async def run_git_status() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="git_status",
        argv=["git", "status", "--short", "--branch"],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
    )


async def run_docker_compose_config() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="docker_compose_config",
        argv=["docker", "compose", "-f", "deploy/docker-compose.yml", "config", "--quiet"],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
        success_message="docker compose config valid",
    )


async def run_git_diff_stat() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="git_diff_stat",
        argv=["git", "diff", "--stat"],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
    )


async def run_docker_compose_bluegreen_config() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="docker_compose_bluegreen_config",
        argv=[
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "-f",
            "deploy/docker-compose.bluegreen.yml",
            "config",
            "--quiet",
        ],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
        success_message="docker compose bluegreen config valid",
    )


async def run_systemctl_status_aiops() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="systemctl_status_aiops",
        argv=[
            "systemctl",
            "show",
            "aiops-orchestrator.service",
            "--no-pager",
            "--property=Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,MainPID,ActiveEnterTimestamp,InactiveEnterTimestamp,NRestarts",
        ],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
    )


async def run_journalctl_aiops_recent() -> ActionExecutionResult:
    repo_root = resolve_action_repo_root()
    settings = get_settings()
    return await asyncio.to_thread(
        _run_fixed_process,
        action_id="journalctl_aiops_recent",
        argv=[
            "journalctl",
            "-u",
            "aiops-orchestrator.service",
            "--no-pager",
            "--since",
            "-15 minutes",
            "-n",
            "100",
            "-o",
            "short-iso",
        ],
        cwd=repo_root,
        timeout_seconds=settings.run_timeout_seconds,
    )


_RUNNERS = {
    "curl_health_8000": run_curl_health_8000,
    "curl_ready_8000": run_curl_ready_8000,
    "curl_health_8001": run_curl_health_8001,
    "curl_ready_8001": run_curl_ready_8001,
    "git_status": run_git_status,
    "docker_compose_config": run_docker_compose_config,
    "git_diff_stat": run_git_diff_stat,
    "docker_compose_bluegreen_config": run_docker_compose_bluegreen_config,
    "systemctl_status_aiops": run_systemctl_status_aiops,
    "journalctl_aiops_recent": run_journalctl_aiops_recent,
}


async def execute_action(action_id: str) -> ActionExecutionResult:
    runner = _RUNNERS.get(action_id)
    if runner is None:
        raise ActionRunError(f"action_id '{action_id}' is not executable in run v1")
    return await runner()
