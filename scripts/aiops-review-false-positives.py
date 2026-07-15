#!/usr/bin/env python3
"""Generate deterministic AgentReview false-positive signatures."""

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

from app.agent_review.contract_suggestions import build_contract_suggestions, suggestions_to_yaml  # noqa: E402
from app.agent_review.false_positive_signatures import (  # noqa: E402
    FalsePositiveError,
    build_false_positive_signatures,
    load_final_review,
    load_json_object,
    load_optional_chunk_results,
    load_optional_markers,
    load_quality_gate,
    load_review_telemetry,
)
from app.services.environment_context import build_environment_context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic AgentReview false-positive signatures.")
    parser.add_argument("--review-telemetry", required=True)
    parser.add_argument("--quality-gate", required=True)
    parser.add_argument("--final-review", required=True)
    parser.add_argument("--chunk-results")
    parser.add_argument("--markers")
    parser.add_argument("--output", required=True)
    parser.add_argument("--suggestions-output")
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-false-positives")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    output_path = Path(args.output).resolve()
    suggestions_path = Path(args.suggestions_output).resolve() if args.suggestions_output else None
    input_paths = _input_paths(args)
    overwrite_error = _output_path_error(output_path, suggestions_path, input_paths)
    if overwrite_error:
        return _fail_json(overwrite_error[0], overwrite_error[1], limitations=[overwrite_error[0]])

    try:
        raw_documents = _load_raw_documents_for_guards(args)
        for path in [output_path, suggestions_path]:
            if path is None:
                continue
            path_error = _target_write_error(path, *raw_documents)
            if path_error:
                return _fail_json(
                    "target_repo_write_blocked",
                    path_error,
                    limitations=["target_repo_must_not_be_modified"],
                )

        review_telemetry = load_review_telemetry(args.review_telemetry)
        quality_gate = load_quality_gate(args.quality_gate)
        final_review = load_final_review(args.final_review)
        chunk_results, chunk_limitations = load_optional_chunk_results(args.chunk_results)
        markers_document, marker_limitations = load_optional_markers(args.markers)
        signatures = build_false_positive_signatures(
            final_review=final_review,
            quality_gate=quality_gate,
            review_telemetry=review_telemetry,
            chunk_results=chunk_results,
            markers_document=markers_document,
            limitations=[*chunk_limitations, *marker_limitations],
        )
        suggestions = build_contract_suggestions(signatures) if suggestions_path else None
        output_text = _to_json(signatures.model_dump(mode="json"))
        suggestions_text = suggestions_to_yaml(suggestions) if suggestions is not None else None
    except FalsePositiveError as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_text, encoding="utf-8")
    suggestions_written = False
    if suggestions_path is not None and suggestions_text is not None:
        suggestions_path.parent.mkdir(parents=True, exist_ok=True)
        suggestions_path.write_text(suggestions_text, encoding="utf-8")
        suggestions_written = True
    print(
        _to_json(
            {
                "ok": True,
                "status": "complete" if not signatures.limitations else "partial",
                "candidates": len(signatures.candidates),
                "suggestions_written": suggestions_written,
                "output_written": True,
            }
        )
    )
    return 0


def _input_paths(args: argparse.Namespace) -> list[Path]:
    paths = [args.review_telemetry, args.quality_gate, args.final_review, args.chunk_results, args.markers]
    return [Path(path).resolve() for path in paths if path]


def _output_path_error(output_path: Path, suggestions_path: Path | None, input_paths: list[Path]) -> tuple[str, str] | None:
    outputs = [output_path, *([suggestions_path] if suggestions_path is not None else [])]
    if len({path for path in outputs}) != len(outputs):
        return "output_overwrites_output", "Blocked: false-positive outputs must be distinct."
    for path in outputs:
        if path in input_paths:
            return "output_overwrites_input", "Blocked: false-positive output must not overwrite any input artifact."
    return None


def _load_raw_documents_for_guards(args: argparse.Namespace) -> list[dict[str, Any]]:
    documents = [
        load_json_object(args.review_telemetry, error_class="review_telemetry_invalid"),
        load_json_object(args.quality_gate, error_class="quality_gate_invalid"),
        load_json_object(args.final_review, error_class="final_review_invalid"),
    ]
    for path, error_class in ((args.chunk_results, "chunk_results_invalid"), (args.markers, "false_positive_markers_invalid")):
        if path:
            try:
                documents.append(load_json_object(path, error_class=error_class))
            except FalsePositiveError:
                pass
    return documents


def _target_write_error(output_path: Path, *documents: dict[str, Any]) -> str | None:
    if _containing_git_worktree(output_path) is not None:
        return "Blocked: AgentReview artifacts cannot be written inside Git worktrees."
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            if _is_relative_to(output_path, root) or output_path == root:
                return "Blocked: AgentReview artifacts cannot be written inside Git worktrees."
    return None


def _containing_git_worktree(path: Path) -> Path | None:
    current = path.resolve()
    if not current.is_dir():
        current = current.parent
    for candidate in (current, *current.parents):
        git_marker = candidate / ".git"
        if git_marker.exists():
            return candidate
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
        return "Blocked: AgentReview false positives are not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview false positives require dev/toolrepo agent_review_tooling environment."


def _fail_json(error_class: str, message: str, *, limitations: list[str]) -> int:
    print(_to_json({"ok": False, "status": "failed", "error_class": error_class, "message": message, "limitations": _dedupe(limitations)}))
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
