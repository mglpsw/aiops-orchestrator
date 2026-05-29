"""Offline environment context parsing for AIOps tooling boundaries."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1

AIOPS_ENVIRONMENT = "AIOPS_ENVIRONMENT"
AIOPS_NODE_ROLE = "AIOPS_NODE_ROLE"
AIOPS_REPO_MODE = "AIOPS_REPO_MODE"
AIOPS_PRODUCTION_RUNTIME = "AIOPS_PRODUCTION_RUNTIME"
AIOPS_ENVIRONMENT_CONFIG = "AIOPS_ENVIRONMENT_CONFIG"

KNOWN_ENVIRONMENTS = {"prod", "dev", "test"}
KNOWN_NODE_ROLES = {"runtime", "toolrepo", "ci"}
KNOWN_REPO_MODES = {"aiops_runtime", "agent_review_tooling", "ci"}

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def build_environment_context(
    env: Mapping[str, str] | None = None,
    *,
    source: str = "environment_context",
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a normalized, read-only context from env vars and optional YAML."""

    environ = env if env is not None else os.environ
    explicit_config_path = config_path if config_path is not None else environ.get(AIOPS_ENVIRONMENT_CONFIG)
    config, config_limitations, error_class = load_environment_config(explicit_config_path)

    values = _extract_config_environment(config)
    values.update(_extract_env_overrides(environ))

    limitations = list(config_limitations)
    schema_version = _schema_version(config)

    environment = _normalize_choice(
        values.get("environment"),
        KNOWN_ENVIRONMENTS,
        "environment",
        limitations,
    )
    node_role = _normalize_choice(
        values.get("node_role"),
        KNOWN_NODE_ROLES,
        "node_role",
        limitations,
    )
    repo_mode = _normalize_choice(
        values.get("repo_mode"),
        KNOWN_REPO_MODES,
        "repo_mode",
        limitations,
    )
    production_runtime = _normalize_bool(
        values.get("production_runtime"),
        "production_runtime",
        limitations,
    )

    if environment == "unknown" or node_role == "unknown" or repo_mode == "unknown":
        _add_limitation(limitations, "environment_not_declared")

    agent_review_tooling_allowed = (
        environment in {"dev", "test"}
        and node_role == "toolrepo"
        and repo_mode == "agent_review_tooling"
        and production_runtime is False
    )

    context: dict[str, Any] = {
        "schema_version": schema_version,
        "source": source,
        "environment": environment,
        "node_role": node_role,
        "repo_mode": repo_mode,
        "production_runtime": production_runtime,
        "agent_review_tooling_allowed": agent_review_tooling_allowed,
        "limitations": limitations,
    }
    if error_class:
        context["error_class"] = error_class
    return context


def load_environment_config(path: str | Path | None) -> tuple[dict[str, Any], list[str], str | None]:
    """Load a non-secret example/config YAML file, returning non-fatal errors."""

    if not path:
        return {}, [], None

    resolved = Path(path).expanduser()
    if not resolved.exists():
        return {}, ["environment_config_not_found"], None

    try:
        import yaml

        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - config errors are reported in JSON.
        return {}, ["environment_config_invalid"], exc.__class__.__name__

    if raw is None:
        return {}, [], None
    if not isinstance(raw, dict):
        return {}, ["environment_config_invalid"], "InvalidEnvironmentConfig"
    return raw, [], None


def has_complete_valid_env_override(env: Mapping[str, str] | None = None) -> bool:
    """Return true when env vars alone provide a complete valid context."""

    environ = env if env is not None else os.environ
    required_keys = (
        AIOPS_ENVIRONMENT,
        AIOPS_NODE_ROLE,
        AIOPS_REPO_MODE,
        AIOPS_PRODUCTION_RUNTIME,
    )
    if any(environ.get(key) in (None, "") for key in required_keys):
        return False

    limitations: list[str] = []
    environment = _normalize_choice(environ.get(AIOPS_ENVIRONMENT), KNOWN_ENVIRONMENTS, "environment", limitations)
    node_role = _normalize_choice(environ.get(AIOPS_NODE_ROLE), KNOWN_NODE_ROLES, "node_role", limitations)
    repo_mode = _normalize_choice(environ.get(AIOPS_REPO_MODE), KNOWN_REPO_MODES, "repo_mode", limitations)
    production_runtime = _normalize_bool(environ.get(AIOPS_PRODUCTION_RUNTIME), "production_runtime", limitations)

    return (
        not limitations
        and environment != "unknown"
        and node_role != "unknown"
        and repo_mode != "unknown"
        and isinstance(production_runtime, bool)
    )


def _extract_env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    mapping = {
        AIOPS_ENVIRONMENT: "environment",
        AIOPS_NODE_ROLE: "node_role",
        AIOPS_REPO_MODE: "repo_mode",
        AIOPS_PRODUCTION_RUNTIME: "production_runtime",
    }
    for env_key, context_key in mapping.items():
        if env_key in env:
            overrides[context_key] = env[env_key]
    return overrides


def _extract_config_environment(config: Mapping[str, Any]) -> dict[str, Any]:
    raw_environment = config.get("environment")
    environment = raw_environment if isinstance(raw_environment, Mapping) else {}

    return {
        "environment": environment.get("name", config.get("environment_name")),
        "node_role": environment.get("node_role", config.get("node_role")),
        "repo_mode": environment.get("repo_mode", config.get("repo_mode")),
        "production_runtime": environment.get("production_runtime", config.get("production_runtime")),
    }


def _schema_version(config: Mapping[str, Any]) -> int:
    raw = config.get("schema_version", SCHEMA_VERSION)
    if isinstance(raw, bool):
        return SCHEMA_VERSION
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return SCHEMA_VERSION


def _normalize_choice(
    value: Any,
    allowed: set[str],
    field_name: str,
    limitations: list[str],
) -> str:
    if value is None:
        return "unknown"

    normalized = str(value).strip().lower()
    if not normalized:
        return "unknown"
    if normalized not in allowed:
        _add_limitation(limitations, f"invalid_{field_name}")
        return "unknown"
    return normalized


def _normalize_bool(value: Any, field_name: str, limitations: list[str]) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    _add_limitation(limitations, f"invalid_{field_name}")
    return False


def _add_limitation(limitations: list[str], limitation: str) -> None:
    if limitation not in limitations:
        limitations.append(limitation)
