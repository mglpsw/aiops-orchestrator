#!/usr/bin/env python3
"""Synthesize offline AgentReview chunk results into final review artifacts."""

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

from app.agent_review.final_synthesizer import (  # noqa: E402
    FinalSynthesizerError,
    load_chunk_results,
    load_intake,
    load_json_object,
    load_redaction_report,
    load_semantic_chunk_plan,
    render_final_review_markdown,
    synthesize_final_review,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthesize offline AgentReview final review artifacts.")
    parser.add_argument("--chunk-results", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-md", required=True)
    parser.add_argument("--intake")
    parser.add_argument("--chunk-plan")
    parser.add_argument("--redaction-report")
    parser.add_argument("--max-findings", type=int, default=10)
    parser.add_argument("--max-risks", type=int, default=10)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-synthesize")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_json = Path(args.output_json).resolve()
    output_md = Path(args.output_md).resolve()
    if output_json == output_md:
        return _fail_json(
            "outputs_not_distinct",
            "Blocked: output-json and output-md must be different files.",
            limitations=["outputs_must_be_distinct"],
        )
    if args.max_findings < 0 or args.max_risks < 0:
        return _fail_json(
            "limit_invalid",
            "Blocked: max-findings and max-risks must be zero or greater.",
            limitations=["limit_invalid"],
        )

    try:
        documents = _load_raw_documents(args)
        path_error = _target_write_error([output_json, output_md], *documents)
        if path_error:
            return _fail_json(
                "target_repo_write_blocked",
                path_error,
                limitations=["target_repo_must_not_be_modified"],
            )

        chunk_results = load_chunk_results(args.chunk_results)
        intake = load_intake(args.intake) if args.intake else None
        chunk_plan = load_semantic_chunk_plan(args.chunk_plan) if args.chunk_plan else None
        redaction_report = load_redaction_report(args.redaction_report) if args.redaction_report else None
        review = synthesize_final_review(
            chunk_results,
            intake=intake,
            chunk_plan=chunk_plan,
            redaction_report=redaction_report,
        )
        markdown = render_final_review_markdown(
            review,
            max_findings=args.max_findings,
            max_risks=args.max_risks,
        )
    except FinalSynthesizerError as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(_to_json(review.model_dump(mode="json")), encoding="utf-8")
    output_md.write_text(markdown, encoding="utf-8")

    print(_to_json({"ok": True, "status": review.status, "verdict": review.verdict, "outputs_written": True}))
    return 0


def _load_raw_documents(args: argparse.Namespace) -> list[dict[str, Any]]:
    documents = [load_json_object(args.chunk_results, error_class="chunk_results_invalid")]
    if args.intake:
        documents.append(load_json_object(args.intake, error_class="intake_invalid"))
    if args.chunk_plan:
        documents.append(load_json_object(args.chunk_plan, error_class="chunk_plan_invalid"))
    if args.redaction_report:
        documents.append(load_json_object(args.redaction_report, error_class="redaction_report_invalid"))
    return documents


def _target_write_error(output_paths: list[Path], *documents: dict[str, Any]) -> str | None:
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            for output_path in output_paths:
                if _is_relative_to(output_path, root) or output_path == root:
                    return "Blocked: target repo must not be modified by AgentReview final review output."
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
        return "Blocked: AgentReview final synthesis is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview final synthesis requires dev/toolrepo agent_review_tooling environment."


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
