#!/usr/bin/env python3
"""Evaluate deterministic AgentReview final review quality."""

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

from app.agent_review.quality_gate import (  # noqa: E402
    QualityGateError,
    evaluate_review_quality_gate,
    load_checks,
    load_chunk_results,
    load_final_review,
    load_intake,
    load_json_object,
    load_redaction_report,
    load_semantic_chunk_plan,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic AgentReview final quality gate.")
    parser.add_argument("--final-review", required=True)
    parser.add_argument("--chunk-results", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--intake")
    parser.add_argument("--chunk-plan")
    parser.add_argument("--redaction-report")
    parser.add_argument("--checks")
    parser.add_argument("--critical-pr", action="store_true", default=False)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-quality-gate")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_path = Path(args.output).resolve()
    input_paths = _input_paths(args)
    overwrite_error = _input_overwrite_error(output_path, input_paths)
    if overwrite_error:
        return _fail_json(
            "output_overwrites_input",
            overwrite_error,
            limitations=["output_must_not_overwrite_input"],
        )

    try:
        raw_documents = _load_raw_documents(args)
        path_error = _target_write_error(output_path, *raw_documents)
        if path_error:
            return _fail_json(
                "target_repo_write_blocked",
                path_error,
                limitations=["target_repo_must_not_be_modified"],
            )

        final_review = load_final_review(args.final_review)
        chunk_results = load_chunk_results(args.chunk_results)
        intake = load_intake(args.intake) if args.intake else None
        chunk_plan = load_semantic_chunk_plan(args.chunk_plan) if args.chunk_plan else None
        redaction_report = load_redaction_report(args.redaction_report) if args.redaction_report else None
        checks = load_checks(args.checks) if args.checks else None
        gate = evaluate_review_quality_gate(
            final_review,
            chunk_results,
            intake=intake,
            chunk_plan=chunk_plan,
            redaction_report=redaction_report,
            checks=checks,
            critical_pr=args.critical_pr,
        )
    except QualityGateError as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_to_json(gate.model_dump(mode="json")), encoding="utf-8")
    print(
        _to_json(
            {
                "ok": gate.status != "failed",
                "status": gate.status,
                "normalized_verdict": gate.normalized_verdict,
                "manual_review_required": gate.manual_review_required,
                "output_written": True,
            }
        )
    )
    return 0


def _input_paths(args: argparse.Namespace) -> list[Path]:
    paths = [
        args.final_review,
        args.chunk_results,
        args.intake,
        args.chunk_plan,
        args.redaction_report,
        args.checks,
    ]
    return [Path(path).resolve() for path in paths if path]


def _input_overwrite_error(output_path: Path, input_paths: list[Path]) -> str | None:
    if any(output_path == input_path for input_path in input_paths):
        return "Blocked: quality gate output must not overwrite any input artifact."
    return None


def _load_raw_documents(args: argparse.Namespace) -> list[dict[str, Any]]:
    documents = [
        load_json_object(args.final_review, error_class="final_review_invalid"),
        load_json_object(args.chunk_results, error_class="chunk_results_invalid"),
    ]
    if args.intake:
        documents.append(load_json_object(args.intake, error_class="intake_invalid"))
    if args.chunk_plan:
        documents.append(load_json_object(args.chunk_plan, error_class="chunk_plan_invalid"))
    if args.redaction_report:
        documents.append(load_json_object(args.redaction_report, error_class="redaction_report_invalid"))
    if args.checks:
        documents.append(load_json_object(args.checks, error_class="checks_invalid"))
    return documents


def _target_write_error(output_path: Path, *documents: dict[str, Any]) -> str | None:
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            if _is_relative_to(output_path, root) or output_path == root:
                return "Blocked: target repo must not be modified by AgentReview quality gate output."
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
        return "Blocked: AgentReview quality gate is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview quality gate requires dev/toolrepo agent_review_tooling environment."


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
