"""Centralized configuration loaded from environment and YAML files."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


BASE_DIR = Path(__file__).resolve().parent.parent.parent  # aiops/


def _load_yaml(name: str) -> dict[str, Any]:
    path = BASE_DIR / "config" / name
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


class Settings(BaseSettings):
    # --- General ---
    app_name: str = "aiops-orchestrator"
    app_version: str = "0.1.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_format: str = "json"  # json | text

    # --- Auth ---
    api_token: str = Field(
        default="",
        validation_alias=AliasChoices("AGENT_ROUTER_API_TOKEN", "AIOPS_API_TOKEN"),
        description="Shared token for protecting sensitive API routes",
    )

    # --- Database ---
    database_url: str = Field(default="sqlite+aiosqlite:///data/aiops.db")

    # --- Providers ---
    ollama_base_url: str = "http://192.168.3.87:11434"
    ollama_default_model: str = "gemma3:4b"

    claude_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-5-nano"     # kept for backward compat
    gpt_model: str = "gpt-5-nano"        # $0.20/$1.25 per 1M (April 2026); needs max_completion_tokens>=4096
    codex_model: str = "gpt-4.1-nano"    # $0.10/$0.40 per 1M; 4× cheaper than gpt-4.1-mini

    # --- Provider routing defaults ---
    planner_default: str = "claude"
    planner_fallback: str = "ollama"
    classifier_default: str = "ollama"
    executor_default: str = "local"

    # --- Executor ---
    executor_timeout_seconds: int = 120
    executor_max_output_bytes: int = 1_048_576  # 1 MB

    # --- Policy ---
    policy_mode: str = "supervised"  # safe | supervised | manual-only
    auto_approve_low_risk: bool = False

    # --- Execution authorization ---
    # Comma-separated list of emails allowed to trigger host execution.
    # Users with role=admin in Open WebUI are also always allowed.
    allowed_exec_users: str = ""
    allow_admin_role: bool = True

    # --- Audit log ---
    audit_log_path: str = "var/audit/aiops_audit.jsonl"
    audit_log_required: bool = True
    audit_log_max_bytes: int = 5_000_000
    audit_log_backup_count: int = 5
    audit_log_rotation_enabled: bool = True

    # --- Approvals ---
    approval_store_path: str = "var/approvals/aiops_approvals.jsonl"
    approval_ttl_max_seconds: int = 3600
    approval_store_max_records: int = 1000

    # --- Runs ---
    run_store_path: str = "var/runs/aiops_runs.jsonl"
    run_timeout_seconds: int = 5
    run_output_max_bytes: int = 4000

    # --- WebAI ---
    webai_base_url: str = "http://open-webui:8080"

    model_config = {"env_prefix": "AIOPS_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if os.getenv("OLLAMA_HOST"):
        settings.ollama_base_url = os.getenv("OLLAMA_HOST", "")
    if not settings.claude_api_key and os.getenv("ANTHROPIC_API_KEY"):
        settings.claude_api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not settings.openai_api_key and os.getenv("OPENAI_API_KEY"):
        settings.openai_api_key = os.getenv("OPENAI_API_KEY", "")
    return settings


def get_providers_config() -> dict[str, Any]:
    return _load_yaml("providers.yml")


def get_policies_config() -> dict[str, Any]:
    return _load_yaml("policies.yml")


def get_routes_config() -> dict[str, Any]:
    return _load_yaml("routes.yml")
