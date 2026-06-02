#!/usr/bin/env python3
"""Emit read-only AIOps runtime postcheck evidence."""

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
    parser = argparse.ArgumentParser(description="Generate read-only AIOps runtime postcheck JSON.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--health-url", default="")
    parser.add_argument("--ready-url", default="")
    parser.add_argument("--metrics-url", default="")
    parser.add_argument("--expected-version", default="")
    args = parser.parse_args(argv)

    try:
        repo_root = Path(args.repo_root).resolve()
        output = Path(args.output).resolve()
        _validate_output_path(repo_root, output)
        urls = {
            "health": args.health_url,
            "ready": args.ready_url,
            "metrics": args.metrics_url,
        }
        _validate_local_urls(urls)

        payload = build_postcheck(repo_root, urls, args.expected_version)
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    except SafeError as exc:
        _emit_error(exc.error_class, exc.message)
        return 1


def build_postcheck(repo_root: Path, urls: dict[str, str], expected_version: str) -> dict[str, Any]:
    limitations: list[str] = []
    version_observed = _read_observed_version(repo_root, limitations)
    path_status = _path_status(repo_root)
    if path_status != "ok":
        limitations.append("minimum_paths_failed")

    http_checks = {
        "health": _local_http_check(urls.get("health", ""), "health", limitations),
        "ready": _local_http_check(urls.get("ready", ""), "ready", limitations),
        "metrics": _local_http_check(urls.get("metrics", ""), "metrics", limitations),
    }
    for label, result in http_checks.items():
        if result["status"] == "skipped":
            limitations.append(f"{label}_check_skipped")

    version_ok = True
    if expected_version:
        version_ok = version_observed == expected_version
        if not version_ok:
            limitations.append("expected_version_mismatch")

    checks = {
        "health": http_checks["health"]["status"],
        "ready": http_checks["ready"]["status"],
        "metrics": http_checks["metrics"]["status"],
        "paths": path_status,
    }
    ready_for_final_release = (
        checks["health"] == "ok"
        and checks["ready"] == "ok"
        and checks["metrics"] == "ok"
        and checks["paths"] == "ok"
        and version_ok
        and not limitations
    )

    return {
        "schema_version": 1,
        "source": "aiops-runtime-postcheck",
        "expected_version": expected_version,
        "version_observed": version_observed,
        "checks": checks,
        "ready_for_final_release": ready_for_final_release,
        "limitations": limitations,
    }


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


def _read_observed_version(repo_root: Path, limitations: list[str]) -> str:
    app_init_version = _extract_regex(
        repo_root / "app" / "__init__.py",
        r"""__version__\s*=\s*["']([^"']+)["']""",
    )
    if app_init_version:
        return app_init_version

    settings_default_app_version = _extract_regex(
        repo_root / "app" / "core" / "config.py",
        r"""app_version\s*:\s*str\s*=\s*["']([^"']+)["']""",
    )
    if settings_default_app_version:
        return settings_default_app_version

    limitations.append("version_unavailable")
    return ""


def _extract_regex(path: Path, pattern: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _path_status(repo_root: Path) -> str:
    required_paths = [
        repo_root / "config" / "actions.yaml",
        repo_root / "var" / "audit",
        repo_root / "var" / "approvals",
        repo_root / "var" / "runs",
        repo_root / "data",
        repo_root / "deploy" / "docker-compose.yml",
    ]
    return "ok" if all(path.exists() for path in required_paths) else "failed"


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
