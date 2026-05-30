#!/usr/bin/env python3
"""Parse offline AgentReview chunk response JSON files."""

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

from app.agent_review.chunk_result_parser import (  # noqa: E402
    ChunkResultParserError,
    load_chunk_plan,
    load_json_object,
    parse_chunk_results,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse offline AgentReview chunk result responses.")
    parser.add_argument("--chunk-plan", required=True)
    parser.add_argument("--responses-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--intake")
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-parse-chunks")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_path = Path(args.output).resolve()
    try:
        chunk_plan_raw = load_json_object(args.chunk_plan, error_class="chunk_plan_invalid")
        intake_raw = load_json_object(args.intake, error_class="intake_invalid") if args.intake else {}
        path_error = _target_write_error(output_path, chunk_plan_raw, intake_raw)
        if path_error:
            return _fail_json(
                "target_repo_write_blocked",
                path_error,
                limitations=["target_repo_must_not_be_modified"],
            )

        chunk_plan = load_chunk_plan(args.chunk_plan)
        results = parse_chunk_results(chunk_plan, responses_dir=args.responses_dir)
    except ChunkResultParserError as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_to_json(results.model_dump(mode="json")), encoding="utf-8")
    print(_to_json({"ok": results.status != "failed", "status": results.status, "output_written": True}))
    return 0 if results.status != "failed" else 1


def _target_write_error(output_path: Path, *documents: dict[str, Any]) -> str | None:
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            if _is_relative_to(output_path, root) or output_path == root:
                return "Blocked: target repo must not be modified by AgentReview chunk parser output."
    return None


def _declared_target_paths(document: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for key in ("target_repo_path", "repo_root", "target_repo_root"):
        value = document.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    profile = document.get("target_profile")
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
        return "Blocked: AgentReview chunk parsing is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview chunk parsing requires dev/toolrepo agent_review_tooling environment."


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
