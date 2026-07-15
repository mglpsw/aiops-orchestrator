#!/usr/bin/env python3
"""Collect deterministic AgentReview telemetry."""

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

from app.agent_review.telemetry import (  # noqa: E402
    TelemetryError,
    build_review_telemetry,
    load_final_review,
    load_json_object,
    load_optional_artifact,
    load_quality_gate,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect deterministic AgentReview telemetry.")
    parser.add_argument("--final-review", required=True)
    parser.add_argument("--quality-gate", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--chunk-results")
    parser.add_argument("--chunk-plan")
    parser.add_argument("--intake")
    parser.add_argument("--redaction-report")
    parser.add_argument("--checks")
    parser.add_argument("--validation-evidence")
    parser.add_argument("--test-intelligence")
    parser.add_argument("--local-code-intelligence")
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--commit-sha")
    parser.add_argument("--review-mode")
    parser.add_argument("--contract-pack")
    parser.add_argument("--critical-pr", action="store_true", default=None)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-telemetry")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_path = Path(args.output).resolve()
    input_paths = _input_paths(args)
    if output_path in input_paths:
        return _fail_json(
            "output_overwrites_input",
            "Blocked: telemetry output must not overwrite any input artifact.",
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
        quality_gate = load_quality_gate(args.quality_gate)
        optional, limitations = _load_optional_documents(args)
        telemetry = build_review_telemetry(
            final_review=final_review,
            quality_gate=quality_gate,
            limitations=limitations,
            pr_number=args.pr_number,
            commit_sha=args.commit_sha,
            review_mode=args.review_mode,
            contract_pack=args.contract_pack,
            critical_pr=args.critical_pr,
            **optional,
        )
    except TelemetryError as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_to_json(telemetry.model_dump(mode="json")), encoding="utf-8")
    print(
        _to_json(
            {
                "ok": telemetry.status != "failed",
                "status": telemetry.status,
                "normalized_verdict": telemetry.quality_gate.get("normalized_verdict"),
                "manual_review_required": telemetry.quality_gate.get("manual_review_required"),
                "output_written": True,
            }
        )
    )
    return 0


def _input_paths(args: argparse.Namespace) -> list[Path]:
    paths = [
        args.final_review,
        args.quality_gate,
        args.chunk_results,
        args.chunk_plan,
        args.intake,
        args.redaction_report,
        args.checks,
        args.validation_evidence,
        args.test_intelligence,
        args.local_code_intelligence,
    ]
    return [Path(path).resolve() for path in paths if path]


def _load_raw_documents(args: argparse.Namespace) -> list[dict[str, Any]]:
    documents = [
        load_json_object(args.final_review, error_class="final_review_invalid"),
        load_json_object(args.quality_gate, error_class="quality_gate_invalid"),
    ]
    for path, error_class in (
        (args.chunk_results, "chunk_results_invalid"),
        (args.chunk_plan, "chunk_plan_invalid"),
        (args.intake, "intake_invalid"),
        (args.redaction_report, "redaction_report_invalid"),
        (args.checks, "checks_invalid"),
        (args.validation_evidence, "validation_evidence_invalid"),
        (args.test_intelligence, "test_intelligence_invalid"),
        (args.local_code_intelligence, "local_code_intelligence_invalid"),
    ):
        if path:
            try:
                documents.append(load_json_object(path, error_class=error_class))
            except TelemetryError:
                pass
    return documents


def _load_optional_documents(args: argparse.Namespace) -> tuple[dict[str, dict[str, Any] | None], list[str]]:
    documents: dict[str, dict[str, Any] | None] = {}
    limitations: list[str] = []
    for name, path in (
        ("chunk_results", args.chunk_results),
        ("chunk_plan", args.chunk_plan),
        ("intake", args.intake),
        ("redaction_report", args.redaction_report),
        ("checks", args.checks),
        ("validation_evidence", args.validation_evidence),
        ("test_intelligence", args.test_intelligence),
        ("local_code_intelligence", args.local_code_intelligence),
    ):
        document, artifact_limitations = load_optional_artifact(path, name=name)
        documents[name] = document
        limitations.extend(artifact_limitations)
    return documents, _dedupe(limitations)


def _target_write_error(output_path: Path, *documents: dict[str, Any]) -> str | None:
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            if _is_relative_to(output_path, root) or output_path == root:
                return "Blocked: target repo must not be modified by AgentReview telemetry output."
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
        return "Blocked: AgentReview telemetry is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview telemetry requires dev/toolrepo agent_review_tooling environment."


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
