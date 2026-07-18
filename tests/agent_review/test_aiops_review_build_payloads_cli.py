from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import socket
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "aiops-review-build-payloads.py"
PR_BRIEF_MODULE = ROOT / "app" / "agent_review" / "pr_brief.py"
PAYLOAD_BUILDER_MODULE = ROOT / "app" / "agent_review" / "chunk_payload_builder.py"


def _dev_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIOPS_")}
    env.update(
        {
            "AIOPS_ENVIRONMENT": "dev",
            "AIOPS_NODE_ROLE": "toolrepo",
            "AIOPS_REPO_MODE": "agent_review_tooling",
            "AIOPS_PRODUCTION_RUNTIME": "false",
        }
    )
    return env


def _load_script_module():
    spec = importlib.util.spec_from_file_location("aiops_review_build_payloads_cli", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(module, args: list[str]) -> tuple[int, str, str]:  # noqa: ANN001
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            returncode = module.main(args)
        except SystemExit as exc:
            returncode = int(exc.code or 0)
    return returncode, stdout.getvalue(), stderr.getvalue()


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _base_artifacts(tmp_path: Path) -> dict[str, Path]:
    redaction_payload = {
        "schema_version": "agent-review.redaction-report.v1",
        "source": "aiops-review-intake",
        "files_processed": 1,
        "replacements_by_type": {"token_assignment": 1},
        "secret_like_values_found": 1,
        "redacted_lines_present": True,
        "redaction_is_sanitizer_artifact": True,
        "hardcoded_secret_confirmed": False,
        "output_safe_for_llm": True,
        "limitations": [],
    }
    intake = _write_json(
        tmp_path / "aiops-intake.json",
        {
            "schema_version": "agent-review.intake.v1",
            "source": "aiops-review-intake",
            "target_repo": "mglpsw/AgentEscala",
            "target_profile": {
                "schema_version": "agent-review.target-profile.v1",
                "target_repo": "mglpsw/AgentEscala",
                "domain_contracts": {"rules": [{"id": "rule-api", "description": "api contract"}]},
                "review_packs": {"packs": [{"id": "calendar-pack", "description": "calendar"}]},
            },
            "artifacts": {
                "file-diff-context": {
                    "name": "file-diff-context",
                    "path": "file-diff-context.json",
                    "kind": "json",
                    "content": {
                        "review_mode": "offline",
                        "contract_pack": "calendar",
                        "files": [
                            {"path": "backend/api/shifts.py", "status": "modified", "summary": "api"},
                            {"path": "tests/test_shift_service.py", "status": "modified", "summary": "tests"},
                        ],
                        "coverage_requirements": {
                            "must_review_files": ["backend/api/shifts.py"],
                            "should_review_files": ["tests/test_shift_service.py"],
                            "may_summarize_files": [],
                        },
                    },
                },
                "full-diff": {
                    "name": "full-diff",
                    "path": "full.diff",
                    "kind": "diff",
                    "content": "\n".join(
                        [
                            "diff --git a/backend/api/shifts.py b/backend/api/shifts.py",
                            "@@ -1,1 +1,1 @@",
                            "+token=TOPSECRET",
                            "diff --git a/tests/test_shift_service.py b/tests/test_shift_service.py",
                            "@@ -1,1 +1,1 @@",
                            "+assert True",
                        ]
                    ),
                },
            },
            "artifact_status": [
                {"name": "file-diff-context", "path": "file-diff-context.json", "available": True, "valid": True, "status": "available"},
                {"name": "full-diff", "path": "full.diff", "available": True, "valid": True, "status": "available"},
            ],
            "redaction_summary": redaction_payload,
            "limitations": [],
            "completeness": {},
            "created_at": "2026-06-02T00:00:00Z",
            "status": "complete",
        },
    )
    chunk_plan = _write_json(
        tmp_path / "semantic-chunk-plan.json",
        {
            "schema_version": 1,
            "schema_id": "agent-review.semantic-chunk-plan.v1",
            "source": "aiops-semantic-chunk-planner",
            "target_repo": "mglpsw/AgentEscala",
            "max_parallel_blocks": 6,
            "chunks": [
                {
                    "chunk_id": "chunk-01-api_schema_contract",
                    "semantic_group": "api_schema_contract",
                    "order_index": 0,
                    "files": ["backend/api/shifts.py"],
                    "artifacts": ["artifact:file-diff-context"],
                    "contracts": [],
                    "depends_on": [],
                    "coverage": "complete",
                    "prompt_budget_chars": 2000,
                    "estimated_chars": 800,
                    "limitations": [],
                },
                {
                    "chunk_id": "chunk-02-tests",
                    "semantic_group": "tests",
                    "order_index": 1,
                    "files": ["tests/test_shift_service.py"],
                    "artifacts": [],
                    "contracts": [],
                    "depends_on": [],
                    "coverage": "complete",
                    "prompt_budget_chars": 2000,
                    "estimated_chars": 600,
                    "limitations": [],
                },
            ],
            "files_covered": ["backend/api/shifts.py", "tests/test_shift_service.py"],
            "files_partially_covered": [],
            "files_not_covered": [],
            "limitations": [],
            "status": "complete",
            "created_at": "2026-06-02T00:00:00Z",
        },
    )
    redaction = _write_json(
        tmp_path / "redaction-report.json",
        redaction_payload,
    )
    return {"intake": intake, "chunk_plan": chunk_plan, "redaction": redaction}


def _args(paths: dict[str, Path], out_root: Path) -> list[str]:
    return [
        "--intake",
        str(paths["intake"]),
        "--chunk-plan",
        str(paths["chunk_plan"]),
        "--redaction-report",
        str(paths["redaction"]),
        "--brief-output",
        str(out_root / "pr-brief.json"),
        "--payloads-dir",
        str(out_root / "chunk-payloads"),
        "--manifest-output",
        str(out_root / "chunk-payload-manifest.json"),
    ]


def _error_payload(result: tuple[int, str, str]) -> dict:
    assert result[0] == 1, f"expected failure, got {result[0]} stderr={result[2]} stdout={result[1]}"
    return json.loads(result[1])


def _success_payload(result: tuple[int, str, str]) -> dict:
    assert result[0] == 0, f"expected success, got {result[0]} stderr={result[2]} stdout={result[1]}"
    return json.loads(result[1])


def test_cli_builds_outputs_outside_git_worktree(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    assert result[0] == 0, result[2] + result[1]
    brief = out_root / "pr-brief.json"
    manifest = out_root / "chunk-payload-manifest.json"
    payloads_dir = out_root / "chunk-payloads"
    assert brief.exists()
    assert manifest.exists()
    assert payloads_dir.exists()
    payload = json.loads(brief.read_text(encoding="utf-8"))
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema_id"] == "agent-review.pr-brief.v1"
    assert manifest_payload["schema_id"] == "agent-review.chunk-payload-manifest.v1"
    assert manifest_payload["payload_count"] == 2
    assert "optional_artifact_missing:checks" in payload["limitations"]
    assert "optional_artifact_missing:validation_evidence" in payload["limitations"]
    for entry in manifest_payload["chunks"]:
        assert (payloads_dir / entry["payload_path"]).exists()


def test_cli_returns_partial_status_when_manifest_entries_are_truncated(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    module = _load_script_module()
    args = _args(paths, out_root)
    args.extend(["--payload-max-chars", "900"])

    result = _invoke(module, args)

    payload = _success_payload(result)
    assert payload["status"] == "partial"
    manifest = json.loads((out_root / "chunk-payload-manifest.json").read_text(encoding="utf-8"))
    assert any(item["truncation"]["applied"] for item in manifest["chunks"])


def test_cli_blocks_symlinked_output_inside_git_worktree(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    target_worktree = tmp_path / "fake-worktree"
    target_worktree.mkdir()
    (target_worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    linked = tmp_path / "linked-worktree"
    linked.symlink_to(target_worktree, target_is_directory=True)
    module = _load_script_module()
    args = _args(paths, linked)

    result = _invoke(module, args)

    assert result[0] == 1
    payload = json.loads(result[1])
    assert payload["error_class"] == "target_repo_write_blocked"


def test_cli_blocks_input_overwrite(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    args = _args(paths, tmp_path / "out")
    args[args.index("--brief-output") + 1] = str(paths["intake"])

    result = _invoke(module, args)

    assert result[0] == 1
    payload = json.loads(result[1])
    assert payload["error_class"] == "output_overwrites_input"


def test_cli_blocks_brief_or_manifest_inside_payloads_dir(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    out_root = tmp_path / "agent-output"
    args = _args(paths, out_root)
    payloads_dir = out_root / "chunk-payloads"
    args[args.index("--brief-output") + 1] = str(payloads_dir / "pr-brief.json")

    result = _invoke(module, args)

    assert result[0] == 1
    payload = json.loads(result[1])
    assert payload["error_class"] == "output_conflict"


def test_cli_blocks_payloads_dir_nested_under_brief_output_path(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    out_root = tmp_path / "agent-output"
    args = _args(paths, out_root)
    result_root = tmp_path / "result"
    args[args.index("--brief-output") + 1] = str(result_root)
    args[args.index("--payloads-dir") + 1] = str(result_root / "payloads")

    result = _invoke(module, args)

    error = _error_payload(result)
    assert error["error_class"] == "output_conflict"
    assert not result_root.exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()


def test_cli_blocks_payloads_dir_nested_under_manifest_output_path(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    out_root = tmp_path / "agent-output"
    args = _args(paths, out_root)
    result_root = tmp_path / "result"
    args[args.index("--manifest-output") + 1] = str(result_root)
    args[args.index("--payloads-dir") + 1] = str(result_root / "payloads")

    result = _invoke(module, args)

    error = _error_payload(result)
    assert error["error_class"] == "output_conflict"
    assert not result_root.exists()
    assert not (out_root / "pr-brief.json").exists()


def test_cli_blocks_manifest_output_nested_under_brief_output_path(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    out_root = tmp_path / "agent-output"
    args = _args(paths, out_root)
    result_root = tmp_path / "result"
    args[args.index("--brief-output") + 1] = str(result_root)
    args[args.index("--manifest-output") + 1] = str(result_root / "manifest.json")

    result = _invoke(module, args)

    error = _error_payload(result)
    assert error["error_class"] == "output_conflict"
    assert not result_root.exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_blocks_brief_output_nested_under_manifest_output_path(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()
    out_root = tmp_path / "agent-output"
    args = _args(paths, out_root)
    result_root = tmp_path / "result"
    args[args.index("--manifest-output") + 1] = str(result_root)
    args[args.index("--brief-output") + 1] = str(result_root / "brief.json")

    result = _invoke(module, args)

    error = _error_payload(result)
    assert error["error_class"] == "output_conflict"
    assert not result_root.exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_removes_partial_outputs_when_write_fails(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    module = _load_script_module()

    original_write = module._write_file_atomic

    def fail_on_manifest(path: Path, content: str) -> None:  # noqa: ANN001
        if path.name == "chunk-payload-manifest.json":
            raise OSError("simulated write error")
        original_write(path, content)

    monkeypatch.setattr(module, "_write_file_atomic", fail_on_manifest)
    result = _invoke(module, _args(paths, out_root))

    assert result[0] == 1
    payload = json.loads(result[1])
    assert payload["error_class"] == "output_write_failed"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_payloads_dir_already_exists_and_preserves_existing_content(
    monkeypatch, tmp_path: Path
) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    payloads_dir = out_root / "chunk-payloads"
    payloads_dir.mkdir(parents=True)
    sentinel = payloads_dir / "sentinel.txt"
    original = "do not touch"
    sentinel.write_text(original, encoding="utf-8")
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "output_exists"
    assert sentinel.read_text(encoding="utf-8") == original
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()


def test_cli_fails_when_brief_output_already_exists_and_preserves_file(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    out_root.mkdir(parents=True)
    brief = out_root / "pr-brief.json"
    original = '{"keep":"brief"}\n'
    brief.write_text(original, encoding="utf-8")
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "output_exists"
    assert brief.read_text(encoding="utf-8") == original
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_manifest_output_already_exists_and_preserves_file(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    out_root.mkdir(parents=True)
    manifest = out_root / "chunk-payload-manifest.json"
    original = '{"keep":"manifest"}\n'
    manifest.write_text(original, encoding="utf-8")
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "output_exists"
    assert manifest.read_text(encoding="utf-8") == original
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_never_removes_legacy_tmp_build_directory_name(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    legacy_tmp = out_root / "chunk-payloads.tmp-build"
    legacy_tmp.mkdir(parents=True)
    sentinel = legacy_tmp / "sentinel.txt"
    sentinel.write_text("legacy", encoding="utf-8")
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    assert result[0] == 0, result[2] + result[1]
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "legacy"


def test_cli_staging_failure_preserves_preexisting_outputs_and_removes_run_staging(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    out_root = tmp_path / "agent-output"
    legacy_tmp = out_root / "chunk-payloads.tmp-build"
    legacy_tmp.mkdir(parents=True)
    sentinel = legacy_tmp / "sentinel.txt"
    sentinel.write_text("legacy", encoding="utf-8")
    module = _load_script_module()

    original_write = module._write_file_atomic

    def fail_on_payload(path: Path, content: str) -> None:  # noqa: ANN001
        if path.name.endswith(".json") and path.parent.name in {"payloads", "chunk-payloads"}:
            raise OSError("simulated payload write error")
        original_write(path, content)

    monkeypatch.setattr(module, "_write_file_atomic", fail_on_payload)
    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "output_write_failed"
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "legacy"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_closed_on_review_identity_conflict(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    chunk_plan = json.loads(paths["chunk_plan"].read_text(encoding="utf-8"))
    chunk_plan["target_repo"] = "mglpsw/AnotherRepo"
    _write_json(paths["chunk_plan"], chunk_plan)
    out_root = tmp_path / "agent-output"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "review_identity_conflict"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_closed_when_redaction_report_is_not_safe_for_llm(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    redaction = json.loads(paths["redaction"].read_text(encoding="utf-8"))
    redaction["output_safe_for_llm"] = False
    _write_json(paths["redaction"], redaction)
    out_root = tmp_path / "agent-output"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_unsafe"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_accepts_modern_intake_envelope(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = 1
    _write_json(paths["intake"], intake)
    module = _load_script_module()

    result = _invoke(module, _args(paths, tmp_path / "out"))

    payload = _success_payload(result)
    assert payload["ok"] is True


def test_cli_accepts_modern_redaction_report_envelope(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    redaction = json.loads(paths["redaction"].read_text(encoding="utf-8"))
    redaction["schema_id"] = "agent-review.redaction-report.v1"
    redaction["schema_version"] = 1
    _write_json(paths["redaction"], redaction)
    module = _load_script_module()

    result = _invoke(module, _args(paths, tmp_path / "out"))

    payload = _success_payload(result)
    assert payload["ok"] is True


def test_cli_rejects_modern_envelope_with_unsupported_version(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = 2
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "intake_invalid"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_rejects_unknown_schema_id(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["schema_id"] = "agent-review.intake.v999"
    intake["schema_version"] = 1
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "intake_invalid"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_rejects_hybrid_inconsistent_intake_envelope(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = "agent-review.intake.v1"
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "intake_invalid"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_emits_byte_identical_outputs_for_legacy_and_modern_envelopes(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    legacy_paths = _base_artifacts(tmp_path / "legacy")
    modern_paths = _base_artifacts(tmp_path / "modern")

    modern_intake = json.loads(modern_paths["intake"].read_text(encoding="utf-8"))
    modern_intake["schema_id"] = "agent-review.intake.v1"
    modern_intake["schema_version"] = 1
    _write_json(modern_paths["intake"], modern_intake)

    modern_redaction = json.loads(modern_paths["redaction"].read_text(encoding="utf-8"))
    modern_redaction["schema_id"] = "agent-review.redaction-report.v1"
    modern_redaction["schema_version"] = 1
    _write_json(modern_paths["redaction"], modern_redaction)

    module = _load_script_module()
    legacy_out = tmp_path / "legacy-out"
    modern_out = tmp_path / "modern-out"
    legacy_result = _invoke(module, _args(legacy_paths, legacy_out))
    modern_result = _invoke(module, _args(modern_paths, modern_out))

    _success_payload(legacy_result)
    _success_payload(modern_result)

    assert (legacy_out / "pr-brief.json").read_bytes() == (modern_out / "pr-brief.json").read_bytes()
    assert (legacy_out / "chunk-payload-manifest.json").read_bytes() == (
        modern_out / "chunk-payload-manifest.json"
    ).read_bytes()


def test_cli_does_not_call_network_router_or_provider(monkeypatch, tmp_path: Path) -> None:
    def fail_network(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("network should not be called")

    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(socket, "socket", fail_network)
    monkeypatch.setattr(socket, "create_connection", fail_network)
    paths = _base_artifacts(tmp_path)
    module = _load_script_module()

    result = _invoke(module, _args(paths, tmp_path / "out"))

    assert result[0] == 0, result[2] + result[1]


def test_cli_error_class_is_deterministic_for_invalid_required_input(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    paths["chunk_plan"].write_text("{", encoding="utf-8")
    module = _load_script_module()
    args = _args(paths, tmp_path / "out")

    first = _invoke(module, args)
    second = _invoke(module, args)

    assert first[0] == 1 and second[0] == 1
    assert json.loads(first[1])["error_class"] == "chunk_plan_invalid"
    assert json.loads(second[1])["error_class"] == "chunk_plan_invalid"


def test_cli_fails_when_embedded_redaction_report_is_not_safe(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["redaction_summary"]["output_safe_for_llm"] = False
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_unsafe"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_embedded_and_external_redaction_reports_diverge(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["redaction_summary"]["files_processed"] = 99
    _write_json(paths["intake"], intake)
    out_root_a = tmp_path / "out-a"
    out_root_b = tmp_path / "out-b"
    module = _load_script_module()

    first = _invoke(module, _args(paths, out_root_a))
    second = _invoke(module, _args(paths, out_root_b))

    first_error = _error_payload(first)
    second_error = _error_payload(second)
    assert first_error["error_class"] == "redaction_report_mismatch"
    assert second_error["error_class"] == "redaction_report_mismatch"
    assert first_error == second_error
    assert not (out_root_a / "pr-brief.json").exists()
    assert not (out_root_a / "chunk-payload-manifest.json").exists()
    assert not (out_root_a / "chunk-payloads").exists()


def test_cli_fails_when_external_redaction_report_is_not_safe(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    redaction = json.loads(paths["redaction"].read_text(encoding="utf-8"))
    redaction["output_safe_for_llm"] = False
    _write_json(paths["redaction"], redaction)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_unsafe"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_both_redaction_reports_are_not_safe(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["redaction_summary"]["output_safe_for_llm"] = False
    _write_json(paths["intake"], intake)
    redaction = json.loads(paths["redaction"].read_text(encoding="utf-8"))
    redaction["output_safe_for_llm"] = False
    _write_json(paths["redaction"], redaction)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_unsafe"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_redaction_reports_differ_on_replacements(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["redaction_summary"]["replacements_by_type"] = {"token_assignment": 2}
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_mismatch"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_fails_when_redaction_reports_differ_on_limitations(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["redaction_summary"]["limitations"] = ["embedded-only"]
    _write_json(paths["intake"], intake)
    out_root = tmp_path / "out"
    module = _load_script_module()

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "redaction_report_mismatch"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()


def test_cli_accepts_equivalent_modern_intake_and_redaction_reports(monkeypatch, tmp_path: Path) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    paths = _base_artifacts(tmp_path)
    intake = json.loads(paths["intake"].read_text(encoding="utf-8"))
    intake["schema_id"] = "agent-review.intake.v1"
    intake["schema_version"] = 1
    _write_json(paths["intake"], intake)
    redaction = json.loads(paths["redaction"].read_text(encoding="utf-8"))
    redaction["schema_id"] = "agent-review.redaction-report.v1"
    redaction["schema_version"] = 1
    _write_json(paths["redaction"], redaction)
    module = _load_script_module()

    result = _invoke(module, _args(paths, tmp_path / "out"))

    payload = _success_payload(result)
    assert payload["ok"] is True


def test_new_active_paths_do_not_contain_router_or_provider_calls() -> None:
    active_text = (
        SCRIPT.read_text(encoding="utf-8")
        + "\n"
        + PR_BRIEF_MODULE.read_text(encoding="utf-8")
        + "\n"
        + PAYLOAD_BUILDER_MODULE.read_text(encoding="utf-8")
    )
    forbidden = [
        "/v1/chat/ingest",
        "/v1/chat/completions",
        "requests.",
        "urllib.request",
        "openai",
        "anthropic",
        "ollama",
    ]
    for value in forbidden:
        assert value not in active_text.lower()


@pytest.mark.parametrize(
    "invalid_chunk_id",
    ["chunk/backend", "ghp_abcdefghijk_sensitive"],
)
def test_cli_rejects_invalid_chunk_id_before_outputs_are_created(
    monkeypatch,
    tmp_path: Path,
    invalid_chunk_id: str,
) -> None:
    for key, value in _dev_env().items():
        monkeypatch.setenv(key, value)
    module = _load_script_module()
    paths = _base_artifacts(tmp_path)
    chunk_plan = json.loads(paths["chunk_plan"].read_text(encoding="utf-8"))
    chunk_plan["chunks"][0]["chunk_id"] = invalid_chunk_id
    _write_json(paths["chunk_plan"], chunk_plan)
    out_root = tmp_path / "agent-output"
    out_root.mkdir()
    sentinel = out_root / "preexisting-sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _invoke(module, _args(paths, out_root))

    error = _error_payload(result)
    assert error["error_class"] == "chunk_plan_chunk_id_invalid"
    assert invalid_chunk_id not in result[1]
    assert invalid_chunk_id not in result[2]
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not (out_root / "pr-brief.json").exists()
    assert not (out_root / "chunk-payload-manifest.json").exists()
    assert not (out_root / "chunk-payloads").exists()
