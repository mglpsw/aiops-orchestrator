from __future__ import annotations

import json

import pytest

from app.agent_review.pr_brief import PRBriefError, build_pr_brief
from app.agent_review.schemas import RedactionReport, ReviewIntake, SemanticChunk, SemanticChunkPlan


def _intake() -> ReviewIntake:
    return ReviewIntake.model_validate(
        {
            "schema_version": "agent-review.intake.v1",
            "source": "aiops-review-intake",
            "target_repo": "mglpsw/AgentEscala",
            "target_profile": {
                "schema_version": "agent-review.target-profile.v1",
                "target_repo": "mglpsw/AgentEscala",
                "domain_contracts": {
                    "rules": [
                        {"id": "calendar_10_22_independent", "description": "10-22H independent from 24H coverage."}
                    ]
                },
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
                            {"path": "tests/test_shift_service.py", "status": "modified", "summary": "tests"},
                            {"path": "backend/services/shift_service.py", "status": "modified", "summary": "backend"},
                            {"path": "backend/api/shifts.py", "status": "modified", "summary": "api"},
                        ],
                        "coverage_requirements": {
                            "must_review_files": ["backend/api/shifts.py", "backend/services/shift_service.py"],
                            "should_review_files": ["tests/test_shift_service.py"],
                            "may_summarize_files": [],
                        },
                    },
                },
                "checks": {
                    "name": "checks",
                    "path": "checks.json",
                    "kind": "json",
                    "content": {
                        "status": "complete",
                        "checks": [{"name": "pytest", "status": "passed", "command": "python -m pytest"}],
                        "pr_number": 61,
                        "commit_sha": "abc123",
                    },
                },
            },
            "artifact_status": [
                {"name": "checks", "path": "checks.json", "available": True, "valid": True, "status": "available"},
                {
                    "name": "file-diff-context",
                    "path": "file-diff-context.json",
                    "available": True,
                    "valid": True,
                    "status": "available",
                },
            ],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-06-02T00:00:00Z",
            "status": "complete",
        }
    )


def _chunk_plan(target_repo: str = "mglpsw/AgentEscala") -> SemanticChunkPlan:
    return SemanticChunkPlan.model_validate(
        {
            "schema_version": 1,
            "schema_id": "agent-review.semantic-chunk-plan.v1",
            "source": "aiops-semantic-chunk-planner",
            "target_repo": target_repo,
            "max_parallel_blocks": 6,
            "chunks": [
                SemanticChunk(
                    chunk_id="chunk-02-tests",
                    semantic_group="tests",
                    order_index=1,
                    files=["tests/test_shift_service.py"],
                    artifacts=["artifact:checks"],
                    contracts=["target_profile:domain_contracts"],
                    depends_on=[],
                    coverage="complete",
                    prompt_budget_chars=24_000,
                    estimated_chars=1024,
                    limitations=[],
                ).model_dump(mode="json"),
                SemanticChunk(
                    chunk_id="chunk-01-api_schema_contract",
                    semantic_group="api_schema_contract",
                    order_index=0,
                    files=["backend/api/shifts.py", "backend/services/shift_service.py"],
                    artifacts=["artifact:file-diff-context"],
                    contracts=["target_profile:domain_contracts"],
                    depends_on=[],
                    coverage="partial",
                    prompt_budget_chars=24_000,
                    estimated_chars=2048,
                    limitations=["chunk_budget_exceeded:api_schema_contract"],
                ).model_dump(mode="json"),
            ],
            "files_covered": ["backend/services/shift_service.py", "backend/api/shifts.py", "tests/test_shift_service.py"],
            "files_partially_covered": [],
            "files_not_covered": [],
            "limitations": [],
            "status": "partial",
            "created_at": "2026-06-02T00:00:00Z",
        }
    )


def _redaction_report() -> RedactionReport:
    return RedactionReport.model_validate(
        {
            "schema_version": "agent-review.redaction-report.v1",
            "source": "aiops-review-intake",
            "files_processed": 2,
            "replacements_by_type": {"api_key_assignment": 1},
            "secret_like_values_found": 1,
            "redacted_lines_present": True,
            "redaction_is_sanitizer_artifact": True,
            "hardcoded_secret_confirmed": False,
            "output_safe_for_llm": True,
            "limitations": [],
        }
    )


def _rendered(brief) -> str:  # noqa: ANN001
    return json.dumps(brief.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def _canonical_len(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def test_pr_brief_happy_path() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    payload = brief.model_dump(mode="json")
    assert payload["schema_id"] == "agent-review.pr-brief.v1"
    assert payload["target"]["repository"] == "mglpsw/AgentEscala"
    assert payload["target"]["pr_number"] == 61
    assert payload["target"]["commit_sha"] == "abc123"
    assert payload["review"]["mode"] == "offline"
    assert payload["review"]["contract_pack"] == "calendar"
    assert payload["changed_files_summary"]["total_files"] == 3
    assert payload["semantic_groups"][0]["semantic_group"] == "api_schema_contract"
    assert payload["redaction"]["output_safe_for_llm"] is True


def test_pr_brief_marks_optional_artifacts_missing() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        optional_limitations=["optional_artifact_missing:checks", "optional_artifact_missing:validation_evidence"],
    )

    assert "optional_artifact_missing:checks" in brief.limitations
    assert "optional_artifact_missing:validation_evidence" in brief.limitations


def test_pr_brief_marks_invalid_required_artifact() -> None:
    intake = _intake()
    intake.artifact_status[0].status = "invalid"
    intake.artifact_status[0].valid = False

    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    assert "artifact_invalid:checks" in brief.limitations


def test_pr_brief_fails_closed_on_cross_artifact_identity_conflicts() -> None:
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=_intake(),
            chunk_plan=_chunk_plan(target_repo="mglpsw/AnotherRepo"),
            redaction_report=_redaction_report(),
            checks={"pr_number": 99, "commit_sha": "sha-other"},
            validation_evidence={"pr_number": 61, "commit_sha": "abc123"},
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_pr_brief_fails_closed_on_target_repo_conflict() -> None:
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=_intake(),
            chunk_plan=_chunk_plan(target_repo="mglpsw/AnotherRepo"),
            redaction_report=_redaction_report(),
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_pr_brief_fails_closed_on_pr_number_conflict() -> None:
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=_intake(),
            chunk_plan=_chunk_plan(),
            redaction_report=_redaction_report(),
            checks={"pr_number": 99},
            validation_evidence={"pr_number": 61},
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_pr_brief_fails_closed_on_commit_sha_conflict() -> None:
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=_intake(),
            chunk_plan=_chunk_plan(),
            redaction_report=_redaction_report(),
            checks={"commit_sha": "sha-a"},
            validation_evidence={"commit_sha": "sha-b"},
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_pr_brief_fails_closed_on_conflicting_embedded_artifact_identity() -> None:
    intake = _intake()
    intake.artifacts["validation-evidence-result"] = {
        "name": "validation-evidence-result",
        "path": "validation-evidence/validation-evidence-result.json",
        "kind": "json",
        "content": {
            "status": "complete",
            "validation_verdict": "degraded",
            "pr_number": 999,
            "commit_sha": "sha-other",
            "blocking_findings": [],
            "limitations": [],
        },
    }
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=intake,
            chunk_plan=_chunk_plan(),
            redaction_report=_redaction_report(),
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_pr_brief_allows_missing_identity_fields_without_conflict() -> None:
    intake = _intake()
    checks = intake.artifacts["checks"]["content"]
    checks.pop("pr_number")
    checks.pop("commit_sha")
    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )
    assert brief.target["pr_number"] is None
    assert brief.target["commit_sha"] is None


def test_pr_brief_ignores_nested_non_identity_metadata_keys() -> None:
    intake = _intake()
    intake.artifacts["project-context"] = {
        "name": "project-context",
        "path": "project-context.json",
        "kind": "json",
        "content": {
            "dependency": {
                "target_repo": "mglpsw/AnotherRepo",
                "pr_number": 999,
                "commit_sha": "sha-other",
            }
        },
    }
    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )
    assert brief.target["repository"] == "mglpsw/AgentEscala"
    assert brief.target["pr_number"] == 61
    assert brief.target["commit_sha"] == "abc123"


def test_pr_brief_does_not_use_generic_artifact_mode_as_review_mode() -> None:
    intake = _intake()
    intake.artifacts["project-context"] = {
        "name": "project-context",
        "path": "project-context.json",
        "kind": "json",
        "content": {"mode": "current_run_only"},
    }
    file_context = intake.artifacts["file-diff-context"]["content"]
    file_context.pop("review_mode", None)

    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )
    assert brief.review["mode"] is None


def test_pr_brief_uses_stable_ordering() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    files = [item["path"] for item in brief.changed_files_summary["files"]]
    assert files == sorted(files)
    groups = [item["semantic_group"] for item in brief.semantic_groups]
    assert groups == sorted(groups)


def test_pr_brief_sanitizes_secrets_and_absolute_paths() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"][0]["summary"] = (
        "token=SUPERSECRET path=/home/dev/private/file.py win=C:\\Users\\dev\\private\\file.py"
    )

    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    rendered = _rendered(brief)
    assert "SUPERSECRET" not in rendered
    assert "/home/dev/private/file.py" not in rendered
    assert "C:/Users/dev/private/file.py" not in rendered
    assert "[REDACTED]" in rendered or "[LOCAL_PATH_REDACTED]" in rendered


def test_pr_brief_is_byte_deterministic_for_same_inputs() -> None:
    first = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )
    second = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    assert _rendered(first) == _rendered(second)


def test_pr_brief_applies_budget_and_explicit_truncation() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=2500,
    )

    assert brief.truncation.applied is True
    assert brief.truncation.original_chars > brief.truncation.emitted_chars
    assert brief.truncation.omitted_sections
    assert brief.truncation.truncation_reason
    final_payload = brief.model_dump(mode="json")
    assert brief.truncation.emitted_chars == _canonical_len(final_payload)
    assert _canonical_len(final_payload) <= 2500


def test_pr_brief_budget_len_reflects_post_sanitization_serialized_artifact() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"][0]["summary"] = (
        "token=SUPERSECRET path=/opt/private/really/long/path/with/many/segments/example.py"
    )
    brief = build_pr_brief(
        intake=intake,
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=2500,
    )
    final_payload = brief.model_dump(mode="json")
    assert brief.truncation.emitted_chars == _canonical_len(final_payload)
    assert _canonical_len(final_payload) <= 2500


def test_pr_brief_truncation_preserves_true_changed_file_count() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=1300,
    )
    payload = brief.model_dump(mode="json")
    changed = payload["changed_files_summary"]
    assert brief.truncation.applied is True
    assert changed["total_files"] == 3
    assert changed["total_files"] == sum(changed["status_counts"].values())


def test_pr_brief_truncation_preserves_semantic_group_file_counts() -> None:
    baseline = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=20_000,
    )
    baseline_counts = {
        item["semantic_group"]: item["file_count"] for item in baseline.model_dump(mode="json")["semantic_groups"]
    }
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=1300,
    )
    groups = brief.model_dump(mode="json")["semantic_groups"]
    assert brief.truncation.applied is True
    assert any(group["file_count"] > len(group["files"]) for group in groups)
    for group in groups:
        assert group["file_count"] == baseline_counts[group["semantic_group"]]


def test_pr_brief_non_truncated_emitted_chars_match_final_artifact() -> None:
    brief = build_pr_brief(
        intake=_intake(),
        chunk_plan=_chunk_plan(),
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
        max_chars=20_000,
    )
    final_payload = brief.model_dump(mode="json")
    assert brief.truncation.applied is False
    assert brief.truncation.original_chars == _canonical_len(final_payload)
    assert brief.truncation.emitted_chars == _canonical_len(final_payload)
    assert _canonical_len(final_payload) <= 20_000


def test_pr_brief_rejects_non_positive_budget() -> None:
    with pytest.raises(PRBriefError) as exc:
        build_pr_brief(
            intake=_intake(),
            chunk_plan=_chunk_plan(),
            redaction_report=_redaction_report(),
            checks=None,
            validation_evidence=None,
            max_chars=0,
        )
    assert exc.value.error_class == "brief_budget_invalid"


def test_pr_brief_fallback_uses_union_of_coverage_lists_when_file_diff_context_has_no_files() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"] = []
    chunk_plan = _chunk_plan()
    chunk_plan.files_covered = ["src/covered.py", "src/shared.py"]
    chunk_plan.files_partially_covered = ["src/partial.py", "src/shared.py"]
    chunk_plan.files_not_covered = ["src/not-covered.py", "src/shared.py"]

    brief = build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    payload = brief.model_dump(mode="json")
    changed = payload["changed_files_summary"]
    changed_paths = [item["path"] for item in changed["files"]]
    assert changed["total_files"] == 4
    assert changed["status_counts"] == {"unknown": 4}
    assert changed_paths == sorted(changed_paths)
    assert changed_paths == ["src/covered.py", "src/not-covered.py", "src/partial.py", "src/shared.py"]
    assert all(item["status"] == "unknown" for item in changed["files"])
    assert all(item["summary"] is None for item in changed["files"])
    assert payload["coverage"]["files_not_covered"] == ["src/not-covered.py", "src/shared.py"]


def test_pr_brief_fallback_is_byte_deterministic_without_file_diff_context() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"] = []
    chunk_plan = _chunk_plan()
    chunk_plan.files_covered = ["b.py", "a.py"]
    chunk_plan.files_partially_covered = ["c.py"]
    chunk_plan.files_not_covered = ["d.py"]

    first = build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )
    second = build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    assert _rendered(first) == _rendered(second)


def test_pr_brief_fallback_handles_no_covered_or_uncovered_files() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"] = []
    chunk_plan = _chunk_plan()
    chunk_plan.files_covered = []
    chunk_plan.files_partially_covered = []
    chunk_plan.files_not_covered = []

    brief = build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )

    changed = brief.model_dump(mode="json")["changed_files_summary"]
    assert changed["total_files"] == 0
    assert changed["status_counts"] == {}
    assert changed["files"] == []
