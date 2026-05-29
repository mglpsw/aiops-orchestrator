#!/usr/bin/env python3
"""Fail-closed guard for AIOps runtime/toolrepo boundaries."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.environment_context import (  # noqa: E402
    build_environment_context,
    has_complete_valid_env_override,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate AIOps environment boundaries.")
    parser.add_argument("--require-mode", choices=("agent_review_tooling", "aiops_runtime"))
    parser.add_argument("--deny-production-runtime", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    context = build_environment_context(os.environ, source="guard-aiops-environment")
    ok, message = evaluate_guard(
        context,
        require_mode=args.require_mode,
        deny_production_runtime=args.deny_production_runtime,
        env=os.environ,
    )

    if args.json:
        _emit_json(ok, message, context)
    else:
        print(message)
    return 0 if ok else 1


def evaluate_guard(
    context: dict[str, Any],
    *,
    require_mode: str | None,
    deny_production_runtime: bool,
    env: dict[str, str],
) -> tuple[bool, str]:
    if _has_invalid_config(context) and not has_complete_valid_env_override(env):
        return False, "Blocked: environment config is invalid and no complete environment override was provided."

    if deny_production_runtime and context["production_runtime"] is True:
        return False, "Blocked: production runtime is denied for this operation."

    if require_mode == "agent_review_tooling":
        return _evaluate_agent_review_tooling(context)

    if require_mode == "aiops_runtime":
        return _evaluate_aiops_runtime(context)

    return True, "Allowed: environment guard passed."


def _evaluate_agent_review_tooling(context: dict[str, Any]) -> tuple[bool, str]:
    if _is_production_runtime(context):
        return False, "Blocked: agent_review_tooling is not allowed on production runtime."

    if (
        context["repo_mode"] == "agent_review_tooling"
        and context["node_role"] == "toolrepo"
        and context["production_runtime"] is False
        and context["environment"] in {"dev", "test"}
    ):
        return True, "Allowed: agent_review_tooling environment confirmed."

    return False, "Blocked: agent_review_tooling requires dev/test toolrepo context."


def _evaluate_aiops_runtime(context: dict[str, Any]) -> tuple[bool, str]:
    if (
        context["environment"] == "prod"
        and context["node_role"] == "runtime"
        and context["repo_mode"] == "aiops_runtime"
        and context["production_runtime"] is True
    ):
        return True, "Allowed: aiops_runtime environment confirmed."

    return False, "Blocked: aiops_runtime requires prod runtime context."


def _is_production_runtime(context: dict[str, Any]) -> bool:
    return (
        context["environment"] == "prod"
        or context["node_role"] == "runtime"
        or context["production_runtime"] is True
    )


def _has_invalid_config(context: dict[str, Any]) -> bool:
    return "environment_config_invalid" in context.get("limitations", [])


def _emit_json(ok: bool, message: str, context: dict[str, Any]) -> None:
    json.dump({"ok": ok, "message": message, "context": context}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
