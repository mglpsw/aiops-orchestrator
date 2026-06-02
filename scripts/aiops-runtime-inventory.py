#!/usr/bin/env python3
"""Emit read-only AIOps runtime inventory evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0"}
BODY_SUMMARY_MAX = 240


class SafeError(Exception):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate read-only AIOps runtime inventory JSON.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--include-health-url", default="")
    parser.add_argument("--include-ready-url", default="")
    parser.add_argument("--include-metrics-url", default="")
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        output = Path(args.output).resolve()
        _validate_output_path(repo_root, output)
        urls = {
            "health": args.include_health_url,
            "ready": args.include_ready_url,
            "metrics": args.include_metrics_url,
        }
        _validate_local_urls(urls)

        payload = build_inventory(repo_root, urls)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except SafeError as exc:
        _emit_error(exc.error_class, exc.message)
        return 1


def build_inventory(repo_root: Path, urls: dict[str, str]) -> dict[str, Any]:
    limitations: list[str] = []
    git = _read_git(repo_root, limitations)
    version = _read_version(repo_root, limitations)

    payload = {
        "schema_version": 1,
        "source": "aiops-runtime-inventory",
        "repo_root": repo_root.name,
        "git": git,
        "version": version,
        "paths": _read_path_presence(repo_root),
        "local_http_checks": {
            "health": _local_http_check(urls.get("health", ""), "health", limitations),
            "ready": _local_http_check(urls.get("ready", ""), "ready", limitations),
            "metrics": _local_http_check(urls.get("metrics", ""), "metrics", limitations),
        },
        "limitations": limitations,
    }
    return payload


def _validate_output_path(repo_root: Path, output: Path) -> None:
    if output == repo_root or repo_root in output.parents:
        raise SafeError("repo_output_blocked", "Output must be outside --repo-root.")


def _validate_local_urls(urls: dict[str, str]) -> None:
    for label, url in urls.items():
        if not url:
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in LOCAL_HTTP_HOSTS:
            raise SafeError("external_url_blocked", f"{label} URL must be local-only HTTP.")
        if parsed.username or parsed.password:
            raise SafeError("url_credentials_blocked", f"{label} URL must not include credentials.")


def _read_git(repo_root: Path, limitations: list[str]) -> dict[str, str]:
    head_path = repo_root / ".git" / "HEAD"
    head_ref = ""
    head_sha = ""

    try:
        head_value = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        limitations.append("git_head_unavailable")
        return {"head_ref": "", "head_sha": "", "status": "unknown"}

    if head_value.startswith("ref: "):
        head_ref = head_value.removeprefix("ref: ").strip()
        ref_path = repo_root / ".git" / head_ref
        try:
            head_sha = ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            head_sha = _read_packed_ref(repo_root, head_ref)
            if not head_sha:
                limitations.append("git_ref_unavailable")
    else:
        head_sha = head_value
        head_ref = "detached"

    status = "observed" if head_ref or head_sha else "unknown"
    return {"head_ref": head_ref, "head_sha": head_sha, "status": status}


def _read_packed_ref(repo_root: Path, head_ref: str) -> str:
    packed_refs = repo_root / ".git" / "packed-refs"
    try:
        for line in packed_refs.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == head_ref:
                return parts[0].strip()
    except OSError:
        return ""
    return ""


def _read_version(repo_root: Path, limitations: list[str]) -> dict[str, str]:
    app_init_version = _extract_regex(
        repo_root / "app" / "__init__.py",
        r"""__version__\s*=\s*["']([^"']+)["']""",
        "app_init_version_unavailable",
        limitations,
    )
    settings_default_app_version = _extract_regex(
        repo_root / "app" / "core" / "config.py",
        r"""app_version\s*:\s*str\s*=\s*["']([^"']+)["']""",
        "settings_default_app_version_unavailable",
        limitations,
    )
    status = "observed" if app_init_version or settings_default_app_version else "unknown"
    return {
        "app_init_version": app_init_version,
        "settings_default_app_version": settings_default_app_version,
        "status": status,
    }


def _extract_regex(path: Path, pattern: str, limitation: str, limitations: list[str]) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        limitations.append(limitation)
        return ""
    match = re.search(pattern, text)
    if not match:
        limitations.append(limitation)
        return ""
    return match.group(1)


def _read_path_presence(repo_root: Path) -> dict[str, bool]:
    return {
        "config_actions_exists": (repo_root / "config" / "actions.yaml").exists(),
        "audit_dir_exists": (repo_root / "var" / "audit").exists(),
        "approvals_dir_exists": (repo_root / "var" / "approvals").exists(),
        "runs_dir_exists": (repo_root / "var" / "runs").exists(),
        "data_dir_exists": (repo_root / "data").exists(),
        "docker_compose_exists": (repo_root / "deploy" / "docker-compose.yml").exists(),
    }


def _local_http_check(url: str, label: str, limitations: list[str]) -> dict[str, Any]:
    if not url:
        return {"status": "skipped", "http_status": None, "body_summary": ""}

    try:
        response = urllib.request.urlopen(url, timeout=2)
        try:
            body = response.read(4096)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        http_status = _response_status(response)
        status = "ok" if 200 <= http_status < 400 else "failed"
        if status == "failed":
            limitations.append(f"{label}_http_status_failed")
        return {
            "status": status,
            "http_status": http_status,
            "body_summary": _summarize_body(body),
        }
    except (OSError, urllib.error.URLError) as exc:
        limitations.append(f"{label}_http_check_failed")
        return {
            "status": "failed",
            "http_status": None,
            "body_summary": _sanitize_text(exc.__class__.__name__),
        }


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        code = getcode()
        if isinstance(code, int):
            return code
    return 0


def _summarize_body(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = _sanitize_text(text)
    text = " ".join(text.split())
    return text[:BODY_SUMMARY_MAX]


def _sanitize_text(text: str) -> str:
    replacements = [
        (r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"(?i)Authorization\s*[:=]\s*[^,\s]+", "Authorization: [REDACTED]"),
        (r"""(?i)(["']?(?:token|secret|password|api[_-]?key)["']?\s*[:=]\s*["']?)[^"',\s}]+""", r"\1[REDACTED]"),
        (r"(?i)(cookie\s*[:=]\s*)[^,\s]+", r"\1[REDACTED]"),
    ]
    sanitized = text
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized


def _emit_error(error_class: str, message: str) -> None:
    json.dump({"error_class": error_class, "message": message}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
