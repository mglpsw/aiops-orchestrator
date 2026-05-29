#!/usr/bin/env python3
"""Generate offline AgentReview semantic chunk plan JSON."""

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

from app.agent_review.semantic_chunker import (  # noqa: E402
    IntakeValidationError,
    build_semantic_chunk_plan,
    load_intake,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate offline AgentReview semantic chunk plan.")
    parser.add_argument("--intake", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-blocks", type=int, default=6)
    parser.add_argument("--max-chars-per-block", type=int, default=24_000)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-plan-chunks")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_path = Path(args.output).resolve()
    try:
        intake = load_intake(args.intake)
        path_error = _target_write_error(intake, output_path)
        if path_error:
            return _fail_json(
                "target_repo_write_blocked",
                path_error,
                limitations=["target_repo_must_not_be_modified"],
            )
        plan = build_semantic_chunk_plan(
            intake,
            max_blocks=args.max_blocks,
            max_chars_per_block=args.max_chars_per_block,
        )
    except IntakeValidationError as exc:
        return _fail_json("intake_invalid", str(exc), limitations=["intake_invalid"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_to_json(plan.model_dump(mode="json")), encoding="utf-8")
    print(_to_json({"ok": plan.status != "failed", "status": plan.status, "output_written": True}))
    return 0 if plan.status != "failed" else 1


def _target_write_error(intake: dict[str, Any], output_path: Path) -> str | None:
    for candidate in _declared_target_paths(intake):
        root = Path(candidate).expanduser().resolve()
        if _is_relative_to(output_path, root) or output_path == root:
            return "Blocked: target repo must not be modified by AgentReview chunk planner output."
    return None


def _declared_target_paths(intake: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("target_repo_path", "repo_root", "target_repo_root"):
        value = intake.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    profile = intake.get("target_profile")
    if isinstance(profile, dict):
        for key in ("target_repo_path", "repo_root", "target_repo_root"):
            value = profile.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
    return candidates


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _environment_block_message(context: dict[str, Any]) -> str:
    if (
        context.get("environment") == "prod"
        or context.get("node_role") == "runtime"
        or context.get("production_runtime") is True
    ):
        return "Blocked: AgentReview chunk planning is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview chunk planning requires dev/toolrepo agent_review_tooling environment."


def _fail_json(error_class: str, message: str, *, limitations: list[str]) -> int:
    print(
        _to_json(
            {
                "ok": False,
                "status": "failed",
                "error_class": error_class,
                "message": message,
                "limitations": _dedupe(limitations),
            }
        )
    )
    return 1


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
