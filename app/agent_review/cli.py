"""CLI implementation for offline AgentReview intake."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.agent_review.artifact_loader import load_declared_artifacts
from app.agent_review.evidence_index import build_evidence_index
from app.agent_review.redaction import RedactionState, redact_value
from app.agent_review.repo_profile import load_repo_profile
from app.agent_review.schemas import ReviewIntake
from app.services.environment_context import build_environment_context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate offline AgentReview intake artifacts.")
    parser.add_argument("--target-repo", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--agent-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--redaction-report", required=True)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-intake")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    repo_root = Path(args.repo_root).resolve()
    agent_dir = Path(args.agent_dir).resolve()
    output_path = Path(args.output).resolve()
    report_path = Path(args.redaction_report).resolve()

    path_error = _target_write_error(repo_root, output_path, report_path)
    if path_error:
        return _fail_json(
            "target_repo_write_blocked",
            path_error,
            limitations=["target_repo_must_not_be_modified"],
        )

    intake, report = build_intake(
        target_repo=args.target_repo,
        repo_root=repo_root,
        agent_dir=agent_dir,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_to_json(intake.model_dump(mode="json", exclude_none=True)), encoding="utf-8")
    report_path.write_text(_to_json(report.model_dump(mode="json")), encoding="utf-8")

    print(_to_json({"ok": intake.status != "failed", "status": intake.status, "output": str(output_path)}))
    return 0 if intake.status != "failed" else 1


def build_intake(*, target_repo: str, repo_root: Path, agent_dir: Path) -> tuple[ReviewIntake, Any]:
    redaction_state = RedactionState()
    profile_result = load_repo_profile(repo_root, target_repo=target_repo)

    limitations = list(profile_result.limitations)
    artifacts: dict[str, Any] = {}
    artifact_status = []
    completeness: dict[str, Any] = {
        "profile_loaded": profile_result.status != "failed" and "repo_profile_missing" not in limitations,
        "declared_artifacts": len(profile_result.profile.artifacts),
        "loaded_artifacts": 0,
        "required_artifacts_missing": [],
        "invalid_artifacts": [],
    }

    if profile_result.status != "failed" and profile_result.profile.artifacts:
        artifact_result = load_declared_artifacts(
            agent_dir=agent_dir,
            declarations=profile_result.profile.artifacts,
            redaction_state=redaction_state,
        )
        artifacts = {
            name: artifact.model_dump(mode="json")
            for name, artifact in artifact_result.artifacts.items()
        }
        artifact_status = artifact_result.artifact_status
        limitations.extend(artifact_result.limitations)
        completeness.update(
            {
                "loaded_artifacts": len(artifact_result.artifacts),
                "required_artifacts_missing": [
                    status.name
                    for status in artifact_status
                    if status.status == "missing" and _is_required(status.name, profile_result.profile.artifacts)
                ],
                "invalid_artifacts": [
                    status.name for status in artifact_status if status.status in {"invalid", "degraded"}
                ],
                "evidence_sources": build_evidence_index(artifact_result.artifacts, artifact_status),
            }
        )

    redaction_state.record_file()
    target_profile = redact_value(profile_result.profile.model_dump(mode="json"), redaction_state)
    report = redaction_state.to_report(output_safe_for_llm=True)
    status = _intake_status(profile_result.status, limitations, artifact_status)

    intake = ReviewIntake(
        target_repo=target_repo,
        target_profile=target_profile,
        artifacts=artifacts,
        artifact_status=artifact_status,
        redaction_summary=report,
        limitations=_dedupe(limitations),
        completeness=completeness,
        status=status,
        error_class=profile_result.error_class,
    )
    return intake, report


def _intake_status(profile_status: str, limitations: list[str], artifact_status: list[Any]) -> str:
    if profile_status == "failed":
        return "failed"
    if "repo_profile_missing" in limitations:
        return "degraded"
    if any(status.status in {"invalid", "degraded"} for status in artifact_status):
        return "degraded"
    if any(status.status == "missing" and f"required_artifact_missing:{status.name}" in limitations for status in artifact_status):
        return "degraded"
    return "complete"


def _is_required(name: str, declarations: list[Any]) -> bool:
    return any(declaration.name == name and declaration.required for declaration in declarations)


def _target_write_error(repo_root: Path, output_path: Path, report_path: Path) -> str | None:
    if _is_relative_to(output_path, repo_root) or output_path == repo_root:
        return "Blocked: target repo must not be modified by AgentReview intake output."
    if _is_relative_to(report_path, repo_root) or report_path == repo_root:
        return "Blocked: target repo must not be modified by AgentReview redaction report."
    return None


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
        return "Blocked: AgentReview intake is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview intake requires dev/toolrepo agent_review_tooling environment."


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

