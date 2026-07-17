#!/usr/bin/env python3
"""Build deterministic PR brief and bounded chunk payloads for offline AgentReview."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent_review.chunk_payload_builder import (  # noqa: E402
    ChunkPayloadBuilderError,
    build_chunk_payloads,
)
from app.agent_review.pr_brief import PRBriefError, build_pr_brief  # noqa: E402
from app.agent_review.redaction import sanitize_artifact_value  # noqa: E402
from app.agent_review.schemas import (  # noqa: E402
    REDACTION_REPORT_SCHEMA,
    SEMANTIC_CHUNK_PLAN_SCHEMA,
    INTAKE_SCHEMA,
    RedactionReport,
    ReviewIntake,
    SemanticChunkPlan,
)
from app.services.environment_context import build_environment_context  # noqa: E402


class PayloadBuildCliError(ValueError):
    def __init__(self, error_class: str, message: str) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.message = message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic AgentReview PR brief and bounded chunk payloads.")
    parser.add_argument("--intake", required=True)
    parser.add_argument("--chunk-plan", required=True)
    parser.add_argument("--redaction-report", required=True)
    parser.add_argument("--checks")
    parser.add_argument("--validation-evidence")
    parser.add_argument("--brief-output", required=True)
    parser.add_argument("--payloads-dir", required=True)
    parser.add_argument("--manifest-output", required=True)
    parser.add_argument("--brief-max-chars", type=int)
    parser.add_argument("--payload-max-chars", type=int)
    args = parser.parse_args(argv)

    context = build_environment_context(os.environ, source="aiops-review-build-payloads")
    if not context["agent_review_tooling_allowed"]:
        return _fail_json(
            "environment_blocked",
            _environment_block_message(context),
            limitations=["agent_review_tooling_not_allowed", *context.get("limitations", [])],
        )

    paths = _resolved_paths(args)
    overwrite_error = _output_overwrite_error(paths)
    if overwrite_error:
        return _fail_json(overwrite_error[0], overwrite_error[1], limitations=[overwrite_error[0]])
    output_exists_error = _output_exists_error(paths)
    if output_exists_error:
        return _fail_json(output_exists_error[0], output_exists_error[1], limitations=[output_exists_error[0]])

    try:
        raw_documents = _load_raw_documents(paths)
        path_error = _target_write_error(
            [paths["brief_output"], paths["manifest_output"], paths["payloads_dir"]],
            *raw_documents,
        )
        if path_error:
            return _fail_json(
                "target_repo_write_blocked",
                path_error,
                limitations=["target_repo_must_not_be_modified"],
            )

        intake = _load_intake(paths["intake"])
        chunk_plan = _load_chunk_plan(paths["chunk_plan"])
        redaction_report = _load_redaction_report(paths["redaction_report"])
        if redaction_report.output_safe_for_llm is not True:
            raise PayloadBuildCliError(
                "redaction_report_unsafe",
                "redaction report marks output as unsafe for llm routing",
            )
        checks, checks_limitations = _load_optional_json(paths.get("checks"), "checks")
        validation_evidence, validation_limitations = _load_optional_json(
            paths.get("validation_evidence"),
            "validation_evidence",
        )
        optional_limitations = [*checks_limitations, *validation_limitations]

        pr_brief = build_pr_brief(
            intake=intake,
            chunk_plan=chunk_plan,
            redaction_report=redaction_report,
            checks=checks,
            validation_evidence=validation_evidence,
            max_chars=paths["brief_max_chars"],
            optional_limitations=optional_limitations,
        )
        manifest, payloads = build_chunk_payloads(
            intake=intake,
            chunk_plan=chunk_plan,
            pr_brief=pr_brief,
            checks=checks,
            validation_evidence=validation_evidence,
            max_chars_per_payload=paths["payload_max_chars"],
            optional_limitations=optional_limitations,
        )
        _write_outputs(
            brief_output=paths["brief_output"],
            payloads_dir=paths["payloads_dir"],
            manifest_output=paths["manifest_output"],
            pr_brief=pr_brief.model_dump(mode="json"),
            manifest=manifest.model_dump(mode="json"),
            payloads={name: payload.model_dump(mode="json") for name, payload in payloads.items()},
        )
    except (PayloadBuildCliError, PRBriefError, ChunkPayloadBuilderError) as exc:
        return _fail_json(exc.error_class, exc.message, limitations=[exc.error_class])
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _fail_json("payload_builder_unexpected_error", str(exc), limitations=["payload_builder_unexpected_error"])

    has_limited_entries = any(entry.status == "limited" for entry in manifest.chunks)
    has_truncated_entries = any(entry.truncation.applied for entry in manifest.chunks)
    overall_status = "complete"
    if manifest.limitations or pr_brief.truncation.applied or has_limited_entries or has_truncated_entries:
        overall_status = "partial"

    print(
        _to_json(
            {
                "ok": True,
                "status": overall_status,
                "payload_count": manifest.payload_count,
                "output_written": True,
            }
        )
    )
    return 0


def _resolved_paths(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "intake": Path(args.intake).resolve(),
        "chunk_plan": Path(args.chunk_plan).resolve(),
        "redaction_report": Path(args.redaction_report).resolve(),
        "checks": Path(args.checks).resolve() if args.checks else None,
        "validation_evidence": Path(args.validation_evidence).resolve() if args.validation_evidence else None,
        "brief_output": Path(args.brief_output).resolve(),
        "payloads_dir": Path(args.payloads_dir).resolve(),
        "manifest_output": Path(args.manifest_output).resolve(),
        "brief_max_chars": args.brief_max_chars,
        "payload_max_chars": args.payload_max_chars,
    }


def _output_overwrite_error(paths: dict[str, Any]) -> tuple[str, str] | None:
    brief_output = paths["brief_output"]
    manifest_output = paths["manifest_output"]
    payloads_dir = paths["payloads_dir"]
    outputs = [brief_output, manifest_output]

    if _file_output_paths_conflict(brief_output, manifest_output):
        return "output_conflict", "Blocked: brief-output and manifest-output must be different files."
    inputs = [
        paths["intake"],
        paths["chunk_plan"],
        paths["redaction_report"],
        paths["checks"],
        paths["validation_evidence"],
    ]
    input_paths = [path for path in inputs if isinstance(path, Path)]
    for output in outputs:
        if output in input_paths:
            return "output_overwrites_input", "Blocked: output file must not overwrite any input artifact."
    for output in outputs:
        if _file_and_directory_paths_conflict(output, payloads_dir):
            return (
                "output_conflict",
                "Blocked: brief-output and manifest-output must be outside payloads-dir.",
            )
    for input_path in input_paths:
        if input_path == payloads_dir or _is_relative_to(input_path, payloads_dir):
            return (
                "output_overwrites_input",
                "Blocked: payloads-dir must not contain input artifacts.",
            )
    return None


def _file_and_directory_paths_conflict(file_path: Path, directory_path: Path) -> bool:
    return (
        file_path == directory_path
        or _is_relative_to(file_path, directory_path)
        or _is_relative_to(directory_path, file_path)
    )


def _file_output_paths_conflict(left: Path, right: Path) -> bool:
    return left == right or _is_relative_to(left, right) or _is_relative_to(right, left)


def _output_exists_error(paths: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("brief_output", "manifest_output", "payloads_dir"):
        path = paths[key]
        if isinstance(path, Path) and path.exists():
            return "output_exists", f"Blocked: output already exists and must be absent before execution ({path})."
    return None


def _load_raw_documents(paths: dict[str, Any]) -> list[dict[str, Any]]:
    documents = [
        _load_json_object(paths["intake"], error_class="intake_invalid"),
        _load_json_object(paths["chunk_plan"], error_class="chunk_plan_invalid"),
        _load_json_object(paths["redaction_report"], error_class="redaction_report_invalid"),
    ]
    for key, error_class in (("checks", "checks_invalid"), ("validation_evidence", "validation_evidence_invalid")):
        path = paths.get(key)
        if isinstance(path, Path) and path.exists():
            try:
                documents.append(_load_json_object(path, error_class=error_class))
            except PayloadBuildCliError:
                continue
    return documents


def _load_intake(path: Path) -> ReviewIntake:
    raw = _load_json_object(path, error_class="intake_invalid")
    normalized = _normalize_schema_envelope(
        raw,
        expected_schema_id=INTAKE_SCHEMA,
        error_class="intake_invalid",
    )
    try:
        return ReviewIntake.model_validate(normalized)
    except ValidationError as exc:
        raise PayloadBuildCliError("intake_invalid", "intake structure is invalid") from exc


def _load_chunk_plan(path: Path) -> SemanticChunkPlan:
    raw = _load_json_object(path, error_class="chunk_plan_invalid")
    if raw.get("schema_id") != SEMANTIC_CHUNK_PLAN_SCHEMA or raw.get("schema_version") != 1:
        raise PayloadBuildCliError("chunk_plan_invalid", "chunk plan schema is invalid")
    try:
        plan = SemanticChunkPlan.model_validate(raw)
    except ValidationError as exc:
        raise PayloadBuildCliError("chunk_plan_invalid", "chunk plan structure is invalid") from exc
    if plan.status == "failed":
        raise PayloadBuildCliError("chunk_plan_invalid", "chunk plan status must not be failed")
    return plan


def _load_redaction_report(path: Path) -> RedactionReport:
    raw = _load_json_object(path, error_class="redaction_report_invalid")
    normalized = _normalize_schema_envelope(
        raw,
        expected_schema_id=REDACTION_REPORT_SCHEMA,
        error_class="redaction_report_invalid",
    )
    try:
        return RedactionReport.model_validate(normalized)
    except ValidationError as exc:
        raise PayloadBuildCliError("redaction_report_invalid", "redaction report structure is invalid") from exc


def _normalize_schema_envelope(
    raw: dict[str, Any],
    *,
    expected_schema_id: str,
    error_class: str,
) -> dict[str, Any]:
    normalized = dict(raw)

    if "schema_id" in raw:
        if raw.get("schema_id") != expected_schema_id or raw.get("schema_version") != 1:
            raise PayloadBuildCliError(error_class, "input schema is invalid")
        normalized.pop("schema_id", None)
        normalized["schema_version"] = expected_schema_id
        return normalized

    if raw.get("schema_version") != expected_schema_id:
        raise PayloadBuildCliError(error_class, "input schema is invalid")

    return normalized


def _load_optional_json(path: Path | None, name: str) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, [f"optional_artifact_missing:{name}"]
    if not path.exists():
        return None, [f"optional_artifact_missing:{name}"]
    try:
        return _load_json_object(path, error_class=f"{name}_invalid"), []
    except PayloadBuildCliError:
        return None, [f"optional_artifact_invalid:{name}"]


def _load_json_object(path: Path, *, error_class: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PayloadBuildCliError(error_class, "input file not found") from exc
    except json.JSONDecodeError as exc:
        raise PayloadBuildCliError(error_class, "input JSON is invalid") from exc
    if not isinstance(raw, dict):
        raise PayloadBuildCliError(error_class, "input JSON must be an object")
    return raw


def _target_write_error(output_paths: list[Path], *documents: dict[str, Any]) -> str | None:
    for output_path in output_paths:
        if _containing_git_worktree(output_path) is not None:
            return "Blocked: AgentReview artifacts cannot be written inside Git worktrees."
    for document in documents:
        for candidate in _declared_target_paths(document):
            root = Path(candidate).expanduser().resolve()
            for output_path in output_paths:
                if _is_relative_to(output_path, root) or output_path == root:
                    return "Blocked: AgentReview artifacts cannot be written inside Git worktrees."
    return None


def _containing_git_worktree(path: Path) -> Path | None:
    current = path.resolve()
    if not current.is_dir():
        current = current.parent
    for candidate in (current, *current.parents):
        marker = candidate / ".git"
        if marker.exists():
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
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _write_outputs(
    *,
    brief_output: Path,
    payloads_dir: Path,
    manifest_output: Path,
    pr_brief: dict[str, Any],
    manifest: dict[str, Any],
    payloads: dict[str, dict[str, Any]],
) -> None:
    payloads_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="aiops-review-build-payloads-", dir=str(payloads_dir.parent)))
    staging_payloads_dir = staging_root / "payloads"
    staging_brief = staging_root / "pr-brief.json"
    staging_manifest = staging_root / "chunk-payload-manifest.json"
    created: list[Path] = []
    try:
        staging_payloads_dir.mkdir(parents=True, exist_ok=False)
        for filename, payload in sorted(payloads.items(), key=lambda item: item[0]):
            _write_file_atomic(staging_payloads_dir / filename, _to_json(payload))

        _write_file_atomic(staging_brief, _to_json(pr_brief))
        _write_file_atomic(staging_manifest, _to_json(manifest))

        _write_file_exclusive(brief_output, staging_brief.read_text(encoding="utf-8"))
        created.append(brief_output)
        _write_file_exclusive(manifest_output, staging_manifest.read_text(encoding="utf-8"))
        created.append(manifest_output)

        staging_payloads_dir.rename(payloads_dir)
        created.append(payloads_dir)
    except Exception as exc:
        for path in created:
            if path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
        raise PayloadBuildCliError("output_write_failed", str(exc)) from exc
    finally:
        if staging_root.exists():
            shutil.rmtree(staging_root, ignore_errors=True)


def _write_file_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _write_file_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def _environment_block_message(context: dict[str, Any]) -> str:
    if (
        context.get("environment") == "prod"
        or context.get("node_role") == "runtime"
        or context.get("production_runtime") is True
    ):
        return "Blocked: AgentReview payload building is not allowed on production runtime."
    if "invalid_production_runtime" in context.get("limitations", []):
        return "Blocked: production runtime flag is invalid."
    return "Blocked: AgentReview payload building requires dev/toolrepo agent_review_tooling environment."


def _fail_json(error_class: str, message: str, *, limitations: list[str]) -> int:
    payload = sanitize_artifact_value(
        {
            "ok": False,
            "status": "failed",
            "error_class": error_class,
            "message": message,
            "limitations": _dedupe(limitations),
        }
    )
    print(_to_json(payload))
    return 1


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


if __name__ == "__main__":
    raise SystemExit(main())
