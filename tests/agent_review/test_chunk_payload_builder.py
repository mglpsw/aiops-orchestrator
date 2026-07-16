from __future__ import annotations

import json

import pytest

from app.agent_review.chunk_payload_builder import ChunkPayloadBuilderError, build_chunk_payloads
from app.agent_review.pr_brief import build_pr_brief
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
                        {"id": "rule-api", "description": "API contract preservation"},
                        {"id": "rule-tests", "description": "tests must cover changed behavior"},
                    ]
                },
                "review_packs": {
                    "packs": [
                        {
                            "id": "agentescala-calendar",
                            "description": "Calendar review pack",
                            "recommended_review_preset": "review:deep",
                        }
                    ]
                },
            },
            "artifacts": {
                "file-diff-context": {
                    "name": "file-diff-context",
                    "path": "file-diff-context.json",
                    "kind": "json",
                    "content": {
                        "files": [
                            {"path": "backend/api/shifts.py", "status": "modified", "summary": "api update"},
                            {"path": "tests/test_shift_service.py", "status": "modified", "summary": "test update"},
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
                            "index 111..222 100644",
                            "--- a/backend/api/shifts.py",
                            "+++ b/backend/api/shifts.py",
                            "@@ -10,1 +10,1 @@",
                            "+token=SUPERSECRET",
                            "diff --git a/tests/test_shift_service.py b/tests/test_shift_service.py",
                            "index 333..444 100644",
                            "--- a/tests/test_shift_service.py",
                            "+++ b/tests/test_shift_service.py",
                            "@@ -1,1 +1,1 @@",
                            "+assert True",
                        ]
                    ),
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
                "validation-evidence-result": {
                    "name": "validation-evidence-result",
                    "path": "validation-evidence/validation-evidence-result.json",
                    "kind": "json",
                    "content": {
                        "validation_verdict": "degraded",
                        "blocking_findings": [
                            {"title": "API risk", "severity": "P1", "file_path": "backend/api/shifts.py"},
                            {"title": "Other risk", "severity": "P2", "file_path": "other/file.py"},
                        ],
                        "limitations": [],
                    },
                },
                "project-context": {
                    "name": "project-context",
                    "path": "project-context.json",
                    "kind": "json",
                    "content": {
                        "status": "complete",
                        "modules": {
                            "backend/api/shifts.py": "API handlers",
                            "tests/test_shift_service.py": "Regression tests",
                        },
                    },
                },
                "test-intelligence": {
                    "name": "test-intelligence",
                    "path": "test-intelligence.json",
                    "kind": "json",
                    "content": {
                        "changed_tests": ["tests/test_shift_service.py"],
                        "failed_tests": [],
                    },
                },
                "local-code-intelligence": {
                    "name": "local-code-intelligence",
                    "path": "local-code-intelligence.json",
                    "kind": "json",
                    "content": {
                        "mode": "current_run_only",
                        "files_analyzed": ["backend/api/shifts.py"],
                        "confirmed_local_failures": [],
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
                {"name": "full-diff", "path": "full.diff", "available": True, "valid": True, "status": "available"},
            ],
            "redaction_summary": {"schema_version": "agent-review.redaction-report.v1"},
            "limitations": [],
            "completeness": {},
            "created_at": "2026-06-02T00:00:00Z",
            "status": "complete",
        }
    )


def _chunk_plan(reverse_order: bool = False, include_empty_chunk: bool = False) -> SemanticChunkPlan:
    chunks = [
        SemanticChunk(
            chunk_id="chunk-01-api_schema_contract",
            semantic_group="api_schema_contract",
            order_index=0,
            files=["backend/api/shifts.py"],
            artifacts=["artifact:file-diff-context"],
            contracts=["target_profile:domain_contracts"],
            depends_on=[],
            coverage="complete",
            prompt_budget_chars=3_000,
            estimated_chars=1_000,
            limitations=[],
        ),
        SemanticChunk(
            chunk_id="chunk-02-tests",
            semantic_group="tests",
            order_index=1,
            files=["tests/test_shift_service.py"],
            artifacts=["artifact:checks"],
            contracts=[],
            depends_on=[],
            coverage="complete",
            prompt_budget_chars=3_000,
            estimated_chars=900,
            limitations=[],
        ),
    ]
    if include_empty_chunk:
        chunks.append(
            SemanticChunk(
                chunk_id="chunk-03-unknown",
                semantic_group="unknown",
                order_index=2,
                files=[],
                artifacts=[],
                contracts=[],
                depends_on=[],
                coverage="degraded",
                prompt_budget_chars=1_000,
                estimated_chars=0,
                limitations=["chunk_degraded"],
            )
        )
    if reverse_order:
        chunks = list(reversed(chunks))
    return SemanticChunkPlan.model_validate(
        {
            "schema_version": 1,
            "schema_id": "agent-review.semantic-chunk-plan.v1",
            "source": "aiops-semantic-chunk-planner",
            "target_repo": "mglpsw/AgentEscala",
            "max_parallel_blocks": 6,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
            "files_covered": ["backend/api/shifts.py", "tests/test_shift_service.py"],
            "files_partially_covered": [],
            "files_not_covered": [],
            "limitations": [],
            "status": "complete",
            "created_at": "2026-06-02T00:00:00Z",
        }
    )


def _redaction_report() -> RedactionReport:
    return RedactionReport.model_validate(
        {
            "schema_version": "agent-review.redaction-report.v1",
            "source": "aiops-review-intake",
            "files_processed": 2,
            "replacements_by_type": {"token_assignment": 1},
            "secret_like_values_found": 1,
            "redacted_lines_present": True,
            "redaction_is_sanitizer_artifact": True,
            "hardcoded_secret_confirmed": False,
            "output_safe_for_llm": True,
            "limitations": [],
        }
    )


def _brief(intake: ReviewIntake, chunk_plan: SemanticChunkPlan):  # noqa: ANN201
    return build_pr_brief(
        intake=intake,
        chunk_plan=chunk_plan,
        redaction_report=_redaction_report(),
        checks=None,
        validation_evidence=None,
    )


def _render(payload) -> str:  # noqa: ANN001
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _canonical_len(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def test_chunk_payload_builder_generates_one_payload_per_chunk() -> None:
    intake = _intake()
    plan = _chunk_plan()
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )

    assert manifest.payload_count == len(plan.chunks)
    assert len(payloads) == len(plan.chunks)
    assert {entry.chunk_id for entry in manifest.chunks} == {chunk.chunk_id for chunk in plan.chunks}


def test_chunk_payload_builder_keeps_context_bounded_to_chunk_files() -> None:
    intake = _intake()
    plan = _chunk_plan()
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=20_000,
    )

    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    test_payload = payloads["chunk-02-tests.json"].model_dump(mode="json")
    assert [item["path"] for item in api_payload["chunk_context"]["files"]] == ["backend/api/shifts.py"]
    assert [item["path"] for item in test_payload["chunk_context"]["files"]] == ["tests/test_shift_service.py"]
    assert "tests/test_shift_service.py" not in _render(api_payload["chunk_context"]["chunk_hunks"])


def test_chunk_payload_builder_includes_hunks_contracts_evidence_and_response_contract() -> None:
    intake = _intake()
    plan = _chunk_plan()
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=20_000,
    )

    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    assert api_payload["chunk_context"]["chunk_hunks"]
    assert api_payload["chunk_context"]["contracts_context"]["domain_contracts"]
    evidence = api_payload["chunk_context"]["evidence_context"]["validation_evidence"]["blocking_findings"]
    assert evidence and evidence[0]["file_path"] == "backend/api/shifts.py"
    assert "required_fields" in api_payload["response_contract"]


def test_chunk_payload_builder_handles_empty_chunk_as_limited() -> None:
    intake = _intake()
    plan = _chunk_plan(include_empty_chunk=True)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )

    empty_entry = next(item for item in manifest.chunks if item.chunk_id == "chunk-03-unknown")
    assert empty_entry.status == "limited"
    assert "chunk_has_no_files:chunk-03-unknown" in empty_entry.limitations
    assert "chunk-03-unknown.json" in payloads


def test_chunk_payload_builder_applies_explicit_truncation_for_min_budget() -> None:
    intake = _intake()
    plan = _chunk_plan()
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=900,
    )

    assert any(entry.truncation.applied for entry in manifest.chunks)
    assert any(payload.truncation.applied for payload in payloads.values())
    for payload in payloads.values():
        dumped = payload.model_dump(mode="json")
        assert payload.truncation.emitted_chars == _canonical_len(dumped)
        if payload.truncation.truncation_reason != "max_chars_exceeded_minimum_required_sections":
            assert _canonical_len(dumped) <= 900


def test_chunk_payload_builder_identity_stable_when_plan_chunk_list_order_changes() -> None:
    intake = _intake()
    plan_a = _chunk_plan(reverse_order=False)
    plan_b = _chunk_plan(reverse_order=True)
    brief_a = _brief(intake, plan_a)
    brief_b = _brief(intake, plan_b)
    manifest_a, payloads_a = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan_a,
        pr_brief=brief_a,
        checks=None,
        validation_evidence=None,
    )
    manifest_b, payloads_b = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan_b,
        pr_brief=brief_b,
        checks=None,
        validation_evidence=None,
    )

    hashes_a = {entry.chunk_id: entry.payload_sha256 for entry in manifest_a.chunks}
    hashes_b = {entry.chunk_id: entry.payload_sha256 for entry in manifest_b.chunks}
    assert hashes_a == hashes_b
    assert _render(payloads_a["chunk-01-api_schema_contract.json"].model_dump(mode="json")) == _render(
        payloads_b["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    )


def test_chunk_payload_builder_redacts_absolute_paths_and_secrets() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[0].files = ["/tmp/backend/api/shifts.py"]
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=20_000,
    )

    rendered = _render(payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json"))
    assert "/tmp/backend/api/shifts.py" not in rendered
    assert "SUPERSECRET" not in rendered
    assert "[LOCAL_PATH_REDACTED]" in rendered


def test_chunk_payload_builder_fails_closed_on_target_repo_identity_conflict() -> None:
    intake = _intake()
    plan = _chunk_plan()
    brief = _brief(intake, plan)
    plan.target_repo = "mglpsw/AnotherRepo"

    with pytest.raises(ChunkPayloadBuilderError) as exc:
        build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=brief,
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_chunk_payload_builder_fails_closed_on_pr_number_identity_conflict() -> None:
    intake = _intake()
    plan = _chunk_plan()
    brief = _brief(intake, plan)
    brief.target["pr_number"] = 999

    with pytest.raises(ChunkPayloadBuilderError) as exc:
        build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=brief,
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_chunk_payload_builder_fails_closed_on_validation_evidence_identity_conflict() -> None:
    intake = _intake()
    plan = _chunk_plan()
    brief = _brief(intake, plan)

    with pytest.raises(ChunkPayloadBuilderError) as exc:
        build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=brief,
            checks=None,
            validation_evidence={"pr_number": 999},
        )
    assert exc.value.error_class == "review_identity_conflict"


def test_chunk_payload_builder_filters_local_failures_by_chunk_scope() -> None:
    intake = _intake()
    intake.artifacts["local-code-intelligence"]["content"]["confirmed_local_failures"] = [
        {"title": "api failure", "file_path": "backend/api/shifts.py"},
        {"title": "tests failure", "path": "tests/test_shift_service.py"},
        {"title": "global failure", "scope": "global"},
        {"title": "unscoped failure"},
    ]
    plan = _chunk_plan()
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )

    api_failures = payloads["chunk-01-api_schema_contract.json"].chunk_context["evidence_context"]["local_code_intelligence"][
        "confirmed_local_failures"
    ]
    tests_failures = payloads["chunk-02-tests.json"].chunk_context["evidence_context"]["local_code_intelligence"][
        "confirmed_local_failures"
    ]
    assert {item["title"] for item in api_failures} == {"api failure", "global failure"}
    assert {item["title"] for item in tests_failures} == {"tests failure", "global failure"}


def test_chunk_payload_builder_filters_file_scoped_checks_by_chunk() -> None:
    intake = _intake()
    plan = _chunk_plan()
    checks = {
        "status": "complete",
        "checks": [
            {"name": "api-check", "status": "passed", "command": "api", "files": ["backend/api/shifts.py"]},
            {"name": "tests-check", "status": "passed", "command": "tests", "paths": ["tests/test_shift_service.py"]},
            {"name": "global-check", "status": "passed", "command": "global", "scope": "global"},
        ],
    }
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=checks,
        validation_evidence=None,
    )

    api_checks = payloads["chunk-01-api_schema_contract.json"].chunk_context["checks_context"]["checks"]
    tests_checks = payloads["chunk-02-tests.json"].chunk_context["checks_context"]["checks"]
    assert {item["name"] for item in api_checks} == {"api-check", "global-check"}
    assert {item["name"] for item in tests_checks} == {"tests-check", "global-check"}


def test_chunk_payload_builder_keeps_document_scoped_checks_for_each_chunk() -> None:
    intake = _intake()
    plan = _chunk_plan()
    checks = {
        "status": "complete",
        "mode": "current_run_only",
        "checks": [
            {"name": "pytest", "status": "passed", "command": "python -m pytest"},
            {"name": "ruff", "status": "passed", "command": "ruff check ."},
        ],
    }
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=checks,
        validation_evidence=None,
    )

    for payload in payloads.values():
        checks_context = payload.chunk_context["checks_context"]
        assert {item["name"] for item in checks_context["checks"]} == {"pytest", "ruff"}
        assert {item["scope"] for item in checks_context["checks"]} == {"document"}
    assert not any("check_scope_unclassified:" in item for entry in manifest.chunks for item in entry.limitations)


def test_chunk_payload_builder_response_contract_uses_parser_supported_fields() -> None:
    intake = _intake()
    plan = _chunk_plan()
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )
    payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    requirements = payload["response_contract"]["finding_requirements"]
    assert "source_artifact" in requirements
    assert "line_or_hunk" in requirements
    assert "source_artifact_or_line_or_hunk" not in requirements
    assert payload["response_contract"]["finding_provenance_requirement"] == "at_least_one_of:source_artifact,line_or_hunk"


def test_chunk_payload_builder_sanitizes_manifest_metadata() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[0].chunk_id = "chunk-01-token=SUPERSECRET"
    brief = _brief(intake, plan)
    manifest, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    rendered = _render(manifest.model_dump(mode="json"))
    assert "SUPERSECRET" not in rendered
    payload_paths = {entry.payload_path for entry in manifest.chunks}
    assert all(payload_path and "SUPERSECRET" not in payload_path for payload_path in payload_paths)
    assert payload_paths == set(payloads)


def test_chunk_payload_builder_does_not_fallback_to_arbitrary_contracts() -> None:
    intake = _intake()
    intake.target_profile["domain_contracts"]["rules"] = [
        {"id": "backend_service_rule", "description": "backend service contract"},
        {"id": "database_rule", "description": "database contract"},
    ]
    intake.target_profile["review_packs"]["packs"] = [
        {"id": "review-pack-backend", "description": "backend review pack"},
    ]
    plan = _chunk_plan()
    plan.chunks[0].semantic_group = "frontend_ui"
    plan.chunks[0].contracts = []
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )

    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    contracts_context = api_payload["chunk_context"]["contracts_context"]
    assert contracts_context["domain_contracts"] == []
    assert contracts_context["review_packs"] == []
    assert "contracts_context_not_relevant:chunk-01-api_schema_contract" in api_payload["limitations"]


def test_chunk_payload_builder_keeps_selected_contract_pack_from_brief() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["contract_pack"] = "calendar"
    plan = _chunk_plan()
    plan.chunks[0].contracts = []
    brief = _brief(intake, plan)
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=brief,
        checks=None,
        validation_evidence=None,
    )

    packs = payloads["chunk-01-api_schema_contract.json"].chunk_context["contracts_context"]["review_packs"]
    assert [item["id"] for item in packs] == ["agentescala-calendar"]


def test_chunk_payload_builder_parses_quoted_unicode_rename_and_deleted_diff_paths() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"] = [
        {"path": "backend/my file.py", "status": "modified", "summary": "spaces"},
        {"path": "docs/ação clínica.md", "status": "modified", "summary": "unicode"},
        {"path": "new.py", "status": "renamed", "summary": "rename"},
        {"path": "obsolete.py", "status": "removed", "summary": "removed"},
    ]
    intake.artifacts["full-diff"]["content"] = "\n".join(
        [
            'diff --git "a/backend/my file.py" "b/backend/my file.py"',
            "index 111..222 100644",
            '--- "a/backend/my file.py"',
            '+++ "b/backend/my file.py"',
            "@@ -1 +1 @@",
            "+print('ok')",
            'diff --git "a/docs/ação clínica.md" "b/docs/ação clínica.md"',
            "index 333..444 100644",
            '--- "a/docs/ação clínica.md"',
            '+++ "b/docs/ação clínica.md"',
            "@@ -1 +1 @@",
            "+conteúdo",
            'diff --git "a/old.py" "b/new.py"',
            "similarity index 95%",
            "rename from old.py",
            "rename to new.py",
            "--- a/old.py",
            "+++ b/new.py",
            "@@ -1 +1 @@",
            "+renamed",
            "diff --git a/obsolete.py b/obsolete.py",
            "deleted file mode 100644",
            "index 444..0000000",
            "--- a/obsolete.py",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-old content",
        ]
    )
    plan = _chunk_plan()
    plan.chunks[0].files = ["backend/my file.py", "docs/ação clínica.md", "new.py", "obsolete.py"]
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=20_000,
    )
    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    hunk_paths = {item["path"] for item in api_payload["chunk_context"]["chunk_hunks"]}
    assert hunk_paths == {"backend/my file.py", "docs/ação clínica.md", "new.py", "obsolete.py"}


def test_chunk_payload_builder_parses_octal_quoted_header_paths_without_plus_markers() -> None:
    intake = _intake()
    intake.artifacts["file-diff-context"]["content"]["files"] = [
        {"path": "docs/ação clínica.md", "status": "modified", "summary": "unicode"},
    ]
    intake.artifacts["full-diff"]["content"] = "\n".join(
        [
            'diff --git "a/docs/a\\303\\247\\303\\243o\\040cl\\303\\255nica.md" "b/docs/a\\303\\247\\303\\243o\\040cl\\303\\255nica.md"',
            "@@ -1 +1 @@",
            "+conteúdo",
        ]
    )
    plan = _chunk_plan()
    plan.chunks[0].files = ["docs/ação clínica.md"]
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=20_000,
    )

    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    hunk_paths = {item["path"] for item in api_payload["chunk_context"]["chunk_hunks"]}
    assert hunk_paths == {"docs/ação clínica.md"}


def test_chunk_payload_builder_records_missing_hunks_as_limitations() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[0].files = ["backend/api/shifts.py", "backend/missing.py"]
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
    )
    api_payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    assert "chunk_diff_hunk_missing:backend/missing.py" in api_payload["limitations"]
    assert api_payload["coverage"]["hunks_included"] == 1
    assert api_payload["coverage"]["chunk_file_count"] == 2


def test_chunk_payload_builder_updates_hunk_coverage_after_truncation_removes_hunks() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[0].files = ["backend/api/shifts.py"]
    _, payloads = build_chunk_payloads(
        intake=intake,
        chunk_plan=plan,
        pr_brief=_brief(intake, plan),
        checks=None,
        validation_evidence=None,
        max_chars_per_payload=900,
    )
    payload = payloads["chunk-01-api_schema_contract.json"].model_dump(mode="json")
    assert payload["coverage"]["hunks_included"] == len(payload["chunk_context"]["chunk_hunks"])


def test_chunk_payload_builder_rejects_duplicate_chunk_ids() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[1].chunk_id = plan.chunks[0].chunk_id
    with pytest.raises(ChunkPayloadBuilderError) as exc:
        build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=_brief(intake, plan),
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "chunk_plan_duplicate_chunk_id"


def test_chunk_payload_builder_rejects_duplicate_order_indexes() -> None:
    intake = _intake()
    plan = _chunk_plan()
    plan.chunks[1].order_index = plan.chunks[0].order_index
    with pytest.raises(ChunkPayloadBuilderError) as exc:
        build_chunk_payloads(
            intake=intake,
            chunk_plan=plan,
            pr_brief=_brief(intake, plan),
            checks=None,
            validation_evidence=None,
        )
    assert exc.value.error_class == "chunk_plan_duplicate_order_index"
